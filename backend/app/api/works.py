"""作品与作品版本接口。

控制器只做三件事：解析参数、调服务、声明响应模型。业务规则全在
``app/services/works.py``，这样它们能脱离 HTTP 单独测。

可见性设计：``GET /works`` 与 ``GET /works/{id}`` 允许匿名访问，
所以依赖用 ``current_user``（未登录返回 None）；``scope=mine`` 的登录
要求在服务层判断。写操作统一走 ``require_user`` + ``require_csrf``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.works import (
    Scope,
    Work,
    WorkCreate,
    WorkCreated,
    WorkPage,
    WorkVersionCreate,
    WorkVersionDetail,
    WorkVersionPage,
)
from app.services import works as works_service

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
