"""S1 沙箱与策略的请求/响应模型。

字段严格对齐 ``docs/openapi.json`` 里的 ``SandboxCreate`` / ``SandboxRun`` /
``PolicyValues`` / ``Policies`` / ``PolicyUpdate``——前端是照着那份 schema 写的，
这里多一个字段少一个字段都会让它解析失败。唯一的新增是细则第 7 节明确允许的
三个可选项：``policySnapshot``、``metrics.peakMemoryMiB``、``auditFindings``，
以及 ``Policies.auditRules``。

几条不肯让步的取舍：

* ``SandboxRun`` **不返回源码**。契约只说「源码/策略快照由 A 在数据库审计关联
  存储，响应不暴露敏感信息」，所以 ``source_hash`` 出去、``source`` 留在库里。
* ``metrics`` 的所有数字都来自真实执行。queued/running 时为 ``null``，不填一堆
  0 假装「已经跑过了，只是很快」。
* ``auditFindings`` 里的 ``message`` 有长度上限且不回显内部路径：它会进前端页面，
  也会被截图传播，不能把 ``C:\\Users\\...`` 这种信息带出去。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ir import NodeId

#: 与 ``engine/dsl.py`` 的 ``MAX_SOURCE_BYTES`` 一致。这里再写一遍是刻意的：
#: 请求在进入服务层之前就该被拒，不能等解析时才发现超长。
MAX_SOURCE_LENGTH = 16384

JobStatus = Literal[
    "queued", "running", "succeeded", "failed", "timed_out", "cancelled"
]

#: 终态集合。服务层用它判断「还能不能改状态」。
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "timed_out", "cancelled"}
)

DslFunction = Literal["circle", "polygon", "polyline", "set_param"]


class SandboxCreate(BaseModel):
    """POST /sandbox-runs 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    baseGenerationId: NodeId
    #: 源码。上限 16KiB：这是「能写出一张有意义的图」和「不该塞进数据库」
    #: 之间的分界，也保证了单次审计的 AST 节点数能被预算兜住。
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)


class AuditFinding(BaseModel):
    """一条静态审计发现。"""

    model_config = ConfigDict(extra="forbid")

    ruleId: str = Field(min_length=1, max_length=80)
    #: 源码里的位置。JSON 没有行列概念，这里给的是「第几个字符/第几层」，
    #: 前端只用来定位，不当作精确光标。
    line: int = Field(ge=0, le=1_000_000)
    column: int = Field(ge=0, le=1_000_000)
    message: str = Field(min_length=1, max_length=300)


class SandboxMetrics(BaseModel):
    """真实执行指标。全部来自 worker 回报，不由客户端上传。"""

    model_config = ConfigDict(extra="forbid")

    wallTimeMs: int = Field(ge=0)
    cpuTimeMs: int = Field(ge=0)
    drawCalls: int = Field(ge=0)
    steps: int = Field(ge=0)
    #: 细则新增：峰值常驻内存。取不到时为 null，不填 0。
    peakMemoryMiB: int | None = Field(default=None, ge=0)


class JobError(BaseModel):
    """失败原因。``details`` 只放可解释的结构，不放堆栈。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)
    details: dict | None = None


class SandboxPolicySnapshot(BaseModel):
    """本次 run 实际生效的限额与允许函数（细则新增的 ``policySnapshot``）。

    只放 DSL 相关的项。把整份 ``PolicyValues`` 塞进来会让前端误以为
    render/turtle 那些「本次根本没参与」的值也是这次执行的一部分。
    """

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    dslWallTimeMs: int = Field(ge=0)
    dslCpuTimeSeconds: int = Field(ge=0)
    dslMemoryMiB: int = Field(ge=0)
    dslMaxDrawCalls: int = Field(ge=0)
    dslMaxAstNodes: int = Field(ge=0)
    enabledDslFunctions: list[DslFunction] = Field(max_length=4)


class SandboxRun(BaseModel):
    """一次 DSL 执行的对外表示。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    ownerId: NodeId
    baseGenerationId: NodeId
    status: JobStatus
    policyRevision: int = Field(ge=1)
    #: 源码的 SHA-256。想看源码得走归属授权，不在这个响应里。
    sourceHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    startedAt: datetime | None
    finishedAt: datetime | None
    #: 成功时是新产出的 fragment generation；失败一定为 null。
    generationId: NodeId | None
    metrics: SandboxMetrics | None
    error: JobError | None
    #: ---- 以下三项为 S1 增量，旧记录读出来是 null / 空数组 ----
    policySnapshot: SandboxPolicySnapshot | None = None
    auditFindings: list[AuditFinding] = Field(default_factory=list, max_length=100)


# ---- 策略 ----------------------------------------------------------------


class PolicyValues(BaseModel):
    """策略的完整取值集合。

    字段与取值范围照抄 OpenAPI，但**能取到哪一端由 ``HARD_LIMITS`` 决定**：
    schema 给的是契约的理论区间（比如 maxIRNodes 能到 3000），而本实例的
    core-demo 全局硬限是 200 节点——PATCH 到 3000 会被拒，不是因为类型不对，
    而是因为那会让 ``validate_ir`` 和策略各说一套。
    """

    model_config = ConfigDict(extra="forbid")

    maxIRNodes: int = Field(ge=1, le=3000)
    maxVertices: int = Field(ge=3, le=20000)
    maxRequestBytes: int = Field(ge=16384, le=262144)
    maxIRBytes: int = Field(ge=16384, le=2097152)
    maxParameters: int = Field(ge=1, le=32)
    dslWallTimeMs: int = Field(ge=100, le=5000)
    dslCpuTimeSeconds: int = Field(ge=1, le=3)
    dslMemoryMiB: int = Field(ge=64, le=512)
    dslMaxDrawCalls: int = Field(ge=1, le=2000)
    dslMaxAstNodes: int = Field(ge=100, le=2000)
    dslMaxRangeIterations: int = Field(ge=1, le=200)
    renderWallTimeMs: int = Field(ge=1000, le=120000)
    turtleMemoryMiB: int = Field(ge=128, le=1024)
    browserMemoryMiB: int = Field(ge=512, le=2048)
    enabledDslFunctions: list[DslFunction] = Field(max_length=4)


class AuditRule(BaseModel):
    """固定的 AST 拒绝规则。只读、不可关闭、不可上传表达式。"""

    model_config = ConfigDict(extra="forbid")

    ruleId: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    #: 恒为 true。留着这个字段是为了让前端能把「必需规则」渲染成禁用开关，
    #: 而不是干脆不显示——不显示会让管理员以为这里可以配。
    enabled: bool


class Policies(BaseModel):
    """GET /admin/policies 的响应。"""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    updatedAt: datetime
    values: PolicyValues
    #: 不可被 PATCH 突破的上界。前端据此把输入框的 max 定死。
    hardLimits: PolicyValues
    #: S1 增量：固定审计规则清单。
    auditRules: list[AuditRule] = Field(default_factory=list, max_length=32)


class PolicyUpdate(BaseModel):
    """PATCH /admin/policies 的请求体。

    契约要求 ``values`` 是完整对象（不是部分更新）。这是有意的：部分更新下
    「这个字段没传」和「这个字段被改成默认值」分不清，而策略是要被审计的。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    values: PolicyValues


#: 便于 API 层声明 202 响应
AcceptedSandboxRun = Annotated[SandboxRun, Field(description="已受理的执行任务")]
