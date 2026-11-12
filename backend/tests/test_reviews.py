"""P1A-04 审核流程集成测试。

用真实 SQLite 跑迁移，不 mock 服务层——这几条规则的整点就在「数据库
状态真的变了 / 真的没变」，mock 掉之后什么都验不出来。

覆盖的是**拒绝**比覆盖「能通过」更重要：
状态机跳转、自审、无理由驳回、派生作品送审，这四条每条都能对应一次
线上事故，所以每条都单独一个用例。
"""

from __future__ import annotations

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


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


def template_ir() -> str:
    """能通过引擎校验的最小 IR：一个圆，无参数绑定。"""
    return json.dumps(
        {
            "schemaVersion": "1.0",
            "seed": 7,
            "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
            "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
            "durationMs": 0,
            "nodes": [
                {
                    "id": "c1",
                    "parentId": None,
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 100},
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
    )


class ReviewFlowTests(unittest.TestCase):
    """送审 → 队列 → 裁决 → 公开指针。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self._seq = 0

    # ---- 辅助 -----------------------------------------------------------
    def _csrf(self) -> str:
        return self.client.get(f"{API}/auth/csrf").json()["csrfToken"]

    def _register(self, name: str, role: str = "developer") -> str:
        """建用户并直接把 role 改到位。

        走注册 + 审批流程会给每个用例套上一层与审核无关的状态机，
        这里要测的是审核本身，所以角色直接落库。
        """
        token = self._csrf()
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertIn(response.status_code, (201, 409), response.text)
        user_id = response.json()["id"] if response.status_code == 201 else None
        if user_id is None:
            login = self.client.post(
                f"{API}/auth/login",
                json={"username": name, "password": password},
                headers={"X-CSRF-Token": self._csrf()},
            )
            user_id = login.json()["user"]["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()
        connection.close()
        return user_id

    def _login(self, name: str) -> str:
        token = self._csrf()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": "Aa1!" + "x" * 12},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def _logout(self) -> None:
        token = self._csrf()
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": token})

    def _unique(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _seed_template_version(
        self, owner_id: str, status: str = "draft", *, submitted_by: str | None = None
    ) -> tuple[str, str]:
        """插一个模板容器 + 一个版本，返回 (template_id, version_id)。"""
        template_id = self._unique("tpl")
        version_id = self._unique("tv")
        now = "2026-11-09T10:00:00+00:00"
        metadata = json.dumps({"name": f"模板{version_id}", "description": ""})
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO templates (id, owner_id, name, description, status,"
            " supported_backends_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (template_id, owner_id, f"模板{template_id}", "", "draft",
             '["canvas2d"]', 1, now, now),
        )
        connection.execute(
            "INSERT INTO template_versions (id, template_id, version_number, created_by,"
            " status, metadata_json, ir_template_json, parameter_schema_json,"
            " bindings_json, constraints_json, supported_backends_json,"
            " ontology_version, change_note, submitted_by, submitted_at, revision,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id, template_id, 1, owner_id, status, metadata,
                template_ir(), "[]", "[]", "[]", '["canvas2d"]', 1, "",
                submitted_by, now if submitted_by else None, 1, now, now,
            ),
        )
        connection.commit()
        connection.close()
        return template_id, version_id

    def _seed_work_version(
        self, owner_id: str, status: str = "private", *, unpublished_source: int = 0
    ) -> tuple[str, str, str]:
        """插 generation + 完成的渲染任务 + 作品 + 版本。

        返回 (work_id, version_id, generation_id)。
        """
        work_id = self._unique("wrk")
        version_id = self._unique("wv")
        generation_id = self._unique("gen")
        job_id = self._unique("job")
        now = "2026-11-09T10:00:00+00:00"
        provenance = json.dumps(
            {
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
        )
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation_id, owner_id, "ir", "canvas2d", None, "{}", 7,
                template_ir(), "b" * 64, provenance,
                '{"parameterValid":true,"nodeCount":1,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", unpublished_source, "req-test", now,
            ),
        )
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, owner_id, generation_id, "canvas2d", "browser_upload",
                "succeeded", "completed", "[0]", 1, "test-1.0",
                '{"userAgent":"t","deviceScaleFactor":1}', 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, revision,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (work_id, owner_id, "作品", "", 1, now, now),
        )
        # latest/display 都是必填指针：create_work 建完就补上，缺了会让
        # build_work 的响应模型校验直接报「latestVersionId 至少 1 个字符」。
        connection.execute(
            "UPDATE works SET latest_version_id = ?, display_version_id = ? WHERE id = ?",
            (version_id, version_id, work_id),
        )
        if status == "published":
            # 公开指针必须和版本状态一起写。``can_read_version`` 现在要求
            # 「版本是 published **且** 容器仍挂着公开版本」——只写状态不写指针
            # 造出的是生产路径下不可能出现的数据（decide_review 两者同时写），
            # 拿它去断言可见性只会测出一个假象。
            connection.execute(
                "UPDATE works SET published_version_id = ? WHERE id = ?",
                (version_id, work_id),
            )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id, work_id, 1, owner_id, status, "作品", "",
                generation_id, job_id, "", None, None, 1, now, now,
            ),
        )
        connection.commit()
        connection.close()
        return work_id, version_id, generation_id

    def _seed_extra_version(
        self, work_id: str, owner_id: str, number: int, status: str = "private"
    ) -> tuple[str, str]:
        """给已有作品补一个版本（自带 generation + 完成的渲染任务）。

        真实流程里 v2 是作者从旧版改 IR 后新建的，所以它有自己的快照与渲染
        任务；共用 v1 的 job 就测不出「私有新版会不会把公开视图顶掉」。
        返回 (version_id, generation_id)。
        """
        version_id = self._unique("wv")
        generation_id = self._unique("gen")
        job_id = self._unique("job")
        now = "2026-11-09T11:00:00+00:00"
        provenance = json.dumps(
            {
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
        )
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation_id, owner_id, "ir", "canvas2d", None, "{}", 8,
                template_ir(), "c" * 64, provenance,
                '{"parameterValid":true,"nodeCount":1,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", 0, "req-test", now,
            ),
        )
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, owner_id, generation_id, "canvas2d", "browser_upload",
                "succeeded", "completed", "[0]", 1, "test-1.0",
                '{"userAgent":"t","deviceScaleFactor":1}', 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id, work_id, number, owner_id, status, "作品", "",
                generation_id, job_id, "", None, None, 1, now, now,
            ),
        )
        connection.commit()
        connection.close()
        return version_id, generation_id

    def _point_work_at(
        self,
        work_id: str,
        *,
        published: str | None,
        latest: str,
        display: str,
        thumbnail: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE works SET published_version_id = ?, latest_version_id = ?,"
            " display_version_id = ?, thumbnail_artifact_id = ? WHERE id = ?",
            (published, latest, display, thumbnail, work_id),
        )
        connection.commit()
        connection.close()

    def _status_of(self, table: str, row_id: str) -> str:
        connection = sqlite3.connect(self.settings.database_path)
        row = connection.execute(
            f"SELECT status FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        connection.close()
        return row[0] if row else ""

    def _column(self, table: str, row_id: str, column: str):
        connection = sqlite3.connect(self.settings.database_path)
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        connection.close()
        return row[0] if row else None

    # ---- 送审 -----------------------------------------------------------
    def test_submit_template_moves_to_submitted(self) -> None:
        owner = self._register("tpl_owner")
        self._login("tpl_owner")
        template_id, version_id = self._seed_template_version(owner)

        response = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["submittedBy"], owner)
        # 容器状态同步，前端列表才显示"审核中"
        self.assertEqual(self._status_of("templates", template_id), "submitted")

    def test_submit_twice_is_rejected(self) -> None:
        owner = self._register("tpl_twice")
        self._login("tpl_twice")
        _, version_id = self._seed_template_version(owner, "submitted", submitted_by=owner)

        response = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_STATE")

    def test_submit_with_stale_revision_conflicts(self) -> None:
        owner = self._register("tpl_stale")
        self._login("tpl_stale")
        _, version_id = self._seed_template_version(owner)

        response = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 99},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_other_user_cannot_submit_someone_elses_version(self) -> None:
        owner = self._register("tpl_real_owner")
        self._register("tpl_intruder")
        self._login("tpl_intruder")
        _, version_id = self._seed_template_version(owner)

        response = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        # 无所有权按 404，不确认资源存在
        self.assertEqual(response.status_code, 404, response.text)

    def test_submit_revalidates_broken_definition(self) -> None:
        """binding 指向不存在的节点，送审必须 422 而不是进队列。"""
        owner = self._register("tpl_broken")
        self._login("tpl_broken")
        _, version_id = self._seed_template_version(owner)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE template_versions SET bindings_json = ?, parameter_schema_json = ?"
            " WHERE id = ?",
            (
                json.dumps([{"parameter": "r", "nodeId": "nope", "property": "geometry.r"}]),
                json.dumps(
                    [
                        {
                            "type": "integer",
                            "name": "r",
                            "label": "半径",
                            "description": "",
                            "default": 100,
                            "min": 1,
                            "max": 300,
                            "step": 1,
                        }
                    ]
                ),
                version_id,
            ),
        )
        connection.commit()
        connection.close()

        response = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("模板定义", response.json()["error"]["message"])

    # ---- 裁决 -----------------------------------------------------------
    def test_reviewer_cannot_review_own_submission(self) -> None:
        """禁止自审——整个审核体系的地基。"""
        owner = self._register("self_reviewer", role="expert")
        self._login("self_reviewer")
        _, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )

        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "SELF_REVIEW_FORBIDDEN")
        # 状态没被改动
        self.assertEqual(self._status_of("template_versions", version_id), "submitted")

    def test_approve_publishes_and_moves_pointer(self) -> None:
        owner = self._register("appr_owner")
        self._register("appr_expert", role="expert")
        template_id, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )

        self._logout()
        self._login("appr_expert")
        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        review = response.json()
        self.assertEqual(review["decision"], "approve")
        self.assertEqual(review["targetRevision"], 1)

        self.assertEqual(self._status_of("template_versions", version_id), "published")
        self.assertEqual(
            self._column("templates", template_id, "published_version_id"), version_id
        )
        self.assertEqual(self._status_of("templates", template_id), "published")
        self.assertIsNotNone(self._column("template_versions", version_id, "published_at"))

    def test_reject_requires_reason(self) -> None:
        owner = self._register("rej_owner")
        self._register("rej_expert", role="expert")
        _, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )

        self._logout()
        self._login("rej_expert")
        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "reject",
                "reason": "   ",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_reject_keeps_previous_published_version(self) -> None:
        """待审新版本被驳回，公开指针不能动。"""
        owner = self._register("keep_owner")
        self._register("keep_expert", role="expert")
        template_id, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )
        # 先把容器指针指到一个"旧发布版"的占位 ID
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE templates SET published_version_id = ?, status = 'published'"
            " WHERE id = ?",
            ("tv-old-published", template_id),
        )
        connection.commit()
        connection.close()

        self._logout()
        self._login("keep_expert")
        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "reject",
                "reason": "构图太乱",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self._status_of("template_versions", version_id), "rejected")
        self.assertEqual(
            self._column("templates", template_id, "published_version_id"),
            "tv-old-published",
            "驳回不该动公开指针",
        )
        # 驳回理由留在版本上，作者能看到
        last_review = json.loads(
            self._column("template_versions", version_id, "last_review_json")
        )
        self.assertEqual(last_review["reason"], "构图太乱")
        self.assertEqual(last_review["decision"], "reject")

    def test_published_cannot_be_rejected(self) -> None:
        """状态机的核心：published -> rejected 两个值都合法，但没有这条边。"""
        owner = self._register("pub_owner")
        self._register("pub_expert", role="expert")
        _, version_id = self._seed_template_version(
            owner, "published", submitted_by=owner
        )

        self._logout()
        self._login("pub_expert")
        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "reject",
                "reason": "反悔了",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_STATE")
        self.assertEqual(self._status_of("template_versions", version_id), "published")

    def test_non_expert_cannot_review(self) -> None:
        owner = self._register("norev_owner")
        self._register("norev_dev", role="developer")
        _, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )

        self._logout()
        self._login("norev_dev")
        response = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "template_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 403, response.text)

    # ---- 归档 -----------------------------------------------------------
    def test_archive_published_clears_pointer(self) -> None:
        owner = self._register("arc_owner")
        self._login("arc_owner")
        template_id, version_id = self._seed_template_version(owner, "published")
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE templates SET published_version_id = ?, status = 'published'"
            " WHERE id = ?",
            (version_id, template_id),
        )
        connection.commit()
        connection.close()

        response = self.client.post(
            f"{API}/template-versions/{version_id}/archive",
            json={"expectedRevision": 1, "reason": "被新版本替代"},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "archived")
        self.assertIsNone(
            self._column("templates", template_id, "published_version_id"),
            "归档必须清掉公开指针",
        )
        self.assertEqual(self._status_of("templates", template_id), "archived")

    def test_archive_draft_is_rejected(self) -> None:
        owner = self._register("arc_draft")
        self._login("arc_draft")
        _, version_id = self._seed_template_version(owner, "draft")

        response = self.client.post(
            f"{API}/template-versions/{version_id}/archive",
            json={"expectedRevision": 1, "reason": "试试"},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_STATE")

    def test_archive_requires_reason(self) -> None:
        owner = self._register("arc_reason")
        self._login("arc_reason")
        _, version_id = self._seed_template_version(owner, "published")

        response = self.client.post(
            f"{API}/template-versions/{version_id}/archive",
            json={"expectedRevision": 1, "reason": ""},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 422, response.text)

    # ---- 退回后修订（BE-TEMPLATE-EDIT-001）-------------------------------
    def test_edit_rejected_version_in_place(self) -> None:
        """退回的版本能就地改，改完直接再送审。

        没有这条接口时前端只能 405，作者只能新建一个版本冒充修订——
        版本号、变更说明、审核记录全部错位。
        """
        owner = self._register("edit_owner")
        csrf = self._login("edit_owner")
        _template_id, version_id = self._seed_template_version(owner, status="rejected")

        patched = self.client.patch(
            f"{API}/template-versions/{version_id}",
            json={"expectedRevision": 1, "changeNote": "调整中心半径后重交"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        body = patched.json()
        self.assertEqual(body["changeNote"], "调整中心半径后重交")
        self.assertEqual(body["revision"], 2, "改内容必须推进 revision")
        self.assertEqual(body["versionNumber"], 1, "就地修订不产生新版本号")
        self.assertEqual(body["status"], "rejected", "改内容不该顺手改状态")

        # 修订后的 revision 就是送审要带的那一个
        submitted = self.client.post(
            f"{API}/template-versions/{version_id}/submit",
            json={"expectedRevision": 2},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["status"], "submitted")

    def test_cannot_edit_version_under_review_or_published(self) -> None:
        """submitted 已进队列、published 是别人引用过的历史，都不能改。"""
        owner = self._register("edit_locked")
        csrf = self._login("edit_locked")
        for status in ("submitted", "published"):
            _template_id, version_id = self._seed_template_version(owner, status=status)
            response = self.client.patch(
                f"{API}/template-versions/{version_id}",
                json={"expectedRevision": 1, "changeNote": "偷改一句"},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(response.status_code, 409, f"{status}: {response.text}")
            self.assertEqual(response.json()["error"]["code"], "INVALID_STATE")

    def test_edit_version_rejects_stale_revision(self) -> None:
        owner = self._register("edit_stale")
        csrf = self._login("edit_stale")
        _template_id, version_id = self._seed_template_version(owner, status="draft")

        first = self.client.patch(
            f"{API}/template-versions/{version_id}",
            json={"expectedRevision": 1, "changeNote": "第一次"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(first.status_code, 200, first.text)

        stale = self.client.patch(
            f"{API}/template-versions/{version_id}",
            json={"expectedRevision": 1, "changeNote": "第二次"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")

    def test_edit_version_revalidates_definition(self) -> None:
        """绑定指向不存在的节点 = 定义不自洽，必须在写库前 422。"""
        owner = self._register("edit_bad")
        csrf = self._login("edit_bad")
        _template_id, version_id = self._seed_template_version(owner, status="draft")

        response = self.client.patch(
            f"{API}/template-versions/{version_id}",
            json={
                "expectedRevision": 1,
                "parameterSchema": [
                    {
                        "name": "r",
                        "label": "半径",
                        "description": "",
                        "type": "number",
                        "default": 10,
                        "min": 1,
                        "max": 100,
                        "step": 1,
                    }
                ],
                "bindings": [
                    {"parameter": "r", "nodeId": "nope", "property": "geometry.r"}
                ],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_other_user_cannot_edit_version(self) -> None:
        owner = self._register("edit_a")
        _template_id, version_id = self._seed_template_version(owner, status="draft")
        self._logout()
        self._register("edit_b")
        csrf = self._login("edit_b")

        response = self.client.patch(
            f"{API}/template-versions/{version_id}",
            json={"expectedRevision": 1, "changeNote": "改别人的"},
            headers={"X-CSRF-Token": csrf},
        )
        # 404 而不是 403：403 等于承认这个版本 id 真实存在
        self.assertEqual(response.status_code, 404, response.text)

    # ---- 作品送审 -------------------------------------------------------
    def test_work_version_submit(self) -> None:
        owner = self._register("wv_owner", role="creator")
        self._login("wv_owner")
        _, version_id, _ = self._seed_work_version(owner)

        response = self.client.post(
            f"{API}/work-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "pending_review")

    def test_unpublished_source_work_cannot_be_submitted(self) -> None:
        """派生自未发布模板的作品不得公开送审（契约 5.1）。"""
        owner = self._register("wv_derived", role="creator")
        self._login("wv_derived")
        _, version_id, _ = self._seed_work_version(owner, unpublished_source=1)

        response = self.client.post(
            f"{API}/work-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNPUBLISHED_SOURCE")
        self.assertEqual(
            self._status_of("work_versions", version_id), "private", "状态不该变"
        )

    def test_pending_work_does_not_replace_public_version(self) -> None:
        """待审期间公开页继续指向旧发布版。"""
        owner = self._register("wv_public", role="creator")
        self._login("wv_public")
        work_id, version_id, _ = self._seed_work_version(owner)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE works SET published_version_id = ?, display_version_id = ?,"
            " latest_version_id = ? WHERE id = ?",
            ("wv-old", "wv-old", "wv-old", work_id),
        )
        connection.commit()
        connection.close()

        response = self.client.post(
            f"{API}/work-versions/{version_id}/submit",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self._column("works", work_id, "published_version_id"),
            "wv-old",
            "送审不能替换公开指针",
        )

    # ---- 队列 -----------------------------------------------------------
    def test_queue_marks_own_submission_as_not_reviewable(self) -> None:
        owner = self._register("q_owner", role="expert")
        self._login("q_owner")
        _, version_id = self._seed_template_version(
            owner, "submitted", submitted_by=owner
        )

        response = self.client.get(f"{API}/reviews?status=pending")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        target = next((i for i in items if i["targetId"] == version_id), None)
        self.assertIsNotNone(target, f"队列里应包含 {version_id}：{items}")
        self.assertFalse(target["canReview"], "自己的提交不该显示为可审")

    def test_queue_requires_review_permission(self) -> None:
        self._register("q_creator", role="creator")
        self._login("q_creator")
        response = self.client.get(f"{API}/reviews")
        self.assertEqual(response.status_code, 403, response.text)

    # ---- 评审 2026-11-10（PR #11 五条）----------------------------------
    def _add_artifact(
        self, generation_id: str, owner_id: str, rgb: tuple[int, int, int] = (200, 120, 90)
    ) -> str:
        """真落一张 PNG 并登记，否则读取时是"文件已丢失"的 404，测不出授权。

        用 struct+zlib 手写 PNG，不依赖 Pillow——测试不该要求产品依赖清单里
        没有的库。
        """
        import struct
        import zlib

        from app.services import uploads

        width, height = 8, 6
        raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )
        stored = uploads.store_frame(self.settings, owner_id, png, 0)

        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        job_id = connection.execute(
            "SELECT id FROM render_jobs WHERE generation_id = ?", (generation_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO artifacts (id, owner_id, kind, content_type, byte_size,"
            " storage_path, sha256, width, height, time_ms, render_job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (stored.artifact_id, owner_id, stored.kind, stored.content_type,
             stored.byte_size, stored.storage_path, stored.sha256,
             stored.width, stored.height, 0, job_id, "2026-11-09T10:00:00+00:00"),
        )
        connection.commit()
        connection.close()
        return stored.artifact_id

    def test_reviewer_can_read_pending_version(self) -> None:
        """待审版本要能被审核人读到，否则审核台点不动。

        PR #11 [P1]：队列给非提交者专家返回 canReview=true，但
        can_read_version 只放行本人/admin/published，专家 GET 版本和 PNG 都 404。
        """
        owner_id = self._register("rev_owner")
        work_id, version_id, generation_id = self._seed_work_version(
            owner_id, status="pending_review"
        )
        artifact_id = self._add_artifact(generation_id, owner_id)
        self._logout()

        self._register("rev_expert", role="expert")
        self._login("rev_expert")  # 角色变更要重新登录才会进会话
        got = self.client.get(f"{API}/work-versions/{version_id}")
        self.assertEqual(got.status_code, 200, got.text)
        png = self.client.get(f"{API}/artifacts/{artifact_id}")
        self.assertEqual(png.status_code, 200, f"审核人打不开要审的图：{png.text[:120]}")

    def test_artifact_visibility_follows_work_publication(self) -> None:
        """发布后匿名可读产物；别人的私有作品仍 404。

        PR #11 [P2]：作品详情匿名 200，但同一作品的 PNG 只有作者能取，
        公共列表就是一片破图。
        """
        owner_id = self._register("art_owner")
        public_work, public_version, public_gen = self._seed_work_version(
            owner_id, status="published"
        )
        private_work, private_version, private_gen = self._seed_work_version(
            owner_id, status="private"
        )
        public_artifact = self._add_artifact(public_gen, owner_id)
        private_artifact = self._add_artifact(private_gen, owner_id)
        self._logout()

        anonymous = self.client.get(f"{API}/artifacts/{public_artifact}")
        self.assertEqual(anonymous.status_code, 200, anonymous.text)
        hidden = self.client.get(f"{API}/artifacts/{private_artifact}")
        self.assertEqual(hidden.status_code, 404, "私有作品的图不能匿名读")

    def test_anonymous_work_detail_points_at_published_version(self) -> None:
        """有更新的私有版本时，公共详情与缩略图都必须来自发布版（契约 5.1）。

        BE-WORK-PUBLIC-POINTER-001：``display`` 以前直接取容器指针，作者一建
        v2 它就指向私有版本，公共列表照这个 id 打开直接 404。缩略图是同源的
        连带缺陷——``create_version`` 一建版就把 ``thumbnail_artifact_id``
        换成新版本第一帧，匿名取图同样 404，而这一点他没提。
        """
        owner_id = self._register("ptr_owner", role="creator")
        work_id, v1, gen1 = self._seed_work_version(owner_id, status="published")
        public_artifact = self._add_artifact(gen1, owner_id, rgb=(10, 200, 10))
        v2, gen2 = self._seed_extra_version(
            work_id, owner_id, 2, status="pending_review"
        )
        private_artifact = self._add_artifact(gen2, owner_id, rgb=(200, 10, 10))
        # 作者建完 v2 后容器的真实状态：最新/展示/缩略图全都跟着新版本走
        self._point_work_at(
            work_id, published=v1, latest=v2, display=v2, thumbnail=private_artifact
        )
        self._logout()

        anonymous = self.client.get(f"{API}/works/{work_id}")
        self.assertEqual(anonymous.status_code, 200, anonymous.text)
        body = anonymous.json()
        self.assertEqual(body["publishedVersionId"], v1)
        self.assertEqual(body["displayVersionId"], v1, "公共详情必须指向发布版")
        self.assertEqual(body["latestVersionId"], v2, "最新版本仍如实返回")
        self.assertEqual(
            body["thumbnailArtifactId"], public_artifact, "缩略图必须来自发布版的渲染"
        )
        # display 是「按这个 id 就能打开」的承诺，两头都要成立
        self.assertEqual(
            self.client.get(f"{API}/work-versions/{body['displayVersionId']}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f"{API}/work-versions/{v2}").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"{API}/artifacts/{private_artifact}").status_code, 404
        )
        listed = self.client.get(f"{API}/works?scope=public").json()
        self.assertEqual([item["id"] for item in listed["items"]], [work_id])
        self.assertEqual(listed["items"][0]["displayVersionId"], v1)
        self.assertEqual(listed["items"][0]["thumbnailArtifactId"], public_artifact)

        # 作者与管理员的视图不受影响，仍看最新版本与自己的缩略图
        self._login("ptr_owner")
        mine = self.client.get(f"{API}/works/{work_id}").json()
        self.assertEqual(mine["displayVersionId"], v2)
        self.assertEqual(mine["thumbnailArtifactId"], private_artifact)

    def test_favorite_state_is_read_from_server(self) -> None:
        """收藏必须由关系表说话（BE-FAVORITES-READ-001）。

        以前 ``build_work`` 把 ``isFavorite`` 写死成 false、``list_works`` 的
        favorites 分支直接返回空页，于是「PUT 成功、刷新前显示已收藏、刷新后
        又变回未收藏」，收藏列表永远是空的。
        """
        owner_id = self._register("fav_owner", role="creator")
        work_id, v1, _gen = self._seed_work_version(owner_id, status="published")
        self._point_work_at(work_id, published=v1, latest=v1, display=v1)
        self._logout()

        self._register("fav_reader", role="creator")
        self._login("fav_reader")
        added = self.client.put(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": self._csrf()}
        )
        self.assertEqual(added.status_code, 200, added.text)
        self.assertTrue(added.json()["isFavorite"])
        detail = self.client.get(f"{API}/works/{work_id}").json()
        self.assertTrue(detail["isFavorite"], "写入后详情必须读到 true")
        listed = self.client.get(f"{API}/works?scope=favorites").json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual([item["id"] for item in listed["items"]], [work_id])

        # 收藏是账号级数据，别人看不到
        self._logout()
        self._register("fav_other", role="creator")
        self._login("fav_other")
        self.assertFalse(self.client.get(f"{API}/works/{work_id}").json()["isFavorite"])
        self.assertEqual(
            self.client.get(f"{API}/works?scope=favorites").json()["total"], 0
        )

        # 作品被下线后从收藏列表消失，但关系保留——恢复上线应重新出现
        self._point_work_at(work_id, published=None, latest=v1, display=v1)
        self._login("fav_reader")
        self.assertEqual(
            self.client.get(f"{API}/works?scope=favorites").json()["total"], 0,
            "读不到的作品不该留在收藏列表里",
        )
        self._point_work_at(work_id, published=v1, latest=v1, display=v1)
        self.assertEqual(
            self.client.get(f"{API}/works?scope=favorites").json()["total"], 1,
            "关系不该被列表过滤顺手删掉",
        )

        removed = self.client.delete(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": self._csrf()}
        )
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertFalse(self.client.get(f"{API}/works/{work_id}").json()["isFavorite"])
        self.assertEqual(
            self.client.get(f"{API}/works?scope=favorites").json()["total"], 0
        )

    def test_archive_keeps_change_note_readable(self) -> None:
        """归档理由不能把 change_note 撑过 500 字上限。

        PR #11 [P2]：原文合法 500 字 + 理由合法，拼成 514 字后 commit 成功
        但响应模型校验失败，之后每次读这个版本都 500。
        """
        owner_id = self._register("arch_owner", role="admin")
        csrf = self._login("arch_owner")
        long_note = "说" * 500
        _template_id, version_id = self._seed_template_version(owner_id, status="published")
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE template_versions SET change_note = ? WHERE id = ?",
            (long_note, version_id),
        )
        connection.commit()
        connection.close()

        archived = self.client.post(
            f"{API}/template-versions/{version_id}/archive",
            json={"expectedRevision": 1, "reason": "内容过时，已归档"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(archived.status_code, 200, archived.text)

        again = self.client.get(f"{API}/template-versions/{version_id}")
        self.assertEqual(again.status_code, 200, "归档后必须还能读出来")
        self.assertLessEqual(len(again.json()["changeNote"]), 500)
        self.assertTrue(again.json()["changeNote"].startswith("说" * 100), "作者原文要保住")

    def test_concurrent_opposite_decisions_only_one_wins(self) -> None:
        """并发提交 approve 与 reject，只能有一个成功。

        PR #11 [P1]：`_check_revision` 与最终写入之间没有原子保护，两个请求
        都用 expectedRevision=1，都通过检查、都写成功——留下两条互相矛盾的
        审核记录，而版本状态只保留后写的那条。
        """
        import threading

        from sqlalchemy import event

        from app.db import create_database, create_session_factory
        from app.errors import ApiError
        from app.models.users import User as UserModel
        from app.schemas.reviews import ReviewCreate
        from app.services import reviews as reviews_service

        owner_id = self._register("cas_owner", role="expert")
        template_id, version_id = self._seed_template_version(
            owner_id, status="submitted", submitted_by=owner_id
        )
        reviewer_id = self._register("cas_reviewer", role="expert")
        self._logout()

        gate = threading.Barrier(2, timeout=5)
        results: list[tuple] = []

        def worker(decision: str) -> None:
            engine = create_database(self.settings)

            @event.listens_for(engine, "before_cursor_execute")
            def _meet(conn, cursor, statement, params, context, executemany):
                lowered = statement.lower()
                if "from template_versions" in lowered and "where" in lowered:
                    try:
                        gate.wait()
                    except threading.BrokenBarrierError:
                        pass

            factory = create_session_factory(engine)
            try:
                with factory() as db:
                    reviewer = db.get(UserModel, reviewer_id)
                    payload = ReviewCreate(
                        targetType="template_version",
                        targetId=version_id,
                        expectedRevision=1,
                        decision=decision,
                        reason="" if decision == "approve" else "不符合要求",
                    )
                    try:
                        reviews_service.decide_review(db, reviewer, payload)
                        results.append(("ok", decision))
                    except ApiError as exc:
                        results.append((exc.status_code, exc.code))
            finally:
                engine.dispose()

        threads = [threading.Thread(target=worker, args=(d,))
                   for d in ("approve", "reject")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        ok = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == 409]
        self.assertEqual(len(results), 2, results)
        self.assertEqual(len(ok), 1, f"只能有一个裁决生效：{results}")
        self.assertEqual(len(conflicts), 1, f"另一个必须 409：{results}")

        connection = sqlite3.connect(self.settings.database_path)
        reviews = connection.execute(
            "SELECT COUNT(*) FROM reviews WHERE target_id = ?", (version_id,)
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status, revision FROM template_versions WHERE id = ?", (version_id,)
        ).fetchone()
        connection.close()
        self.assertEqual(reviews, 1, "只该留下一条审核记录")
        self.assertEqual(status[1], 2, "revision 只加 1")
        self.assertIn(status[0], ("published", "rejected"))

    def test_approve_old_version_keeps_latest_pointer(self) -> None:
        """批准旧版本不该把 latest_version_id 拉回去。

        PR #11 [P2]：v1 送审期间作者建了 v2 草稿，批准 v1 时把 latest 改回
        v1，v2 就从"最新"里消失了。
        """
        owner_id = self._register("latest_owner", role="expert")
        template_id, v1 = self._seed_template_version(
            owner_id, status="submitted", submitted_by=owner_id
        )
        reviewer_id = self._register("latest_reviewer", role="expert")
        self._logout()

        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "INSERT INTO template_versions (id, template_id, version_number, created_by,"
            " status, metadata_json, ir_template_json, parameter_schema_json,"
            " bindings_json, constraints_json, supported_backends_json,"
            " ontology_version, change_note, revision, created_at, updated_at)"
            " SELECT ?, template_id, 2, created_by, 'draft', metadata_json,"
            " ir_template_json, parameter_schema_json, bindings_json, constraints_json,"
            " supported_backends_json, ontology_version, 'v2 草稿', 1, created_at, updated_at"
            " FROM template_versions WHERE id = ?",
            ("tv-latest-v2", v1),
        )
        connection.execute(
            "UPDATE templates SET latest_version_id = 'tv-latest-v2' WHERE id = ?",
            (template_id,),
        )
        connection.commit()
        connection.close()

        self._login("latest_reviewer")
        decided = self.client.post(
            f"{API}/reviews",
            json={"targetType": "template_version", "targetId": v1,
                  "expectedRevision": 1, "decision": "approve", "reason": ""},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(decided.status_code, 201, decided.text)

        connection = sqlite3.connect(self.settings.database_path)
        published, latest = connection.execute(
            "SELECT published_version_id, latest_version_id FROM templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        connection.close()
        self.assertEqual(published, v1)
        self.assertEqual(latest, "tv-latest-v2", "最新版本指针不该被批准动作拉回去")

    def test_artifact_isolated_between_jobs_of_same_generation(self) -> None:
        """同一快照的两次渲染要各自授权，不能因为一个版本公开就放出另一个。

        评审 [P1]：`can_read_artifact` 原本按 generation 判可见性，于是作者把
        J1 的结果发布后，同一 generation 下 J2（私有作品用的那份）的产物也
        变成匿名可读——响应字节就是私有 PNG。
        """
        owner_id = self._register("iso_owner")
        public_work, public_version, generation_id = self._seed_work_version(
            owner_id, status="published"
        )
        public_artifact = self._add_artifact(generation_id, owner_id, rgb=(200, 120, 90))

        # 同一个 generation 下再造一个任务，挂在私有作品上
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-iso-private", owner_id, generation_id, "canvas2d", "browser_upload",
             "succeeded", "completed", "[0]", 1, "test-1.0",
             '{"userAgent":"t","deviceScaleFactor":1}', 1,
             "2026-11-09T10:00:00+00:00", "2026-11-09T10:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, revision,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("wrk-iso-private", owner_id, "私有作品", "", 1,
             "2026-11-09T10:00:00+00:00", "2026-11-09T10:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("wv-iso-private", "wrk-iso-private", 1, owner_id, "private", "私有作品", "",
             generation_id, "job-iso-private", "", None, None, 1,
             "2026-11-09T10:00:00+00:00", "2026-11-09T10:00:00+00:00"),
        )
        connection.commit()
        connection.close()

        # 私有那份的产物
        private_artifact = self._add_artifact_for_job("job-iso-private", owner_id, (10, 200, 10))

        self._logout()
        self.assertEqual(
            self.client.get(f"{API}/artifacts/{public_artifact}").status_code, 200,
            "公开作品的产物要匿名可读",
        )
        leaked = self.client.get(f"{API}/artifacts/{private_artifact}")
        self.assertEqual(
            leaked.status_code, 404,
            "同一快照下私有任务的产物不能因为别的版本公开就跟着公开",
        )

    def _add_artifact_for_job(
        self, job_id: str, owner_id: str, rgb: tuple[int, int, int]
    ) -> str:
        """给指定 job 落一张 PNG（_add_artifact 按 generation 找 job，这里要指定）。"""
        import struct
        import zlib

        from app.services import uploads

        width, height = 8, 6
        raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )
        stored = uploads.store_frame(self.settings, owner_id, png, 0)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "INSERT INTO artifacts (id, owner_id, kind, content_type, byte_size,"
            " storage_path, sha256, width, height, time_ms, render_job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (stored.artifact_id, owner_id, stored.kind, stored.content_type,
             stored.byte_size, stored.storage_path, stored.sha256,
             stored.width, stored.height, 0, job_id, "2026-11-09T10:00:00+00:00"),
        )
        connection.commit()
        connection.close()
        return stored.artifact_id

    # ---- #13 P2：批准旧版本不能吃掉新草稿的工作元数据 --------------------
    def test_approving_old_version_keeps_newer_draft_metadata(self) -> None:
        """批准旧版本时，作者给更新草稿改的标题不能被清掉。

        评审 #13 [P2]：原来「批准任何版本都清空 working_*」，于是作者给 v2
        改好的标题在审核人批准 v1 时被丢弃，而 latest 仍指向 v2，之后建 v3
        会回落到 v1 的旧标题，编辑无声丢失。
        """
        owner = self._register("meta_old_owner")
        self._login("meta_old_owner")
        work_id, v1_id, _generation = self._seed_work_version(
            owner, status="pending_review"
        )

        # 送审记录要指向真实提交人：reviews.submitted_by 有外键约束，
        # 种子数据默认留空会让裁决时的审核记录插不进去
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE work_versions SET submitted_by = ?, submitted_at = ? WHERE id = ?",
            (owner, "2026-11-11T00:00:00+00:00", v1_id),
        )

        # 造一个更新的草稿 v2，让 latest 指向它
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " SELECT ?, work_id, 2, created_by, 'private', title, description,"
            " generation_id, render_job_id, 'v2 草稿', NULL, NULL, 1,"
            " created_at, updated_at FROM work_versions WHERE id = ?",
            ("wv-meta-v2", v1_id),
        )
        connection.execute(
            "UPDATE works SET latest_version_id = 'wv-meta-v2' WHERE id = ?",
            (work_id,),
        )
        connection.commit()
        connection.close()

        # 作者给下一版改标题（此时作品还没公开，容器字段会一起同步）
        revision = self._column("works", work_id, "revision")
        patched = self.client.patch(
            f"{API}/works/{work_id}",
            json={"expectedRevision": revision, "title": "给 v3 准备的标题"},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(
            self._column("works", work_id, "working_title"), "给 v3 准备的标题"
        )

        # 审核人批准的是**旧**版本 v1
        self._register("meta_old_reviewer", role="expert")
        csrf = self._login("meta_old_reviewer")
        decided = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "work_version",
                "targetId": v1_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(decided.status_code, 201, decided.text)

        self.assertEqual(
            self._column("works", work_id, "working_title"), "给 v3 准备的标题",
            "批准旧版本把作者给新草稿改的标题清掉了",
        )
        # 公开元数据仍然来自被批准的那一版
        self.assertEqual(self._column("works", work_id, "title"), "作品")
        self.assertEqual(
            self._column("works", work_id, "published_version_id"), v1_id
        )

    # ---- #13 第三轮 P1：审核也要走 revision 协议 -------------------------
    def test_approval_advances_work_revision_and_rejects_stale_patch(self) -> None:
        """批准必须推进 Work.revision，否则旧读的 PATCH 能绕过审核写公开页。

        评审 #13 [P1] 的交错顺序：作者 PATCH 先读到 ``publishedVersionId=None``
        （据此决定同步容器标题）→ 另一会话批准首个版本 → PATCH 恢复执行。
        如果审核不推进 revision，PATCH 的 CAS 仍然成立，于是一份未经审核的
        标题被写进已经公开的容器，而公开版本的快照还是原批准标题。
        """
        owner = self._register("race_owner")
        self._login("race_owner")
        work_id, version_id, _generation = self._seed_work_version(
            owner, status="pending_review"
        )
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE work_versions SET submitted_by = ?, submitted_at = ? WHERE id = ?",
            (owner, "2026-11-11T00:00:00+00:00", version_id),
        )
        connection.commit()
        connection.close()

        # 作者这一侧读到的 revision（PATCH 就是拿它当 expectedRevision）
        stale_revision = self._column("works", work_id, "revision")

        self._register("race_reviewer", role="expert")
        csrf = self._login("race_reviewer")
        approved = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "work_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(approved.status_code, 201, approved.text)
        self.assertEqual(
            self._column("works", work_id, "revision"), stale_revision + 1,
            "审核改了 Work 却没推进 revision，作者那一侧的老 revision 依然有效",
        )

        # 作者带着审核前的 revision 提交 PATCH，必须被拒
        owner_csrf = self._login("race_owner")
        stale_patch = self.client.patch(
            f"{API}/works/{work_id}",
            json={"expectedRevision": stale_revision, "title": "未经审核的标题"},
            headers={"X-CSRF-Token": owner_csrf},
        )
        self.assertEqual(stale_patch.status_code, 409, stale_patch.text)
        self.assertEqual(stale_patch.json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(
            self._column("works", work_id, "title"), "作品",
            "公开标题必须还是被批准那一版，不能被旧读的 PATCH 改写",
        )

    # ---- #13 第三轮 P2：版本创建后才保存的工作元数据 ---------------------
    def test_metadata_saved_after_submit_reaches_next_version(self) -> None:
        """送审后改的标题要留给下一版，不能被「批准当前 latest」清掉。

        评审 #13 [P2]：``latest_version_id == row.id`` 不代表 ``working_*``
        已被这一版消费。作者建完并送审 v1 之后又改了标题（给下一版准备），
        批准 v1 时把它清空，随后建的 v2 只有旧标题、空描述。
        """
        owner = self._register("staged_owner")
        self._login("staged_owner")
        work_id, version_id, generation_id = self._seed_work_version(
            owner, status="pending_review"
        )
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE work_versions SET submitted_by = ?, submitted_at = ? WHERE id = ?",
            (owner, "2026-11-11T00:00:00+00:00", version_id),
        )
        job_id = connection.execute(
            "SELECT render_job_id FROM work_versions WHERE id = ?", (version_id,)
        ).fetchone()[0]
        connection.commit()
        connection.close()

        # 送审之后，作者给下一版改标题（此时作品还没公开，容器字段一起同步）
        csrf = self._csrf()
        staged = self.client.patch(
            f"{API}/works/{work_id}",
            json={
                "expectedRevision": self._column("works", work_id, "revision"),
                "title": "给下一版的标题",
                "description": "给下一版的说明",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(staged.status_code, 200, staged.text)

        self._register("staged_reviewer", role="expert")
        reviewer_csrf = self._login("staged_reviewer")
        approved = self.client.post(
            f"{API}/reviews",
            json={
                "targetType": "work_version",
                "targetId": version_id,
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "",
            },
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        self.assertEqual(approved.status_code, 201, approved.text)
        self.assertEqual(
            self._column("works", work_id, "working_title"), "给下一版的标题",
            "批准 v1 把作者送审后攒着的编辑清掉了",
        )
        self.assertEqual(self._column("works", work_id, "title"), "作品")

        # 再建 v2：必须继承作者攒下的标题与描述
        owner_csrf = self._login("staged_owner")
        created = self.client.post(
            f"{API}/works/{work_id}/versions",
            json={
                "expectedRevision": self._column("works", work_id, "revision"),
                "generationId": generation_id,
                "renderJobId": job_id,
                "changeNote": "下一版",
            },
            headers={"X-CSRF-Token": owner_csrf},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["version"]["title"], "给下一版的标题")
        self.assertEqual(created.json()["version"]["description"], "给下一版的说明")
        # 工作元数据是持续的默认值，不清空：清空会让再下一版回落到旧公开版
        # 的标题（评审 #13 第四轮 P2），这里明确锁住"建完版本后它还在"
        self.assertEqual(
            self._column("works", work_id, "working_title"), "给下一版的标题"
        )
