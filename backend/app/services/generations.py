"""生成快照的业务编排。

控制器只管 HTTP，这里负责：解析模板数据 → 实例化 → 校验 → 算哈希 → 落库。

三条路线里 P0-03 只实现两条：

* ``template`` —— 读模板版本的 IR 骨架与声明式绑定，用参数实例化出来；
* ``ir`` —— 前端提交完整 IR，只做校验与快照化。

``semantic`` 明确拒绝（422 UNSUPPORTED_CAPABILITY）。契约强调「不把未实现
说成暂时渲染成功」，所以这里不留一个假成功的分支。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.engine import semantic as semantic_engine
from app.engine.instantiate import TemplateDefinitionError, instantiate
from app.engine.validate import validate_ir
from app.models.creations import Generation as GenerationModel
from app.models.templates import TemplateVersion
from app.models.users import User
from app.models.knowledge import KnowledgeEntity as KnowledgeModel
from app.models.rules import MappingRule as RuleModel
from app.schemas.generation import (
    Binding,
    Constraint,
    Generation as GenerationSchema,
    GenerationCreate,
    IRGeneration,
    ParameterDefinition,
    Provenance,
    SemanticGeneration,
    TemplateGeneration,
    TemplateSelection,
)
from app.schemas.ir import DrawingIR
from app.schemas.validation import Issue, ValidationReport
from app.services import knowledge as knowledge_service
from app.services import rules as rules_service

# 写进来源快照的引擎版本。改动实例化/校验逻辑时递增，方便复现时追溯。
ENGINE_VERSION = "p0-03.1"
ONTOLOGY_VERSION = 1
POLICY_REVISION = 1

# 这些 code 只警告不阻断：越界的图形仍然能画出来，只是会被画布裁掉一部分。
# 其余问题（重复 id、父引用悬空、自交多边形等）会让渲染结果不可预期，必须拒绝。
_WARNING_CODES = frozenset({"IR_OUT_OF_BOUNDS"})

_PARAM_ADAPTER = TypeAdapter(ParameterDefinition)
_CONSTRAINT_ADAPTER = TypeAdapter(Constraint)


# ---- 工具 ----------------------------------------------------------------


def compute_ir_hash(ir: DrawingIR) -> str:
    """内容哈希。同一份 IR 无论序列化顺序如何，都得到同一个值。

    ``sort_keys`` 保证字典顺序稳定，``separators`` 去掉空格，
    ``mode="json"`` 把 float 统一成 JSON 表达，避免 2 与 2.0 算出不同哈希。
    """
    canonical = json.dumps(
        ir.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_generation_id() -> str:
    """契约里的 ID 是不透明串，只约束字符集与长度，不含语义。"""
    return f"gen-{uuid4().hex}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---- 读快照 --------------------------------------------------------------


def can_read(user: User, row: GenerationModel) -> bool:
    """V 权限：本人或管理员。其他人的快照一律当作不存在。"""
    return row.owner_id == user.id or user.role == "admin"


def build_generation_schema(row: GenerationModel) -> GenerationSchema:
    """把库里的一行还原成契约里的 Generation 对象。"""
    return GenerationSchema(
        id=row.id,
        createdAt=_parse_dt(row.created_at),
        ownerId=row.owner_id,
        mode=row.mode,
        backend=row.backend,
        ir=DrawingIR.model_validate_json(row.ir_json),
        params=json.loads(row.params_json),
        seed=row.seed,
        templateVersionId=row.template_version_id,
        unpublishedSource=bool(row.unpublished_source),
        irHash=row.ir_hash,
        provenance=Provenance.model_validate_json(row.provenance_json),
        validationReport=ValidationReport.model_validate_json(
            row.validation_report_json
        ),
        warnings=[Issue.model_validate(item) for item in json.loads(row.warnings_json)],
        requestId=row.request_id,
    )


def get_generation(db: Session, user: User, generation_id: str) -> GenerationSchema:
    """按 id 取快照。无权访问和不存在返回同一个 404，不泄露存在性。"""
    row = db.get(GenerationModel, generation_id)
    if row is None or not can_read(user, row):
        raise errors.not_found("生成快照不存在")
    return build_generation_schema(row)


# ---- 模板路线 ------------------------------------------------------------


def _load_template_parts(version: TemplateVersion):
    """把模板版本里的四份 JSON 解析成模型。数据坏了就是模板的问题，报 500。"""
    try:
        ir_template = DrawingIR.model_validate_json(version.ir_template_json)
        schema = [_PARAM_ADAPTER.validate_python(item) for item in json.loads(version.parameter_schema_json)]
        bindings = [Binding.model_validate(item) for item in json.loads(version.bindings_json)]
        constraints = [_CONSTRAINT_ADAPTER.validate_python(item) for item in json.loads(version.constraints_json)]
    except Exception as exc:  # noqa: BLE001 —— 模板数据损坏的形态很多，统一兜住
        raise TemplateDefinitionError(
            f"模板版本 {version.id} 的数据无法解析：{exc}"
        ) from exc
    return ir_template, schema, bindings, constraints


def _can_preview_template(user: User, version: TemplateVersion) -> bool:
    """已发布版本所有人可读；未发布的草稿只有作者和管理员能预览。"""
    if version.status == "published":
        return True
    return version.created_by == user.id or user.role == "admin"


def _prepare_from_template(
    db: Session, user: User, payload: TemplateGeneration
) -> tuple[DrawingIR, dict, str, Provenance, bool, int]:
    version = db.get(TemplateVersion, payload.templateVersionId)
    # 无权预览与不存在返回同样的 404，避免用 ID 试探出草稿存在与否
    if version is None or not _can_preview_template(user, version):
        raise errors.not_found("模板版本不存在")

    supported = json.loads(version.supported_backends_json)
    if payload.backend not in supported:
        raise errors.unsupported_capability(
            f"该模板版本不支持后端 {payload.backend}，可用：{'、'.join(supported) or '无'}"
        )

    ir_template, schema, bindings, constraints = _load_template_parts(version)

    try:
        outcome = instantiate(
            ir_template, schema, bindings, constraints, payload.params
        )
    except TemplateDefinitionError:
        # 模板自身写坏了，是服务端数据问题，交给全局处理器记日志并返回 500
        raise

    if not outcome.ok:
        raise errors.validation_error(
            "参数不合法",
            [
                {"path": issue.path, "message": issue.message}
                for issue in outcome.issues
            ],
        )

    provenance = Provenance(
        kind="template",
        baseGenerationId=None,
        templateSelection=TemplateSelection(
            templateVersionId=version.id,
            reason="用户显式选择该模板版本",
        ),
        engineVersion=ENGINE_VERSION,
        ontologyVersion=version.ontology_version,
        policyRevision=POLICY_REVISION,
    )
    # 草稿模板生成出来的快照带「未发布」标记，会沿派生链继承
    unpublished = version.status != "published"
    return outcome.ir, dict(outcome.params), version.id, provenance, unpublished, payload.seed


# ---- 手工 IR 路线 --------------------------------------------------------


def _prepare_from_semantic(
    db: Session, user: User, payload: SemanticGeneration
) -> tuple[DrawingIR, dict, None, Provenance, bool, int]:
    """语义生成：文字 + 实体 → 规则命中 → 图形决策 → IR。

    没有任何规则命中时抛 422 ``NO_SEMANTIC_MATCH``，**不画兜底图**：
    用户输入"随便什么"却得到一朵莲，会以为规则生效了，其实是系统替
    它做了决定。契约把 NO_SEMANTIC_MATCH 列在 422 分支里，就是要求这样。
    """
    text = payload.input.text
    entity_ids = list(payload.input.entityIds or [])
    matched = rules_service.load_matched_rules(db, text=text, entity_ids=entity_ids)
    if not matched:
        raise errors.no_semantic_match(
            f"输入没有匹配到任何映射规则：{text[:40] or '(空文本)'}",
            details={"text": text, "entityIds": entity_ids},
        )

    synth = semantic_engine.synthesize(matched)
    ir = semantic_engine.build_ir(synth, seed=payload.seed, entity_ids=entity_ids)

    # 规则快照：记下"当时用的是第几版规则、内容是什么"。
    # 规则以后被改，旧作品的溯源仍然准确。
    rule_snapshots = []
    palette_rule_ids: list[str] = []
    composition_rule_ids: list[str] = []
    for index, item in enumerate(matched):
        row = db.get(RuleModel, item.rule_id)
        if row is None:
            continue
        rule_snapshots.append({
            "ruleId": row.id,
            "version": row.version,
            "content": rules_service._build(row).model_dump(mode="json"),  # noqa: SLF001
        })
        if index == 0:
            palette_rule_ids.append(row.id)
            composition_rule_ids.append(row.id)

    # 实体快照：作品详情要能显示"创作时引用了哪些知识"
    parents = knowledge_service._tag_tree(db)  # noqa: SLF001
    entity_snapshots = []
    for entity_id in entity_ids:
        row = db.get(KnowledgeModel, entity_id)
        if row is None:
            continue
        entity_snapshots.append({
            "entityId": row.id,
            "revision": row.revision,
            "content": knowledge_service._build_entity(row, parents).model_dump(mode="json"),  # noqa: SLF001
        })

    provenance = Provenance(
        kind="semantic",
        baseGenerationId=None,
        matchedEntities=entity_snapshots,
        matchedRules=rule_snapshots,
        selectedSymbols=list(synth.symbol_ids),
        paletteDecision={
            "value": ",".join(synth.palette),
            "ruleIds": palette_rule_ids,
            "reason": f"取最高优先级规则命中的调色板（{len(synth.palette)} 色）",
        },
        compositionDecision={
            "value": synth.composition,
            "ruleIds": composition_rule_ids,
            "reason": "取最高优先级规则的构图方式",
        },
        timelineDecision=None,
        templateSelection=None,
        conflicts=[],
        warnings=[],
        engineVersion=ENGINE_VERSION,
        ontologyVersion=ONTOLOGY_VERSION,
        policyRevision=POLICY_REVISION,
    )
    return ir, {}, None, provenance, False, payload.seed


def _prepare_from_ir(
    db: Session, user: User, payload: IRGeneration
) -> tuple[DrawingIR, dict, None, Provenance, bool, int]:
    base = db.get(GenerationModel, payload.baseGenerationId)
    if base is None or not can_read(user, base):
        raise errors.not_found("基底生成快照不存在")

    provenance = Provenance(
        kind="manual_edit",
        baseGenerationId=base.id,
        engineVersion=ENGINE_VERSION,
        ontologyVersion=ONTOLOGY_VERSION,
        policyRevision=POLICY_REVISION,
    )
    # 手工编辑继承基底的未发布标记：基于草稿改出来的东西仍然是草稿
    unpublished = bool(base.unpublished_source)
    base_params = json.loads(base.params_json) if base.params_json else {}
    return payload.ir, base_params, None, provenance, unpublished, payload.ir.seed


# ---- 入口 ----------------------------------------------------------------


def _split_issues(
    issues: list[Issue],
) -> tuple[list[Issue], list[Issue]]:
    blocking = [item for item in issues if item.code not in _WARNING_CODES]
    warnings = [item for item in issues if item.code in _WARNING_CODES]
    return blocking, warnings


def create_generation(
    db: Session, user: User, payload: GenerationCreate, request_id: str
) -> GenerationSchema:
    """创建一份不可变快照。任何一步不合法都不落库。"""
    if isinstance(payload, SemanticGeneration):
        ir, params, template_version_id, provenance, unpublished, seed = (
            _prepare_from_semantic(db, user, payload)
        )
    elif isinstance(payload, TemplateGeneration):
        ir, params, template_version_id, provenance, unpublished, seed = (
            _prepare_from_template(db, user, payload)
        )
    else:
        ir, params, template_version_id, provenance, unpublished, seed = (
            _prepare_from_ir(db, user, payload)
        )

    report = validate_ir(ir)
    blocking, warnings = _split_issues(report.issues)
    if blocking:
        raise errors.ir_invalid(
            f"IR 有 {len(blocking)} 处问题，无法生成快照",
            details=report.model_dump(mode="json"),
        )

    row = GenerationModel(
        id=_new_generation_id(),
        owner_id=user.id,
        mode=payload.mode,
        backend=payload.backend,
        template_version_id=template_version_id,
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
        seed=seed,
        ir_json=ir.model_dump_json(),
        ir_hash=compute_ir_hash(ir),
        provenance_json=provenance.model_dump_json(),
        validation_report_json=report.model_dump_json(),
        warnings_json=json.dumps(
            [item.model_dump(mode="json") for item in warnings], ensure_ascii=False
        ),
        unpublished_source=1 if unpublished else 0,
        request_id=request_id,
        created_at=_now_iso(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return build_generation_schema(row)


__all__ = [
    "ENGINE_VERSION",
    "build_generation_schema",
    "can_read",
    "compute_ir_hash",
    "create_generation",
    "get_generation",
]
