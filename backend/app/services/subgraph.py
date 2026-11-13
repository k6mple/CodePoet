"""IR 子树替换/裁剪。

做法是「按 rootNodeId 找出整棵子树 → 用 replacementNodes 换掉 → 其余节点
原样保留」。三点值得说明：

1. **子树包含所有后代**，不只是直接子节点。只换一层的话，父节点没了
   子节点还在，新 IR 会立刻挂在「父引用悬空」上。
2. **新节点 ID 不能撞到子树之外的现有节点**。撞了就是两个节点同 ID，
   IR 校验会拒，但那时错误信息只说「重复 id」，看不出是这次编辑引入的。
3. **被替换掉的节点记为 removedNodeIds**，前端要据此高亮"这次改了哪些"。

分层：不导入 FastAPI，只接 Pydantic 与 Session。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app import errors
from app.engine.validate import validate_ir
from app.models.creations import Generation as GenerationModel
from app.models.users import User
from app.schemas.ir import DrawingIR, IRNode, Viewport
from app.schemas.subgraph import SubgraphEdit, SubgraphGeneration
from app.services import audit
from app.services.generations import (
    build_generation_schema,
    can_read,
    compute_ir_hash,
    inherited_provenance,
)

__all__ = ["edit_subgraph"]

# 引擎版本：子树编辑属于 P1-B，与模板实例化分开记，方便复现时区分来源
_SUBGRAPH_ENGINE_VERSION = "p1b-02.1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _collect_subtree(nodes: list[IRNode], root_id: str) -> set[str]:
    """收集 root 及其全部后代。

    用显式栈而不是递归：IR 允许 200 个节点，链式结构最深可以到 200 层，
    Python 默认递归深度够但没必要冒这个险。
    """
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parentId is not None:
            children[node.parentId].append(node.id)

    seen: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, ()))
    return seen


def edit_subgraph(
    db: Session, user: User, generation_id: str, payload: SubgraphEdit
) -> SubgraphGeneration:
    base = db.get(GenerationModel, generation_id)
    # 契约：基础 generation 必须属本人。看不了的一律 404，不确认存在性
    if base is None or base.owner_id != user.id:
        raise errors.not_found("基底生成快照不存在")

    ir = DrawingIR.model_validate_json(base.ir_json)
    nodes = list(ir.nodes)
    by_id = {node.id: node for node in nodes}

    if payload.rootNodeId not in by_id:
        raise errors.validation_error(
            f"rootNodeId「{payload.rootNodeId}」不存在于基底 IR"
        )

    subtree = _collect_subtree(nodes, payload.rootNodeId)

    replacement_ids = [node.id for node in payload.replacementNodes]
    duplicated = {
        node_id for node_id in replacement_ids if replacement_ids.count(node_id) > 1
    }
    if duplicated:
        raise errors.validation_error(
            f"replacementNodes 内 id 重复：{sorted(duplicated)[:5]}"
        )

    # 新节点不能撞到子树以外的现有节点：撞了就会有两个同 ID 的节点，
    # IR 校验会说「重复 id」，但看不出是这次编辑引入的
    outside = set(by_id) - subtree
    conflict = set(replacement_ids) & outside
    if conflict:
        raise errors.validation_error(
            f"replacementNodes 的 id 与子树外的现有节点冲突：{sorted(conflict)[:5]}"
        )

    kept = [node for node in nodes if node.id not in subtree]
    merged = kept + list(payload.replacementNodes)

    viewport = payload.viewport or ir.viewport
    new_ir = DrawingIR.model_validate({
        "schemaVersion": ir.schemaVersion,
        "seed": ir.seed,
        "canvas": ir.canvas.model_dump(),
        "viewport": viewport.model_dump(),
        "durationMs": ir.durationMs,
        "nodes": [node.model_dump() for node in merged],
        "animations": [
            a.model_dump(by_alias=True)
            for a in ir.animations
            # 被删掉的节点不该留着动画轨道
            if a.nodeId not in subtree
        ],
        "narrative": [n.model_dump() for n in ir.narrative],
    })

    report = validate_ir(new_ir)
    blocking = [issue for issue in report.issues if issue.code != "IR_OUT_OF_BOUNDS"]
    if blocking:
        raise errors.ir_invalid(
            f"子树替换后的 IR 有 {len(blocking)} 处问题，无法生成快照",
            details=report.model_dump(mode="json"),
        )

    provenance = inherited_provenance(base, 'subgraph_edit').model_dump(mode='json')
    provenance.update(engineVersion=_SUBGRAPH_ENGINE_VERSION,
                      fragmentSource=f'subtree:{payload.rootNodeId}',
                      fragmentSourceHash=compute_ir_hash(ir))

    row = GenerationModel(
        id=f"gen-{uuid4().hex}",
        owner_id=user.id,
        mode="subgraph",
        backend=base.backend,
        template_version_id=base.template_version_id,
        params_json=base.params_json,
        seed=new_ir.seed,
        ir_json=new_ir.model_dump_json(),
        ir_hash=compute_ir_hash(new_ir),
        provenance_json=__import__("json").dumps(provenance, ensure_ascii=False),
        validation_report_json=report.model_dump_json(),
        warnings_json="[]",
        # 派生自未发布的基底，仍然是未发布
        unpublished_source=base.unpublished_source,
        request_id=base.request_id,
        created_at=_now_iso(),
    )
    db.add(row)
    audit.record(
        db,
        actor=user,
        action="generation.subgraph",
        target_type="generation",
        target_id=row.id,
        input_summary={
            "baseGenerationId": base.id,
            "rootNodeId": payload.rootNodeId,
            "replacementCount": len(payload.replacementNodes),
        },
        before={"nodeCount": len(nodes)},
        after={"nodeCount": len(merged)},
        result="success",
    )
    db.commit()
    db.refresh(row)

    removed = sorted(subtree)
    return SubgraphGeneration(
        generation=build_generation_schema(row),
        changedNodeIds=replacement_ids,
        removedNodeIds=removed,
    )
