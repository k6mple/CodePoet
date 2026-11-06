"""幂等种子：写入四个演示角色账号。

用法（仓库根目录）：
    uv run --project backend python scripts/seed.py
    uv run --project backend python scripts/seed.py --reset-passwords

幂等性：按 username 判断，已存在就跳过，重复执行不会产生重复账号，
也不会覆盖用户改过的昵称和简介（除非显式要求重置密码）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402

from app.db import create_database, create_session_factory  # noqa: E402
from app.models.users import User  # noqa: E402
from app.services.security import hash_password  # noqa: E402
from app.settings import Settings  # noqa: E402

# 与契约 POST /auth/login 的 example 保持一致，同学照文档就能登录
DEMO_PASSWORD = "DemoPassword2026"

DEMO_USERS: list[tuple[str, str, str, str]] = [
    ("creator_demo", "创作者示例", "普通创作者，能建作品、管自己的模板", "creator"),
    ("developer_demo", "开发者示例", "额外负责知识与本体维护", "developer"),
    ("expert_demo", "专家示例", "额外拥有审核与下线权限", "expert"),
    ("admin_demo", "管理员示例", "全部权限", "admin"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description="写入演示账号")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="已存在的账号也把密码重置成演示密码",
    )
    args = parser.parse_args()

    settings = Settings()
    engine = create_database(settings)
    session_factory = create_session_factory(engine)

    with engine.connect() as connection:
        has_users = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        ).first()
    if has_users is None:
        print("数据库还没有 users 表，请先运行：python scripts/migrate.py")
        return 1

    created = 0
    skipped = 0
    try:
        with session_factory() as db:
            for username, nickname, bio, role in DEMO_USERS:
                existing = db.scalars(
                    select(User).where(User.username == username)
                ).first()
                if existing is not None:
                    if args.reset_passwords:
                        existing.password_hash = hash_password(DEMO_PASSWORD)
                        db.commit()
                        print(f"  重置密码  {username}")
                    else:
                        print(f"  已存在    {username}（角色 {existing.role}）")
                    skipped += 1
                    continue

                stamp = now_iso()
                db.add(
                    User(
                        id=f"usr-{uuid4().hex}",
                        username=username,
                        password_hash=hash_password(DEMO_PASSWORD),
                        nickname=nickname,
                        bio=bio,
                        role=role,
                        status="active",
                        role_applied_at=None,
                        role_applied_for=None,
                        revision=1,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
                db.commit()
                print(f"  创建      {username}（{role}）")
                created += 1
    finally:
        engine.dispose()

    print()
    print(f"完成：新建 {created} 个，跳过 {skipped} 个")
    print(f"演示密码：{DEMO_PASSWORD}")
    print("账号：" + "、".join(name for name, *_ in DEMO_USERS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
