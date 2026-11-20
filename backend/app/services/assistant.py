"""X1 LLM 智能辅助业务编排：受理、后台生成+自动修复+隔离校验、显式幂等应用。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import errors
from app.engine import llm_client
from app.engine.instantiate import TemplateDefinitionError, check_template_integrity, instantiate
from app.engine.validate import MAX_NODE_DEPTH, validate_ir
from app.models.assistant import AssistantProposal as AssistantProposalModel
from app.models.creations import Generation as GenerationModel
from app.models.templates import TemplateVersion
from app.models.users import User
from app.schemas.assistant import (
    TERMINAL_STATUSES,
    AssistantProposal,
    AssistantProposalCreate,
    IRFragmentProposal,
    ParameterSchemaProposal,
    ProposalError,
    ProposalValidation,
)
from app.schemas.generation import (
    AssistantProvenance,
    Generation as GenerationSchema,
    Provenance,
)
from app.schemas.ir import DrawingIR, IRNode
from app.schemas.validation import Issue
from app.services import audit, locking
from app.services import generations as generations_service
from app.services import policy as policy_service
from app.services import sandbox as sandbox_service
from app.settings import Settings

__all__ = [
    "AssistantExecutor",
    "MAX_ACTIVE_PROPOSALS_PER_USER",
    "MAX_ATTEMPTS",
    "apply_proposal",
    "create_proposal",
    "execute_proposal",
    "get_proposal",
    "is_configured",
    "recover_orphans",
    "wait_for",
]

MAX_ACTIVE_PROPOSALS_PER_USER = 2
MAX_ATTEMPTS = 2
_WORKER_PROTOCOL = 1
_ASSISTANT_ENGINE_VERSION = "x1-assistant-1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_configured(settings: Settings | None) -> bool:
    return bool(
        settings
        and settings.llm_api_key.strip()
        and settings.llm_base_url.strip()
    )


def _to_schema(row: AssistantProposalModel) -> AssistantProposal:
    proposal_raw = (
        json.loads(row.proposal_json)
        if row.status == "ready" and row.proposal_json
        else None
    )
    validation_raw = json.loads(row.validation_json) if row.validation_json else None
    error_raw = json.loads(row.error_json) if row.error_json else None
    return AssistantProposal(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        baseGenerationId=row.base_generation_id,
        target=row.target,  # type: ignore[arg-type]
        rootNodeId=row.root_node_id,
        model=row.model,
        attempts=row.attempts,
        proposal=proposal_raw,
        proposalHash=row.proposal_hash if row.status == "ready" else None,
        validation=(
            ProposalValidation.model_validate(validation_raw)
            if validation_raw
            else None
        ),
        error=ProposalError.model_validate(error_raw) if error_raw else None,
        appliedGenerationId=row.applied_generation_id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        finishedAt=_parse_dt(row.finished_at),
    )


def _is_unmodified_template_base(db: Session, base: GenerationModel) -> TemplateVersion | None:
    """仅接受直接来自模板且未经手改/子树/片段派生的基底快照。"""
    if base.mode != "template" or not base.template_version_id:
        return None
    prov = json.loads(base.provenance_json or "{}")
    if prov.get("kind") != "template":
        return None
    if prov.get("assistant") is not None:
        return None
    return db.get(TemplateVersion, base.template_version_id)


def _active_count(db: Session, owner_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AssistantProposalModel)
            .where(
                AssistantProposalModel.owner_id == owner_id,
                AssistantProposalModel.status.in_(("queued", "running")),
            )
        )
        or 0
    )


def create_proposal(
    db: Session,
    user: User,
    payload: AssistantProposalCreate,
    request_id: str,
    settings: Settings,
) -> AssistantProposal:
    """校验并受理提案，返回 202 queued 状态（proposal 为 null）。"""
    if not is_configured(settings):
        raise errors.assistant_unavailable("当前未配置模型密钥，AI 智能辅助提案不可用")

    available, reason = sandbox_service.probe()
    if not available:
        raise errors.assistant_unavailable(
            reason or "隔离校验环境不可用，AI 智能辅助提案暂不可用"
        )

    base = db.get(GenerationModel, payload.baseGenerationId)
    if base is None or not generations_service.can_read(db, user, base):
        raise errors.not_found("基底生成快照不存在")

    base_ir = DrawingIR.model_validate_json(base.ir_json)
    if payload.target == "ir_fragment":
        node_ids = {node.id for node in base_ir.nodes}
        if not payload.rootNodeId or payload.rootNodeId not in node_ids:
            raise errors.validation_error(
                f"rootNodeId「{payload.rootNodeId}」不存在于基底 IR",
                field_errors=[
                    {"path": "rootNodeId", "message": "指定的目标节点不存在于当前 IR 中"}
                ],
            )
    else:
        version = _is_unmodified_template_base(db, base)
        if version is None:
            raise errors.assistant_unsupported_base(
                "parameter_schema 提案仅支持基于未手改模板生成的快照，当前基底不受支持"
            )

    policy_revision, _ = policy_service.snapshot(db)
    prompt_hash = _sha256(payload.prompt)
    model_name = (settings.llm_model or "deepseek-chat").strip() or "deepseek-chat"

    locking.begin_immediate(db)
    try:
        active = _active_count(db, user.id)
        if active >= MAX_ACTIVE_PROPOSALS_PER_USER:
            raise errors.execution_busy(
                f"进行中的 AI 提案已达上限（{MAX_ACTIVE_PROPOSALS_PER_USER} 个），请等待完成",
                details={"maxActiveProposals": MAX_ACTIVE_PROPOSALS_PER_USER, "active": active},
            )
        row = AssistantProposalModel(
            id=f"prop-{uuid4().hex}",
            owner_id=user.id,
            base_generation_id=base.id,
            target=payload.target,
            root_node_id=payload.rootNodeId,
            prompt=payload.prompt,
            prompt_hash=prompt_hash,
            status="queued",
            model=model_name,
            attempts=0,
            policy_revision=policy_revision,
            proposal_json=None,
            proposal_hash=None,
            validation_json=None,
            error_json=None,
            applied_generation_id=None,
            request_id=request_id,
            created_at=_now_iso(),
            finished_at=None,
        )
        db.add(row)
        audit.record(
            db,
            actor=user,
            action="assistant.proposal.created",
            target_type="assistant_proposal",
            target_id=row.id,
            input_summary={
                "baseGenerationId": base.id,
                "target": payload.target,
                "rootNodeId": payload.rootNodeId,
                "promptHash": prompt_hash,
                "policyRevision": policy_revision,
            },
            request_id=request_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    return _to_schema(row)


def get_proposal(db: Session, user: User, proposal_id: str) -> AssistantProposal:
    """仅提案归属者可读；他人或不存在统一返回 404。"""
    row = db.get(AssistantProposalModel, proposal_id)
    if row is None or row.owner_id != user.id:
        raise errors.not_found("智能辅助提案不存在")
    return _to_schema(row)


def wait_for(
    db: Session, proposal_id: str, *, timeout: float = 60.0, interval: float = 0.05
) -> AssistantProposalModel:
    """等待提案进入终态（仅供测试/脚本使用）。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.get(AssistantProposalModel, proposal_id)
        if row is not None and row.status in TERMINAL_STATUSES:
            return row
        time.sleep(interval)
    db.expire_all()
    row = db.get(AssistantProposalModel, proposal_id)
    raise TimeoutError(
        f"proposal {proposal_id} 在 {timeout}s 内未进入终态（当前 {row.status if row else 'missing'}）"
    )


