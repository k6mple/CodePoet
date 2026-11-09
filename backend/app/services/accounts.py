"""账号业务逻辑：注册、登录校验、会话发放与吊销。

控制器（``app/api/auth.py``）只负责解析请求和拼响应，判断都在这里，
方便单独测试，也不会把 Session 操作散落到路由里。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.deps import (
    ANONYMOUS_TTL,
    AUTHENTICATED_TTL,
    parse_iso,
    to_iso,
    utcnow,
)
from app.models.users import AuthSession, User
from app.schemas.auth import RegisterRequest, RoleApplication, User as UserSchema
from app.services import permissions
from app.services.security import (
    hash_password,
    hash_session_token,
    new_csrf_token,
    new_session_token,
    verify_password,
)

# 用户不存在时用它跑一次哈希，让「查无此人」和「密码错误」耗时接近。
# 否则攻击者靠响应时间就能枚举出哪些用户名存在。
_DUMMY_HASH = hash_password("dummy-password-for-timing", iterations=600_000)


def build_user_schema(user: User) -> UserSchema:
    """ORM 对象转对外模型。权限由角色推导，不存库。"""
    application: RoleApplication | None = None
    if user.role_applied_for is not None:
        # P1A-01 起状态、理由、决定时间都来自真实列；
        # 历史行由 005 的回填 + 触发器保证一定有 status，缺了才回落 pending
        application = RoleApplication(
            requestedRole=user.role_applied_for,  # type: ignore[arg-type]
            status=user.role_application_status or "pending",  # type: ignore[arg-type]
            reason=user.role_decision_reason or "",
            decidedAt=user.role_decided_at,
        )
    return UserSchema(
        id=user.id,
        revision=user.revision,
        createdAt=user.created_at,
        updatedAt=user.updated_at,
        username=user.username,
        nickname=user.nickname,
        bio=user.bio,
        role=user.role,  # type: ignore[arg-type]
        status=user.status,  # type: ignore[arg-type]
        roleApplication=application,
        permissions=permissions.permissions_for(user.role),
    )


def register_user(db: Session, payload: RegisterRequest) -> User:
    """注册一律成为 creator；申请的更高角色只记下来等审批。"""
    now = utcnow()
    user = User(
        id=f"usr-{uuid4().hex}",
        username=payload.username,
        password_hash=hash_password(payload.password),
        nickname=payload.nickname or payload.username,
        bio="",
        # 绝不接受客户端直接指定 role=admin，只能申请
        role="creator",
        status="active",
        role_applied_at=to_iso(now) if payload.requestedRole else None,
        role_applied_for=payload.requestedRole,
        revision=1,
        created_at=to_iso(now),
        updated_at=to_iso(now),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 唯一索引是 COLLATE NOCASE，所以 Alice 和 alice 会撞车
        db.rollback()
        raise errors.ApiError(409, "USERNAME_TAKEN", "该用户名已被占用") from None
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    """校验账号密码。任何失败都返回同一个错误，不区分原因。"""
    user = db.scalars(
        select(User).where(User.username == username)
    ).first()

    if user is None:
        # 空跑一次哈希，保持与「密码错误」相同的耗时
        verify_password(password, _DUMMY_HASH)
        raise errors.invalid_credentials()

    if not verify_password(password, user.password_hash):
        raise errors.invalid_credentials()

    if user.status != "active":
        raise errors.ApiError(403, "FORBIDDEN", "该账号已被停用")
    return user


def issue_session(db: Session, user: User | None) -> tuple[str, AuthSession]:
    """创建会话并返回 (明文 token, 记录)。

    明文 token 只返回给调用方写进 Cookie，库里存哈希。
    """
    raw_token = new_session_token()
    now = utcnow()
    ttl = AUTHENTICATED_TTL if user is not None else ANONYMOUS_TTL
    row = AuthSession(
        id=f"ses-{uuid4().hex}",
        user_id=user.id if user is not None else None,
        token_hash=hash_session_token(raw_token),
        csrf_token=new_csrf_token(),
        kind="authenticated" if user is not None else "anonymous",
        expires_at=to_iso(now + ttl),
        created_at=to_iso(now),
        last_seen_at=to_iso(now),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw_token, row


def revoke_session(db: Session, row: AuthSession) -> None:
    db.delete(row)
    db.commit()


def touch(db: Session, row: AuthSession) -> None:
    """记录最后活跃时间，供后续会话列表使用（这里只更新，不延长有效期）。"""
    row.last_seen_at = to_iso(utcnow())
    db.commit()


def update_profile(
    db: Session, user: User, nickname: str | None, bio: str | None, expected_revision: int
) -> User:
    """按乐观并发更新资料。版本号不匹配说明别人先改了，返回 409。"""
    if user.revision != expected_revision:
        raise errors.revision_conflict(expected_revision, user.revision)

    if nickname is not None:
        user.nickname = nickname
    if bio is not None:
        user.bio = bio
    user.revision += 1
    user.updated_at = to_iso(utcnow())
    db.commit()
    db.refresh(user)
    return user


def session_is_expired(row: AuthSession) -> bool:
    expires_at: datetime | None = parse_iso(row.expires_at)
    return expires_at is None or expires_at <= utcnow()
