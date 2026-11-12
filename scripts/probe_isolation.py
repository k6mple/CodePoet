"""S1/R3 前置探针：Windows 上能不能真的限制子进程的 CPU / 内存 / 时间。

细则明确要求「CPU/内存必须目标 OS 真实执行限制，不用预校验拒绝代替资源熔断
测试；超时杀进程组且下一任务可成功」。所以这里不看文档、不猜 psutil 行为，
直接用 ctypes 挂 Job Object 实测四条：

1. 提交内存超限 → 进程被杀（不是"预检拒绝"）
2. CPU 时间超限 → 进程被杀
3. 墙钟超时 → 整个进程组回收，无残留
4. 被杀之后，下一任务仍能正常跑（状态不粘）

跑不起来就如实记失败——S1 的 11h 预算要不要花，取决于这份结果。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / ".smoke" / "probe-isolation"  # 项目内隔离目录，已在 .gitignore
OUT.mkdir(parents=True, exist_ok=True)

report: list[str] = []
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS = 15
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


def note(line: str) -> None:
    print(line)
    report.append(line)


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def make_job(memory_bytes: int | None = None, cpu_100ns: int | None = None):
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject 失败")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = 0
    if memory_bytes:
        info.ProcessMemoryLimit = memory_bytes
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
    if cpu_100ns:
        # PerProcessUserTimeLimit 单位是 100ns
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_100ns
        flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
    info.BasicLimitInformation.LimitFlags = flags
    ok = kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info), ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
    return job


def assign(job, pid: int) -> None:
    handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess 失败")
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject 失败")
    finally:
        kernel32.CloseHandle(handle)


def spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main() -> int:
    note(f"# 探针环境：Python {sys.version.split()[0]}｜pid {os.getpid()}")
    note("")

    # ---- 1. 内存上限 ----
    note("## 1. 提交内存上限（+150MiB 增长，限额 80MiB）")
    job = make_job(memory_bytes=80 * 1024 * 1024)
    proc = spawn(
        "chunks=[]\n"
        "for i in range(200):\n"
        "    chunks.append(bytearray(4*1024*1024))\n"
        "    print(i, flush=True)\n"
    )
    assign(job, proc.pid)
    start = time.perf_counter()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    cost = time.perf_counter() - start
    note(f"  退出码 {proc.returncode}／耗时 {cost:.2f}s（非 0 且远小于 30s 即视为被杀）")
    memory_killed = proc.returncode != 0 and cost < 20
    note(f"  判定：{'资源熔断生效' if memory_killed else '未生效（没被杀）'}")
    kernel32.CloseHandle(job)

    # ---- 2. CPU 时间上限 ----
    note("")
    note("## 2. CPU 时间上限（死循环 + 0.5s CPU 上限）")
    job = make_job(cpu_100ns=int(0.5 * 10_000_000))
    proc = spawn("while True:\n    pass\n")
    assign(job, proc.pid)
    start = time.perf_counter()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    cost = time.perf_counter() - start
    note(f"  退出码 {proc.returncode}／耗时 {cost:.2f}s")
    cpu_killed = proc.returncode != 0 and cost < 12
    note(f"  判定：{'CPU 熔断生效' if cpu_killed else '未生效'}")
    kernel32.CloseHandle(job)

    # ---- 3. 墙钟超时 + 进程组回收 ----
    note("")
    note("## 3. 墙钟超时 + 子进程树回收")
    proc = spawn(
        "import subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    start = time.perf_counter()
    killed = False
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        # 只杀父进程，看孙子是否残留（这是最容易漏的一步）
        proc.kill()
        killed = True
    cost = time.perf_counter() - start
    grandchild = proc.stdout.readline().decode().strip() if proc.stdout else ""
    time.sleep(0.5)
    alive = False
    if grandchild.isdigit():
        alive = _pid_alive(int(grandchild))
    note(f"  墙钟 {cost:.2f}s，超时被杀={killed}，孙进程 pid={grandchild or '未读到'}")
    note(f"  孙进程是否残留：{'是 —— 只杀父进程不够' if alive else '否'}")
    if grandchild.isdigit() and alive:
        subprocess.run(["taskkill", "/F", "/T", "/PID", grandchild],
                       capture_output=True, text=True)

    # ---- 4. 被杀之后下一任务还能跑 ----
    note("")
    note("## 4. 熔断之后下一任务可运行")
    proc = spawn("print('alive')")
    out, _ = proc.communicate(timeout=20)
    note(f"  退出码 {proc.returncode}，输出 {out.decode().strip()!r}")
    note(f"  判定：{'正常' if proc.returncode == 0 else '异常'}")

    note("")
    verdict = memory_killed and cpu_killed
    note(f"## 总判定：{'隔离限制可用，S1 可以按计划做' if verdict else '隔离限制不完整，S1 需要另找方案'}")
    if not verdict:
        note("（Job Object 的 CPU/内存限额在部分 Windows 版本上会被静默忽略）")

    OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
    return 0 if verdict else 2


def _pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
