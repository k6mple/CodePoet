"""隔离 worker：DSL 执行的子进程入口。

这个模块只能通过 ``python -m app.engine.dsl_worker`` 被子进程拉起来，
**不能被 API 进程 import 后直接调用**——它的全部意义就是「跑在另一个进程里」。

## 协议

stdin 收一份有界 JSON（上限 1MiB）：

```json
{
  "protocol": 1,
  "runId": "run-...",
  "source": "...",
  "baseIr": { ...完整 DrawingIR... },
  "limits": {"maxDrawCalls":1000,"maxSteps":10000,"maxAstNodes":2000},
  "cpuMs": 1000,
  "memoryMiB": 256,
  "enabledFunctions": ["circle","polygon","polyline","set_param"],
  "paramContext": null
}
```

stdout 回一份有界 JSON：

```json
{"protocol":1,"ok":true,"ir":{...},"metrics":{...},"auditFindings":[]}
{"protocol":1,"ok":false,"error":{"code":"...","message":"...","details":{}},"metrics":null}
```

## 为什么用退出码而不是只靠 JSON

三档预算里有两档（CPU、内存）是「必须立刻停」的：如果不打断解释循环，
用户写一个 10 万点的 polyline 就能把这一份内存吃到宿主机的极限，而
「等它自己发现超了再回报」根本等不到。所以守门的是看门狗线程，发现越界直接
``os._exit``，**此时来不及写任何 JSON**——父进程只能靠退出码判因：

* ``70``  CPU 超限
* ``71``  内存超限
* ``72``  输入协议错误（父进程 bug，不是用户源码问题）
* ``73``  wall 时间超限

wall 为什么也放在这里判：策略里的 ``dslWallTimeMs`` 指的是「解释这段源码能花多久」，
不包括 Python 启动和 import pydantic 的那两三百毫秒。只有从 worker 内部开始计时
才能把启动开销排除掉；父进程那边的 wall 判据因此放得更宽，只当兜底（防死锁、
防看门狗本身失效），命中时同样记 ``DSL_TIMEOUT``。

## 边界

worker 不持有数据库连接、不持有应用密钥。父进程 spawn 时只传最小
env（见 ``services/sandbox.py``），所以这里也 import 不到 ``Settings``——
即便 import 了，环境里也没有那些值。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

#: 输入上限。源码本身只允许 16KiB，base IR 再大也有限；1MiB 是「明确不合理」
#: 的界，用来防止父进程传了半张表进来时 worker 把内存吃干净。
MAX_INPUT_BYTES = 1024 * 1024

#: 输出上限由父进程校验，这里只定一个自己写不出去的绝对界
MAX_IR_NODES = 3000

PROTOCOL = 1

EXIT_OK = 0
EXIT_CPU = 70
EXIT_MEMORY = 71
EXIT_PROTOCOL = 72
EXIT_WALL = 73

#: 看门狗的采样间隔。50ms 在「及时打断」和「不浪费 CPU」之间：
#: 更密会让守门线程自己吃掉可观的 CPU 配额，更疏则可能放纵一次大分配。
WATCHDOG_INTERVAL_SECONDS = 0.05

#: v1 的步数上限。策略里没有这一项（``PolicyValues`` 不含 maxSteps），
#: 所以按细则「解释步数上限 v1 设 10000」写死。
DEFAULT_MAX_STEPS = 10000


# ---------------------------------------------------------------------------
# 资源观测
# ---------------------------------------------------------------------------


def _rss_mib() -> float:
    """当前进程常驻内存（MiB）。取不到时返回 0.0，**不假装成功**。

    取不到就不熔断，交给父进程的 wall 超时和内核级兜底——这比因为一个
    统计接口不可用就把所有正常请求判成内存超限要好。
    """
    try:
        if sys.platform == "win32":
            return _windows_rss_mib()
        return _posix_rss_mib()
    except Exception:  # noqa: BLE001 - 统计失败不该让执行失败
        return 0.0


def _windows_rss_mib() -> float:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        # 字段顺序与大小必须与 PROCESS_MEMORY_COUNTERS 完全一致，
        # 少一个字段会让 cb 与实际结构大小不符，调用直接失败。
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    # 必须显式声明签名：默认 HANDLE 按 c_int 处理，64 位下进程句柄被截断，
    # 调用会安静地失败并返回 0——内存守卫从此永远不触发，而这正是最需要它的场景。
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_uint32,
    ]
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb
    ):
        return 0.0
    return counters.WorkingSetSize / (1024 * 1024)


def _posix_rss_mib() -> float:
    with open("/proc/self/statm", "r", encoding="ascii") as handle:
        fields = handle.read().split()
    # 第二列是常驻页数
    pages = int(fields[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


class _Watchdog(threading.Thread):
    """盯着 CPU、RSS 与 wall，越界就立即终止整个进程。

    用 ``daemon=True`` + ``os._exit``：这不是清理型的退出，而是「马上停」。
    内存要爆的时候留给清理代码的时间本身就是风险。
    """

    def __init__(
        self, cpu_seconds: float, memory_mib: float, wall_seconds: float = 0.0
    ) -> None:
        super().__init__(name="dsl-watchdog", daemon=True)
        self._cpu_limit = cpu_seconds
        self._memory_limit = memory_mib
        self._wall_limit = wall_seconds
        self._stop = threading.Event()
        self.peak_mib = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        started_cpu = time.process_time()
        started_wall = time.monotonic()
        while not self._stop.is_set():
            used_cpu = time.process_time() - started_cpu
            rss = _rss_mib()
            if rss > self.peak_mib:
                self.peak_mib = rss
            if self._cpu_limit > 0 and used_cpu > self._cpu_limit:
                os._exit(EXIT_CPU)
            if self._memory_limit > 0 and rss > self._memory_limit:
                os._exit(EXIT_MEMORY)
            if (
                self._wall_limit > 0
                and time.monotonic() - started_wall > self._wall_limit
            ):
                os._exit(EXIT_WALL)
            time.sleep(WATCHDOG_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any]) -> None:
    """把结果写到 stdout。任何异常都不该再往 stdout 追加内容。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _fail(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    *,
    rule: str | None = None,
    line: int = 0,
    column: int = 0,
) -> int:
    """业务失败：给出错误码，同时留一条审计发现。

    ``auditFindings`` 的 ``ruleId`` 用 ``DslError.rule``（规则 ID），
    不是错误码。两者语义不同：同为 ``DSL_AUDIT_REJECTED``，
    可能是 op 白名单、字段白名单或数值范围拒绝的，前端要据 rule 才能
    告诉用户该改哪里。

    位置信息优先用真实行列（JSON 解析错误能给出），其次用 ops 下标换算。
    """
    _emit(
        {
            "protocol": PROTOCOL,
            "ok": False,
            "error": {"code": code, "message": message, "details": details or {}},
            "metrics": None,
            "auditFindings": [
                {
                    "ruleId": (rule or code)[:80],
                    "line": max(0, line),
                    "column": max(0, column),
                    "message": message[:300],
                }
            ],
        }
    )
    return EXIT_OK


