"""DSL 沙箱：受理、隔离执行、落库。

## 分工

* **控制器**只解析请求、调本模块、格式化响应；
* **本模块**负责状态机、配额、把源码交给隔离进程、以及在父进程里校验
  worker 的产物后落库；
* **worker 子进程**（``engine/dsl_worker.py``）只做纯计算，不碰数据库、
  不拿应用密钥。

## 为什么不把 SQLAlchemy Session 传进线程

细则第 7 节点名了这条。除了「Session 不是线程安全的」这个技术理由，还有一个
更实际的原因：如果执行线程和 HTTP 线程共用 Session，用户在一次请求里改的东西
会悄悄出现在另一次执行的提交里，出问题时根本分不清是谁写的。所以执行线程
**自己开会话、自己提交、自己关闭**（见 ``SandboxExecutor._run_safely``）。

## 状态机

```
queued --(抢到执行权)--> running --> succeeded
                                 --> failed      (源码/策略/校验拒绝)
                                 --> timed_out    (wall 超时)
                                 --> DSL_CPU_LIMIT / DSL_MEMORY_LIMIT 也走 failed
```

两次状态写入都用「带条件的 UPDATE」，而不是「先读后写」：执行器线程和
HTTP 线程可能同时碰同一行（比如用户在 running 时查详情），读-改-写会丢更新。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import errors
from app.engine import dsl
from app.engine.validate import validate_ir
from app.models.creations import Generation as GenerationModel
from app.models.sandbox import SandboxRun as SandboxRunModel
from app.models.templates import TemplateVersion
from app.models.users import User
from app.schemas.generation import (
    Binding,
    Constraint,
    ParameterDefinition,
    Provenance,
)
from app.schemas.ir import DrawingIR
from app.schemas.sandbox import (
    TERMINAL_STATUSES,
    AuditFinding,
    JobError,
    SandboxCreate,
    SandboxMetrics,
    SandboxPolicySnapshot,
    SandboxRun,
)
from app.services import audit
from app.services import policy as policy_service
from app.services.generations import (
    ENGINE_VERSION,
    ONTOLOGY_VERSION,
    _WARNING_CODES,
    can_read,
    compute_ir_hash,
)

__all__ = [
    "MAX_ACTIVE_RUNS_PER_USER",
    "SandboxExecutor",
    "create_run",
    "execute_run",
    "get_run",
    "is_available",
    "is_memory_exit",
    "probe",
    "wait_for",
]

#: 每用户同时最多几个未完成的 run。细则第 7 节要求「并发配额与后续任务可运行」：
#: 配额满了要明确 429，而不是静默排队把请求线程挂死。
MAX_ACTIVE_RUNS_PER_USER = 2

#: worker 输出的上限。IR 有 200 节点上限，正常几 MB 都到不了；超过这个数
#: 说明 worker 行为异常，宁可判失败也不要把它整个读进父进程内存。
MAX_WORKER_OUTPUT_BYTES = 4 * 1024 * 1024

#: 给父进程 wall 超时留的余量。
#:
#: 真正判 ``DSL_TIMEOUT`` 的是 worker 内部的看门狗（它从解释开始计时，不含
#: Python 启动与 import 开销）。父进程这道只是兜底：看门狗线程本身失效、
#: 或者进程卡在无法被抢占的地方时，总得有个东西把它收掉。所以这边放得很宽
#: ——策略值 + 启动允许 + 余量，宁可晚一点杀，也不要在正常执行时误判超时。
STARTUP_ALLOWANCE_SECONDS = 2.0
WALL_GRACE_SECONDS = 0.5

#: 父进程实际等待的上限
def wall_deadline_seconds(dsl_wall_ms: int) -> float:
    return dsl_wall_ms / 1000.0 + STARTUP_ALLOWANCE_SECONDS + WALL_GRACE_SECONDS

#: v1 步数上限，与 worker 里的常量一致（策略里没有这一项）
MAX_STEPS = 10000

_WORKER_PROTOCOL = 1

_PROJECT_BACKEND = Path(__file__).resolve().parents[2]

#: worker 只继承这些环境变量。``CODEPOET_*``（数据库路径、知识图谱密钥）
#: 和 ``GITHUB_TOKEN`` 之类绝不下传——「不持有密钥」不能靠 worker 自觉不读。
_INHERITED_ENV = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "PATH",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
)

_probe_lock = Lock()
_probe_result: tuple[bool, str | None] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_run_id() -> str:
    return f"run-{uuid4().hex}"


def _new_generation_id() -> str:
    return f"gen-{uuid4().hex}"


# ---------------------------------------------------------------------------
# 可用性
# ---------------------------------------------------------------------------


def probe() -> tuple[bool, str | None]:
    """真跑一次最小 worker，判断本机能不能执行。

    为什么不用「文件存在 + 解释器存在」这种廉价判据：那两条都会在
    「PYTHONPATH 配错」「site-packages 装到别处」时同时成立，而 worker 实际上
    一启动就 ImportError。细则要求 503 有真实依据，所以这里就真跑一次——
    结果缓存，整个进程只付一次几百毫秒的代价。
    """
    global _probe_result
    with _probe_lock:
        if _probe_result is not None:
            return _probe_result
        _probe_result = _probe_once()
        return _probe_result


def _probe_once() -> tuple[bool, str | None]:
    payload = {
        "protocol": _WORKER_PROTOCOL,
        "runId": "run-probe",
        # 空 ops 会被静态审计拒掉（「ops 必须是非空数组」），那样探针测到的
        # 会是「审计能跑」而不是「解释器能跑」。所以给它一条最小但合法的指令。
        "source": json.dumps(
            {"version": 1, "ops": [{"op": "circle", "cx": 10, "cy": 10, "r": 5}]}
        ),
        # DrawingIR 要求至少一个节点，空数组会被模型直接拒掉。
        # 自检的 base 用一张最小的单圆图，代表真实调用的形状。
        "baseIr": {
            "schemaVersion": "1.0",
            "seed": 0,
            "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
            "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
            "durationMs": 0,
            "nodes": [
                {
                    "id": "probe-circle",
                    "parentId": None,
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 20},
                    "style": {
                        "fill": "#FFFFFF",
                        "stroke": None,
                        "strokeWidth": 0,
                        "opacity": 1,
                    },
                }
            ],
            "animations": [],
            "narrative": [],
        },
        "limits": {"maxDrawCalls": 1000, "maxSteps": MAX_STEPS, "maxAstNodes": 2000},
        "cpuMs": 1000,
        "memoryMiB": 256,
        "wallMs": 2000,
        "enabledFunctions": ["circle", "polygon", "polyline", "set_param"],
        "paramContext": None,
    }
    try:
        result = _run_worker(
            payload,
            wall_seconds=wall_deadline_seconds(2000),
            job_memory_mib=256,
        )
    except OSError as exc:
        return False, f"无法启动隔离执行进程：{type(exc).__name__}"
    if result.returncode != 0:
        return False, f"隔离执行进程启动失败（退出码 {result.returncode}）"
    try:
        body = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "隔离执行进程返回了无法解析的结果"
    if not body.get("ok"):
        # 把 worker 自己报的原因带出来，否则 503 只有一个笼统的「自检未通过」，
        # 排查时还得手工复现一次才知道是 ImportError 还是模型校验失败。
        error = body.get("error") or {}
        detail = str(error.get("message") or "自检未通过")
        code = str(error.get("code") or "")
        return False, f"{code}：{detail}" if code else detail
    return True, None


def is_available() -> bool:
    return probe()[0]


# ---------------------------------------------------------------------------
# worker 调用
# ---------------------------------------------------------------------------


class WorkerResult:
    """父进程侧看到的 worker 结果。"""

    __slots__ = ("returncode", "stdout", "stderr", "timed_out")

    def __init__(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
        timed_out: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def _worker_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _INHERITED_ENV if name in os.environ}
    env["PYTHONPATH"] = str(_PROJECT_BACKEND)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_worker(
    payload: dict[str, Any], *, wall_seconds: float, job_memory_mib: int
) -> WorkerResult:
    """起一个 worker，喂 JSON，收 JSON。超时就整组回收。

    ``job_memory_mib`` 只用于 **Job Object 那层内核兜底**。worker 内部看门狗
    实际用的上限来自 ``payload["memoryMiB"]``（也就是策略快照里的值）——
    两个参数分开是因为内核那层有「低于 60MiB 连解释器都起不来」的下限，
    而策略值可以更小。
    """
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        # 不弹窗；没有新进程组的概念，靠 Job Object 兜底
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        # POSIX：独立会话，超时时按进程组杀，避免孙进程留下来
        start_new_session = True

    process = subprocess.Popen(
        # 不用 ``-I``：isolated 模式会连 ``PYTHONPATH`` 一起忽略，worker 就
        # import 不到 ``app.*`` 了。隔离靠的是「只传最小 env」和「进程外执行」，
        # 不是靠解释器开关。``-B`` 只是别往仓库里写 __pycache__。
        [sys.executable, "-B", "-m", "app.engine.dsl_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_PROJECT_BACKEND),
        env=_worker_env(),
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    job = _WindowsJob(job_memory_mib) if sys.platform == "win32" else None
    if job is not None:
        job.assign(process.pid)

    try:
        stdout, stderr = process.communicate(
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=wall_seconds,
        )
    except subprocess.TimeoutExpired:
        _kill_tree(process, job)
        stdout, stderr = process.communicate()
        return WorkerResult(-1, stdout[:MAX_WORKER_OUTPUT_BYTES], stderr, timed_out=True)
    finally:
        if job is not None:
            job.close()

    return WorkerResult(
        process.returncode, stdout[:MAX_WORKER_OUTPUT_BYTES], stderr
    )


def _kill_tree(process: subprocess.Popen, job: "_WindowsJob | None") -> None:
    if job is not None:
        job.terminate()
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except (ProcessLookupError, PermissionError):
        pass


class _WindowsJob:
    """把 worker 绑进 Job Object，超时时能连同子进程一起收回。

    设置失败一律降级为「没有这道内核防线」，不抛异常：worker 内部还有看门狗，
    父进程还有 wall 超时和 ``process.kill()``。少一层防护不该让功能整体不可用。
    """

    __slots__ = ("_handle",)

    def __init__(self, memory_mib: int) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        try:
            self._handle = _create_job(memory_mib)
        except Exception:  # noqa: BLE001 - 兜底层失败不影响主流程
            self._handle = None

    def assign(self, pid: int) -> None:
        if self._handle is None:
            return
        try:
            _assign_to_job(self._handle, pid)
        except Exception:  # noqa: BLE001
            pass

    def terminate(self) -> None:
        if self._handle is None:
            return
        try:
            _terminate_job(self._handle)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            _close_handle(self._handle)
        except Exception:  # noqa: BLE001
            pass
        self._handle = None


#: Job Object 的内核内存上限不能设得太小：低于约 60MiB 时 Python 连 DLL 都
#: 加载不完，进程会在解释器起来之前就死在 0xC0000142，worker 自己的看门狗
#: 根本没机会运行。这里的下限只是「让进程能活着启动」，
#: 真正细粒度的内存判定由 worker 内的看门狗按策略值执行。
MIN_JOB_MEMORY_MIB = 64


def _create_job(memory_mib: int) -> Any:
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    # 必须显式声明句柄返回类型：默认按 c_int 处理，64 位下句柄会被截断，
    # 后续 SetInformationJobObject 全部失败（而且失败得很安静）。
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    # JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x100 | 0x2000
    info.ProcessMemoryLimit = max(memory_mib, MIN_JOB_MEMORY_MIB) * 1024 * 1024
    # JobObjectExtendedLimitInformation = 9
    ok = kernel32.SetInformationJobObject(
        handle, 9, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _assign_to_job(handle: Any, pid: int) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE = 0x0100 | 0x0001
    process_handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
    if not process_handle:
        return
    try:
        kernel32.AssignProcessToJobObject(handle, process_handle)
    finally:
        kernel32.CloseHandle(process_handle)


def _terminate_job(handle: Any) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateJobObject(handle, 1)


def _close_handle(handle: Any) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def _to_schema(row: SandboxRunModel) -> SandboxRun:
    metrics_raw = json.loads(row.metrics_json) if row.metrics_json else None
    error_raw = json.loads(row.error_json) if row.error_json else None
    snapshot_raw = json.loads(row.policy_snapshot_json)
    findings_raw = json.loads(row.audit_findings_json or "[]")
    return SandboxRun(
        id=row.id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        ownerId=row.owner_id,
        baseGenerationId=row.base_generation_id,
        status=row.status,  # type: ignore[arg-type]
        policyRevision=row.policy_revision,
        sourceHash=row.source_hash,
        startedAt=_parse_dt(row.started_at),
        finishedAt=_parse_dt(row.finished_at),
        generationId=row.generation_id,
        metrics=SandboxMetrics.model_validate(metrics_raw) if metrics_raw else None,
        error=JobError.model_validate(error_raw) if error_raw else None,
        policySnapshot=SandboxPolicySnapshot.model_validate(snapshot_raw)
        if snapshot_raw
        else None,
        auditFindings=[AuditFinding.model_validate(item) for item in findings_raw],
    )


def get_run(db: Session, user: User, run_id: str) -> SandboxRun:
    """按归属读取。别人的 run 一律 404——不区分「不存在」和「不是你的」。"""
    row = db.get(SandboxRunModel, run_id)
    if row is None or row.owner_id != user.id:
        raise errors.not_found("执行记录不存在")
    return _to_schema(row)


def wait_for(
    db: Session, run_id: str, *, timeout: float = 10.0, interval: float = 0.02
) -> SandboxRunModel:
    """等到 run 进入终态。只给测试和脚本用，HTTP 路径不调它。

    HTTP 侧拿到 202 就该返回，不能在一次请求里等执行完——那等于把异步做成了
    同步，还占着请求线程。
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.get(SandboxRunModel, run_id)
        if row is not None and row.status in TERMINAL_STATUSES:
            return row
        time.sleep(interval)
    db.expire_all()
    row = db.get(SandboxRunModel, run_id)
    raise TimeoutError(f"run {run_id} 在 {timeout}s 内没有进入终态（当前 {row.status}）")


