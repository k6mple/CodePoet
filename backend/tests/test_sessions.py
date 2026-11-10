"""P1A-05 会话/快照/收藏集成测试。

真实 SQLite 跑全量迁移，不 mock 服务层——这里要验的核心是
「保存真的产生快照」「恢复真的留下退路」，都是数据库层面的事实。

两个重点：

* **恢复要能反悔**：恢复前必须自动存一份，否则点错就丢失现场。
* **收藏幂等**：重复 PUT 不报错、重复 DELETE 不报错。前端重试是常态，
  第二次返回 500 会让用户以为操作失败而其实第一次已经成功。
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


def editor_state(**overrides) -> dict:
    """契约里 EditorState 的 11 个字段全必填。"""
    base = {
        "mode": "template",
        "templateVersionId": None,
        "params": {},
        "semanticInput": {"text": "", "entityIds": []},
        "draftIR": None,
        "fragmentSource": "",
        "backend": "canvas2d",
        "seed": 42,
        "timelineTimeMs": 0,
        "selectedNodeId": None,
        "generationId": None,
    }
    base.update(overrides)
    return base


class SessionFlowTests(unittest.TestCase):
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

    def _register(self, name: str, role: str = "creator") -> str:
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
        self.client.post(
            f"{API}/auth/logout", headers={"X-CSRF-Token": self._csrf()}
        )

    def _unique(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _create_session(self, csrf: str, name: str = "会话") -> dict:
        response = self.client.post(
            f"{API}/sessions",
            json={"name": name, "state": editor_state()},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _seed_work(self, owner_id: str, *, published: bool = False) -> str:
        """造一个作品（含 generation 与完成的渲染任务）。"""
        work_id = self._unique("wrk")
        gen_id = self._unique("gen")
        job_id = self._unique("job")
        now = "2026-11-10T00:00:00+00:00"
        ir = json.dumps(
            {
                "schemaVersion": "1.0",
                "seed": 1,
                "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
                "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
                "durationMs": 0,
                "nodes": [
                    {
                        "id": "c1",
                        "parentId": None,
                        "type": "circle",
                        "geometry": {"cx": 400, "cy": 300, "r": 50},
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
                gen_id, owner_id, "ir", "canvas2d", None, "{}", 1, ir, "d" * 64,
                provenance,
                '{"parameterValid":true,"nodeCount":1,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", 0, "req-t", now,
            ),
        )
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, owner_id, gen_id, "canvas2d", "browser_upload", "succeeded",
                "completed", "[0]", 1, "t-1.0",
                '{"userAgent":"t","deviceScaleFactor":1}', 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, published_version_id,"
            " revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                work_id, owner_id, "作品", "",
                f"{work_id}-pub" if published else None, 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"{work_id}-v1", work_id, 1, owner_id,
                "published" if published else "private",
                "作品", "", gen_id, job_id, "", None, None, 1, now, now,
            ),
        )
        connection.commit()
        connection.close()
        return work_id

    # ---- 会话 -----------------------------------------------------------
    def test_create_session_adds_first_snapshot(self) -> None:
        owner = self._register("ses_owner")
        csrf = self._login("ses_owner")
        session = self._create_session(csrf, "我的草稿")

        self.assertEqual(session["revision"], 1)
        self.assertEqual(session["name"], "我的草稿")
        self.assertEqual(session["ownerId"], owner)
        self.assertIsNotNone(session["latestSnapshotId"])

        snapshots = self.client.get(f"{API}/sessions/{session['id']}/snapshots").json()
        self.assertEqual(snapshots["total"], 1, snapshots)
        first = snapshots["items"][0]
        self.assertEqual(first["id"], session["latestSnapshotId"])
        self.assertEqual(first["sessionRevision"], 1)
        self.assertEqual(first["note"], "创建会话")

    def test_save_appends_snapshot_and_bumps_revision_once(self) -> None:
        """契约 5.2：显式保存同事务追加快照，revision 只加 1。"""
        self._register("ses_save")
        csrf = self._login("ses_save")
        session = self._create_session(csrf)

        response = self.client.patch(
            f"{API}/sessions/{session['id']}",
            json={
                "expectedRevision": 1,
                "name": "改过的名字",
                "state": editor_state(seed=123, timelineTimeMs=800),
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["name"], "改过的名字")
        self.assertEqual(updated["state"]["seed"], 123)
        self.assertNotEqual(updated["latestSnapshotId"], session["latestSnapshotId"])

        snapshots = self.client.get(f"{API}/sessions/{session['id']}/snapshots").json()
        self.assertEqual(snapshots["total"], 2, snapshots)
        # revision 降序：最新那条 sessionRevision = 2
        self.assertEqual(snapshots["items"][0]["sessionRevision"], 2)
        self.assertEqual(snapshots["items"][0]["state"]["seed"], 123)

    def test_patch_only_name_keeps_state(self) -> None:
        """PATCH 语义：省略的字段保持原值。"""
        self._register("ses_partial")
        csrf = self._login("ses_partial")
        session = self._create_session(csrf, "原名")
        # 先把 state 改成有特征的值
        self.client.patch(
            f"{API}/sessions/{session['id']}",
            json={"expectedRevision": 1, "state": editor_state(seed=999)},
            headers={"X-CSRF-Token": csrf},
        )

        response = self.client.patch(
            f"{API}/sessions/{session['id']}",
            json={"expectedRevision": 2, "name": "只改名字"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "只改名字")
        self.assertEqual(response.json()["state"]["seed"], 999, "state 不该被清掉")

    def test_save_with_stale_revision_conflicts(self) -> None:
        self._register("ses_stale")
        csrf = self._login("ses_stale")
        session = self._create_session(csrf)
        response = self.client.patch(
            f"{API}/sessions/{session['id']}",
            json={"expectedRevision": 99, "name": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_other_user_cannot_read_session(self) -> None:
        self._register("ses_real_owner")
        self._register("ses_intruder")
        csrf = self._login("ses_real_owner")
        session = self._create_session(csrf)

        self._logout()
        self._login("ses_intruder")
        self.assertEqual(
            self.client.get(f"{API}/sessions/{session['id']}").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"{API}/sessions/{session['id']}/snapshots").status_code, 404
        )

    def test_session_list_is_scoped_to_owner(self) -> None:
        self._register("ses_a")
        csrf_a = self._login("ses_a")
        self._create_session(csrf_a, "A 的会话")

        self._logout()
        self._register("ses_b")
        csrf_b = self._login("ses_b")
        self._create_session(csrf_b, "B 的会话")

        listed = self.client.get(f"{API}/sessions").json()
        self.assertEqual(listed["total"], 1, listed)
        self.assertEqual(listed["items"][0]["name"], "B 的会话")

    # ---- 恢复 -----------------------------------------------------------
    def test_restore_creates_safety_snapshot_and_restored_one(self) -> None:
        """恢复要留退路：恢复前一份 + 恢复后一份，revision 只加 1。"""
        self._register("ses_restore")
        csrf = self._login("ses_restore")
        session = self._create_session(csrf, "初始")
        first_snapshot = session["latestSnapshotId"]

        # 再保存一次，现场变成 seed=777
        self.client.patch(
            f"{API}/sessions/{session['id']}",
            json={"expectedRevision": 1, "name": "改过", "state": editor_state(seed=777)},
            headers={"X-CSRF-Token": csrf},
        )

        response = self.client.post(
            f"{API}/sessions/{session['id']}/restore",
            json={"expectedRevision": 2, "snapshotId": first_snapshot},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["session"]["revision"], 3, "revision 只加 1")
        self.assertEqual(body["session"]["state"]["seed"], 42, "现场应回到初始 seed")
        self.assertEqual(body["snapshot"]["restoredFromSnapshotId"], first_snapshot)
        self.assertIn(first_snapshot, body["snapshot"]["note"])

        snapshots = self.client.get(
            f"{API}/sessions/{session['id']}/snapshots?pageSize=50"
        ).json()
        # 创建 1 + 保存 1 + 恢复前 1 + 恢复后 1 = 4
        self.assertEqual(snapshots["total"], 4, snapshots)
        notes = [item["note"] for item in snapshots["items"]]
        self.assertIn("恢复前自动保存", notes, notes)
        self.assertTrue(any("恢复" in n and first_snapshot in n for n in notes), notes)

    def test_restore_with_foreign_snapshot_is_404(self) -> None:
        self._register("ses_own_a")
        csrf_a = self._login("ses_own_a")
        session_a = self._create_session(csrf_a)

        self._logout()
        self._register("ses_own_b")
        csrf_b = self._login("ses_own_b")
        session_b = self._create_session(csrf_b)

        # 拿 A 的快照去恢复 B 的会话
        response = self.client.post(
            f"{API}/sessions/{session_b['id']}/restore",
            json={"expectedRevision": 1, "snapshotId": session_a["latestSnapshotId"]},
            headers={"X-CSRF-Token": csrf_b},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_snapshot_is_immutable(self) -> None:
        """快照写入后连数据库层面都改不了。"""
        self._register("ses_immutable")
        csrf = self._login("ses_immutable")
        session = self._create_session(csrf)
        connection = sqlite3.connect(self.settings.database_path)
        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE session_snapshots SET note = ? WHERE id = ?",
                ("偷偷改", session["latestSnapshotId"]),
            )
        connection.close()

    # ---- EditorState 校验 ----------------------------------------------
    def test_params_over_limit_is_rejected(self) -> None:
        self._register("ses_params")
        csrf = self._login("ses_params")
        too_many = {f"p{i}": i for i in range(33)}
        response = self.client.post(
            f"{API}/sessions",
            json={"name": "x", "state": editor_state(params=too_many)},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_params_out_of_range_is_rejected(self) -> None:
        self._register("ses_range")
        csrf = self._login("ses_range")
        response = self.client.post(
            f"{API}/sessions",
            json={"name": "x", "state": editor_state(params={"big": 10**7})},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_semantic_input_requires_text(self) -> None:
        self._register("ses_semantic")
        csrf = self._login("ses_semantic")
        response = self.client.post(
            f"{API}/sessions",
            json={
                "name": "x",
                "state": editor_state(semanticInput={"entityIds": []}),
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_fragment_source_must_be_empty(self) -> None:
        self._register("ses_fragment")
        csrf = self._login("ses_fragment")
        response = self.client.post(
            f"{API}/sessions",
            json={"name": "x", "state": editor_state(fragmentSource="something")},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    # ---- 收藏 -----------------------------------------------------------
    def test_favorite_is_idempotent(self) -> None:
        owner = self._register("fav_owner")
        csrf = self._login("fav_owner")
        work_id = self._seed_work(owner, published=True)

        first = self.client.put(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["isFavorite"])

        second = self.client.put(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(second.status_code, 200, "重复收藏不该报错")
        self.assertTrue(second.json()["isFavorite"])

        connection = sqlite3.connect(self.settings.database_path)
        count = connection.execute(
            "SELECT COUNT(*) FROM work_favorites WHERE work_id = ?", (work_id,)
        ).fetchone()[0]
        connection.close()
        self.assertEqual(count, 1, "复合主键应保证只有一行")

    def test_unfavorite_is_idempotent(self) -> None:
        owner = self._register("unfav_owner")
        csrf = self._login("unfav_owner")
        work_id = self._seed_work(owner, published=True)

        self.client.put(f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf})
        first = self.client.delete(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(first.status_code, 204, first.text)
        second = self.client.delete(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(second.status_code, 204, "没收藏也返回 204")

    def test_cannot_favorite_invisible_work(self) -> None:
        owner = self._register("fav_private_owner")
        self._seed_work(owner, published=False)
        self._register("fav_stranger")
        csrf = self._login("fav_stranger")
        connection = sqlite3.connect(self.settings.database_path)
        work_id = connection.execute(
            "SELECT id FROM works WHERE published_version_id IS NULL LIMIT 1"
        ).fetchone()[0]
        connection.close()

        response = self.client.put(
            f"{API}/works/{work_id}/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_unfavorite_works_even_when_work_vanished(self) -> None:
        """作品没了也能清掉自己的收藏关系，否则那条记录永远删不掉。"""
        owner = self._register("fav_gone")
        csrf = self._login("fav_gone")
        response = self.client.delete(
            f"{API}/works/does-not-exist/favorite", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 204, response.text)
