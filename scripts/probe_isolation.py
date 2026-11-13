"""S1/R3 前置探针：Windows 上能不能真的限制子进程的 CPU / 内存 / 时间。

细则要求「CPU/内存必须目标 OS 真实执行限制，不用预校验拒绝代替资源熔断测试；
超时杀进程组且下一任务可成功」。

## 为什么这个版本比上一版可信

上一版只用「退出码非零 + 耗时比预期短」判断熔断。**这个判据分不清三件事**：
资源超限被杀、普通异常退出、外部终止——三者都是「非零 + 短」。评审用一个
普通异常退出的进程（退出码 1、0.24s）就复现了误判。

所以这一版给每一组都配**同负载无约束对照组**，并读取 **Job 完成端口通知**：

1. **对照组**：同样的负载、不挂任何限额，用来排除「这个负载自己就会提前退出」。
   实验组与对照组的**负载时长完全一致**，比时长才有意义。
2. **Job 通知**：内核会明确告诉你进程是 NORMAL_EXIT 还是 ABNORMAL_EXIT，
   还是触发了 PROCESS_MEMORY_LIMIT / END_OF_PROCESS_TIME。这比猜退出码可靠。
3. **进度输出**：内存负载每分配一块就打印序号。对照组跑到 N 块，实验组只跑到
   M < N 块 —— 说明它是被拦住的，不是自己退出的。

三条证据互相独立。任何一条拿不到，报告里会明确标注，不拿它当结论。

另外补两项 S1 worker 真正需要的验证：`TerminateJobObject` 能不能整体回收进程树、
回收后活跃进程是否归零。上一轮验证过「只 kill 父进程会留下孙进程」，
这次要证明正确的做法有效。
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

# --- 常量 ---------------------------------------------------------------
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION_CLASS = 7
# 违规信息类在 Win8 起有多个版本，实际值靠探测（见 find_violation_class）
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 完成端口消息号（MSDN: Job object notifications）
JOB_MESSAGES = {
    1: "END_OF_JOB_TIME",
    2: "END_OF_PROCESS_TIME",
    3: "ACTIVE_PROCESS_LIMIT",
    4: "ACTIVE_PROCESS_ZERO",
    6: "NEW_PROCESS",
    7: "EXIT_PROCESS",
    8: "ABNORMAL_EXIT_PROCESS",
    9: "PROCESS_MEMORY_LIMIT",
    10: "JOB_MEMORY_LIMIT",
    11: "NOTIFICATION_LIMIT",
}
LIMIT_MESSAGES = {
    "END_OF_JOB_TIME",
    "END_OF_PROCESS_TIME",
    "PROCESS_MEMORY_LIMIT",
    "JOB_MEMORY_LIMIT",
    "ACTIVE_PROCESS_LIMIT",
}


def note(line: str = "") -> None:
    print(line)
    report.append(line)


# --- 结构体 -------------------------------------------------------------
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


class JOBOBJECT_LIMIT_VIOLATION_INFORMATION(ctypes.Structure):
    """违规信息。字段名按 MSDN，本探针只用 LimitFlags 与前三个计数字段。"""

    _fields_ = [
        ("LimitFlags", wt.DWORD),
        ("ViolationLimitFlags", wt.DWORD),
        ("IoReadBytes", ctypes.c_uint64),
        ("IoReadOperations", ctypes.c_uint64),
        ("IoWriteBytes", ctypes.c_uint64),
        ("IoWriteOperations", ctypes.c_uint64),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("JobPeakMemoryUsed", ctypes.c_size_t),
        ("JobMaximumWorkingSetSize", ctypes.c_size_t),
        ("JobMinimumWorkingSetSize", ctypes.c_size_t),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("ProcessPeakMemoryUsed", ctypes.c_size_t),
        ("ProcessMaximumWorkingSetSize", ctypes.c_size_t),
        ("ProcessMinimumWorkingSetSize", ctypes.c_size_t),
    ]


class JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
    _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", wt.HANDLE)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wt.DWORD),
        ("TotalProcesses", wt.DWORD),
        ("ActiveProcesses", wt.DWORD),
        ("TotalTerminatedProcesses", wt.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 句柄必须显式声明类型：默认 restype 是 c_int，64 位句柄会被截成 32 位；
# 而 INVALID_HANDLE_VALUE 直接当 int 传又会 unsigned 溢出。两类错都只会在
# 「句柄恰好高位非零」时炸，属于最难复现的那种。
_void_p = ctypes.c_void_p
kernel32.CreateJobObjectW.restype = wt.HANDLE
kernel32.CreateJobObjectW.argtypes = [_void_p, wt.LPCWSTR]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CreateIoCompletionPort.restype = wt.HANDLE
kernel32.CreateIoCompletionPort.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.c_size_t, wt.DWORD]
kernel32.GetQueuedCompletionStatus.argtypes = [
    wt.HANDLE,
    ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.POINTER(_void_p),
    wt.DWORD,
]
kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, _void_p, wt.DWORD]
kernel32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, _void_p, wt.DWORD, _void_p]
kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


# --- Job 基础操作 -------------------------------------------------------
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
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_100ns
        flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
    info.BasicLimitInformation.LimitFlags = flags
    ok = kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
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


def job_process_count(job) -> int:
    """读活跃进程数。用基本统计信息（class 1），字段稳定。"""
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    ok = kernel32.QueryInformationJobObject(
        job, 1, ctypes.byref(info), ctypes.sizeof(info), None
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "QueryInformationJobObject(统计) 失败")
    return int(info.ActiveProcesses)


def find_violation_class(job) -> int | None:
    """探测「限额违规信息」的信息类编号。

    不靠记忆写死：不同 Windows 版本这个枚举值不一样，写错会静默读到垃圾。
    逐个试，谁能让 Query 成功且 LimitFlags 与设置值一致，就认谁。
    """
    info = JOBOBJECT_LIMIT_VIOLATION_INFORMATION()
    for cls in range(11, 21):
        ok = kernel32.QueryInformationJobObject(
            job, cls, ctypes.byref(info), ctypes.sizeof(info), None
        )
        if ok and info.LimitFlags:
            note(f"  （探测到限额违规信息类 = {cls}，读回 LimitFlags=0x{info.LimitFlags:X}）")
            return cls
    note("  （未取到限额违规信息类；退出原因以 Job 通知为准）")
    return None


def violation_flags(job, cls: int | None) -> tuple[int, int] | None:
    if cls is None:
        return None
    info = JOBOBJECT_LIMIT_VIOLATION_INFORMATION()
    ok = kernel32.QueryInformationJobObject(
        job, cls, ctypes.byref(info), ctypes.sizeof(info), None
    )
    if not ok:
        return None
    return int(info.LimitFlags), int(info.ViolationLimitFlags)


class JobNotifications:
    """Job 完成端口：拿内核给的可区分原因。

    拿不到也不影响结论——主判据是对照组。但拿到了就能直接说清
    「这个进程是被 PROCESS_MEMORY_LIMIT 杀的」而不是靠退出码反推。
    """

    def __init__(self, job):
        self.messages: list[str] = []
        self.port = kernel32.CreateIoCompletionPort(_void_p(-1), None, 0, 1)
        self.ok = bool(self.port) and self.port != ctypes.c_void_p(-1).value
        if not self.ok:
            return
        assoc = JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
        assoc.CompletionKey = ctypes.c_void_p(1)
        assoc.CompletionPort = self.port
        self.ok = bool(
            kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION_CLASS,
                ctypes.byref(assoc),
                ctypes.sizeof(assoc),
            )
        )

    def drain(self) -> None:
        if not self.ok:
            return
        while True:
            transferred = wt.DWORD(0)
            key = ctypes.c_size_t(0)
            overlapped = ctypes.c_void_p()
            ok = kernel32.GetQueuedCompletionStatus(
                self.port,
                ctypes.byref(transferred),
                ctypes.byref(key),
                ctypes.byref(overlapped),
                0,  # 立即返回，不阻塞
            )
            if not ok:
                return
            name = JOB_MESSAGES.get(int(transferred.value), f"UNKNOWN({transferred.value})")
            self.messages.append(name)

    def close(self) -> None:
        if self.ok:
            kernel32.CloseHandle(self.port)


def limit_hits(messages: list[str]) -> list[str]:
    return [m for m in messages if m in LIMIT_MESSAGES]


def abnormal_exits(messages: list[str]) -> int:
    return sum(1 for m in messages if m == "ABNORMAL_EXIT_PROCESS")


# --- 进程辅助 -----------------------------------------------------------
def spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


MEMORY_LOAD = """chunks = []
for i in range(%d):
    chunks.append(bytearray(4 * 1024 * 1024))
    print(i, flush=True)