def _locate(details: dict[str, Any]) -> tuple[int, int]:
    """从错误 details 里推出 (line, column)。

    源码是 JSON，没有统一的「第几行」概念，所以按可信度依次退让：

    1. JSON 解析错误自带真实行列；
    2. op 级错误带 ``index``，换算成「第几条指令」——比恒填 0 更能指路；
    3. 什么都没有就给 0，并保持 0 而不是编一个位置。
    """
    line = details.get("line")
    column = details.get("column")
    if isinstance(line, int) and isinstance(column, int):
        return line, column
    index = details.get("index")
    if isinstance(index, int):
        return index + 1, 0
    return 0, 0


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("输入超过 1MiB 上限")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("输入必须是 JSON 对象")
    if data.get("protocol") != PROTOCOL:
        raise ValueError("协议版本不匹配")
    return data


def _check_enabled_functions(program: Any, enabled: list[str]) -> None:
    """按策略的 ``enabledDslFunctions`` 再过一遍。

    ``compile_program`` 的白名单是「DSL 支持哪些 op」，这里是「这一版策略
    放开了哪些 op」。两者都要过：关掉 ``polygon`` 之后再用它，属于策略拒绝，
    要给 ``DSL_AUDIT_REJECTED`` 而不是悄悄降级成折线。
    """
    from app.engine import dsl

    allowed = set(enabled)
    for op in program.ops:
        if op["op"] not in allowed:
            raise dsl.DslError(
                "DSL_AUDIT_REJECTED",
                f"当前策略未开启该操作：{op['op']}",
                {"op": op["op"], "enabledDslFunctions": sorted(allowed)},
                rule=dsl.RULE_OP_WHITELIST,
            )
    if program.set_param and "set_param" not in allowed:
        raise dsl.DslError(
            "DSL_AUDIT_REJECTED",
            "当前策略未开启 set_param",
            {"enabledDslFunctions": sorted(allowed)},
            rule=dsl.RULE_OP_WHITELIST,
        )


