"""创作相关接口：生成快照的创建与读取。

控制器只做三件事：解析请求、调用 services、拼响应。所有判断逻辑在
``app/services/generations.py``，这样业务规则能脱离 HTTP 单独测试。

权限记号来自接口文档 2.1：
* ``POST /generations`` —— L（登录），写操作还要过 CSRF；
* ``GET /generations/{id}`` —— V（本人 / 管理员），无权访问返回 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import AuthSession, User as UserModel
from app.schemas.generation import Generation as GenerationSchema
from app.schemas.generation import GenerationCreate
from app.services import generations

router = APIRouter(tags=["创作"])


@router.post(
    "/generations",
    response_model=GenerationSchema,
    status_code=status.HTTP_201_CREATED,
    summary="模板/语义/手工 IR 生成",
    description=(
        "创建一份不可变快照。template 模式按模板版本的声明式绑定实例化 IR；"
        "ir 模式接收前端提交的完整 IR。semantic 模式尚未实现，会返回 "
        "422 UNSUPPORTED_CAPABILITY。"
    ),
)
def create_generation(
    payload: GenerationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: AuthSession = Depends(require_csrf),
) -> GenerationSchema:
    # requestId 由中间件统一生成并挂在 request.state 上，
    # 直接读它，不要在这里另造一个 —— 否则响应体的 requestId
    # 和响应头 X-Request-Id 会对不上，排查时无法关联。
    request_id = getattr(request.state, "request_id", "req-unknown")
    return generations.create_generation(db, user, payload, request_id)


@router.get(
    "/generations/{generation_id}",
    response_model=GenerationSchema,
    summary="生成快照详情",
    description="本人或管理员可读。无权访问与不存在返回同一个 404，不暴露存在性。",
)
def read_generation(
    generation_id: str,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> GenerationSchema:
    return generations.get_generation(db, user, generation_id)
