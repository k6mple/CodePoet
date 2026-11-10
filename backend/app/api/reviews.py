"""审核队列与裁决接口。

只有两个路由，业务规则全在 ``app/services/reviews.py``。这里只解析
参数、调服务、声明响应模型。

权限是 E/A（专家或管理员），所以在服务层用 ``has_permission`` 判，
而不是依赖角色字符串比较——权限点改名时只需要动 ``permissions.py``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.reviews import (
    Review,
    ReviewCreate,
    ReviewQueueItemPage,
    ReviewStatusFilter,
    ReviewTargetType,
)
from app.services import reviews as reviews_service

router = APIRouter(tags=["审核"])


@router.get(
    "/reviews",
    response_model=ReviewQueueItemPage,
    summary="审核队列与处理记录",
)
def list_reviews(
    targetType: ReviewTargetType | None = Query(default=None),
    review_status: ReviewStatusFilter = Query(default="all", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    viewer: UserModel = Depends(require_user),
) -> ReviewQueueItemPage:
    return reviews_service.list_reviews(
        db, viewer, targetType, review_status, page, page_size
    )


@router.post(
    "/reviews",
    response_model=Review,
    status_code=status.HTTP_201_CREATED,
    summary="审核确定版本",
)
def create_review(
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    reviewer: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Review:
    return reviews_service.decide_review(db, reviewer, payload)
