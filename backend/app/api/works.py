"""作品与作品版本接口。

控制器只做三件事：解析参数、调服务、声明响应模型。业务规则全在
``app/services/works.py``，这样它们能脱离 HTTP 单独测。

可见性设计：``GET /works`` 与 ``GET /works/{id}`` 允许匿名访问，
所以依赖用 ``current_user``（未登录返回 None）；``scope=mine`` 的登录
要求在服务层判断。写操作统一走 ``require_user`` + ``require_csrf``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.reproduce import ReproduceRequest
from app.schemas.reviews import ArchiveRequest, RevisionRequest
from app.schemas.sessions import FavoriteResult
from app.schemas.works import (
    RenderJob,
    Scope,
    Work,
    WorkCreate,
    WorkCreated,
    WorkPage,
    WorkVersion,
    WorkVersionCreate,
    WorkUpdate,
    WorkVersionDetail,
    WorkVersionPage,
)
from app.services import favorites as favorites_service
from app.services import reproduce as reproduce_service
from app.services import reviews as reviews_service
from app.services import works as works_service
from app.settings import Settings

router = APIRouter(tags=["作品"])

"""路径 ID 的可复用注解。

不能用模块级变量 ``ID_PATH = Path(...)`` 再给多个参数当默认值——
FastAPI 会把第一个注册时的参数名记在这个对象上，第二个路由就会
要求第一个路由的参数名，表现为「访问 /work-versions/x 报 work_id 缺失」。
``Annotated`` 每次生成独立副本，可以安全复用。
"""
IdPath = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


@router.post(
    "/works",
    response_model=WorkCreated,
    status_code=status.HTTP_201_CREATED,
    summary="创建私有作品及首个版本",
)
def create_work(
    payload: WorkCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> WorkCreated:
    return works_service.create_work(db, user, payload)


@router.get("/works", response_model=WorkPage, summary="公开/本人/收藏作品列表")
def list_works(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    scope: Scope = Query(default="public"),
    q: str | None = Query(default=None, max_length=80),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> WorkPage:
    return works_service.list_works(db, viewer, scope, q, page, page_size)


@router.get("/works/{work_id}", response_model=Work, summary="作品元数据")
def read_work(
    work_id: IdPath,
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> Work:
    return works_service.get_work(db, viewer, work_id)


@router.patch(
    "/works/{work_id}",
    response_model=Work,
    summary="更新作品工作元数据",
    description=(
        "仅作品本人。带 expectedRevision 的乐观锁，冲突返回 409。"
        "改的是工作元数据（workingTitle/workingDescription），"
        "不修改任何既有版本的标题与描述——版本的标题是当初审核的快照。"
    ),
)
def update_work(
    payload: WorkUpdate,
    work_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Work:
    return works_service.update_work(db, user, work_id, payload)


@router.post(
    "/works/{work_id}/unpublish",
    response_model=Work,
    summary="下线公开作品",
    description=(
        "仅管理员/专家（works.unpublishAny）。清除 publishedVersionId，"
        "同时关闭该作品所有旧公开版本与产物的公共访问。"
        "已发布版本的状态不变——那是当初的审核结论；重新公开需要新版本过审。"
    ),
)
def unpublish_work(
    payload: ArchiveRequest,
    work_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Work:
    return works_service.unpublish_work(db, user, work_id, payload)


@router.get(
    "/works/{work_id}/versions",
    response_model=WorkVersionPage,
    summary="可见作品版本列表",
)
def list_versions(
    work_id: IdPath,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> WorkVersionPage:
    return works_service.list_versions(db, viewer, work_id, page, page_size)


@router.post(
    "/works/{work_id}/versions",
    response_model=WorkCreated,
    status_code=status.HTTP_201_CREATED,
    summary="创建新的 private 作品版本",
)
def create_version(
    payload: WorkVersionCreate,
    work_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> WorkCreated:
    return works_service.create_version(db, user, work_id, payload)


@router.get(
    "/work-versions/{version_id}",
    response_model=WorkVersionDetail,
    summary="版本详情含生成与渲染快照",
)
def read_version(
    version_id: IdPath,
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> WorkVersionDetail:
    return works_service.get_version_detail(db, viewer, version_id)


@router.post(
    "/work-versions/{version_id}/submit",
    response_model=WorkVersion,
    summary="提交作品版本审核",
    description=(
        "权限 O（本人）。只允许 private/rejected → pending_review。"
        "派生自未发布模板的版本（generation.unpublishedSource=true）会被 409 "
        "UNPUBLISHED_SOURCE 拒绝——审核人看到的会是一张来源尚不存在的作品。"
        "渲染任务未完成同样拒绝。"
    ),
)
def submit_work_version(
    payload: RevisionRequest,
    version_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> WorkVersion:
    return reviews_service.submit_work_version(
        db, user, version_id, payload.expectedRevision
    )


@router.put(
    "/works/{work_id}/favorite",
    response_model=FavoriteResult,
    summary="幂等收藏可见作品",
    description=(
        "权限 L。重复收藏返回 200 与 isFavorite=true，不报错——前端重试是常态。"
        "看不见的作品一律 404，不确认存在性。"
    ),
)
def favorite_work(
    work_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> FavoriteResult:
    return favorites_service.add_favorite(db, user, work_id)


@router.delete(
    "/works/{work_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="幂等取消本人收藏",
    description=(
        "权限 L。没有收藏也返回 204；作品已下线仍可删除本人收藏关系"
        "——那条记录属于用户，不该被作品状态绑架。"
    ),
)
def unfavorite_work(
    work_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> None:
    favorites_service.remove_favorite(db, user, work_id)


"""渲染任务的 settings 从 app.state 取，与 render.py 保持一致。"""
def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/work-versions/{version_id}/reproduce",
    response_model=RenderJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="按历史快照创建复现任务",
    description=(
        "权限 V，必须登录且能看该版本。IR、backend、采样时刻全部从历史读取，"
        "不接受客户端传——传了就等于允许「复现成别的东西」。"
        "返回 browser_upload 任务等待浏览器绘制上传，不重新推理。"
    ),
)
def reproduce_work_version(
    payload: ReproduceRequest,
    version_id: IdPath,
    request: Request,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> RenderJob:
    return reproduce_service.reproduce_version(
        db, _settings(request), user, version_id, payload
    )
