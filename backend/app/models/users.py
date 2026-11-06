"""用户、角色申请与服务端认证会话的 ORM 映射。

表结构由 ``migrations/*.sql`` 定义，这里只做映射、不负责建表。
这样「数据库结构的唯一真相」在迁移文件里，不会出现 ORM 与 SQL 各写一套、
慢慢长歪的情况。

注意：``users.username`` 的 UNIQUE 是 ``COLLATE NOCASE``（大小写不敏感），
这个特性 ORM 表达不出来，只能靠 001_init.sql 里的定义。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # 只存哈希，格式见 app/services/security.py
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    nickname: Mapped[str] = mapped_column(String(40), nullable=False)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # creator / developer / expert / admin
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="creator")
    # active / disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 角色申请：申请时间与目标角色，审批后清空
    role_applied_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_applied_for: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 乐观并发版本号，从 1 起
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AuthSession(Base):
    """服务端会话。匿名会话 user_id 为空，只用于发放 CSRF token。"""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # 库里只存 Cookie 中 token 的 SHA-256，明文仅存在于浏览器
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # anonymous / authenticated
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str | None] = mapped_column(Text, nullable=True)
