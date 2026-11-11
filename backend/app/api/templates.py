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

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.reviews import ArchiveRequest, RevisionRequest
from app.schemas.templates import (
    Backend,
    Scope,
    Symbol,
    Template,
    TemplateCreate,
    TemplatePage,
    TemplateUpdate,
    TemplateVersion,
    TemplateVersionPage,
    VersionCreate,
)
from app.services import reviews as reviews_service
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


# ---- P1A-03：容器与版本的写路径 -------------------------------------------

TemplateIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="模板 ID，不透明串",
    ),
]


@router.post(
    "/templates",
    response_model=Template,
    status_code=status.HTTP_201_CREATED,
    summary="新建模板容器",
    description="只建容器与工作元数据，版本另由 POST /templates/{id}/versions 创建。",
)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Template:
    return templates.create_template(db, user, payload)


@router.get(
    "/templates/{template_id}",
    response_model=Template,
    summary="模板详情",
    description="从未发布的模板对非作者按不存在处理，不暴露草稿的存在性。",
)
def read_template(
    template_id: TemplateIdPath,
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> Template:
    return templates.get_template(db, viewer, template_id)


@router.patch(
    "/templates/{template_id}",
    response_model=Template,
    summary="更新工作元数据",
    description="不修改已发布版本元数据；新版本创建时复制工作元数据。",
)
def update_template(
    payload: TemplateUpdate,
    template_id: TemplateIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Template:
    return templates.update_template(db, user, template_id, payload)


@router.get(
    "/templates/{template_id}/versions",
    response_model=TemplateVersionPage,
    summary="版本列表",
    description="作者与管理员看全部版本；其他读者只看已发布版本。",
)
def list_versions(
    template_id: TemplateIdPath,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> TemplateVersionPage:
    return templates.list_template_versions(
        db, viewer, template_id, page=page, page_size=page_size
    )


@router.post(
    "/templates/{template_id}/versions",
    response_model=TemplateVersion,
    status_code=status.HTTP_201_CREATED,
    summary="创建或克隆草稿版本",
    description=(
        "无 fromVersionId 时 irTemplate / parameterSchema / supportedBackends 必填；"
        "metadata 默认容器工作元数据。克隆时显式字段覆盖来源版本，其余继承。"
        "落库前会用引擎校验模板自洽性与参数默认值，不自洽直接 422。"
    ),
)
def create_version(
    payload: VersionCreate,
    template_id: TemplateIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> TemplateVersion:
    return templates.create_template_version(db, user, template_id, payload)


"""模板版本的路径 ID 注解。

与 ``TemplateIdPath`` 分开定义而不是共用：两者约束相同，但混用会让
以后改其中一个的约束时意外改到另一个路由。
"""
VersionIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="模板版本 ID，不透明串",
    ),
]


@router.post(
    "/template-versions/{version_id}/submit",
    response_model=TemplateVersion,
    summary="提交模板版本审核",
    description=(
        "权限 D-own/A。只允许 draft/rejected → submitted。"
        "送审会用引擎重验 IR/参数/绑定/约束，不自洽直接 422，"
        "不让坏定义进审核队列白费审核人时间。"
    ),
)
def submit_template_version(
    payload: RevisionRequest,
    version_id: VersionIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> TemplateVersion:
    return reviews_service.submit_template_version(
        db, user, version_id, payload.expectedRevision
    )


@router.post(
    "/template-versions/{version_id}/archive",
    response_model=TemplateVersion,
    summary="归档已发布模板版本",
    description=(
        "权限 D-own/A。只允许 published → archived。"
        "归档会清除容器的公开指针；若还有别的已发布版本，指针转过去，"
        "否则整块模板标为 archived。历史作品不受影响，仍可按原 generation 复现。"
    ),
)
def archive_template_version(
    payload: ArchiveRequest,
    version_id: VersionIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> TemplateVersion:
    return reviews_service.archive_template_version(
        db, user, version_id, payload.expectedRevision, payload.reason
    )