# ---------------------------------------------------------------------------
# 受理
# ---------------------------------------------------------------------------


def _active_count(db: Session, owner_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(SandboxRunModel)
            .where(
                SandboxRunModel.owner_id == owner_id,
                SandboxRunModel.status.in_(("queued", "running")),
            )
        )
        or 0
    )


def create_run(
    db: Session,
    user: User,
    payload: SandboxCreate,
    request_id: str,
) -> SandboxRun:
    """校验并受理一次执行。返回 queued 状态的 run（对应 HTTP 202）。

    这里**不执行任何用户源码**，连 JSON 解析都不做——静态审计也在 worker 里。
    控制器调用方拿到返回值后再把它扔给执行器。
    """
    available, reason = probe()
    if not available:
        raise errors.sandbox_unavailable(reason or "隔离执行环境不可用")

    base = db.get(GenerationModel, payload.baseGenerationId)
    # 授权先于一切：读不到就 404，不因为「后端没启用」「配额满了」泄露它存在
    if base is None or not can_read(db, user, base):
        raise errors.not_found("基底生成快照不存在")

    source_bytes = len(payload.source.encode("utf-8"))
    if source_bytes > dsl.MAX_SOURCE_BYTES:
        raise errors.validation_error(
            f"源码超过 {dsl.MAX_SOURCE_BYTES} 字节上限",
            field_errors=[{"path": "source", "message": "源码过长"}],
            details={"bytes": source_bytes, "maxBytes": dsl.MAX_SOURCE_BYTES},
        )

    if _active_count(db, user.id) >= MAX_ACTIVE_RUNS_PER_USER:
        raise errors.execution_busy(
            f"未完成的执行任务已达上限（{MAX_ACTIVE_RUNS_PER_USER} 个），请等待旧任务结束",
            details={"maxActiveRuns": MAX_ACTIVE_RUNS_PER_USER},
        )

    revision, values = policy_service.snapshot(db)
    snapshot = policy_service.to_snapshot(revision, values)
    source_hash = _sha256(payload.source)

    row = SandboxRunModel(
        id=_new_run_id(),
        owner_id=user.id,
        base_generation_id=base.id,
        status="queued",
        policy_revision=revision,
        policy_snapshot_json=snapshot.model_dump_json(),
        source_hash=source_hash,
        source_json=payload.source,
        generation_id=None,
        metrics_json=None,
        error_json=None,
        audit_findings_json="[]",
        request_id=request_id,
        created_at=_now_iso(),
        started_at=None,
        finished_at=None,
    )
    db.add(row)
    # 审计只落 hash 与关联 ID，不落源码
    audit.record(
        db,
        actor=user,
        action="sandbox.run.created",
        target_type="sandbox_run",
        target_id=row.id,
        input_summary={
            "baseGenerationId": base.id,
            "sourceHash": source_hash,
            "sourceBytes": source_bytes,
            "policyRevision": revision,
        },
        request_id=request_id,
    )
    db.commit()
    db.refresh(row)
    return _to_schema(row)


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _claim(db: Session, run_id: str) -> bool:
    """原子地把 queued 改成 running。抢到返回 True。

    用带条件的 UPDATE 而不是「读出来看是不是 queued 再改」：两个执行线程
    同时进来时，后者的 WHERE 条件会不成立，rowcount=0，它就知道自己没抢到。
    """
    result = db.execute(
        update(SandboxRunModel)
        .where(
            SandboxRunModel.id == run_id,
            SandboxRunModel.status == "queued",
        )
        .values(status="running", started_at=_now_iso())
    )
    db.commit()
    return result.rowcount == 1


