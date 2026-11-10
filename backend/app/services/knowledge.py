"""知识条目（人物 / 典故 / 概念 / 符号）的业务规则。

三件事：写入前的引用校验、标签继承的推导、组合检索。

## 为什么标签继承不落库

契约里 ``effectiveTagIds`` 与 ``inheritedTagIds`` 是只读派生字段。如果把它们
存进表，每次调整标签树的父子关系都得回头批量重写所有知识条目，而且历史作品
快照里引用的是「当时的」标签集合，重写会把历史改掉。所以一律现算：
``effectiveTagIds = 直接标签 ∪ 这些标签的祖先链``。

## 检索为什么不全下推到 SQL

``tagIds`` 过滤的语义是「有效标签命中」，而有效标签要先走一遍标签树才知道，
SQL 里表达不了。所以先用 SQL 做能做的维度（type/timeTheme/entityIds/q/status），
再把候选集拉到内存里算标签，最后分页。当前知识条目是十级规模，这样做比为了
一条查询去建标签闭包表划算；过千条时再把 tag 关系抽成关联表下推。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.models.knowledge import Concept as ConceptModel
from app.models.knowledge import KnowledgeEntity as KnowledgeModel
from app.models.knowledge import Tag as TagModel
from app.models.users import User
from app.schemas.knowledge import (
    KnowledgeCreate,
    KnowledgeEntity,
    KnowledgeEntityPage,
    KnowledgeUpdate,
    Relation,
    SourceReference,
)
from app.schemas.taxonomy import ManageScope
from app.services import audit
from app.services import permissions
from app.services import taxonomy

MAX_LINKS = 100


# ---- 基础工具 -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(value or "")
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _load_json(raw: str | None, default):
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _dedupe(values: list[str], *, limit: int = MAX_LINKS) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _sources_of(row: KnowledgeModel) -> list[SourceReference]:
    """来源引用逐条过一遍模型，脏数据丢掉，不让一条坏记录打崩整个列表。"""
    out: list[SourceReference] = []
    for item in _load_json(row.source_references_json, []):
        try:
            out.append(SourceReference.model_validate(item))
        except Exception:  # noqa: BLE001 - 结构不合法的历史数据直接跳过
            continue
    return out[:20]


def _relations_of(row: KnowledgeModel) -> list[Relation]:
    out: list[Relation] = []
    for item in _load_json(row.relations_json, []):
        try:
            out.append(Relation.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return out[:100]


def _aliases_of(row: KnowledgeModel) -> list[str]:
    return _dedupe(_load_json(row.alias_json, []), limit=20)


# ---- 标签继承 -------------------------------------------------------------


def _split_tags(
    direct: list[str], parents: dict[str, str | None]
) -> tuple[list[str], list[str]]:
    """返回 ``(effectiveTagIds, inheritedTagIds)``。

    继承部分只保留祖先，不回填直接标签；有效部分是两者的并集。
    """
    inherited: list[str] = []
    for tag_id in direct:
        inherited.extend(taxonomy.ancestors(parents, tag_id))
    inherited_only = _dedupe(inherited)
    effective = _dedupe(direct + inherited_only)
    return effective, inherited_only


def _tag_tree(db: Session) -> dict[str, str | None]:
    return taxonomy.parent_map(db, TagModel)


# ---- 引用校验 -------------------------------------------------------------


def _check_concepts(db: Session, concept_ids: list[str]) -> None:
    for concept_id in concept_ids:
        row = db.get(ConceptModel, concept_id)
        if row is None or row.status != "active":
            raise errors.validation_error(
                "引用的本体不存在或已停用",
                [{"path": "conceptIds", "message": f"{concept_id} 不可用"}],
            )


def _check_tags(db: Session, tag_ids: list[str]) -> None:
    for tag_id in tag_ids:
        row = db.get(TagModel, tag_id)
        if row is None or row.status != "active":
            raise errors.validation_error(
                "引用的标签不存在或已停用",
                [{"path": "tagIds", "message": f"{tag_id} 不可用"}],
            )


def _check_relations(db: Session, relations: list[Relation], self_id: str | None) -> None:
    """关系目标必须真实存在，且不能指向自己（自环没有语义价值）。"""
    for relation in relations:
        if self_id is not None and relation.targetEntityId == self_id:
            raise errors.validation_error(
                "关系不能指向实体自身",
                [{"path": "relations", "message": "targetEntityId 不能是当前条目"}],
            )
        if db.get(KnowledgeModel, relation.targetEntityId) is None:
            raise errors.validation_error(
                "关系目标实体不存在",
                [
                    {
                        "path": "relations",
                        "message": f"{relation.targetEntityId} 不存在",
                    }
                ],
            )


# ---- 序列化 ---------------------------------------------------------------


def _build_entity(
    row: KnowledgeModel, parents: dict[str, str | None]
) -> KnowledgeEntity:
    direct = _dedupe(_load_json(row.tag_ids_json, []))
    effective, inherited = _split_tags(direct, parents)
    return KnowledgeEntity(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_iso(row.created_at),
        updatedAt=_parse_iso(row.updated_at),
        type=row.type,  # type: ignore[arg-type]
        name=row.name,
        aliases=_aliases_of(row),
        summary=row.summary,
        content=row.content,
        sourceReferences=_sources_of(row),
        conceptIds=_dedupe(_load_json(row.concept_ids_json, [])),
        tagIds=direct,
        relations=_relations_of(row),
        timeTheme=row.time_theme,
        status=row.status,  # type: ignore[arg-type]
        effectiveTagIds=effective,
        inheritedTagIds=inherited,
    )


# ---- 用例 -----------------------------------------------------------------


def get_entity(db: Session, viewer: User | None, entity_id: str) -> KnowledgeEntity:
    """停用的条目对普通读者按不存在处理，维护者仍能读（要能把它救回来）。"""
    row = db.get(KnowledgeModel, entity_id)
    if row is None:
        raise errors.not_found("知识条目不存在")
    if row.status != "active" and not permissions.has_permission(
        viewer, permissions.KNOWLEDGE_WRITE
    ):
        raise errors.not_found("知识条目不存在")
    return _build_entity(row, _tag_tree(db))


def list_entities(
    db: Session,
    viewer: User | None,
    *,
    scope: ManageScope = "public",
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    type_: str | None = None,
    tag_ids: list[str] | None = None,
    entity_ids: list[str] | None = None,
    time_theme: str | None = None,
) -> KnowledgeEntityPage:
    if scope == "manage":
        if viewer is None:
            raise errors.auth_required()
        if not permissions.has_permission(viewer, permissions.KNOWLEDGE_WRITE):
            raise errors.forbidden("需要「知识维护」权限")
        statement = select(KnowledgeModel)
    else:
        statement = select(KnowledgeModel).where(KnowledgeModel.status == "active")

    if type_:
        statement = statement.where(KnowledgeModel.type == type_)
    if time_theme:
        statement = statement.where(KnowledgeModel.time_theme == time_theme)
    if entity_ids:
        statement = statement.where(KnowledgeModel.id.in_(entity_ids))
    if q:
        # 名称、别名、摘要、正文四处一起匹配，别名在 JSON 里所以用 like 兜住
        like = f"%{q}%"
        statement = statement.where(
            or_(
                KnowledgeModel.name.like(like),
                KnowledgeModel.summary.like(like),
                KnowledgeModel.content.like(like),
                KnowledgeModel.alias_json.like(like),
            )
        )

    rows = list(
        db.scalars(
            statement.order_by(
                KnowledgeModel.created_at.desc(), KnowledgeModel.id.desc()
            )
        )
    )
    parents = _tag_tree(db)
    views = [_build_entity(row, parents) for row in rows]

    if tag_ids:
        wanted = set(tag_ids)
        # 同维度 OR：命中任一标签即算（用有效标签，继承来的也算）
        views = [item for item in views if wanted.intersection(item.effectiveTagIds)]

    total = len(views)
    start = (page - 1) * page_size
    return KnowledgeEntityPage(
        items=views[start : start + page_size],
        total=total,
        page=page,
        pageSize=page_size,
    )


def create_entity(db: Session, actor: User, payload: KnowledgeCreate) -> KnowledgeEntity:
    if not permissions.has_permission(actor, permissions.KNOWLEDGE_WRITE):
        raise errors.forbidden("需要「知识维护」权限")

    _check_concepts(db, payload.conceptIds)
    _check_tags(db, payload.tagIds)
    _check_relations(db, payload.relations, None)
    # 互斥要按「有效标签」判：继承来的一级标签与直接打的二级标签也可能冲突
    parents = _tag_tree(db)
    effective, _ = _split_tags(_dedupe(payload.tagIds), parents)
    taxonomy.check_tag_selection(db, effective)

    now = _iso(_now())
    row = KnowledgeModel(
        id=f"kn-{uuid4().hex}",
        type=payload.type,
        name=payload.name,
        alias_json=json.dumps(_dedupe(payload.aliases, limit=20), ensure_ascii=False),
        summary=payload.summary,
        content=payload.content,
        source_references_json=json.dumps(
            [item.model_dump() for item in payload.sourceReferences],
            ensure_ascii=False,
        ),
        concept_ids_json=json.dumps(
            sorted(set(payload.conceptIds)), ensure_ascii=False
        ),
        tag_ids_json=json.dumps(sorted(set(payload.tagIds)), ensure_ascii=False),
        relations_json=json.dumps(
            [item.model_dump() for item in payload.relations], ensure_ascii=False
        ),
        time_theme=payload.timeTheme,
        status=payload.status,
        owner_id=actor.id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    audit.record(
        db,
        actor=actor,
        action="knowledge.create",
        target_type="knowledge_entity",
        target_id=row.id,
        input_summary={"type": payload.type, "name": payload.name},
        after={"status": payload.status, "revision": 1},
        result="success",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise errors.validation_error("知识条目写入失败，请重试")
    return _build_entity(row, parents)


def update_entity(
    db: Session, actor: User, entity_id: str, payload: KnowledgeUpdate
) -> KnowledgeEntity:
    if not permissions.has_permission(actor, permissions.KNOWLEDGE_WRITE):
        raise errors.forbidden("需要「知识维护」权限")

    row = db.get(KnowledgeModel, entity_id)
    if row is None:
        raise errors.not_found("知识条目不存在")
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    changed = payload.model_fields_set
    if "conceptIds" in changed and payload.conceptIds is not None:
        _check_concepts(db, payload.conceptIds)
        row.concept_ids_json = json.dumps(
            sorted(set(payload.conceptIds)), ensure_ascii=False
        )
    if "tagIds" in changed and payload.tagIds is not None:
        _check_tags(db, payload.tagIds)
        row.tag_ids_json = json.dumps(sorted(set(payload.tagIds)), ensure_ascii=False)
        # 互斥只在「本次真的改了标签」时判。对历史上本就互斥的脏数据，
        # 改个摘要不该被拦下来——把老数据修干净才是目的，不是罚它。
        parents = _tag_tree(db)
        effective, _ = _split_tags(_dedupe(payload.tagIds), parents)
        taxonomy.check_tag_selection(db, effective)
    if "relations" in changed and payload.relations is not None:
        _check_relations(db, payload.relations, entity_id)
        row.relations_json = json.dumps(
            [item.model_dump() for item in payload.relations], ensure_ascii=False
        )

    if "type" in changed and payload.type is not None:
        row.type = payload.type
    if "name" in changed and payload.name is not None:
        row.name = payload.name
    if "aliases" in changed and payload.aliases is not None:
        row.alias_json = json.dumps(
            _dedupe(payload.aliases, limit=20), ensure_ascii=False
        )
    if "summary" in changed and payload.summary is not None:
        row.summary = payload.summary
    if "content" in changed and payload.content is not None:
        row.content = payload.content
    if "sourceReferences" in changed and payload.sourceReferences is not None:
        row.source_references_json = json.dumps(
            [item.model_dump() for item in payload.sourceReferences],
            ensure_ascii=False,
        )
    if "timeTheme" in changed:
        row.time_theme = payload.timeTheme
    if "status" in changed and payload.status is not None:
        row.status = payload.status

    previous_revision = row.revision
    row.revision += 1
    row.updated_at = _iso(_now())
    audit.record(
        db,
        actor=actor,
        action="knowledge.update",
        target_type="knowledge_entity",
        target_id=row.id,
        # 只记改了哪些字段名，不把整份正文塞进审计
        input_summary={"changed": sorted(changed)},
        before={"revision": previous_revision},
        after={"revision": row.revision},
        result="success",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise errors.validation_error("知识条目更新失败，请重试")
    # 重新取一次标签树：上面可能刚改过父链（别的请求），不能复用局部变量
    return _build_entity(row, _tag_tree(db))


def deactivate_entity(
    db: Session, actor: User, entity_id: str, expected_revision: int
) -> None:
    """软停用。不物理删除——作品快照里存着 entityId，删了来源追溯就断。"""
    if not permissions.has_permission(actor, permissions.KNOWLEDGE_WRITE):
        raise errors.forbidden("需要「知识维护」权限")

    row = db.get(KnowledgeModel, entity_id)
    if row is None:
        raise errors.not_found("知识条目不存在")
    # 幂等优先于乐观锁：已经停用就是目标状态，版本号过时也不报 409
    if row.status == "inactive":
        return
    if row.revision != expected_revision:
        raise errors.revision_conflict(expected_revision, row.revision)

    row.status = "inactive"
    row.revision += 1
    row.updated_at = _iso(_now())
    audit.record(
        db,
        actor=actor,
        action="knowledge.delete",
        target_type="knowledge_entity",
        target_id=row.id,
        before={"status": "active", "revision": expected_revision},
        after={"status": "inactive", "revision": row.revision},
        result="success",
    )
    db.commit()