def _build_param_context(raw: Any) -> Any:
    from app.engine import dsl
    from app.schemas.generation import Binding, Constraint, ParameterDefinition
    from app.schemas.ir import DrawingIR

    if not raw:
        return None
    return dsl.ParamContext(
        template_ir=DrawingIR.model_validate(raw["templateIr"]),
        parameter_schema=[
            ParameterDefinition.model_validate(item) for item in raw["parameterSchema"]
        ],
        bindings=[Binding.model_validate(item) for item in raw["bindings"]],
        constraints=[Constraint.model_validate(item) for item in raw["constraints"]],
        provided=dict(raw.get("provided") or {}),
    )


def main() -> int:
    try:
        payload = _read_payload()
    except Exception as exc:  # noqa: BLE001 - 协议坏了只能靠退出码说
        print(f"dsl_worker: 输入协议错误：{exc}", file=sys.stderr)
        return EXIT_PROTOCOL

    from app.engine import dsl
    from app.schemas.ir import DrawingIR

    limits_raw = payload.get("limits") or {}
    limits = dsl.DslLimits(
        max_draw_calls=int(limits_raw.get("maxDrawCalls", 1000)),
        # 步数不在策略里，按 v1 细则写死
        max_steps=int(limits_raw.get("maxSteps", DEFAULT_MAX_STEPS)),
        max_ast_nodes=int(limits_raw.get("maxAstNodes", 2000)),
    )

    watchdog = _Watchdog(
        cpu_seconds=float(payload.get("cpuMs", 1000)) / 1000.0,
        memory_mib=float(payload.get("memoryMiB", 256)),
        wall_seconds=float(payload.get("wallMs", 0)) / 1000.0,
    )
    watchdog.start()
    rss_before = _rss_mib()

    try:
        base_ir = DrawingIR.model_validate(payload["baseIr"])
        program = dsl.compile_program(payload["source"], limits)
        _check_enabled_functions(program, list(payload.get("enabledFunctions") or []))
        param_context = _build_param_context(payload.get("paramContext"))
        outcome = dsl.run(
            program,
            base_ir,
            run_id=str(payload["runId"]),
            limits=limits,
            param_context=param_context,
        )
    except dsl.DslError as exc:
        watchdog.stop()
        line, column = _locate(exc.details)
        return _fail(
            exc.code,
            exc.message,
            exc.details,
            rule=exc.rule,
            line=line,
            column=column,
        )
    except Exception as exc:  # noqa: BLE001 - 未预期异常也不能把堆栈抛给用户
        watchdog.stop()
        # 这里刻意只报类型，不回显 repr(exc)：异常消息里可能带本机路径
        return _fail(
            "DSL_EXECUTION_FAILED",
            "沙箱执行进程内部错误",
            {"reason": type(exc).__name__},
        )

    # 先停看门狗再写结果：否则它可能在序列化到一半时把进程杀掉，
    # 父进程会收到一段截断的 JSON。
    watchdog.stop()
    rss_after = _rss_mib()
    # 短任务可能一次采样都没赶上（看门狗 50ms 一轮，正常执行十几毫秒），
    # 所以取「执行前后各测一次」与看门狗峰值的最大值。三者都取不到时给 null
    # ——填 0 会让「没测到」看起来像「没占内存」。
    peak = max(watchdog.peak_mib, rss_before, rss_after)
    metrics = dict(outcome.metrics)
    metrics["peakMemoryMiB"] = int(round(peak)) if peak > 0 else None
    _emit(
        {
            "protocol": PROTOCOL,
            "ok": True,
            "ir": outcome.ir.model_dump(mode="json"),
            "metrics": metrics,
            "auditFindings": [],
        }
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
