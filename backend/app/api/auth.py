"""账号相关接口：注册、登录、注销、CSRF 获取、当前用户、改资料。

控制器只做三件事：解析请求、调用 services、拼响应。所有判断逻辑在
``app/services/accounts.py`` 里，方便单独测试。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app import errors
from app.deps import (
    ANONYMOUS_TTL,
    AUTHENTICATED_TTL,
    SESSION_COOKIE,
    current_session,
    get_db,
    require_csrf,
    require_user,
)
from app.models.users import AuthSession, User as UserModel
from app.schemas.auth import AuthSession as AuthSessionSchema
from app.schemas.auth import (
    CsrfResponse,
    LoginRequest,
    MeUpdate,
    RegisterRequest,
    User as UserSchema,
)
from app.services import accounts

router = APIRouter(tags=["账号"])


def _cookie_secure(request: Request) -> bool:
    """生产环境走 HTTPS 时必须置 True，本地 http 开发默认 False。"""
    settings = getattr(request.app.state, "settings", None)
    return bool(getattr(settings, "cookie_secure", False))


def _set_session_cookie(
    response: Response, request: Request, token: str, ttl: timedelta
) -> None:
    # 契约 2.1：HttpOnly、SameSite=Lax、Path=/，生产 Secure
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
        secure=_cookie_secure(request),
    )


@router.get("/auth/csrf", response_model=CsrfResponse, summary="获取 CSRF token")
def get_csrf(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthSession | None = Depends(current_session),
) -> CsrfResponse:
    """拿 CSRF token 的入口。已有有效会话就复用，不重复建。

    这里刻意不做 CSRF 校验——它就是发 token 的地方，校验会形成死循环。
    """
    if auth is not None:
        return CsrfResponse(csrfToken=auth.csrf_token, expiresAt=auth.expires_at)

    raw_token, row = accounts.issue_session(db, None)
    _set_session_cookie(response, request, raw_token, ANONYMOUS_TTL)
    return CsrfResponse(csrfToken=row.csrf_token, expiresAt=row.expires_at)


@router.post(
    "/auth/register",
    response_model=UserSchema,
    status_code=status.HTTP_201_CREATED,
    summary="注册普通创作者并申请角色",
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    _csrf: AuthSession = Depends(require_csrf),
) -> UserSchema:
    """注册一律成为 creator；requestedRole 只记录为「申请」，不直接生效。"""
    user = accounts.register_user(db, payload)
    return accounts.build_user_schema(user)


@router.post("/auth/login", response_model=AuthSessionSchema, summary="登录并建立会话")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthSession = Depends(require_csrf),
) -> AuthSessionSchema:
    user = accounts.authenticate(db, payload.username, payload.password)

    # 轮换会话：登录前的匿名会话立刻作废。
    # 这能防「会话固定攻击」——攻击者预先埋一个 session id 诱导你用它登录，
    # 登录后该 id 就变成已认证会话，攻击者也就登进来了。
    accounts.revoke_session(db, auth)

    raw_token, row = accounts.issue_session(db, user)
    _set_session_cookie(response, request, raw_token, AUTHENTICATED_TTL)
    return AuthSessionSchema(
        user=accounts.build_user_schema(user),
        csrfToken=row.csrf_token,
        expiresAt=row.expires_at,
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="注销当前会话",
)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthSession = Depends(require_csrf),
) -> Response:
    # 先删库记录再清 Cookie：只清 Cookie 的话 token 在服务端仍然有效，
    # 谁捡到那个 token 还能继续用。
    accounts.revoke_session(db, auth)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthSessionSchema, summary="当前用户和 CSRF")
def read_me(
    db: Session = Depends(get_db),
    auth: AuthSession | None = Depends(current_session),
) -> AuthSessionSchema:
    """前端刷新页面后用它恢复登录态，所以不该把会话存进 localStorage。"""
    if auth is None or auth.user_id is None:
        raise errors.auth_required()
    user = db.get(UserModel, auth.user_id)
    if user is None or user.status != "active":
        raise errors.auth_required()

    accounts.touch(db, auth)
    return AuthSessionSchema(
        user=accounts.build_user_schema(user),
        csrfToken=auth.csrf_token,
        expiresAt=auth.expires_at,
    )


@router.patch("/me", response_model=AuthSessionSchema, summary="更新个人信息")
def update_me(
    payload: MeUpdate,
    db: Session = Depends(get_db),
    auth: AuthSession = Depends(require_csrf),
    user: UserModel = Depends(require_user),
) -> AuthSessionSchema:
    updated = accounts.update_profile(
        db, user, payload.nickname, payload.bio, payload.expectedRevision
    )
    return AuthSessionSchema(
        user=accounts.build_user_schema(updated),
        csrfToken=auth.csrf_token,
        expiresAt=auth.expires_at,
    )