# ---------------------------------------------------------------------------
# Prompt 构建与隔离校验
# ---------------------------------------------------------------------------


def _collect_subtree_ids(nodes: list[IRNode], root_id: str) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parentId is not None:
            children[node.parentId].append(node.id)
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        curr = stack.pop()
        if curr in seen:
            continue
        seen.add(curr)
        stack.extend(children.get(curr, ()))
    return seen


def _ancestor_depth(nodes: list[IRNode], node_id: str) -> int:
    by_id = {n.id: n for n in nodes}
    depth = 0
    curr = by_id.get(node_id)
    seen: set[str] = set()
    while curr is not None and curr.parentId is not None and curr.parentId not in seen:
        seen.add(curr.id)
        curr = by_id.get(curr.parentId)
        if curr is not None:
            depth += 1
    return depth


def _build_ir_fragment_messages(
    base_ir: DrawingIR,
    root_node_id: str,
    user_prompt: str,
) -> list[dict[str, str]]:
    by_id = {n.id: n for n in base_ir.nodes}
    subtree = _collect_subtree_ids(list(base_ir.nodes), root_node_id)
    target_node = by_id[root_node_id]
    outside_ids = sorted(set(by_id) - subtree)
    subtree_nodes = [n.model_dump(mode="json") for n in base_ir.nodes if n.id in subtree]
    ancestor_depth = _ancestor_depth(list(base_ir.nodes), root_node_id)
    remaining_depth = max(1, MAX_NODE_DEPTH - ancestor_depth)

    system_msg = (
        "你是代码诗（CodePoet）廉洁文化生成式可视计算平台的 IR 架构助手。\n"
        "严格规则：\n"
        "1. 只能输出一个合法 JSON 对象，绝对禁止输出 Python、JavaScript、HTML5 Canvas、SVG 或任何绘图代码。\n"
        "2. 顶层结构固定且仅允许两个键：{\"rootNodeId\": string, \"replacementNodes\": [IRNode, ...]}。\n"
        "3. IRNode 严格字段（不允许任何额外键）：\n"
        "   - id: 匹配 ^[A-Za-z0-9_-]{1,80}$ 且不可与子树外既有节点 ID 冲突，replacementNodes 内部也不可重复。\n"
        "   - 子树根连续性（强制）：replacementNodes 必须包含且仅包含 1 个替换根节点，其 id 必须严格等于 rootNodeId，"
        "其 parentId 必须严格延续原目标节点的 parentId；其余所有替换节点的 parentId 必须指向 replacementNodes 内部节点，禁止脱离原父节点或改挂到子树外其他节点；总嵌套深度 <= 4。\n"
        "   - type: \"group\" | \"circle\" | \"polygon\" | \"polyline\"\n"
        "   - transform(可选): {\"x\": number, \"y\": number, \"rotation\": number, \"scaleX\": 0.1..10, \"scaleY\": 0.1..10}\n"
        "   - style:\n"
        "     * group 仅允许 {\"opacity\": 0..1}（不可含 fill/stroke/strokeWidth，且不可含 geometry）。\n"
        "     * circle/polygon/polyline 必填 {\"fill\": \"#RRGGBB\"|null, \"stroke\": \"#RRGGBB\"|null, \"strokeWidth\": 0..20, \"opacity\": 0..1}。\n"
        "   - geometry（仅叶子节点）：\n"
        "     * circle: {\"cx\": number, \"cy\": number, \"r\": 0.1..2048}\n"
        "     * polygon: {\"points\": [[x,y], ...]}（至少 3 点，简单非自交且面积 > 0）\n"
        "     * polyline: {\"points\": [[x,y], ...]}（至少 2 点）\n"
        "4. 所有颜色必须是 6 位十六进制 #RRGGBB 或 null；所有几何图形在画布坐标系下须严格落在 800x600 画布内（0<=x<=800, 0<=y<=600）。\n"
        "5. 请围绕廉洁文化美学（如清莲、翠竹、墨梅、宣纸素雅配色）产出清晰、美观且可复现的矢量图元组合。"
    )
    user_msg = (
        f"任务：将基底 IR 中的目标子树 rootNodeId=\"{root_node_id}\" 替换为满足用户需求的新子树。\n"
        f"画布尺寸：{base_ir.canvas.width}x{base_ir.canvas.height}，背景色：{base_ir.canvas.background}。\n"
        f"目标节点原 parentId：{json.dumps(target_node.parentId, ensure_ascii=False)}，"
        f"当前祖先层数：{ancestor_depth}，新子树自身最大层数：{remaining_depth}。\n"
        f"当前被替换子树节点（供位置与比例参考）：\n{json.dumps(subtree_nodes, ensure_ascii=False)}\n"
        f"子树外既有节点 ID（禁止复用）：{json.dumps(outside_ids, ensure_ascii=False)}\n"
        f"用户需求：{user_prompt}\n"
        f"请直接输出符合契约的 JSON（rootNodeId 必须等于 \"{root_node_id}\"）。"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _build_parameter_schema_messages(
    template_ir: DrawingIR,
    existing_schema: list[dict[str, Any]],
    existing_bindings: list[dict[str, Any]],
    user_prompt: str,
) -> list[dict[str, str]]:
    nodes_summary = [
        {
            "id": n.id,
            "type": n.type,
            "transform": n.transform.model_dump(mode="json"),
            "style": n.style.model_dump(mode="json"),
            **(
                {"geometry": n.geometry.model_dump(mode="json")}
                if hasattr(n, "geometry")
                else {}
            ),
        }
        for n in template_ir.nodes[:60]
    ]
    system_msg = (
        "你是代码诗（CodePoet）廉洁文化生成式可视计算平台的模板参数空间设计助手。\n"
        "严格规则：\n"
        "1. 只能输出一个合法 JSON 对象，绝对禁止输出 Python、JavaScript、Canvas 或任何绘图代码。\n"
        "2. 顶层结构固定且仅允许三个键：{\"parameterSchema\": [...], \"bindings\": [...], \"defaults\": {...}}。\n"
        "3. parameterSchema（1~16 项，name 匹配 ^[a-zA-Z][a-zA-Z0-9_]{0,39}$ 且唯一）：\n"
        "   - integer: {name, label, description, type:\"integer\", default, min, max, step(>=1)}\n"
        "   - number: {name, label, description, type:\"number\", default, min, max, step(>=0.000001)}\n"
        "   - color: {name, label, description, type:\"color\", default:\"#RRGGBB\"}\n"
        "   - boolean: {name, label, description, type:\"boolean\", default:true|false}\n"
        "   - enum: {name, label, description, type:\"enum\", default, options:[...]}\n"
        "   数值类型必须满足 min <= default <= max，且 min/max 都在所绑定 IR 属性的合法区间内！\n"
        "4. bindings（1~64 项）：每项为 {parameter, nodeId, property}。\n"
        "   - parameter 必须定义于 parameterSchema，nodeId 必须存在于模板 irTemplate.nodes 中。\n"
        "   - property 白名单及节点兼容性：\n"
        "     * group: transform.x, transform.y, transform.rotation, transform.scaleX(0.1..10), transform.scaleY(0.1..10), style.opacity(0..1)\n"
        "     * circle: 以上全部 + style.fill(#RRGGBB), style.stroke(#RRGGBB), style.strokeWidth(0..20), geometry.cx, geometry.cy, geometry.r(>=0.1)\n"
        "     * polygon/polyline: transform.* + style.opacity(0..1), style.fill(#RRGGBB), style.stroke(#RRGGBB), style.strokeWidth(0..20)\n"
        "   - 参数类型必须与属性兼容：color 绑 fill/stroke；number/integer 绑几何/变换/opacity/strokeWidth。\n"
        "5. defaults: {paramName: value}，键必须全部来自 parameterSchema，且实例化后所有绑定节点须位于 800x600 画布内。"
    )
    user_msg = (
        f"模板画布：{template_ir.canvas.width}x{template_ir.canvas.height}，背景色：{template_ir.canvas.background}。\n"
        f"模板可绑定节点清单：\n{json.dumps(nodes_summary, ensure_ascii=False)}\n"
        f"当前模板既有参数定义（参考）：{json.dumps(existing_schema, ensure_ascii=False)}\n"
        f"当前模板既有绑定（参考）：{json.dumps(existing_bindings, ensure_ascii=False)}\n"
        f"用户需求：{user_prompt}\n"
        "请直接输出包含 parameterSchema、bindings、defaults 的合法 JSON。"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _build_repair_messages(
    base_messages: list[dict[str, str]],
    previous_raw: str,
    issues: list[dict[str, Any]],
) -> list[dict[str, str]]:
    issue_lines = "\n".join(
        f"- [{item.get('code')}] {item.get('message')}"
        + (f" (path={item.get('path')})" if item.get("path") else "")
        + (f" (nodeIds={item.get('nodeIds')})" if item.get("nodeIds") else "")
        for item in issues[:20]
    )
    return [
        *base_messages,
        {"role": "assistant", "content": previous_raw[:12000]},
        {
            "role": "user",
            "content": (
                "你上一次输出的 JSON 未通过平台隔离校验，检出以下问题：\n"
                f"{issue_lines}\n"
                "请逐条修复上述全部语法、字段、ID 冲突、范围或越界问题，"
                "仅输出修复后的完整合法 JSON 对象，不要包含任何解释或绘图代码。"
            ),
        },
    ]


def _validate_in_isolation(
    *,
    target: str,
    base_ir: dict[str, Any],
    proposal: dict[str, Any],
    root_node_id: str | None,
    template_context: dict[str, Any] | None,
    policy_values: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]]]:
    """在独立子进程中执行提案校验与采样，绝不污染主 API 进程。"""
    worker_payload = {
        "protocol": _WORKER_PROTOCOL,
        "target": target,
        "baseIr": base_ir,
        "proposal": proposal,
        "rootNodeId": root_node_id,
        "templateContext": template_context,
        "policy": {
            "maxIRNodes": int(policy_values.get("maxIRNodes", 200)),
            "maxVertices": int(policy_values.get("maxVertices", 2000)),
            "maxParameters": int(policy_values.get("maxParameters", 16)),
            "wallMs": max(int(policy_values.get("dslWallTimeMs", 2000)), 2000),
            "cpuMs": max(int(policy_values.get("dslCpuTimeSeconds", 1)) * 1000, 1500),
            "memoryMiB": int(policy_values.get("dslMemoryMiB", 256)),
        },
    }
    wall_seconds = sandbox_service.wall_deadline_seconds(worker_payload["policy"]["wallMs"]) + 2.0
    try:
        res = sandbox_service.run_isolated_module(
            "app.engine.assistant_worker",
            worker_payload,
            wall_seconds=wall_seconds,
            job_memory_mib=worker_payload["policy"]["memoryMiB"],
        )
    except OSError as exc:
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_UNAVAILABLE",
                    "message": f"无法启动隔离校验子进程（{type(exc).__name__}）",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )

    if res.timed_out or res.returncode == 73:
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_TIMEOUT",
                    "message": "提案隔离校验超时，请精简生成节点或参数规模",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )
    if res.returncode == 70:
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_CPU_LIMIT",
                    "message": "提案隔离校验触发 CPU 限额",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )
    if sandbox_service.is_memory_exit(res.returncode):
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_MEMORY_LIMIT",
                    "message": "提案隔离校验触发内存限额",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )
    if res.returncode != 0:
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_ERROR",
                    "message": f"隔离校验子进程异常退出（退出码 {res.returncode}）",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )

    try:
        body = json.loads(res.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (
            False,
            None,
            [
                {
                    "code": "ASSISTANT_ISOLATED_ERROR",
                    "message": "隔离校验子进程返回了无法解析的结果",
                    "path": None,
                    "nodeIds": [],
                }
            ],
        )

    if body.get("ok"):
        return True, body, list(body.get("issues") or [])
    return False, None, list(body.get("issues") or [])


# ---------------------------------------------------------------------------
# 后台执行状态机
# ---------------------------------------------------------------------------


def _claim(db: Session, proposal_id: str) -> bool:
    result = db.execute(
        update(AssistantProposalModel)
        .where(
            AssistantProposalModel.id == proposal_id,
            AssistantProposalModel.status == "queued",
        )
        .values(status="running")
    )
    db.commit()
    return result.rowcount == 1


def _finish_failed(
    db: Session,
    row: AssistantProposalModel,
    *,
    model: str,
    attempts: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
    repaired_issues: list[dict[str, Any]] | None = None,
    actor: User | None = None,
) -> None:
    row.status = "failed"
    row.model = model
    row.attempts = attempts
    row.proposal_json = None
    row.proposal_hash = None
    row.finished_at = _now_iso()
    if issues is not None or repaired_issues is not None:
        validation = ProposalValidation(
            policyRevision=row.policy_revision,
            issues=[Issue.model_validate(item) for item in (issues or [])[:100]],
            isolatedValidationPassed=False,
            repairedIssues=[
                Issue.model_validate(item) for item in (repaired_issues or [])[:100]
            ],
        )
        row.validation_json = validation.model_dump_json()
    row.error_json = ProposalError(
        code=code, message=message[:1000], details=details
    ).model_dump_json()
    audit.record(
        db,
        actor=actor,
        action="assistant.proposal.finished",
        target_type="assistant_proposal",
        target_id=row.id,
        input_summary={"target": row.target, "attempts": attempts, "model": model},
        after={"status": "failed", "errorCode": code},
        result="failure",
        error_code=code,
        request_id=row.request_id,
    )
    db.commit()


def execute_proposal(db: Session, proposal_id: str, settings: Settings) -> None:
    """后台执行单条提案：生成 -> 隔离校验 -> 失败自动修复（最多 1 次）-> 落终态。"""
    row = db.get(AssistantProposalModel, proposal_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    if not _claim(db, proposal_id):
        return
    db.refresh(row)

    actor = db.get(User, row.owner_id)
    base = db.get(GenerationModel, row.base_generation_id)
    if base is None:
        _finish_failed(
            db,
            row,
            model=row.model,
            attempts=0,
            code="ASSISTANT_UNSUPPORTED_BASE",
            message="基底生成快照已不存在",
            actor=actor,
        )
        return

    base_ir = DrawingIR.model_validate_json(base.ir_json)
    # M5: 校验所用策略值与记录的 policy_revision 必须严格来自同一次快照，避免受理→执行间改策略导致溯源失真
    exec_policy_revision, policy_vals_model = policy_service.snapshot(db)
    row.policy_revision = exec_policy_revision
    policy_vals = policy_vals_model.model_dump(mode="json")

    template_context: dict[str, Any] | None = None
    if row.target == "ir_fragment":
        if not row.root_node_id or row.root_node_id not in {n.id for n in base_ir.nodes}:
            _finish_failed(
                db,
                row,
                model=row.model,
                attempts=0,
                code="ASSISTANT_VALIDATION_FAILED",
                message="目标节点不存在于基底 IR 中",
                actor=actor,
            )
            return
        base_messages = _build_ir_fragment_messages(base_ir, row.root_node_id, row.prompt)
    else:
        version = _is_unmodified_template_base(db, base)
        if version is None:
            _finish_failed(
                db,
                row,
                model=row.model,
                attempts=0,
                code="ASSISTANT_UNSUPPORTED_BASE",
                message="基底快照不再支持 parameter_schema 提案",
                actor=actor,
            )
            return
        template_ir = DrawingIR.model_validate_json(version.ir_template_json)
        existing_schema = json.loads(version.parameter_schema_json or "[]")
        existing_bindings = json.loads(version.bindings_json or "[]")
        template_context = {"templateIr": template_ir.model_dump(mode="json")}
        base_messages = _build_parameter_schema_messages(
            template_ir, existing_schema, existing_bindings, row.prompt
        )

    actual_model = row.model
    attempt1_issues: list[dict[str, Any]] = []
    previous_raw = ""
    current_messages = base_messages

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            llm_resp = llm_client.chat_json(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                messages=current_messages,
            )
            actual_model = llm_resp.model
            previous_raw = llm_resp.content
        except llm_client.LlmClientError as exc:
            _finish_failed(
                db,
                row,
                model=actual_model,
                attempts=attempt,
                code=exc.code,
                message=exc.message,
                details=exc.details,
                repaired_issues=attempt1_issues or None,
                actor=actor,
            )
            return

        try:
            parsed_json = llm_client.extract_json_object(previous_raw)
        except Exception as exc:  # noqa: BLE001
            issues = [
                {
                    "code": "ASSISTANT_JSON_PARSE_FAILED",
                    "message": f"模型输出不是合法 JSON 对象（{type(exc).__name__}）",
                    "path": None,
                    "nodeIds": [],
                }
            ]
            if attempt < MAX_ATTEMPTS:
                attempt1_issues = issues
                current_messages = _build_repair_messages(base_messages, previous_raw, issues)
                continue
            _finish_failed(
                db,
                row,
                model=actual_model,
                attempts=attempt,
                code="ASSISTANT_VALIDATION_FAILED",
                message="模型两次输出均未能解析为合法提案 JSON",
                details={"issues": issues},
                issues=issues,
                repaired_issues=attempt1_issues,
                actor=actor,
            )
            return

        ok, validated, issues = _validate_in_isolation(
            target=row.target,
            base_ir=base_ir.model_dump(mode="json"),
            proposal=parsed_json,
            root_node_id=row.root_node_id,
            template_context=template_context,
            policy_values=policy_vals,
        )
        if ok and validated is not None:
            normalized = validated["normalizedProposal"]
            canonical = _canonical_json(normalized)
            proposal_hash = _sha256(canonical)
            validation = ProposalValidation(
                policyRevision=row.policy_revision,
                issues=[Issue.model_validate(item) for item in issues[:100]],
                isolatedValidationPassed=True,
                repairedIssues=[
                    Issue.model_validate(item) for item in attempt1_issues[:100]
                ],
            )
            row.status = "ready"
            row.model = actual_model
            row.attempts = attempt
            row.proposal_json = canonical
            row.proposal_hash = proposal_hash
            row.validation_json = validation.model_dump_json()
            row.error_json = None
            row.finished_at = _now_iso()
            audit.record(
                db,
                actor=actor,
                action="assistant.proposal.finished",
                target_type="assistant_proposal",
                target_id=row.id,
                input_summary={"target": row.target, "attempts": attempt, "model": actual_model},
                after={
                    "status": "ready",
                    "proposalHash": proposal_hash,
                    "repairedIssueCount": len(attempt1_issues),
                },
                request_id=row.request_id,
            )
            db.commit()
            return

        if attempt < MAX_ATTEMPTS:
            attempt1_issues = issues
            current_messages = _build_repair_messages(base_messages, previous_raw, issues)
            continue

        _finish_failed(
            db,
            row,
            model=actual_model,
            attempts=attempt,
            code="ASSISTANT_VALIDATION_FAILED",
            message=f"提案自动修复后仍未通过隔离校验（剩余 {len(issues)} 项问题）",
            details={"issues": issues[:20]},
            issues=issues,
            repaired_issues=attempt1_issues,
            actor=actor,
        )
        return


# ---------------------------------------------------------------------------
# 显式幂等应用
# ---------------------------------------------------------------------------


def apply_proposal(
    db: Session,
    user: User,
    proposal_id: str,
    request_id: str,
) -> GenerationSchema:
    """显式应用已就绪提案，事务幂等保护，返回 201 Generation。"""
    row = db.get(AssistantProposalModel, proposal_id)
    if row is None or row.owner_id != user.id:
        raise errors.not_found("智能辅助提案不存在")

    if row.status != "ready" or not row.proposal_json or not row.proposal_hash:
        raise errors.invalid_state("提案尚未就绪或已失败，无法应用")

    # 权限重检：防止提案生成后源作品被设为私有或下线导致越权派生
    base = db.get(GenerationModel, row.base_generation_id)
    if base is None or not generations_service.can_read(db, user, base):
        raise errors.not_found("基底生成快照已不可访问，无法应用提案")

    locking.begin_immediate(db)
    try:
        db.expire_all()
        row = db.get(AssistantProposalModel, proposal_id)
        if row is None or row.owner_id != user.id:
            raise errors.not_found("智能辅助提案不存在")
        if row.status != "ready" or not row.proposal_json or not row.proposal_hash:
            raise errors.invalid_state("提案尚未就绪或已失败，无法应用")

        base = db.get(GenerationModel, row.base_generation_id)
        if base is None or not generations_service.can_read(db, user, base):
            raise errors.not_found("基底生成快照已不可访问，无法应用提案")

        # 事务内幂等：已经应用过的提案直接返回同一份 Generation，不重复造版本
        if row.applied_generation_id:
            existing = db.get(GenerationModel, row.applied_generation_id)
            if existing is not None:
                db.commit()
                return generations_service.build_generation_schema(existing)

        # M5: apply 前重检当前生效策略限额，防止管理员收紧策略后旧 ready 提案突破最新限额
        _, current_policy = policy_service.snapshot(db)

        assistant_prov = AssistantProvenance(
            proposalId=row.id,
            model=row.model,
            attempts=row.attempts,
            proposalHash=row.proposal_hash,
            policyRevision=row.policy_revision,
        )

        if row.target == "ir_fragment":
            parsed = IRFragmentProposal.model_validate_json(row.proposal_json)
            base_ir = DrawingIR.model_validate_json(base.ir_json)
            by_id = {n.id: n for n in base_ir.nodes}
            if parsed.rootNodeId not in by_id:
                raise errors.ir_invalid("基底 IR 节点已不匹配提案目标")
            if row.root_node_id and parsed.rootNodeId != row.root_node_id:
                raise errors.ir_invalid("提案 rootNodeId 与记录不一致")

            # M1: apply 侧复核子树根连续性（不单独信任 worker 结果）
            target_node = by_id[parsed.rootNodeId]
            rep_ids = {n.id for n in parsed.replacementNodes}
            roots = [n for n in parsed.replacementNodes if n.parentId not in rep_ids]
            if (
                len(roots) != 1
                or roots[0].id != parsed.rootNodeId
                or roots[0].parentId != target_node.parentId
            ):
                raise errors.ir_invalid(
                    "提案替换根节点未保持原节点 ID 或原 parentId，无法作为合法子树替换应用"
                )

            subtree = _collect_subtree_ids(list(base_ir.nodes), parsed.rootNodeId)
            kept = [n for n in base_ir.nodes if n.id not in subtree]
            merged = kept + list(parsed.replacementNodes)
            if len(merged) > current_policy.maxIRNodes:
                raise errors.invalid_state(
                    f"当前执行策略已收紧，替换后节点数 {len(merged)} 超过上限 {current_policy.maxIRNodes}"
                )
            new_ir = DrawingIR.model_validate(
                {
                    "schemaVersion": base_ir.schemaVersion,
                    "seed": base_ir.seed,
                    "canvas": base_ir.canvas.model_dump(),
                    "viewport": base_ir.viewport.model_dump(),
                    "durationMs": base_ir.durationMs,
                    "nodes": [n.model_dump() for n in merged],
                    "animations": [
                        a.model_dump(by_alias=True)
                        for a in base_ir.animations
                        if a.nodeId not in subtree
                    ],
                    "narrative": [item.model_dump() for item in base_ir.narrative],
                }
            )
            total_vertices = sum(
                len(n.geometry.points)
                for n in new_ir.nodes
                if n.type in ("polygon", "polyline")
            )
            if total_vertices > current_policy.maxVertices:
                raise errors.invalid_state(
                    f"当前执行策略已收紧，替换后顶点数 {total_vertices} 超过上限 {current_policy.maxVertices}"
                )
            report = validate_ir(new_ir)
            blocking = [i for i in report.issues if i.code != "IR_OUT_OF_BOUNDS"]
            if blocking:
                raise errors.ir_invalid(
                    f"应用提案后的 IR 校验失败（{len(blocking)} 处问题）",
                    details=report.model_dump(mode="json"),
                )
            warnings = [i for i in report.issues if i.code == "IR_OUT_OF_BOUNDS"]

            prov_obj = generations_service.inherited_provenance(base, "subgraph_edit")
            prov_dict = prov_obj.model_dump(mode="json")
            prov_dict.update(
                engineVersion=_ASSISTANT_ENGINE_VERSION,
                policyRevision=row.policy_revision,
                assistant=assistant_prov.model_dump(mode="json"),
            )
            provenance = Provenance.model_validate(prov_dict)
            gen_mode = "subgraph"
            template_version_id = base.template_version_id
            params_json = base.params_json or "{}"
        else:
            version = _is_unmodified_template_base(db, base)
            if version is None:
                raise errors.assistant_unsupported_base(
                    "基底模板版本已不可用于 parameter_schema 应用"
                )
            parsed_ps = ParameterSchemaProposal.model_validate_json(row.proposal_json)
            if len(parsed_ps.parameterSchema) > current_policy.maxParameters:
                raise errors.invalid_state(
                    f"当前执行策略已收紧，参数数 {len(parsed_ps.parameterSchema)} 超过上限 {current_policy.maxParameters}"
                )
            template_ir = DrawingIR.model_validate_json(version.ir_template_json)
            try:
                check_template_integrity(
                    template_ir,
                    parsed_ps.parameterSchema,
                    parsed_ps.bindings,
                    [],
                )
                outcome = instantiate(
                    template_ir,
                    parsed_ps.parameterSchema,
                    parsed_ps.bindings,
                    [],
                    parsed_ps.defaults,
                )
            except TemplateDefinitionError as exc:
                raise errors.ir_invalid(f"临时模板实例化失败：{exc}") from exc
            if not outcome.ok:
                raise errors.ir_invalid(
                    f"临时模板参数校验失败：{outcome.issues[0].message}"
                )
            new_ir = outcome.ir
            if len(new_ir.nodes) > current_policy.maxIRNodes:
                raise errors.invalid_state(
                    f"当前执行策略已收紧，实例化节点数 {len(new_ir.nodes)} 超过上限 {current_policy.maxIRNodes}"
                )
            total_vertices = sum(
                len(n.geometry.points)
                for n in new_ir.nodes
                if n.type in ("polygon", "polyline")
            )
            if total_vertices > current_policy.maxVertices:
                raise errors.invalid_state(
                    f"当前执行策略已收紧，实例化顶点数 {total_vertices} 超过上限 {current_policy.maxVertices}"
                )
            report = validate_ir(new_ir)
            blocking = [i for i in report.issues if i.code != "IR_OUT_OF_BOUNDS"]
            if blocking:
                raise errors.ir_invalid(
                    f"应用参数空间提案后的 IR 校验失败（{len(blocking)} 处问题）",
                    details=report.model_dump(mode="json"),
                )
            warnings = [i for i in report.issues if i.code == "IR_OUT_OF_BOUNDS"]

            prov_obj = generations_service.inherited_provenance(base, "template")
            prov_dict = prov_obj.model_dump(mode="json")
            prov_dict.update(
                engineVersion=_ASSISTANT_ENGINE_VERSION,
                policyRevision=row.policy_revision,
                assistant=assistant_prov.model_dump(mode="json"),
            )
            provenance = Provenance.model_validate(prov_dict)
            gen_mode = "template"
            template_version_id = base.template_version_id
            params_json = json.dumps(outcome.params, ensure_ascii=False, sort_keys=True)

        generation = GenerationModel(
            id=f"gen-{uuid4().hex}",
            owner_id=user.id,
            mode=gen_mode,
            backend=base.backend,
            template_version_id=template_version_id,
            params_json=params_json,
            seed=new_ir.seed,
            ir_json=new_ir.model_dump_json(),
            ir_hash=generations_service.compute_ir_hash(new_ir),
            provenance_json=provenance.model_dump_json(),
            validation_report_json=report.model_dump_json(),
            warnings_json=json.dumps(
                [item.model_dump(mode="json") for item in warnings], ensure_ascii=False
            ),
            unpublished_source=base.unpublished_source,
            request_id=request_id,
            created_at=_now_iso(),
        )
        db.add(generation)
        db.flush()
        row.applied_generation_id = generation.id
        audit.record(
            db,
            actor=user,
            action="assistant.proposal.applied",
            target_type="assistant_proposal",
            target_id=row.id,
            input_summary={
                "baseGenerationId": base.id,
                "target": row.target,
                "proposalHash": row.proposal_hash,
            },
            after={
                "generationId": generation.id,
                "irHash": generation.ir_hash,
            },
            request_id=request_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(generation)
    return generations_service.build_generation_schema(generation)


# ---------------------------------------------------------------------------
# 后台执行器与启动清理
# ---------------------------------------------------------------------------


class AssistantExecutor:
    """有界后台执行器：独立线程池调用模型与隔离校验，避免阻塞 HTTP 线程。"""

    def __init__(self, session_factory, settings: Settings, max_workers: int = 2) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="assistant"
        )
        self._closed = False
        self._lock = Lock()

    def submit(self, proposal_id: str) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("智能辅助执行器已关闭")
            self._pool.submit(self._run_safely, proposal_id)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=wait)

    def _run_safely(self, proposal_id: str) -> None:
        db = self._sessions()
        try:
            execute_proposal(db, proposal_id, self._settings)
        except Exception:  # noqa: BLE001
            db.rollback()
            try:
                _mark_crashed(db, proposal_id)
            except Exception:  # noqa: BLE001
                db.rollback()
        finally:
            db.close()


def _mark_crashed(db: Session, proposal_id: str) -> None:
    row = db.get(AssistantProposalModel, proposal_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    row.status = "failed"
    row.proposal_json = None
    row.proposal_hash = None
    row.finished_at = _now_iso()
    row.error_json = ProposalError(
        code="ASSISTANT_EXECUTION_FAILED",
        message="提案执行线程异常终止，请重试",
        details={},
    ).model_dump_json()
    db.commit()


def recover_orphans(db: Session) -> int:
    """服务重启时清理遗留的 queued/running 提案，置为 failed。"""
    now = _now_iso()
    rows = db.scalars(
        select(AssistantProposalModel).where(
            AssistantProposalModel.status.in_(("queued", "running"))
        )
    ).all()
    for row in rows:
        row.status = "failed"
        row.proposal_json = None
        row.proposal_hash = None
        row.finished_at = now
        row.error_json = ProposalError(
            code="ASSISTANT_INTERRUPTED",
            message="服务重启导致提案生成中断，请重新发起",
            details={"reason": "worker_restarted"},
        ).model_dump_json()
    if rows:
        db.commit()
    return len(rows)
