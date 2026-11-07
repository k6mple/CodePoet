"""幂等种子：写入四个演示角色账号和演示模板。

用法（仓库根目录）：
    uv run --project backend python scripts/seed.py
    uv run --project backend python scripts/seed.py --reset-passwords
    uv run --project backend python scripts/seed.py --skip-templates

幂等性：账号按 username 判断、模板按 versionId 判断，已存在就跳过，
重复执行不会产生重复数据，也不会覆盖用户改过的昵称和简介
（除非显式要求重置密码）。

模板数据来源是 ``shared/demo/templates/*.template.json``，
那是开发者 B 维护的演示数据目录，这里只负责导入，不硬编码内容。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
TEMPLATES_DIR = PROJECT_ROOT / "shared" / "demo" / "templates"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import create_database, create_session_factory  # noqa: E402
from app.models.templates import Template, TemplateVersion  # noqa: E402
from app.models.users import User  # noqa: E402
from app.schemas.ir import DrawingIR  # noqa: E402
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

# 模板归属：文档里模板维护由 developer 负责
TEMPLATE_OWNER = "developer_demo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed_template(db: Session, payload: dict, owner_id: str) -> str:
    """导入一份模板。已存在就跳过，返回 created / skipped。"""
    version_id = payload["versionId"]
    if db.get(TemplateVersion, version_id) is not None:
        print(f"  已存在    {payload['name']}（{version_id}）")
        return "skipped"

    # 先用 DrawingIR 校验一遍再存，保证库里不会有结构非法的模板骨架
    ir_template = DrawingIR.model_validate(payload["irTemplate"])

    stamp = now_iso()
    template_id = payload["templateId"]
    template = db.get(Template, template_id)
    if template is None:
        template = Template(
            id=template_id,
            owner_id=owner_id,
            name=payload["name"],
            description=payload.get("description", ""),
            status="draft",
            category=payload.get("category"),
            published_version_id=None,
            latest_version_id=None,
            working_metadata_json=None,
            supported_backends_json=json.dumps(payload["supportedBackends"]),
            thumbnail_artifact_id=None,
            revision=1,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(template)
        db.flush()

    version = TemplateVersion(
        id=version_id,
        template_id=template_id,
        version_number=payload["versionNumber"],
        created_by=owner_id,
        status=payload["status"],
        metadata_json=json.dumps({"name": payload["name"]}, ensure_ascii=False),
        ir_template_json=ir_template.model_dump_json(),
        parameter_schema_json=json.dumps(
            payload["parameterSchema"], ensure_ascii=False
        ),
        bindings_json=json.dumps(payload["bindings"], ensure_ascii=False),
        constraints_json=json.dumps(payload["constraints"], ensure_ascii=False),
        supported_backends_json=json.dumps(payload["supportedBackends"]),
        fragment_source=None,
        ontology_version=payload.get("ontologyVersion", 1),
        change_note=payload.get("changeNote", ""),
        submitted_by=owner_id,
        submitted_at=stamp,
        published_at=stamp if payload["status"] == "published" else None,
        last_review_json=None,
        revision=1,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(version)

    template.latest_version_id = version.id
    template.updated_at = stamp
    if payload["status"] == "published":
        template.published_version_id = version.id
        template.status = "published"

    db.commit()
    print(
        f"  导入模板  {payload['name']}（{version_id}，"
        f"{len(payload['parameterSchema'])} 参数 / {len(payload['bindings'])} 绑定）"
    )
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description="写入演示账号与演示模板")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="已存在的账号也把密码重置成演示密码",
    )
    parser.add_argument(
        "--skip-templates",
        action="store_true",
        help="只写账号，不导入 shared/demo/templates 下的模板",
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

            template_created = 0
            template_skipped = 0
            if not args.skip_templates:
                owner = db.scalars(
                    select(User).where(User.username == TEMPLATE_OWNER)
                ).first()
                if owner is None:
                    print(f"  跳过模板：找不到归属账号 {TEMPLATE_OWNER}")
                else:
                    files = sorted(TEMPLATES_DIR.glob("*.template.json"))
                    if not files:
                        print(f"  跳过模板：{TEMPLATES_DIR} 下没有 *.template.json")
                    for path in files:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        if seed_template(db, payload, owner.id) == "created":
                            template_created += 1
                        else:
                            template_skipped += 1
    finally:
        engine.dispose()

    print()
    print(f"完成：账号新建 {created} 个、跳过 {skipped} 个")
    if not args.skip_templates:
        print(f"      模板新建 {template_created} 个、跳过 {template_skipped} 个")
    print(f"演示密码：{DEMO_PASSWORD}")
    print("账号：" + "、".join(name for name, *_ in DEMO_USERS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
