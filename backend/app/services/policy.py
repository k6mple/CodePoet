"""全局执行策略：读、改、以及在任务提交时冻结快照。

为什么策略要存数据库而不是写常量：

细则允许管理员调 DSL 限额。但如果只改页面上的数字、``validate_ir`` 仍用旧的
硬编码常量，页面会显示「已放宽到 500 节点」而实际还在 200 就拒——这种「显示
一套、执行一套」比不给配更糟。所以策略必须只有一份事实来源，且**写进去的值
立刻对后续任务生效**。

为什么又要 ``HARD_LIMITS``：

``PolicyValues`` 的 schema 区间来自契约（比如 ``maxIRNodes`` 允许到 3000），
但本实例的全局 IR 校验只认 200 节点。如果策略能把限额抬到 3000，就会出现
「策略说可以、``validate_ir`` 说不行」的两套标准。因此本期把「可调区间」夹在
``HARD_LIMITS`` 里，它等于 core-demo 的既有常量；超出直接 422。

可写字段只有 DSL 那一组（外加允许函数）。``renderWallTimeMs`` / ``turtleMemoryMiB``
/ ``browserMemoryMiB`` / ``maxIRNodes`` 等都属于别的任务的预留位，本期只读——
让一个还没实现的东西可配，等于承诺一个不存在的开关。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import errors
from app.engine import dsl
from app.models.sandbox import Policy as PolicyModel
from app.models.users import User
from app.schemas.sandbox import (
    AuditRule,
    Policies,
    PolicyUpdate,
    PolicyValues,
    SandboxPolicySnapshot,
)
from app.services import audit, permissions

__all__ = [
    "AUDIT_RULES",
    "DEFAULT_VALUES",
    "HARD_LIMITS",
    "READ_ONLY_FIELDS",
    "WRITABLE_FIELDS",
    "get_policies",
    "read_policies",
    "snapshot",
    "to_snapshot",
    "update_policies",
]

#: 初始策略值。必须与 ``migrations/013_sandbox_runs_and_policies.sql`` 里插入的
#: JSON 一致——由 ``test_policy.DefaultsTests`` 读库比对，不靠人工同步。
DEFAULT_VALUES = PolicyValues(
    maxIRNodes=200,
    maxVertices=2000,
    maxRequestBytes=262144,
    maxIRBytes=262144,
    maxParameters=16,
    dslWallTimeMs=2000,
    dslCpuTimeSeconds=1,
    dslMemoryMiB=256,
    dslMaxDrawCalls=1000,
    dslMaxAstNodes=2000,
    dslMaxRangeIterations=1,
    renderWallTimeMs=120000,
    turtleMemoryMiB=1024,
    browserMemoryMiB=2048,
    enabledDslFunctions=["circle", "polygon", "polyline", "set_param"],
)

#: 不可被 PATCH 突破的上界。数值等于 core-demo 的既有常量，注释里逐个交代来源：
#:
#: * ``maxIRNodes=200``        —— ``engine/sample.py`` 的 ``MAX_NODES``
#: * ``maxVertices=2000``      —— ``engine/validate.py`` 的 ``MAX_TOTAL_VERTICES``
#: * ``maxRequestBytes``       —— ``main.py`` 的 256KiB 请求上限
#: * ``maxIRBytes``            —— core-demo 没有独立的 IR 字节上限，取与请求体
#:                                一致的值作为本次硬限，而不是凭空造一个更严的数
#: * ``maxParameters=16``      —— ``Capabilities.maxParameters``
#: * ``dslWallTimeMs/Cpu/Mem`` —— 细则第 7 节「默认 2 秒/CPU 1 秒/256MiB」
#: * ``dslMaxDrawCalls=1000``  —— 同上「1000 calls」
#: * ``dslMaxAstNodes=2000``   —— 契约 dslMaxAstNodes 的上界
#: * ``dslMaxRangeIterations=1`` —— DSL v1 没有循环，只能取 1
HARD_LIMITS = PolicyValues(
    maxIRNodes=200,
    maxVertices=2000,
    maxRequestBytes=262144,
    maxIRBytes=262144,
    maxParameters=16,
    dslWallTimeMs=2000,
    dslCpuTimeSeconds=1,
    dslMemoryMiB=256,
    dslMaxDrawCalls=1000,
    dslMaxAstNodes=2000,
    dslMaxRangeIterations=1,
    renderWallTimeMs=120000,
    turtleMemoryMiB=1024,
    browserMemoryMiB=2048,
    enabledDslFunctions=["circle", "polygon", "polyline", "set_param"],
)

#: 本期唯一允许改的字段。DSL 限额 + 允许函数，别的都在只读集里。
WRITABLE_FIELDS = frozenset(
    {
        "dslWallTimeMs",
        "dslCpuTimeSeconds",
        "dslMemoryMiB",
        "dslMaxDrawCalls",
        "dslMaxAstNodes",
        "dslMaxRangeIterations",
        "enabledDslFunctions",
    }
)

READ_ONLY_FIELDS = frozenset(PolicyValues.model_fields) - WRITABLE_FIELDS

#: 固定审计规则。``enabled`` 恒为 true——这些是 AST 安全边界本身，
#: 「关掉某条必需规则」不是一个应当存在的操作，所以没有对应的写入口。
#:
#: 规则 ID 直接取自 ``engine.dsl`` 的常量，而不是在这里另写一遍字符串：
#: 拒绝时写进 finding 的 ``ruleId`` 必须和这份清单对得上，否则前端拿到一个
#: 在清单里查不到的规则名，既没法展示说明也没法做「点这条规则看详情」。
AUDIT_RULES: tuple[AuditRule, ...] = (
    AuditRule(
        ruleId=dsl.RULE_DOCUMENT_SHAPE,
        description="源码必须是合法 JSON 对象，version=1，ops 是非空数组",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_OP_WHITELIST,
        description="只解释 circle / polygon / polyline / set_param 四种操作",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_FIELD_WHITELIST,
        description="每个操作只允许固定字段，多一个字段整份拒绝",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_NUMERIC_RANGE,
        description="坐标、半径、描边、透明度必须落在 IR schema 范围内，拒绝 NaN/Infinity",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_NODE_ID_UNIQUE,
        description="生成节点 ID 不得与 base 既有节点冲突",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_BASE_UNMODIFIED,
        description="base 的节点、动画、叙事原样保留，新增图元只挂在末尾 root group 下",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_SET_PARAM_BASE,
        description="set_param 只用于「有模板来源、未手改、未子图派生」的 base，且必须先于绘图调用",
        enabled=True,
    ),
    AuditRule(
        ruleId=dsl.RULE_BUDGET_LIMIT,
        description="绘图调用数、解释步数、AST 节点数、全图顶点数不得超过策略限额",
        enabled=True,
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # 库里被手改坏的时间不该让 GET 直接 500：给一个可解释的兜底值。
        return datetime.now(timezone.utc)


def _dumps(values: PolicyValues) -> str:
    return json.dumps(values.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _load(db: Session) -> PolicyModel:
    """取单行策略；不存在就按默认值补一行。

    迁移已经插了初始行，这里的兜底是给「用旧库手工升级」和「测试临时库」用的：
    缺表行时应当自动恢复，而不是让整个 `/admin/policies` 500。
    """
    row = db.get(PolicyModel, 1)
    if row is None:
        row = PolicyModel(
            id=1,
            revision=1,
            values_json=_dumps(DEFAULT_VALUES),
            updated_at=_now_iso(),
            updated_by=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _to_values(raw: str) -> PolicyValues:
    """反序列化策略值。

    库里存的是 JSON 文本；如果它坏了（手工改过、旧版本字段缺失），
    退化成默认值而不是抛 500——策略读不出来时，用「最严格的那一版」
    继续服务比整体不可用好，而且默认值就是最严格的那一版。
    """
    try:
        return PolicyValues.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return DEFAULT_VALUES


def get_policies(db: Session) -> Policies:
    """内部读取，不做鉴权。对外接口走 ``read_policies``。"""
    row = _load(db)
    return Policies(
        revision=row.revision,
        updatedAt=_parse_dt(row.updated_at),
        values=_to_values(row.values_json),
        hardLimits=HARD_LIMITS,
        auditRules=list(AUDIT_RULES),
    )


def read_policies(db: Session, actor: User) -> Policies:
    """GET /admin/policies 的服务层入口。权限判断留在这里，控制器不碰。"""
    if not permissions.has_permission(actor, permissions.POLICIES_READ):
        raise errors.forbidden("只有管理员可以查看执行策略")
    return get_policies(db)


def _changed_fields(current: PolicyValues, submitted: PolicyValues) -> set[str]:
    today = current.model_dump(mode="json")
    wanted = submitted.model_dump(mode="json")
    return {name for name, value in wanted.items() if today.get(name) != value}


def _over_hard_limits(submitted: PolicyValues) -> list[str]:
    """返回超出硬上限（或低于硬下限）的字段名。

    硬限是**上界**语义：策略只能收紧，不能放松。所以判据是
    ``submitted > hard``，而不是「不等于」——管理员把 2000ms 调成 1500ms
    完全合法，那只是让用户可用的时间更短。
    """
    over: list[str] = []
    hard = HARD_LIMITS.model_dump(mode="json")
    for name, value in submitted.model_dump(mode="json").items():
        limit = hard[name]
        if isinstance(limit, list):
            # 允许函数：不能加入本期未实现的名字（类型已挡），但也不能反而
            # 变成「比硬限还多」，列表比较就是子集判断。
            if set(value) - set(limit):
                over.append(name)
        elif value > limit:
            over.append(name)
    return over


def update_policies(
    db: Session,
    actor: User,
    payload: PolicyUpdate,
    request_id: str,
) -> Policies:
    """改策略。三道门：先鉴权，再只读，最后硬限。

    顺序是有讲究的：只读判定必须在硬限之前。若先判硬限，管理员把只读字段
    改成一个超过硬限的值时会收到「超上限」，而真实原因是他根本没资格改这个
    字段——错误信息会把排查方向带偏。
    """
    if not permissions.has_permission(actor, permissions.POLICIES_WRITE):
        raise errors.forbidden("只有管理员可以修改执行策略")

    row = _load(db)
    if payload.expectedRevision != row.revision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    current = _to_values(row.values_json)
    changed = _changed_fields(current, payload.values)
    if not changed:
        # 提交了一份与现状完全相同的策略。不递增 revision、不写审计：
        # 版本号是给「这次真的改了东西」用的，空改动也 +1 会让前端在
        # 「什么都没动但 revision 变了」之后误判有人并发修改。
        return get_policies(db)

    read_only_touched = sorted(changed & READ_ONLY_FIELDS)
    if read_only_touched:
        raise errors.policy_field_read_only(read_only_touched)

    over = _over_hard_limits(payload.values)
    if over:
        raise errors.validation_error(
            f"以下策略值超出本实例硬上限：{', '.join(sorted(over))}",
            field_errors=[
                {"path": f"values.{name}", "message": "超出自 core-demo 硬上限"}
                for name in sorted(over)
            ],
            details={"hardLimits": HARD_LIMITS.model_dump(mode="json")},
        )

    before = current.model_dump(mode="json")
    # 乐观锁必须是**原子**的。
    #
    # 原先写的是「先比 expectedRevision，再赋值提交」：两个并发 PATCH 都读到
    # revision=N，比较都能过，后提交者直接覆盖前者——客户端拿不到 409，
    # 还会以为自己那版生效了。条件放进 UPDATE 的 WHERE 之后，数据库保证只有
    # 一个请求能把 N 推到 N+1，另一个 rowcount=0 就是 409。
    # 这与 works.create_version 的修法是同一套（那边踩过同样的坑）。
    affected = db.execute(
        update(PolicyModel)
        .where(
            PolicyModel.id == 1,
            PolicyModel.revision == payload.expectedRevision,
        )
        .values(
            values_json=_dumps(payload.values),
            revision=payload.expectedRevision + 1,
            updated_at=_now_iso(),
            updated_by=actor.id,
        )
    ).rowcount
    if affected != 1:
        db.rollback()
        actual = db.scalar(select(PolicyModel.revision).where(PolicyModel.id == 1)) or 0
        raise errors.revision_conflict(payload.expectedRevision, actual)

    # 审计跟随业务事务：预算改了什么、谁改的、从哪版到哪版，必须和改动同生共死
    audit.record(
        db,
        actor=actor,
        action="policy.update",
        target_type="policies",
        target_id="policies",
        input_summary={
            "changedFields": sorted(changed),
            "fromRevision": payload.expectedRevision,
            "toRevision": payload.expectedRevision + 1,
        },
        before={"revision": payload.expectedRevision, "values": before},
        after={
            "revision": payload.expectedRevision + 1,
            "values": payload.values.model_dump(mode="json"),
        },
        request_id=request_id,
    )
    db.commit()
    # UPDATE 语句绕过了 ORM，identity map 里那一行的属性已经陈旧；
    # 不 expire 的话 get_policies 会命中缓存对象、返回旧的 revision。
    db.expire_all()
    return get_policies(db)


def snapshot(db: Session) -> tuple[int, PolicyValues]:
    """取「此刻」的策略快照，用于在 run 提交时冻结。

    返回 ``(revision, values)`` 而不是 ORM 行：调用方拿到的是值拷贝，
    后续管理员再 PATCH 也影响不到已经受理的任务。
    """
    row = _load(db)
    return row.revision, _to_values(row.values_json)


def to_snapshot(revision: int, values: PolicyValues) -> SandboxPolicySnapshot:
    """裁出与 DSL 执行相关的那部分，作为 run 响应的 ``policySnapshot``。"""
    return SandboxPolicySnapshot(
        revision=revision,
        dslWallTimeMs=values.dslWallTimeMs,
        dslCpuTimeSeconds=values.dslCpuTimeSeconds,
        dslMemoryMiB=values.dslMemoryMiB,
        dslMaxDrawCalls=values.dslMaxDrawCalls,
        dslMaxAstNodes=values.dslMaxAstNodes,
        enabledDslFunctions=list(values.enabledDslFunctions),
    )