def _param_context(
    db: Session, base: GenerationModel
) -> dict[str, Any] | None:
    """给 ``set_param`` 准备模板上下文。

    只有「有模板来源、未手改、未子图派生」的 base 才给。判据是
    ``mode == "template"`` 且挂着一个模板版本——手工编辑（ir）、子图派生
    （subgraph）、以及别的 fragment 都不满足，此时返回 None，worker 会报
    ``DSL_INVALID_BASE``。

    这里不尝试「猜一个上下文」让它跑通：那样等于拿模板重画一遍，用户的编辑
    就没了。
    """
    if base.mode != "template" or not base.template_version_id:
        return None
    version = db.get(TemplateVersion, base.template_version_id)
    if version is None:
        return None
    try:
        parameter_schema = [
            ParameterDefinition.model_validate(item).model_dump(mode="json")
            for item in json.loads(version.parameter_schema_json or "[]")
        ]
        bindings = [
            Binding.model_validate(item).model_dump(mode="json")
            for item in json.loads(version.bindings_json or "[]")
        ]
        constraints = [
            Constraint.model_validate(item).model_dump(mode="json")
            for item in json.loads(version.constraints_json or "[]")
        ]
        # 传的是**未实例化**的模板 IR：worker 里会用既有绑定 + set_param 的
        # 覆盖值做一次 instantiate，再在结果上追加图元。传已实例化的 IR 等于
        # 让 set_param 无效。
        template_ir = json.loads(version.ir_template_json)
    except (json.JSONDecodeError, ValueError):
        # 模板定义坏了：不给上下文，让执行走到 DSL_INVALID_BASE 而不是 500
        return None
    return {
        "templateIr": template_ir,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": constraints,
        "provided": json.loads(base.params_json or "{}"),
    }


