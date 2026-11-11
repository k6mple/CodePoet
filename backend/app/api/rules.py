"""映射规则接口。

读取只要登录（L）：规则决定了所有人看到的画面，但不泄露任何私有数据。
写入要管理权限（A）：开放编辑等于开放"给别人画什么"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.rules import (
    MappingRule,
    MappingRulePage,
    RuleCreate,
    RuleUpdate,
)
from app.services import rules as rules_service

router = APIRouter(tags=["映射规则"])

RuleIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="规则 ID，不透明串",
    ),
]


@router.get("/rules", response_model=MappingRulePage, summary="映射规则列表")
def list_rules(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    enabled: bool | None = Query(default=None),
    q: str | None = Query(default=None, description="按名称与说明模糊匹配"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> MappingRulePage:
    return rules_service.list_rules(
        db, viewer, page=page, page_size=page_size, enabled=enabled, q=q
    )


@router.post(
    "/rules",
    response_model=MappingRule,
    status_code=status.HTTP_201_CREATED,
    summary="创建映射规则",
    description=(
        "权限 A。空条件（四组全空）的规则不会命中任何输入——"
        "一条刚建好还没填条件的规则不该静默覆盖所有其他规则。"
    ),
)
def create_rule(
    payload: RuleCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> MappingRule:
    return rules_service.create_rule(db, user, payload)


@router.patch(
    "/rules/{rule_id}",
    response_model=MappingRule,
    summary="更新映射规则并增加 version",
    description=(
        "权限 A。同时推进 revision（乐观锁）与 version（内容版本）。"
        "后者供推理快照记录「当时用的是第几版规则」。"
    ),
)
def update_rule(
    payload: RuleUpdate,
    rule_id: RuleIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> MappingRule:
    return rules_service.update_rule(db, user, rule_id, payload)
