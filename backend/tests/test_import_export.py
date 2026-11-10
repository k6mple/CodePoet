"""P1A-07 导入导出与审计测试。

看板的验收标准是两句原话：

* 「错误包无部分写入」
* 「日志找到审核与知识修改」

所以这个文件里最重要的不是「导入成功」的用例，而是
``test_invalid_package_writes_nothing``：一个只有最后一条会炸的包，
导入后库里必须**一条都不多**。事务性如果只靠「相信 begin/commit 写对了」
是测不出来的。

模板导入的包用「先导出、换掉 ID 前缀、再导入」来造：手写 22 个必填字段
一定会写出和契约不一致的东西，那样测的是我的笔误，不是产品。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

API = "/api/v1"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = "2026-11-10T00:00:00+00:00"


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


def tag_obj(tag_id: str, name: str, parent: str | None = None) -> dict:
    return {
        "id": tag_id,
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
        "name": name,
        "description": "",
        "parentId": parent,
        "status": "active",
        "conflictsWith": [],
        "inheritedTagIds": [],
    }


def concept_obj(concept_id: str, name: str, parent: str | None = None) -> dict:
    return {
        "id": concept_id,
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
        "name": name,
        "description": "",
        "parentId": parent,
        "status": "active",
    }


def entity_obj(
    entity_id: str, name: str, tag_ids: list[str] | None = None,
    concept_ids: list[str] | None = None,
) -> dict:
    return {
        "id": entity_id,
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
        "type": "symbol",
        "name": name,
        "aliases": [],
        "summary": "",
        "content": "",
        "sourceReferences": [],
        "conceptIds": concept_ids or [],
        "tagIds": tag_ids or [],
        "relations": [],
        "timeTheme": None,
        "status": "active",
        "effectiveTagIds": [],
        "inheritedTagIds": [],
    }


def build_package(
    *, tags=None, concepts=None, entities=None, templates=None, rules=None,
    counts_override: dict | None = None,
) -> dict:
    data = {
        "concepts": concepts or [],
        "tags": tags or [],
        "entities": entities or [],
        "rules": rules or [],
        "templates": templates or [],
        "workSnapshots": [],
    }
    counts = {
        "concepts": len(data["concepts"]),
        "tags": len(data["tags"]),
        "entities": len(data["entities"]),
        "rules": len(data["rules"]),
        "templates": len(data["templates"]),
        "workSnapshots": 0,
    }
    if counts_override:
        counts.update(counts_override)
    return {
        "schemaVersion": 1.0,
        "exportedAt": NOW,
        "manifest": {"counts": counts, "includesArtifacts": False},
        "data": data,
    }


class ImportExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""

    # ---- 辅助 -----------------------------------------------------------
    def _register(self, name: str, role: str) -> str:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertIn(response.status_code, (201, 409), response.text)
        user_id = response.json()["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()
        connection.close()
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        self.csrf = login.json()["csrfToken"]
        return user_id

    def _login_admin(self, name: str = "ops_admin") -> str:
        return self._register(name, "admin")

    def _logout(self) -> None:
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": self.csrf})

    def _seed_rows(self) -> None:
        """用 API 造一些真实数据，供导出验证。"""
        self.client.post(
            f"{API}/tags", json={"name": "导出标签"}, headers={"X-CSRF-Token": self.csrf}
        )
        self.client.post(
            f"{API}/knowledge",
            json={
                "type": "symbol", "name": "导出符号", "aliases": [],
                "summary": "", "content": "", "tagIds": [], "conceptIds": [],
                "relations": [], "timeTheme": None,
            },
            headers={"X-CSRF-Token": self.csrf},
        )

    def _validate(self, package: dict) -> dict:
        response = self.client.post(
            f"{API}/admin/imports/validate",
            json=package,
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _apply(self, validation_id: str, expected_revision: int):
        return self.client.post(
            f"{API}/admin/imports/apply",
            json={"validationId": validation_id, "expectedDataRevision": expected_revision},
            headers={"X-CSRF-Token": self.csrf},
        )

    def _count(self, table: str) -> int:
        connection = sqlite3.connect(self.settings.database_path)
        value = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        connection.close()
        return value

    # ---- 导出 -----------------------------------------------------------
    def test_export_counts_match_data(self) -> None:
        """看板：导出清单与实际数量相符。"""
        self._login_admin()
        self._seed_rows()
        response = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["tags", "concepts", "entities", "templates"]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        package = response.json()

        self.assertEqual(package["schemaVersion"], 1.0)
        self.assertFalse(package["manifest"]["includesArtifacts"])
        for key in ("concepts", "tags", "entities", "rules", "templates", "workSnapshots"):
            declared = package["manifest"]["counts"][key]
            actual = len(package["data"][key])
            self.assertEqual(declared, actual, f"{key} 计数不符")

        self.assertGreaterEqual(package["manifest"]["counts"]["tags"], 1)

    def test_export_respects_types(self) -> None:
        self._login_admin()
        self._seed_rows()
        response = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["tags"]},
            headers={"X-CSRF-Token": self.csrf},
        )
        package = response.json()
        self.assertGreaterEqual(len(package["data"]["tags"]), 1)
        self.assertEqual(package["data"]["entities"], [], "未请求的类型应为空")

    def test_export_rejects_ids_selection(self) -> None:
        """契约：core-demo 不支持按 ids 选择导出。"""
        self._login_admin()
        response = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["tags"], "ids": {"tags": ["t1"]}},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_export_requires_admin(self) -> None:
        self._register("plain_dev", "developer")
        response = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["tags"]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 403, response.text)

    # ---- 校验 -----------------------------------------------------------
    def test_validate_accepts_good_package(self) -> None:
        self._login_admin()
        package = build_package(
            tags=[tag_obj("imp-tag-1", "导入标签")],
            concepts=[concept_obj("imp-concept-1", "导入概念")],
            entities=[entity_obj("imp-ent-1", "导入符号", ["imp-tag-1"], ["imp-concept-1"])],
        )
        result = self._validate(package)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"]["tags"], 1)
        self.assertEqual(len(result["contentHash"]), 64)
        self.assertIsNotNone(result["validationId"])

    def test_validate_detects_duplicate_ids(self) -> None:
        self._login_admin()
        package = build_package(tags=[tag_obj("dup-1", "甲"), tag_obj("dup-1", "乙")])
        result = self._validate(package)
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("DUPLICATE_ID", codes)

    def test_validate_detects_db_conflict(self) -> None:
        self._login_admin()
        self._seed_rows()
        # 先导出拿到库里真实存在的 tag ID
        exported = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["tags"]},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        existing = exported["data"]["tags"][0]
        # 原样导回：ID 冲突，整批拒绝
        result = self._validate(build_package(tags=[existing]))
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("ID_CONFLICT", codes)

    def test_validate_detects_missing_parent(self) -> None:
        self._login_admin()
        package = build_package(
            tags=[tag_obj("imp-child", "子标签", parent="not-exists")]
        )
        result = self._validate(package)
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("MISSING_REFERENCE", codes)

    def test_validate_detects_missing_entity_reference(self) -> None:
        self._login_admin()
        package = build_package(
            entities=[entity_obj("imp-ent-x", "符号", tag_ids=["ghost-tag"])]
        )
        result = self._validate(package)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any(item["code"] == "MISSING_REFERENCE" for item in result["errors"]),
            result["errors"],
        )

    def test_validate_detects_count_mismatch(self) -> None:
        self._login_admin()
        package = build_package(
            tags=[tag_obj("imp-tag-c", "标签")], counts_override={"tags": 5}
        )
        result = self._validate(package)
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("COUNT_MISMATCH", codes)

    def test_validate_rejects_rules(self) -> None:
        self._login_admin()
        package = build_package(rules=[{"id": "rule-1"}])
        result = self._validate(package)
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("UNSUPPORTED_TYPE", codes)

    def test_validate_detects_bad_shape(self) -> None:
        """少 name 的 tag 应在校验阶段定位到下标，而不是等到插入时炸。"""
        self._login_admin()
        broken = tag_obj("imp-tag-broken", "标签")
        broken.pop("name")
        result = self._validate(build_package(tags=[broken]))
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("INVALID_SHAPE", codes)

    # ---- 执行 -----------------------------------------------------------
    def test_apply_writes_data(self) -> None:
        self._login_admin()
        package = build_package(
            tags=[tag_obj("imp-tag-a", "甲标签"), tag_obj("imp-tag-b", "乙标签", "imp-tag-a")],
            concepts=[concept_obj("imp-concept-a", "甲概念")],
            entities=[entity_obj("imp-ent-a", "甲符号", ["imp-tag-b"], ["imp-concept-a"])],
        )
        validation = self._validate(package)
        self.assertTrue(validation["valid"], validation["errors"])

        response = self._apply(validation["validationId"], validation["expectedDataRevision"])
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["dataRevision"], 1)
        self.assertEqual(body["counts"]["tags"], 2)

        self.assertEqual(self._count("tags"), 2)
        self.assertEqual(self._count("ontology_concepts"), 1)
        self.assertEqual(self._count("knowledge_entities"), 1)

        # 父子关系真的建立了
        connection = sqlite3.connect(self.settings.database_path)
        parent = connection.execute(
            "SELECT parent_id FROM tags WHERE id = 'imp-tag-b'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(parent, "imp-tag-a")

    def test_imported_template_is_draft(self) -> None:
        """契约：导入的模板一律落 draft，包里写着 published 也不采纳。"""
        self._login_admin()
        # 先建一个真实模板并导出，拿到结构完全正确的包
        created = self.client.post(
            f"{API}/templates",
            json={
                "metadata": {
                    "name": "导出模板", "description": "",
                    "entityIds": [], "tagIds": [], "symbols": [], "timeTheme": None,
                }
            },
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={
                "irTemplate": {
                    "schemaVersion": "1.0", "seed": 1,
                    "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
                    "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "durationMs": 0,
                    "nodes": [{
                        "id": "c1", "parentId": None, "type": "circle",
                        "geometry": {"cx": 400, "cy": 300, "r": 60},
                        "style": {"fill": "#E8A0A0", "stroke": "#8B3A3A",
                                  "strokeWidth": 2, "opacity": 1},
                    }],
                    "animations": [], "narrative": [],
                },
                "parameterSchema": [], "bindings": [], "constraints": [],
                "supportedBackends": ["canvas2d"],
                "metadata": {
                    "name": "导出模板", "description": "",
                    "entityIds": [], "tagIds": [], "symbols": [], "timeTheme": None,
                },
                "changeNote": "初版",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        exported = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["templates"]},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        self.assertEqual(len(exported["data"]["templates"]), 1)

        # 换掉 ID 前缀，避免与库里冲突
        package = copy.deepcopy(exported)
        item = package["data"]["templates"][0]
        old_template_id = item["template"]["id"]
        new_template_id = f"imp-{old_template_id}"
        item["template"]["id"] = new_template_id
        item["template"]["status"] = "published"  # 故意写成 published
        for version in item["versions"]:
            version["id"] = f"imp-{version['id']}"
            version["templateId"] = new_template_id
            version["status"] = "published"

        validation = self._validate(package)
        self.assertTrue(validation["valid"], validation["errors"])
        response = self._apply(validation["validationId"], validation["expectedDataRevision"])
        self.assertEqual(response.status_code, 201, response.text)

        connection = sqlite3.connect(self.settings.database_path)
        status = connection.execute(
            "SELECT status FROM templates WHERE id = ?", (new_template_id,)
        ).fetchone()[0]
        version_status = connection.execute(
            "SELECT status FROM template_versions WHERE template_id = ?", (new_template_id,)
        ).fetchone()[0]
        published = connection.execute(
            "SELECT published_version_id FROM templates WHERE id = ?", (new_template_id,)
        ).fetchone()[0]
        connection.close()
        self.assertEqual(status, "draft", "导入模板必须落 draft")
        self.assertEqual(version_status, "draft")
        self.assertIsNone(published, "导入不能带公开指针")

    def test_invalid_package_writes_nothing(self) -> None:
        """看板验收：错误包无部分写入。

        包里最后一条 entity 引用了不存在的标签，前面三条本身合法。
        校验阶段就该拦下；即使有人绕过校验，apply 也必须是全或无。
        """
        self._login_admin()
        package = build_package(
            tags=[tag_obj("imp-ok-1", "甲"), tag_obj("imp-ok-2", "乙")],
            concepts=[concept_obj("imp-ok-c1", "概念")],
            entities=[entity_obj("imp-ok-e1", "符号", tag_ids=["ghost"])],
        )
        before_tags = self._count("tags")
        before_entities = self._count("knowledge_entities")

        validation = self._validate(package)
        self.assertFalse(validation["valid"])
        code = self._apply(validation["validationId"], validation["expectedDataRevision"])
        self.assertEqual(code.status_code, 409, code.text)
        self.assertEqual(code.json()["error"]["code"], "INVALID_STATE")

        self.assertEqual(self._count("tags"), before_tags, "不存在的数据不该被写入")
        self.assertEqual(self._count("knowledge_entities"), before_entities)

    def test_validation_is_single_use(self) -> None:
        self._login_admin()
        package = build_package(tags=[tag_obj("imp-once", "一次性")])
        validation = self._validate(package)
        first = self._apply(validation["validationId"], validation["expectedDataRevision"])
        self.assertEqual(first.status_code, 201, first.text)
        second = self._apply(validation["validationId"], validation["expectedDataRevision"])
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["error"]["code"], "INVALID_STATE")
        self.assertEqual(self._count("tags"), 1, "第二次不该再写入")

    def test_apply_rejects_stale_data_revision(self) -> None:
        """验证之后数据变了，之前的结构与引用结论就失效了。"""
        self._login_admin()
        first = build_package(tags=[tag_obj("imp-r1", "第一批")])
        v1 = self._validate(first)
        self.assertEqual(
            self._apply(v1["validationId"], v1["expectedDataRevision"]).status_code, 201
        )

        second = build_package(tags=[tag_obj("imp-r2", "第二批")])
        v2 = self._validate(second)
        # 用旧的 revision 去执行
        response = self._apply(v2["validationId"], 0)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    # ---- 审计 -----------------------------------------------------------
    def test_audit_records_knowledge_writes(self) -> None:
        """看板：日志能找到知识修改。"""
        self._login_admin()
        created = self.client.post(
            f"{API}/knowledge",
            json={
                "type": "symbol", "name": "审计符号", "aliases": [],
                "summary": "", "content": "", "tagIds": [], "conceptIds": [],
                "relations": [], "timeTheme": None,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(created.status_code, 201, created.text)
        entity_id = created.json()["id"]

        response = self.client.get(f"{API}/audit-logs?action=knowledge.create")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        target = next((i for i in items if i["targetId"] == entity_id), None)
        self.assertIsNotNone(target, f"应能找到创建记录：{items}")
        self.assertEqual(target["result"], "success")
        self.assertEqual(target["targetType"], "knowledge_entity")
        # requestId 必须存在，它是追溯的钥匙
        self.assertTrue(target["requestId"].startswith("req-"))

    def test_audit_records_import(self) -> None:
        self._login_admin()
        package = build_package(tags=[tag_obj("imp-audit", "审计标签")])
        validation = self._validate(package)
        self._apply(validation["validationId"], validation["expectedDataRevision"])

        response = self.client.get(f"{API}/audit-logs?action=import.apply")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json()["total"], 1)

    def test_audit_filters_by_request_id(self) -> None:
        """一个请求改多张表时，靠 requestId 串起来。"""
        self._login_admin()
        self.client.post(
            f"{API}/knowledge",
            json={
                "type": "concept", "name": "追溯符号", "aliases": [],
                "summary": "", "content": "", "tagIds": [], "conceptIds": [],
                "relations": [], "timeTheme": None,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        listing = self.client.get(f"{API}/audit-logs?action=knowledge.create").json()
        request_id = listing["items"][0]["requestId"]
        filtered = self.client.get(f"{API}/audit-logs?requestId={request_id}").json()
        self.assertGreaterEqual(filtered["total"], 1)
        self.assertTrue(all(i["requestId"] == request_id for i in filtered["items"]))

    def test_audit_requires_admin(self) -> None:
        self._register("audit_dev", "developer")
        response = self.client.get(f"{API}/audit-logs")
        self.assertEqual(response.status_code, 403, response.text)

    def test_audit_log_is_immutable(self) -> None:
        """审计记录在数据库层面就改不了。"""
        self._login_admin()
        self.client.post(
            f"{API}/knowledge",
            json={
                "type": "symbol", "name": "不可改", "aliases": [],
                "summary": "", "content": "", "tagIds": [], "conceptIds": [],
                "relations": [], "timeTheme": None,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        connection = sqlite3.connect(self.settings.database_path)
        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute("UPDATE audit_logs SET action = 'x'")
        connection.close()