def _worker_payload(
    db: Session, row: SandboxRunModel, base: GenerationModel
) -> dict[str, Any]:
    snapshot = json.loads(row.policy_snapshot_json)
    return {
        "protocol": _WORKER_PROTOCOL,
        "runId": row.id,
        "source": row.source_json,
        "baseIr": json.loads(base.ir_json),
        "limits": {
            "maxDrawCalls": snapshot["dslMaxDrawCalls"],
            "maxSteps": MAX_STEPS,
            "maxAstNodes": snapshot["dslMaxAstNodes"],
        },
        "cpuMs": snapshot["dslCpuTimeSeconds"] * 1000,
        "memoryMiB": snapshot["dslMemoryMiB"],
        "wallMs": snapshot["dslWallTimeMs"],
        "enabledFunctions": snapshot["enabledDslFunctions"],
        "paramContext": _param_context(db, base),
    }


def _finish_failed(
    db: Session,
    row: SandboxRunModel,
    *,
    status: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
    actor: User | None,
) -> None:
    """写失败终态。``generation_id`` 保持 NULL（库里有 CHECK 兜着）。"""
    row.status = status
    row.finished_at = _now_iso()
    row.error_json = JobError(
        code=code, message=message, details=details or {}
    ).model_dump_json()
    if metrics is not None:
        row.metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    if findings is not None:
        row.audit_findings_json = json.dumps(findings, ensure_ascii=False)
    audit.record(
        db,
        actor=actor,
        action="sandbox.run.finished",
        target_type="sandbox_run",
        target_id=row.id,
        input_summary={"sourceHash": row.source_hash, "status": status},
        after={"status": status, "errorCode": code},
        result="failure",
        error_code=code,
        request_id=row.request_id,
    )
    db.commit()