print("done", flush=True)
"""

BUSY_LOAD = """import time
end = time.time() + %d
n = 0
while time.time() < end:
    n += 1
print(n, flush=True)
"""


def run_memory(chunks: int, limit_bytes: int | None, label: str) -> dict:
    """跑内存增长负载。limit_bytes 为 None 即对照组。"""
    job = make_job(memory_bytes=limit_bytes) if limit_bytes else None
    notif = JobNotifications(job) if job else None

    proc = spawn(MEMORY_LOAD % chunks)
    if job:
        assign(job, proc.pid)

    start = time.perf_counter()
    timed_out = False
    try:
        out, err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        timed_out = True
    cost = time.perf_counter() - start

    if notif:
        time.sleep(0.2)
        notif.drain()
        notif.close()

    # 违规信息必须在进程结束**之后**读：还没超限时 LimitFlags 恒为 0，
    # 早读会误判成「本平台不支持这个信息类」。
    cls = find_violation_class(job) if job else None

    lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    progressed = sum(1 for line in lines if line.strip().isdigit())
    finished = lines and lines[-1].strip() == "done"

    result = {
        "label": label,
        "returncode": proc.returncode,
        "cost": cost,
        "progressed": progressed,
        "finished": bool(finished),
        "timed_out": timed_out,
        "messages": list(notif.messages) if notif else [],
        "limit_hits": limit_hits(notif.messages) if notif else [],
        "abnormal": abnormal_exits(notif.messages) if notif else 0,
        "violation": violation_flags(job, cls) if job else None,
    }
    note(
        f"  退出码 {proc.returncode}（0x{proc.returncode & 0xFFFFFFFF:08X}）"
        f"／耗时 {cost:.2f}s／分配了 {progressed} 块／跑完={bool(finished)}"
    )
    if proc.returncode == 1:
        note(
            "  注：这里的退出码 1 是 MemoryError 冒泡出来的，与「普通异常退出」"
            "数值上无法区分——正是评审指出的那个反例。区分靠进度块数与 Job 通知。"
        )
    if notif and notif.messages:
        note(f"  Job 通知：{notif.messages}")
    elif notif:
        note("  Job 通知：未取到消息")
    if result["violation"]:
        note(f"  违规标志：LimitFlags=0x{result['violation'][0]:X} 触发=0x{result['violation'][1]:X}")
    if job:
        kernel32.CloseHandle(job)
    return result


def main() -> int:
    note(f"# 探针环境：Python {sys.version.split()[0]}｜pid {os.getpid()}")
    note("")
    note("判据说明：**判定只认内核 Job 通知**（PROCESS_MEMORY_LIMIT / END_OF_PROCESS_TIME）。")
    note("「提前退出 + 耗时短」这类行为证据只作旁证——普通异常退出、外部终止同样符合，")
    note("拿它判定就会把「程序自己崩了」当成「限额生效」。取不到通知时标「未能判定」。")
    note("每一组另配同负载无约束对照组，负载时长与实验组完全一致。")
    note("")

    problems: list[str] = []

    # ---- 1. 内存：对照组 vs 限额组 ----
    note("## 1. 提交内存上限（负载 60 块 × 4MiB = 240MiB，限额 80MiB）")
    control = run_memory(60, None, "对照（无约束）")
    note("")
    note("  —— 实验组 ——")
    limit = run_memory(60, 80 * 1024 * 1024, "限额 80MiB")

    control_ok = control["finished"] and control["returncode"] == 0
    has_mem_evidence = any("MEMORY" in m for m in limit["limit_hits"])
    # 两条独立证据，任一成立即可判定，**但都必须以对照组有效为前提**：
    #   a) 内核明确发了 PROCESS_MEMORY_LIMIT 通知；
    #   b) 实验组没跑完、分配块数少于对照组、且比对照组早退。
    # 只靠「非零 + 短」不算数——那正是评审指出的误判来源。
    # 行为像熔断**不能**当成熔断：普通异常退出、外部终止同样是「提前 + 分配到一半」。
    # 判定只认一种证据——**内核明确发了 PROCESS_MEMORY_LIMIT 通知**。
    # 行为证据只写进报告做旁证，不参与判定；拿不到通知就标「未能判定」。
    memory_killed = control_ok and has_mem_evidence
    memory_looks_like_kill = (
        not limit["finished"]
        and limit["progressed"] < control["progressed"]
        and limit["cost"] < control["cost"]
    )
    note(
        f"  判定：对照组{'跑完' if control_ok else '未跑完（对照无效）'}；"
        f"实验组分配 {limit['progressed']}/{control['progressed']} 块"
        f"（{limit['progressed'] / max(control['progressed'], 1):.0%}）"
    )
    if memory_killed:
        note("  → 资源熔断生效（依据：内核 PROCESS_MEMORY_LIMIT 通知）")
    elif memory_looks_like_kill:
        note(
            "  → **未能判定**：行为上像被限额拦住，但没收到内核通知，"
            "不排除普通异常退出。不用行为反推原因。"
        )
        problems.append("内存限额：无内核通知，未能判定")
    else:
        note("  → 未生效（对照组跑完、实验组也没被拦住）")
        problems.append("内存限额判据不成立")

    # ---- 2. CPU：对照组 vs 限额组 ----
    note("")
    note("## 2. CPU 时间上限（对照与实验同一份 12s 忙循环，限额 0.5s CPU）")
    # 对照组必须用**同样时长**的负载：3s 对照 vs 12s 实验时，「实验比对照早结束」
    # 本身不说明任何问题——负载长度不同，比时长没有意义。
    control_cpu = spawn(BUSY_LOAD % 12)
    start = time.perf_counter()
    control_cpu.wait(timeout=30)
    control_cost = time.perf_counter() - start
    note(f"  对照（无约束）：退出码 {control_cpu.returncode}／耗时 {control_cost:.2f}s（应正常跑完 12s）")
    note("")
    note("  —— 实验组 ——")

    job = make_job(cpu_100ns=int(0.5 * 10_000_000))
    notif = JobNotifications(job)
    proc = spawn(BUSY_LOAD % 12)
    assign(job, proc.pid)
    start = time.perf_counter()
    timed_out = False
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        timed_out = True
    cpu_cost = time.perf_counter() - start
    time.sleep(0.2)
    notif.drain()
    notif.close()
    cls = find_violation_class(job)
    note(f"  退出码 {proc.returncode}（0x{proc.returncode & 0xFFFFFFFF:08X}）／耗时 {cpu_cost:.2f}s（负载本应跑 12s）")
    if notif.messages:
        note(f"  Job 通知：{notif.messages}")
    cpu_violation = violation_flags(job, cls)
    if cpu_violation:
        note(f"  违规标志：LimitFlags=0x{cpu_violation[0]:X} 触发=0x{cpu_violation[1]:X}")
    kernel32.CloseHandle(job)

    cpu_hits = limit_hits(notif.messages)
    cpu_time_notice = any("PROCESS_TIME" in m for m in cpu_hits)
    # 同样两条独立证据，都要求对照组有效（对照组 3s 负载正常完成）：
    #   a) 内核发了 END_OF_PROCESS_TIME；
    #   b) 没被我们超时杀掉，且耗时远小于负载自述的 12s。
    # 同样只认内核通知。「没被超时杀掉 + 耗时短」在普通异常退出下（0.1s 崩溃）
    # 同样成立，而且旧判据连退出码都没看——这是评审实测出来的漏洞。
    cpu_killed = control_cpu.returncode == 0 and cpu_time_notice
    cpu_looks_like_kill = not timed_out and cpu_cost < control_cost - 2
    note(
        f"  判定：对照组 {'正常完成 12s 负载' if control_cpu.returncode == 0 else '异常'}"
        f"；实验组 {cpu_cost:.2f}s 退出，负载本应 12s"
    )
    if cpu_killed:
        note("  → CPU 熔断生效（依据：内核 END_OF_PROCESS_TIME 通知）")
    elif cpu_looks_like_kill:
        note(
            "  → **未能判定**：比对照早结束，但没收到内核通知，"
            f"退出码 {proc.returncode} 也可能是自身崩溃。不用行为反推原因。"
        )
        problems.append("CPU 限额：无内核通知，未能判定")
    else:
        note("  → 未生效")
        problems.append("CPU 限额判据不成立")

    # ---- 3. 墙钟超时：只 kill 父进程 → 孙进程残留 ----
    note("")
    note("## 3. 墙钟超时只 kill 父进程（复现上一轮的残留问题）")
    proc = spawn(
        "import subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    first_child = int(proc.stdout.readline().decode().strip() or 0)
    start = time.perf_counter()
    killed = False
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        killed = True
    cost = time.perf_counter() - start
    time.sleep(0.5)
    # 直接用父进程报出来的 pid，比反查父链可靠（父进程已死，父链断点就在它身上）
    leftover = [first_child] if first_child and _pid_alive(first_child) else []
    note(f"  墙钟 {cost:.2f}s，超时被杀={killed}，残留子进程 {leftover or '无'}")
    note(f"  → {'只杀父进程会留下孙进程' if leftover else '本次未观察到残留'}")
    for pid in leftover:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)

    # ---- 4. TerminateJobObject：整体回收 ----
    note("")
    note("## 4. TerminateJobObject 整体回收进程树（S1 worker 的正确做法）")
    job = make_job()
    notif = JobNotifications(job)
    proc = spawn(
        "import subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    assign(job, proc.pid)
    child_pid = int(proc.stdout.readline().decode().strip() or 0)
    time.sleep(0.5)
    before = job_process_count(job)
    kernel32.TerminateJobObject(job, 1)
    time.sleep(0.8)
    after = job_process_count(job)
    proc.wait(timeout=10)
    time.sleep(0.3)
    notif.drain()
    notif.close()
    child_alive = _pid_alive(child_pid) if child_pid else True
    note(f"  终止前活跃 {before} 个；终止后活跃 {after} 个；子进程 {child_pid} 存活={child_alive}")
    note(f"  Job 通知：{notif.messages}")
    tree_ok = after == 0 and not child_alive
    note(f"  → {'整体回收成功，无残留' if tree_ok else '仍有残留'}")
    if not tree_ok:
        problems.append("TerminateJobObject 未能整体回收")
    kernel32.CloseHandle(job)

    # ---- 5. 熔断之后下一任务可运行 ----
    note("")
    note("## 5. 熔断之后下一任务可运行（状态不粘）")
    job = make_job(memory_bytes=60 * 1024 * 1024)
    bad = spawn(MEMORY_LOAD % 40)
    assign(job, bad.pid)
    try:
        bad.wait(timeout=60)
    except subprocess.TimeoutExpired:
        bad.kill()
    kernel32.CloseHandle(job)
    good = spawn("print('alive')")
    out, _ = good.communicate(timeout=20)
    note(f"  熔断任务退出码 {bad.returncode}；后续任务退出码 {good.returncode}，输出 {out.decode().strip()!r}")
    follow_ok = good.returncode == 0
    note(f"  → {'正常' if follow_ok else '异常'}")
    if not follow_ok:
        problems.append("熔断后后续任务不可运行")

    # ---- 汇总 ----
    note("")
    verdict = memory_killed and cpu_killed and tree_ok and follow_ok
    note(f"## 总判定：{'隔离限制可用，S1 可以按计划做' if verdict else '隔离限制不完整：' + '、'.join(problems)}")
    note("")
    note("## 与上一版判据的差别（回应评审）")
    note("")
    note("| | 上一版 | 本版 |")
    note("| --- | --- | --- |")
    note("| 内存/CPU 熔断 | 只看「非零 + 耗时短」 | **只认内核通知**；行为证据仅作旁证 |")
    note("| 退出原因 | 由退出码反推 | 读 Job 完成端口消息（LIMIT / ABNORMAL_EXIT 可区分） |")
    note("| 进程树回收 | 只验证「kill 会残留」 | 补 `TerminateJobObject` + 活跃进程数归零 |")
    note("")
    note("判据只认内核通知，所以「普通异常退出 / 外部终止」不可能被判成熔断——")
    note("它们拿不到 PROCESS_MEMORY_LIMIT 或 END_OF_PROCESS_TIME，只会落到「未能判定」。")
    note("代价是：某些环境取不到 Job 通知时，只能诚实地说「没能判定」，不拿行为猜结论。")
    note("这个代价是刻意的。")

    OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
    OUT.joinpath("result.json").write_text(
        __import__("json").dumps(
            {
                "memory": {"control": control, "limit": limit, "killed": memory_killed},
                "cpu": {"killed": cpu_killed, "cost": cpu_cost, "notifications": notif.messages},
                "tree_terminate": {"before": before, "after": after, "child_alive": child_alive},
                "followup_ok": follow_ok,
                "verdict": verdict,
                "problems": problems,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())
