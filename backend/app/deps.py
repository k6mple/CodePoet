"""FastAPI 依赖：数据库会话、当前用户、CSRF 校验。

把「当前用户是谁」「这个写操作有没有带合法 CSRF」做成依赖，
路由函数只需要声明参数，不用每个接口重复一遍取 Cookie、查库、比时间。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.models.users import AuthSession, User
from app.services.security import digest_equal, hash_session_token

# 契约 2.1：Cookie 名与 CSRF 头名是写死的，前端照此实现
SESSION_COOKIE = "codepoet_session"
CSRF_HEADER = "X-CSRF-Token"

# 契约 2.1：匿名 30 分钟、登录 8 小时
ANONYMOUS_TTL = timedelta(minutes=30)
AUTHENTICATED_TTL = timedelta(hours=8)

# 只读方法不需要 CSRF
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    """解析失败一律当作已过期，宁可要求重新登录也不放行。"""
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def get_db(request: Request) -> Iterator[Session]:
    """每个请求一个 Session，请求结束即关闭，避免连接泄漏。"""
    factory = request.app.state.sessions
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def find_session(db: Session, raw_token: str | None) -> AuthSession | None:
    """按 Cookie 里的明文 token 找会话记录（库里存的是哈希）。"""
    if not raw_token:
        return None
    return db.scalars(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_token)
        )
    ).first()


def current_session(
    request: Request, db: Session = Depends(get_db)
) -> AuthSession | None:
    """解析当前会话；过期则顺手删掉，返回 None。"""
    row = find_session(db, request.cookies.get(SESSION_COOKIE))
    if row is None:
        return None
    expires_at = parse_iso(row.expires_at)
    if expires_at is None or expires_at <= utcnow():
        db.delete(row)
        db.commit()
        return None
    return row


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthSession | None = Depends(current_session),
) -> User | None:
    """当前登录用户；匿名会话、被停用的账号都返回 None。"""
    if auth is None or auth.user_id is None:
        return None
    user = db.get(User, auth.user_id)
    if user is None or user.status != "active":
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    """需要登录的接口用它，未登录直接 401。"""
    if user is None:
        raise errors.auth_required()
    return user


def require_csrf(
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthSession | None = Depends(current_session),
) -> AuthSession:
    """所有写操作（含注册、登录）都必须通过这一关。

    注意：依赖的是「会话里的 csrf」，不是「请求头里有什么」——
    攻击者能自动带上 Cookie，但读不到响应里的 csrfToken，
    所以伪造不出匹配的头。
    """
    if request.method in _SAFE_METHODS:
        return auth  # type: ignore[return-value]

    provided = request.headers.get(CSRF_HEADER)
    if auth is None or not provided:
        raise errors.csrf_invalid()
    if not digest_equal(auth.csrf_token, provided):
        raise errors.csrf_invalid()
    return auth