def _finish_succeeded(
    db: Session,
    row: SandboxRunModel,
    base: GenerationModel,
    ir: DrawingIR,
    metrics: dict[str, Any] | None,
    actor: User | None,
) -> None:
    """在**同一个事务**里写 generation 与 run 终态。

    为什么要一个事务：run 说 succeeded 但 generation 没插进去，前端会拿到一个
    指向不存在快照的 id；反过来 generation 插了而 run 还是 running，配额永远
    不释放。两者必须同生共死。
    """
    report = validate_ir(ir)
    blocking = [item for item in report.issues if item.code not in _WARNING_CODES]
    if blocking:
        # 走到这里说明 worker 产出了「解释器认为合法、全局校验认为不合法」的 IR，
        # 是本方 bug 而不是用户源码问题，所以不报 DSL_AUDIT_REJECTED。
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_EXECUTION_FAILED",
            message="沙箱产物未通过全局 IR 校验",
            details={"issues": [item.model_dump(mode="json") for item in blocking]},
            metrics=metrics,
            actor=actor,
        )
        return

    base_provenance = json.loads(base.provenance_json or "{}")
    provenance = Provenance(
        kind="fragment",
        baseGenerationId=base.id,
        engineVersion=ENGINE_VERSION,
        ontologyVersion=ONTOLOGY_VERSION,
        policyRevision=row.policy_revision,
        # 外部图谱快照随派生保留（细则 K1：经 DSL 后仍在）
        externalGraph=base_provenance.get("externalGraph"),
        fragmentSource=row.source_json,
        fragmentSourceHash=row.source_hash,
    )
    warnings = [item for item in report.issues if item.code in _WARNING_CODES]
    generation = GenerationModel(
        id=_new_generation_id(),
        owner_id=row.owner_id,
        mode="fragment",
        backend=base.backend,
        template_version_id=None,
        params_json=base.params_json or "{}",
        seed=ir.seed,
        ir_json=ir.model_dump_json(),
        ir_hash=compute_ir_hash(ir),
        provenance_json=provenance.model_dump_json(),
        validation_report_json=report.model_dump_json(),
        warnings_json=json.dumps(
            [item.model_dump(mode="json") for item in warnings], ensure_ascii=False
        ),
        # 未发布来源沿派生继承：基于草稿改出来的仍是草稿
        unpublished_source=base.unpublished_source,
        request_id=row.request_id,
        created_at=_now_iso(),
    )
    db.add(generation)
    row.status = "succeeded"
    row.finished_at = _now_iso()
    row.generation_id = generation.id
    if metrics is not None:
        row.metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    row.error_json = None
    audit.record(
        db,
        actor=actor,
        action="sandbox.run.finished",
        target_type="sandbox_run",
        target_id=row.id,
        input_summary={
            "sourceHash": row.source_hash,
            "policyRevision": row.policy_revision,
        },
        after={
            "status": "succeeded",
            "generationId": generation.id,
            "irHash": generation.ir_hash,
            "metrics": metrics,
        },
        request_id=row.request_id,
    )
    db.commit()


