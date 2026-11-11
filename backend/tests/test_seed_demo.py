"""验证 ``scripts/seed_demo.py`` 真能把示例数据导进库。

写这个测试而不是"手动跑一遍看看"：脚本读的是仓库里的 JSON，
插入靠的是导入模块的私有函数，任何一处字段对不上都会静默少导几条。
只有断言具体条数才能发现。

重点验三件事：
1. 三类知识数据（9 标签 / 8 概念 / 12 知识）条数与素材一致
2. 模板落的是 **published**（演示要能直接用来生成，不是 draft）
3. 规则能立刻命中语义生成——导入完就是可演示状态
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def load_seed_module():
    """按路径加载 scripts/seed_demo.py（它不在包内，不能直接 import）。"""
    path = ROOT / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


class SeedDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.seed = load_seed_module()

    def _admin(self):
        """直接在库里建一个 admin，避开注册流程与审批。

        幂等：同一个用例里可能被调用多次（比如重复导入的幂等性测试）。
        """
        from sqlalchemy import select

        from app.db import create_database, create_session_factory
        from app.models.users import User
        from app.services.security import hash_password

        engine = create_database(self.settings)
        factory = create_session_factory(engine)
        with factory() as db:
            existing = db.scalar(
                select(User).where(User.username == "admin_demo")
            )
            if existing is not None:
                user_id = existing.id
                engine.dispose()
                return user_id
            user = User(
                id="usr-seed-admin",
                username="admin_demo",
                password_hash=hash_password("DemoPassword2026"),
                nickname="admin_demo",
                role="admin",
                status="active",
                created_at="2026-11-10T00:00:00+00:00",
                updated_at="2026-11-10T00:00:00+00:00",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            engine.dispose()
            return user.id

    def _count(self, table: str) -> int:
        connection = sqlite3.connect(self.settings.database_path)
        value = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        connection.close()
        return value

    def _run_seed(self) -> dict:
        from app.db import create_database, create_session_factory
        from app.models.users import User

        self._admin()
        engine = create_database(self.settings)
        factory = create_session_factory(engine)
        with factory() as db:
            actor = db.query(User).filter(User.username == "admin_demo").one()
            result = self.seed.seed_all(db, actor)
        engine.dispose()
        return result

    def test_seed_loads_all_demo_data(self) -> None:
        result = self._run_seed()
        self.assertFalse(result.get("skipped"))
        # 素材本身就是这些条数，对不上说明有数据被静默跳过
        self.assertEqual(result["tags"], 9, "标签应为 9 条")
        self.assertEqual(result["concepts"], 8, "概念应为 8 条")
        self.assertEqual(result["entities"], 12, "知识应为 12 条")
        self.assertEqual(result["templates"], 3, "模板应为 3 个")
        self.assertEqual(result["rules"], 8, "规则应为 8 条")

        self.assertEqual(self._count("tags"), 9)
        self.assertEqual(self._count("ontology_concepts"), 8)
        self.assertEqual(self._count("knowledge_entities"), 12)
        self.assertEqual(self._count("templates"), 3)
        # 素材里有的模板带多个版本，所以只断言"至少每个模板一版"，
        # 以及下面那条更重要的：每个模板恰好一个 published 版本
        self.assertGreaterEqual(self._count("template_versions"), 3)
        self.assertEqual(self._count("mapping_rules"), 8)

    def test_seeded_templates_are_published(self) -> None:
        """演示模板必须是 published，否则生成时选不到。"""
        self._run_seed()
        connection = sqlite3.connect(self.settings.database_path)
        rows = connection.execute(
            "SELECT id, status, published_version_id FROM templates"
        ).fetchall()
        published_counts = connection.execute(
            "SELECT template_id, COUNT(*) FROM template_versions"
            " WHERE status = 'published' GROUP BY template_id"
        ).fetchall()
        connection.close()

        self.assertEqual(len(rows), 3)
        for template_id, status, published in rows:
            self.assertEqual(status, "published", f"{template_id} 应为 published")
            self.assertIsNotNone(published, f"{template_id} 需要有公开指针")
        # 一个模板只能有一个已发布版本，两个会让公开页不知道该显示哪版
        counts = dict(published_counts)
        for template_id, _status, published in rows:
            self.assertEqual(counts.get(template_id), 1,
                             f"{template_id} 应恰好一个 published 版本")

    def test_seed_is_idempotent(self) -> None:
        self._run_seed()
        second = self._run_seed()
        self.assertTrue(second.get("skipped"), "重复执行应跳过而不是重复插入")
        self.assertEqual(self._count("tags"), 9)

    def test_force_reseeds(self) -> None:
        self._run_seed()
        from app.db import create_database, create_session_factory
        from app.models.users import User

        engine = create_database(self.settings)
        factory = create_session_factory(engine)
        with factory() as db:
            actor = db.query(User).filter(User.username == "admin_demo").one()
            result = self.seed.seed_all(db, actor, force=True)
        engine.dispose()
        self.assertFalse(result.get("skipped"))
        self.assertEqual(self._count("tags"), 9, "重导后条数不变，不应翻倍")

    def test_seeded_data_enables_semantic_generation(self) -> None:
        """导完就该是可演示状态：随便一句莲的输入能命中规则。"""
        self._run_seed()
        token = self.client.get("/api/v1/auth/csrf").json()["csrfToken"]
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin_demo", "password": "DemoPassword2026"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(login.status_code, 200, login.text)
        csrf = login.json()["csrfToken"]

        response = self.client.post(
            "/api/v1/generations",
            json={"mode": "semantic",
                  "input": {"text": "莲花清正", "entityIds": []},
                  "backend": "canvas2d", "seed": 42},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["mode"], "semantic")
        self.assertGreater(len(body["provenance"]["matchedRules"]), 0,
                           "示例规则应该能命中")
        self.assertGreater(len(body["ir"]["nodes"]), 0)
