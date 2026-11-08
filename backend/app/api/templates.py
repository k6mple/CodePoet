"""模板检索与模板版本读取接口。

只做两个只读接口，属 P0-03 的读路径（写路径 P1A-03/04 另行实现）：

* ``GET /templates`` —— 权限 G。匿名可看 ``scope=public``；``mine`` 要登录、
  ``all`` 只给管理员。
* ``GET /template-versions/{id}`` —— 权限 G，但只有已发布版本对所有人可见，
  草稿/待审/驳回版本仅作者与管理员（以及待审时的审核人）可读，其他人拿到
  的响应与「不存在」完全一致。

控制器不写业务判断：过滤、可见性、序列化都在
``app/services/templates.py``，这样规则能脱离 HTTP 单独测。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.deps import current_user, get_db
from app.models.users import User as UserModel
from app.schemas.templates import (
    Backend,
    Scope,
    Symbol,
    TemplatePage,
    TemplateVersion,
)
from app.services import templates

router = APIRouter(tags=["模板"])


@router.get(
    "/templates",
    response_model=TemplatePage,
    summary="模板检索与本人管理",
    description=(
        "scope=public 返回已发布模板（匿名可读）；scope=mine 返回本人全部模板，"
        "需要登录；scope=all 仅管理员可用。entityIds/tagIds/symbols 同维度内按"
        "「命中任一」过滤，不同维度之间取交集。"
    ),
)
def list_templates(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    scope: Scope = Query(default="public", description="public / mine / all"),
    q: str | None = Query(default=None, description="按名称与简介模糊匹配"),
    entity_ids: list[str] | None = Query(default=None, alias="entityIds"),
    tag_ids: list[str] | None = Query(default=None, alias="tagIds"),
    symbols: list[Symbol] | None = Query(default=None),
    backend: Backend | None = Query(default=None),
    time_theme: str | None = Query(default=None, alias="timeTheme"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> TemplatePage:
    # 匿名也能进来，所以用 current_user（返回 None）而不是 require_user；
    # mine/all 的登录要求在服务层按 scope 判断，未登录给 401。
    return templates.list_templates(
        db,
        viewer,
        scope=scope,
        page=page,
        page_size=page_size,
        q=q,
        entity_ids=entity_ids,
        tag_ids=tag_ids,
        symbols=symbols,
        backend=backend,
        time_theme=time_theme,
    )


@router.get(
    "/template-versions/{version_id}",
    response_model=TemplateVersion,
    summary="具体模板版本详情",
    description=(
        "已发布版本公开可读；未发布版本仅作者、管理员与待审时的审核人可读。"
        "无权访问与不存在返回同一个 404，不暴露草稿是否存在。"
    ),
)
def read_template_version(
    version_id: str = Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="模板版本 ID，不透明串",
    ),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> TemplateVersion:
    return templates.get_template_version(db, viewer, version_id)