def _owner(db: Session, row: SandboxRunModel) -> User | None:
    return db.get(User, row.owner_id)


#: NTSTATUS 0xC0000017 = STATUS_NO_MEMORY。Windows 上如果 worker 是被 Job
#: Object 的内存上限干掉的，子进程退出码会是这个值（无符号显示为 3221225773），
#: 而不是 worker 自己 os._exit 的 71——两条路径都得算「内存超限」，
#: 否则同一个原因会在审计里表现为两种不同的码。
_NT_STATUS_NO_MEMORY = 0xC0000017


def is_memory_exit(returncode: int) -> bool:
    return returncode == 71 or (returncode & 0xFFFFFFFF) == _NT_STATUS_NO_MEMORY


def _metrics_from(body: dict[str, Any]) -> dict[str, Any] | None:
    raw = body.get("metrics")
    if not isinstance(raw, dict):
        return None
    try:
        return SandboxMetrics.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return None


def execute_run(db: Session, run_id: str) -> None:
    """执行一次已受理的 run。**必须由执行器线程调用，且自己持有 db。**

    整个函数不抛异常：任何失败都落成 run 的终态。执行线程里抛出去没人接，
    只会让 run 永远停在 running。
    """
    row = db.get(SandboxRunModel, run_id)
    if row is None:
        return
    if row.status in TERMINAL_STATUSES:
        return
    if not _claim(db, run_id):
        # 已经被别的执行线程抢走了
        db.refresh(row)
        return
    db.refresh(row)

    actor = _owner(db, row)
    base = db.get(GenerationModel, row.base_generation_id)
    if base is None:
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_INVALID_BASE",
            message="基底生成快照已不存在",
            actor=actor,
        )
        return

    snapshot = json.loads(row.policy_snapshot_json)
    wall_seconds = wall_deadline_seconds(snapshot["dslWallTimeMs"])

    try:
        result = _run_worker(
            _worker_payload(db, row, base),
            wall_seconds=wall_seconds,
            job_memory_mib=snapshot["dslMemoryMiB"],
        )
    except OSError as exc:
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_EXECUTION_FAILED",
            message="无法启动隔离执行进程",
            details={"reason": type(exc).__name__},
            actor=actor,
        )
        return

    if result.timed_out:
        _finish_failed(
            db,
            row,
            status="timed_out",
            code="DSL_TIMEOUT",
            message=f"执行超过 {snapshot['dslWallTimeMs']}ms 墙钟上限",
            details={"wallLimitMs": snapshot["dslWallTimeMs"]},
            actor=actor,
        )
        return

    # 退出码优先于 stdout：看门狗是用 os._exit 走的，那种情况下 stdout 一定是空的
    if result.returncode == 70:
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_CPU_LIMIT",
            message=f"CPU 时间超过 {snapshot['dslCpuTimeSeconds']}s 上限",
            details={"cpuLimitSeconds": snapshot["dslCpuTimeSeconds"]},
            actor=actor,
        )
        return
    if is_memory_exit(result.returncode):
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_MEMORY_LIMIT",
            message=f"内存占用超过 {snapshot['dslMemoryMiB']}MiB 上限",
            details={"memoryLimitMiB": snapshot["dslMemoryMiB"]},
            actor=actor,
        )
        return
    if result.returncode == 73:
        _finish_failed(
            db,
            row,
            status="timed_out",
            code="DSL_TIMEOUT",
            message=f"执行超过 {snapshot['dslWallTimeMs']}ms 墙钟上限",
            details={"wallLimitMs": snapshot["dslWallTimeMs"]},
            actor=actor,
        )
        return
    if result.returncode != 0:
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_EXECUTION_FAILED",
            message="隔离执行进程异常退出",
            details={"returncode": result.returncode},
            actor=actor,
        )
        return

    try:
        body = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_EXECUTION_FAILED",
            message="隔离执行进程返回了无法解析的结果",
            actor=actor,
        )
        return

    findings_raw = body.get("auditFindings")
    findings = findings_raw if isinstance(findings_raw, list) else None
    metrics = _metrics_from(body)

    if not body.get("ok"):
        error = body.get("error") or {}
        _finish_failed(
            db,
            row,
            status="failed",
            code=str(error.get("code") or "DSL_EXECUTION_FAILED")[:80],
            message=str(error.get("message") or "执行失败")[:1000],
            details=error.get("details") if isinstance(error.get("details"), dict) else None,
            metrics=metrics,
            findings=findings,
            actor=actor,
        )
        return

    try:
        ir = DrawingIR.model_validate(body.get("ir"))
    except ValueError as exc:
        _finish_failed(
            db,
            row,
            status="failed",
            code="DSL_EXECUTION_FAILED",
            message="隔离执行进程产出的 IR 结构不合法",
            details={"reason": type(exc).__name__},
            actor=actor,
        )
        return

    _finish_succeeded(db, row, base, ir, metrics, actor)


