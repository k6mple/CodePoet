"""把 ``shared/demo`` 里的示例数据导入数据库。

演示需要「12 条知识 / 8 条规则 / 3 个模板」在库里，否则语义生成一条规则都
命中不了，模板列表也是空的。素材早就写好了（``shared/demo``），缺的就是
这个搬运脚本。

用法（仓库根目录）：

    uv run --project backend python scripts/seed_demo.py
    uv run --project backend python scripts/seed_demo.py --force   # 清空后重导

设计取舍：

* **模板直接落 published**。复用导入接口的 ``_insert_templates`` 会把它们
  变成 draft（那是"从外部导入"的正确行为），但演示数据是可信的内置素材，
  不该要求演示者先走一遍审核流程。
* **知识/标签/概念/规则复用导入模块的插入函数**，避免两套字段映射各自漂移。
  那些函数已经过测试（含父子依赖排序与成环检测）。
* **默认幂等**：库里已有标签就跳过，不会重复插入。``--force`` 会先清空
  这几张业务表再导。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.db import create_database, create_session_factory  # noqa: E402
from app.models.knowledge import Concept as ConceptModel  # noqa: E402
from app.models.knowledge import KnowledgeEntity as KnowledgeModel  # noqa: E402
from app.models.knowledge import Tag as TagModel  # noqa: E402
from app.models.rules import MappingRule as RuleModel  # noqa: E402
from app.models.templates import Template as TemplateModel  # noqa: E402
from app.models.templates import TemplateVersion as VersionModel  # noqa: E402
from app.models.users import User  # noqa: E402
from app.schemas.rules import RuleCreate  # noqa: E402
from app.services import import_export  # noqa: E402
from app.services import rules as rules_service  # noqa: E402
from app.settings import Settings  # noqa: E402

DEMO = ROOT / "shared" / "demo"


def load(rel: str):
    return json.loads((DEMO / rel).read_text(encoding="utf-8"))


def items_of(payload):
    """素材里有的文件是 ``{"tags": [...]}``，有的是裸数组。"""
    if isinstance(payload, list):
        return payload
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def pick_admin(db) -> User:
    """找一个管理员当 owner。没有就报错，不静默创建用户。"""
    for name in ("admin_demo", "adminone", "smokeadmin"):
        row = db.scalar(select(User).where(User.username == name))
        if row is not None:
            return row
    row = db.scalar(select(User).where(User.role == "admin"))
    if row is None:
        raise SystemExit(
            "库里没有管理员账号；先跑 scripts/seed.py 建四个角色用户"
        )
    return row


def clear_business_tables(db) -> None:
    """只清业务表，不碰用户与会话。"""
    for model in (VersionModel, TemplateModel, KnowledgeModel, ConceptModel,
                  TagModel, RuleModel):
        db.execute(delete(model))
    db.commit()


def seed_templates(db, actor: User) -> int:
    """演示模板直接落 published，不走审核。

    复用导入模块的插入函数会把状态强制成 draft——那对"外部导入"是对的，
    但内置素材不该逼演示者先审一遍。
    """
    count = 0
    for name in ("lotus", "bamboo", "narrative"):
        path = DEMO / "templates" / f"{name}.json"
        if not path.exists():
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        template = item["template"]
        versions = item.get("versions") or []
        if not versions:
            continue

        template_id = template["id"]
        metadata = template.get("metadata") or {}
        working = template.get("workingMetadata") or metadata
        now = "2026-11-10T00:00:00+00:00"
        # 版本号最大的那个作为已发布版本
        published = max(versions, key=lambda v: int(v.get("versionNumber") or 1))
        published_id = published["id"]

        db.add(
            TemplateModel(
                id=template_id,
                owner_id=actor.id,
                name=str(metadata.get("name") or name)[:120],
                description=str(metadata.get("description") or ""),
                status="published",
                category=None,
                published_version_id=published_id,
                latest_version_id=published_id,
                working_metadata_json=json.dumps(working, ensure_ascii=False),
                supported_backends_json=json.dumps(
                    template.get("supportedBackends") or ["canvas2d"], ensure_ascii=False
                ),
                thumbnail_artifact_id=None,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()

        for version in versions:
            status = "published" if version["id"] == published_id else "draft"
            db.add(
                VersionModel(
                    id=version["id"],
                    template_id=template_id,
                    version_number=int(version.get("versionNumber") or 1),
                    created_by=actor.id,
                    status=status,
                    metadata_json=json.dumps(
                        version.get("metadata") or {}, ensure_ascii=False
                    ),
                    ir_template_json=json.dumps(
                        version.get("irTemplate") or {}, ensure_ascii=False
                    ),
                    parameter_schema_json=json.dumps(
                        version.get("parameterSchema") or [], ensure_ascii=False
                    ),
                    bindings_json=json.dumps(
                        version.get("bindings") or [], ensure_ascii=False
                    ),
                    constraints_json=json.dumps(
                        version.get("constraints") or [], ensure_ascii=False
                    ),
                    supported_backends_json=json.dumps(
                        version.get("supportedBackends") or ["canvas2d"],
                        ensure_ascii=False,
                    ),
                    fragment_source=version.get("fragmentSource"),
                    ontology_version=int(version.get("ontologyVersion") or 1),
                    change_note=str(version.get("changeNote") or ""),
                    submitted_by=None,
                    submitted_at=now if status == "published" else None,
                    published_at=now if status == "published" else None,
                    last_review_json=None,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()
        count += 1
    db.commit()
    return count


def seed_rules(db, actor: User) -> int:
    count = 0
    for item in items_of(load("rules/rules.json")):
        payload = RuleCreate(
            name=item["name"],
            enabled=bool(item.get("enabled", True)),
            priority=int(item.get("priority") or 0),
            match=item["match"],
            effects=item["effects"],
            explanation=item.get("explanation") or "",
        )
        rules_service.create_rule(db, actor, payload)
        count += 1
    return count


def seed_all(db, actor: User, *, force: bool = False) -> dict:
    """把示例数据写进给定的 Session。返回各类型条数。

    抽成函数而不是塞在 main 里，是为了能被测试直接调用——否则这段
    逻辑只能靠"跑一遍看看"验证，而那恰恰是最不可靠的方式。
    """
    existing = db.scalar(select(TagModel.id).limit(1))
    if existing is not None and not force:
        return {"skipped": True}
    if force:
        clear_business_tables(db)

    tags = items_of(load("knowledge/tags.json"))
    concepts = items_of(load("knowledge/concepts.json"))
    entities = items_of(load("knowledge/knowledge.json"))

    # 复用导入模块的插入函数：它们已处理父子依赖排序与成环检测
    import_export._insert_concepts(db, concepts, actor)  # noqa: SLF001
    import_export._insert_tags(db, tags, actor)  # noqa: SLF001
    import_export._insert_entities(db, entities, actor)  # noqa: SLF001
    db.commit()

    templates = seed_templates(db, actor)
    rule_count = seed_rules(db, actor)
    return {
        "skipped": False,
        "tags": len(tags),
        "concepts": len(concepts),
        "entities": len(entities),
        "templates": templates,
        "rules": rule_count,
    }


def main() -> int:
    force = "--force" in sys.argv
    settings = Settings()
    engine = create_database(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as db:
            actor = pick_admin(db)
            print(f"执行导入的账号：{actor.username}（{actor.role}）")
            result = seed_all(db, actor, force=force)
            if result.get("skipped"):
                print("库里已有标签，跳过。要重导请加 --force")
                return 0
            print(
                f"标签 {result['tags']} / 概念 {result['concepts']} / "
                f"知识 {result['entities']} / 模板 {result['templates']}（已发布）"
                f" / 规则 {result['rules']}"
            )
            print("\n完成。可以用这些输入试语义生成：")
            print("  莲花清正 / 竹节守正 / 清水廉洁")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
