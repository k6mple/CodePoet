"""映射规则的增删改查，以及给语义生成用的规则装载。

两条约定：
* 读取只要登录（L），写入要管理权限（A）。规则会直接影响所有人看到的画面，
  开放编辑等于开放"给别人画什么"。
* ``update`` 同时推进两个计数器：``revision``（乐观锁）和 ``version``（内容版本）。
  合成一列的话，就没法在推理快照里记"这次用的第几版规则"而不暴露并发细节。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.engine.semantic import MatchedRule, RuleFacts, match_rules
from app.models.knowledge import KnowledgeEntity as KnowledgeModel
from app.models.rules import MappingRule as RuleModel
from app.models.users import User
from app.schemas.rules import (
    MappingRule as MappingRuleSchema,
    MappingRulePage,
    RuleCreate,
    RuleEffects,
    RuleMatch,
    RuleUpdate,
)
from app.services import audit, permissions

__all__ = [
    "create_rule",
    "list_rules",
    "load_matched_rules",
    "update_rule",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _require_rules_read(viewer: User | None) -> User:
    if viewer is None:
        raise errors.auth_required()
    if not permissions.has_permission(viewer, permissions.RULES_READ):
        raise errors.forbidden("没有读取映射规则的权限")
    return viewer


def _require_rules_write(actor: User) -> None:
    """写规则按契约限制到 **admin**（``access=A``）。

    这里刻意不用 ``rules.write`` 权限点：矩阵里 developer 也有它
    （"开发者负责规则内容"），但契约把 POST/PATCH /rules 标成 A。
    规则会改变所有人看到的画面，两边冲突时以契约为准。
    """
    if actor.role != "admin":
        raise errors.forbidden("只有管理员可以维护映射规则")


def _effects_of(row: RuleModel) -> RuleEffects:
    return RuleEffects.model_validate_json(row.effects_json)


def _build(row: RuleModel) -> MappingRuleSchema:
    return MappingRuleSchema(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        updatedAt=_parse_dt(row.updated_at) or datetime.now(timezone.utc),
        name=row.name,
        enabled=bool(row.enabled),
        priority=row.priority,
        match=RuleMatch.model_validate_json(row.match_json),
        effects=_effects_of(row),
        explanation=row.explanation,
        version=row.version,
    )


def list_rules(
    db: Session,
    viewer: User | None,
    *,
    page: int = 1,
    page_size: int = 20,
    enabled: bool | None = None,
    q: str | None = None,
) -> MappingRulePage:
    _require_rules_read(viewer)
    conditions = []
    if enabled is not None:
        conditions.append(RuleModel.enabled == (1 if enabled else 0))

    rows = db.scalars(
        select(RuleModel)
        .where(*conditions)
        .order_by(
            RuleModel.priority.desc(), RuleModel.created_at.asc(), RuleModel.id.asc()
        )
    ).all()

    if q:
        needle = q.strip().lower()
        if needle:
            rows = [
                row
                for row in rows
                if needle in f"{row.name}\n{row.explanation}".lower()
            ]

    total = len(rows)
    start = (page - 1) * page_size
    return MappingRulePage(
        items=[_build(row) for row in rows[start : start + page_size]],
        total=total,
        page=page,
        pageSize=page_size,
    )


def create_rule(db: Session, actor: User, payload: RuleCreate) -> MappingRuleSchema:
    _require_rules_write(actor)
    now = _now_iso()
    row = RuleModel(
        id=f"rule-{uuid4().hex}",
        name=payload.name,
        enabled=1 if payload.enabled else 0,
        priority=payload.priority,
        match_json=payload.match.model_dump_json(),
        effects_json=payload.effects.model_dump_json(),
        explanation=payload.explanation,
        version=1,
        revision=1,
        owner_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    audit.record(
        db,
        actor=actor,
        action="rule.create",
        target_type="mapping_rule",
        target_id=row.id,
        input_summary={
            "name": payload.name,
            "priority": payload.priority,
            "enabled": payload.enabled,
        },
        after={"version": 1},
        result="success",
    )
    db.commit()
    db.refresh(row)
    return _build(row)


def update_rule(
    db: Session, actor: User, rule_id: str, payload: RuleUpdate
) -> MappingRuleSchema:
    _require_rules_write(actor)
    row = db.get(RuleModel, rule_id)
    if row is None:
        raise errors.not_found("映射规则不存在")
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    before = {"revision": row.revision, "version": row.version, "enabled": bool(row.enabled)}
    if payload.name is not None:
        row.name = payload.name
    if payload.enabled is not None:
        row.enabled = 1 if payload.enabled else 0
    if payload.priority is not None:
        row.priority = payload.priority
    if payload.match is not None:
        row.match_json = payload.match.model_dump_json()
    if payload.effects is not None:
        row.effects_json = payload.effects.model_dump_json()
    if payload.explanation is not None:
        row.explanation = payload.explanation

    row.revision += 1
    # 内容版本跟 revision 一起推进，但语义不同：前者给并发控制，
    # 后者供推理快照记录"当时用的是第几版规则"
    row.version += 1
    row.updated_at = _now_iso()
    audit.record(
        db,
        actor=actor,
        action="rule.update",
        target_type="mapping_rule",
        target_id=row.id,
        input_summary={"fields": sorted(payload.model_dump(exclude_none=True))},
        before=before,
        after={"revision": row.revision, "version": row.version},
        result="success",
    )
    db.commit()
    db.refresh(row)
    return _build(row)


# ---------------------------------------------------------------------------
# 语义生成用
# ---------------------------------------------------------------------------


def entity_facts(db: Session, entity_ids: list[str]) -> RuleFacts:
    """把输入实体解析成匹配需要的事实集合（标签与概念）。"""
    if not entity_ids:
        return RuleFacts()
    rows = db.scalars(
        select(KnowledgeModel).where(KnowledgeModel.id.in_(entity_ids))
    ).all()
    tags: set[str] = set()
    concepts: set[str] = set()
    for row in rows:
        try:
            tags.update(json.loads(row.tag_ids_json or "[]"))
            concepts.update(json.loads(row.concept_ids_json or "[]"))
        except ValueError:
            # 单条脏数据不该让整个语义生成失败
            continue
    return RuleFacts(
        entity_ids=frozenset(entity_ids),
        tag_ids=frozenset(tags),
        concept_ids=frozenset(concepts),
    )


def load_matched_rules(
    db: Session, *, text: str, entity_ids: list[str]
) -> list[MatchedRule]:
    """装载启用的规则并做匹配。没有命中时返回空列表，由调用方决定怎么兜底。"""
    rows = db.scalars(
        select(RuleModel).where(RuleModel.enabled == 1)
    ).all()
    if not rows:
        return []
    facts = entity_facts(db, entity_ids)
    facts = RuleFacts(
        text=text,
        entity_ids=facts.entity_ids,
        tag_ids=facts.tag_ids,
        concept_ids=facts.concept_ids,
    )
    return match_rules(list(rows), facts, _effects_of)