class SandboxExecutor:
    """有界执行器：单实例、单并发。

    为什么同时只跑一个：一次 DSL 执行可能吃掉 256MiB 和 1 秒 CPU，并发跑几个
    会把宿主机拖到所有请求都变慢。配额已经限制每用户 2 个未完成任务，两个用户
    同时提交时第二个会排队——这比一起变慢好，至少第一个能按时出结果。

    ``shutdown`` 之后 ``submit`` 不再接活：进程退出时还没跑的任务会被状态机
    的重启清理标成 failed（见 ``recover_orphans``），而不是永远挂在 queued。
    """

    def __init__(self, session_factory, max_workers: int = 1) -> None:
        self._sessions = session_factory
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sandbox"
        )
        self._closed = False
        self._lock = Lock()

    def submit(self, run_id: str) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("执行器已关闭")
            self._pool.submit(self._run_safely, run_id)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=wait)

    def _run_safely(self, run_id: str) -> None:
        db = self._sessions()
        try:
            execute_run(db, run_id)
        except Exception:  # noqa: BLE001 - 执行线程不能把异常扔给谁
            db.rollback()
            try:
                _mark_crashed(db, run_id)
            except Exception:  # noqa: BLE001
                db.rollback()
        finally:
            db.close()


def _mark_crashed(db: Session, run_id: str) -> None:
    """执行线程自己崩了：把 run 标成 failed，别让它永远 running。"""
    row = db.get(SandboxRunModel, run_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    row.status = "failed"
    row.finished_at = _now_iso()
    row.error_json = JobError(
        code="DSL_EXECUTION_FAILED",
        message="执行线程异常终止",
        details={},
    ).model_dump_json()
    db.commit()


def recover_orphans(db: Session) -> int:
    """进程重启时把遗留的 queued/running 标成 failed。

    执行器是进程内的线程池，进程一退出它们就没了。如果不清理，这些 run 会
    永远停在 running：用户看到「执行中」但永远不会结束，配额也一直被占着，
    他既不能取消也不能新建。
    """
    now = _now_iso()
    rows = db.scalars(
        select(SandboxRunModel).where(
            SandboxRunModel.status.in_(("queued", "running"))
        )
    ).all()
    for row in rows:
        row.status = "failed"
        row.finished_at = now
        row.error_json = JobError(
            code="DSL_EXECUTION_FAILED",
            message="服务重启导致执行中断，请重新提交",
            details={"reason": "worker_restarted"},
        ).model_dump_json()
    if rows:
        db.commit()
    return len(rows)
