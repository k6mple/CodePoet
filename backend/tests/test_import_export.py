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
    workSnapshots=None, counts_override: dict | None = None,
) -> dict:
    data = {
        "concepts": concepts or [],
        "tags": tags or [],
        "entities": entities or [],
        "rules": rules or [],
        "templates": templates or [],
        "workSnapshots": workSnapshots or [],
    }
    counts = {
        "concepts": len(data["concepts"]),
        "tags": len(data["tags"]),
        "entities": len(data["entities"]),
        "rules": len(data["rules"]),
        "templates": len(data["templates"]),
        "workSnapshots": len(data["workSnapshots"]),
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

    def _seed_template(self) -> str:
        """造一个真实的模板 + 版本，供导出的结构校验用例使用。"""
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
        return created["id"]

    def _seed_work_snapshot(self, owner_id: str) -> None:
        """直接落一条完整作品链路，供 workSnapshots 导出用例使用。

        走 API 要先生成、再建渲染任务、再上传真 PNG、再保存作品，几十行前置；
        这里只关心导出读不读得出来，字段按最小合法值填。
        """
        import json as _json

        now = "2026-11-12T00:00:00+00:00"
        ir = {
            "schemaVersion": "1.0",
            "seed": 3,
            "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
            "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
            "durationMs": 0,
            "nodes": [
                {
                    "id": "c1",
                    "parentId": None,
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 60},
                    "style": {
                        "fill": "#E8A0A0",
                        "stroke": "#8B3A3A",
                        "strokeWidth": 2,
                        "opacity": 1,
                    },
                }
            ],
            "animations": [],
            "narrative": [],
        }
        provenance = {
            "kind": "manual_edit",
            "baseGenerationId": None,
            "matchedEntities": [],
            "matchedRules": [],
            "selectedSymbols": [],
            "paletteDecision": None,
            "compositionDecision": None,
            "timelineDecision": None,
            "templateSelection": None,
            "conflicts": [],
            "warnings": [],
            "engineVersion": "test-1.0",
            "ontologyVersion": 1,
            "policyRevision": 1,
            "fragmentSource": None,
            "fragmentSourceHash": None,
        }
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "gen-backup", owner_id, "ir", "canvas2d", None, "{}", 3,
                _json.dumps(ir), "d" * 64, _json.dumps(provenance),
                '{"parameterValid":true,"nodeCount":0,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", 0, "req-backup", now,
            ),
        )
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "job-backup", owner_id, "gen-backup", "canvas2d", "browser_upload",
                "succeeded", "completed", "[0]", 1, "test-1.0",
                '{"userAgent":"t","deviceScaleFactor":1}', 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, revision,"
            " published_version_id, latest_version_id, display_version_id,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "wrk-backup", owner_id, "备份作品", "", 1,
                "wv-backup", "wv-backup", "wv-backup", now, now,
            ),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "wv-backup", "wrk-backup", 1, owner_id, "published", "备份作品", "",
                "gen-backup", "job-backup", "", None, None, 1, now, now,
            ),
        )
        connection.commit()
        connection.close()

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
    def test_export_supports_all_six_types(self) -> None:
        """六类都要能导出（BE-EXPORT-TYPES-001）。

        以前 rules / workSnapshots 直接 422：契约的 ``ExportRequest.types``
        列了六类，路由描述也写着「导出知识/模板/规则与作品 IR 备份」，
        前端默认全勾选时首次导出必然失败。
        """
        self._login_admin()
        response = self.client.post(
            f"{API}/admin/exports",
            json={
                "types": [
                    "concepts",
                    "tags",
                    "entities",
                    "rules",
                    "templates",
                    "workSnapshots",
                ]
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        counts = body["manifest"]["counts"]
        for key in ("concepts", "tags", "entities", "rules", "templates", "workSnapshots"):
            self.assertIn(key, body["data"])
            self.assertEqual(counts[key], len(body["data"][key]), f"{key} 计数要如实")
        self.assertFalse(body["manifest"]["includesArtifacts"])

    def test_export_work_snapshots_carry_ir_and_flag(self) -> None:
        """作品快照备份必须带 IR、渲染任务，并如实说明有没有产物。"""
        owner_id = self._login_admin()
        self._seed_work_snapshot(owner_id)
        response = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["workSnapshots"]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        snapshots = response.json()["data"]["workSnapshots"]
        self.assertEqual(len(snapshots), 1, "示例作品应当有一条快照")
        item = snapshots[0]
        self.assertEqual(
            set(item), {"work", "version", "generation", "renderJob", "missingArtifacts"}
        )
        self.assertIsInstance(item["generation"]["ir"], dict)
        self.assertIsInstance(item["missingArtifacts"], bool)

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

    def test_rules_round_trip(self) -> None:
        """规则能导出就能导回：契约只禁 workSnapshots 导入。"""
        self._login_admin()
        rule = {
            "id": "rule-imp-1",
            "revision": 1,
            "createdAt": NOW,
            "updatedAt": NOW,
            "name": "导入规则",
            "enabled": True,
            "priority": 10,
            "match": {
                "keywords": ["莲"],
                "entityIds": [],
                "conceptIds": [],
                "tagIds": [],
                "operator": "any",
            },
            "effects": {
                "symbolIds": ["lotus"],
                "palette": ["#E8A0A0"],
                "composition": "centered",
                "motion": "none",
                "caption": "",
            },
            "explanation": "",
            "version": 1,
        }
        result = self._validate(build_package(rules=[rule]))
        self.assertTrue(result["valid"], result)
        applied = self._apply(result["validationId"], result["expectedDataRevision"])
        self.assertIn(applied.status_code, (200, 201), applied.text)
        self.assertEqual(self._count("mapping_rules"), 1, "规则必须真的落库")

    def test_validate_rejects_work_snapshots(self) -> None:
        """workSnapshots 仍不接受导入：契约写的是「留档备份」。

        rules 已经从「拒绝」改成「支持」——契约只禁了 workSnapshots 导入。
        """
        self._login_admin()
        package = build_package(workSnapshots=[{"work": {"id": "wrk-1"}}])
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

    def test_validate_rejects_hollow_template_version(self) -> None:
        """只有 id 的版本必须在校验阶段被拒，不能落库后让 GET 500。

        评审 [P1] 的复现包：template 只有 id + metadata.name，versions 只有
        一个 {"id": ...}。之前只查 id 存在与否，这种包 valid=true、apply 201，
        之后 GET /template-versions/{id} 因为缺 metadata/irTemplate 直接 500——
        坏包污染了库，而不是被整体拒绝。
        """
        self._login_admin()
        hollow = [
            {
                "template": {"id": "bad-template", "metadata": {"name": "Bad template"}},
                "versions": [{"id": "bad-version"}],
            }
        ]
        result = self._validate(build_package(templates=hollow))
        self.assertFalse(result["valid"], "空壳版本不该通过校验")
        codes = {item["code"] for item in result["errors"]}
        self.assertTrue(
            {"INVALID_TEMPLATE", "INVALID_TEMPLATE_VERSION"} & codes,
            f"应当报模板或版本结构不合法，实际 {codes}",
        )

        # 而且不能签发凭证：apply 必须拿不到 validationId 就没法写库
        apply_response = self._apply(result["validationId"], 0)
        self.assertEqual(apply_response.status_code, 409, apply_response.text)
        self.assertEqual(self._count("template_versions"), 0, "库里不该多出一条坏版本")

    def test_validate_rejects_unknown_template_field(self) -> None:
        """模板容器里出现契约没有的字段要拒绝，而不是静默忽略。

        备份包应当是导出产物。出现未知字段说明它来自另一个版本或被人改过，
        静默忽略会让「导入的东西和包里写的不一样」变成一个看不见的差异。
        """
        self._login_admin()
        self._seed_template()
        exported = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["templates"]},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        self.assertTrue(exported["data"]["templates"], "导出应当包含刚造的模板")

        package = copy.deepcopy(exported)
        item = package["data"]["templates"][0]
        item["template"]["id"] = "unknown-field-tpl"
        item["template"]["status"] = "published"  # 契约里没有这个字段
        for version in item["versions"]:
            version["id"] = "unknown-field-ver"
            version["templateId"] = "unknown-field-tpl"

        result = self._validate(package)
        self.assertFalse(result["valid"])
        codes = {i["code"] for i in result["errors"]}
        self.assertIn("INVALID_TEMPLATE", codes)

    def test_validate_accepts_real_template_roundtrip(self) -> None:
        """导出→导入必须仍然有效，别把校验写得太严把正常包也拦了。"""
        self._login_admin()
        self._seed_template()
        exported = self.client.post(
            f"{API}/admin/exports",
            json={"types": ["templates"]},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        package = exported.json()
        self.assertTrue(package["data"]["templates"], "导出应当包含刚造的模板")

        # 换 id 避免与库内冲突，再走一次校验
        for item in package["data"]["templates"]:
            item["template"]["id"] = "roundtrip-tpl"
            for version in item["versions"]:
                version["id"] = "roundtrip-ver-" + version["id"]
                version["templateId"] = "roundtrip-tpl"
        package["manifest"]["counts"]["templates"] = len(package["data"]["templates"])

        result = self._validate(package)
        self.assertTrue(result["valid"], f"正常包被拦了：{result['errors']}")

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
        # 契约的 Template 容器没有 status 字段（status 在版本上），
        # 往容器里塞一个会被结构校验正确地拒掉，所以这里只改版本的 status：
        # 「包里写着 published 也不采纳」这条规则由版本状态即可验证。
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
