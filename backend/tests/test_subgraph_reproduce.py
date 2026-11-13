"""P1B-02 子树编辑 + P1B-04 复现与像素比较测试。

两块的核心都不是"接口返回 200"，而是：

* 子树编辑要证明**只动了该动的那棵子树**——兄弟节点还在、后代全被删、
  新节点 ID 不撞车。
* 像素比较要证明**同一张图判一致、不同图判不同、不可比时给 null**。
  尤其最后一条：不可比却返回 mae=0，会被读成"完美复现"。
"""

from __future__ import annotations

import io
import json
import sqlite3
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.services import uploads
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


def make_rgba_png(rgba: tuple[int, int, int, int],
                  width: int = 800, height: int = 600) -> bytes:
    """构造纯色 RGBA PNG。alpha 差异必须能被比较接口看出来。"""
    image = Image.new("RGBA", (width, height), rgba)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_png(width: int = 800, height: int = 600,
             rgb: tuple[int, int, int] = (200, 120, 90)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def nested_ir() -> dict:
    """一棵有父子结构的 IR：g1 下挂两个圆，另有一个独立圆。"""
    def leaf(node_id: str, cx: int) -> dict:
        return {
            "id": node_id,
            "parentId": "g1",
            "type": "circle",
            "geometry": {"cx": cx, "cy": 300, "r": 60},
            "style": {"fill": "#E8A0A0", "stroke": "#8B3A3A",
                      "strokeWidth": 2, "opacity": 1},
        }

    return {
        "schemaVersion": "1.0",
        "seed": 7,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": [
            {"id": "g1", "parentId": None, "type": "group",
             "style": {"opacity": 1}},
            leaf("c1", 300),
            leaf("c2", 500),
            {"id": "c3", "parentId": None, "type": "circle",
             "geometry": {"cx": 100, "cy": 100, "r": 40},
             "style": {"fill": "#9FE1CB", "stroke": "#0F6E56",
                       "strokeWidth": 2, "opacity": 1}},
        ],
        "animations": [],
        "narrative": [],
    }


class SubgraphEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""
        self.user_id = ""

    def _register(self, name: str, role: str = "creator") -> None:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.user_id = response.json()["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, self.user_id))
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

    def _logout(self) -> None:
        self.client.post(
            f"{API}/auth/logout",
            headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]},
        )

    def _seed_generation(self, owner_id: str, gen_id: str = "gen-base") -> str:
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        provenance = json.dumps({
            "kind": "manual_edit", "baseGenerationId": None, "matchedEntities": [],
            "matchedRules": [], "selectedSymbols": [], "paletteDecision": None,
            "compositionDecision": None, "timelineDecision": None,
            "templateSelection": None, "conflicts": [], "warnings": [],
            "engineVersion": "test-1.0", "ontologyVersion": 1, "policyRevision": 1,
            "fragmentSource": None, "fragmentSourceHash": None,
        })
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gen_id, owner_id, "ir", "canvas2d", None, "{}", 7,
             json.dumps(nested_ir()), "f" * 64, provenance,
             '{"parameterValid":true,"nodeCount":4,"outOfBoundsNodeCount":0,"issues":[]}',
             "[]", 0, "req-t", NOW),
        )
        connection.commit()
        connection.close()
        return gen_id

    def _replacement(self, node_id: str = "n1", cx: int = 400) -> dict:
        return {
            "id": node_id,
            "parentId": None,
            "type": "circle",
            "geometry": {"cx": cx, "cy": 300, "r": 80},
            "style": {"fill": "#FAC775", "stroke": "#854F0B",
                      "strokeWidth": 3, "opacity": 1},
        }

    def _edit(self, gen_id: str, body: dict):
        return self.client.post(
            f"{API}/generations/{gen_id}/subgraph",
            json=body,
            headers={"X-CSRF-Token": self.csrf},
        )

    # ---- 用例 -----------------------------------------------------------
    def test_replaces_whole_subtree_and_keeps_siblings(self) -> None:
        self._register("sg_owner")
        self._seed_generation(self.user_id)

        response = self._edit("gen-base", {
            "rootNodeId": "g1",
            "replacementNodes": [self._replacement()],
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        # g1 与它的两个后代都被移除
        self.assertEqual(set(body["removedNodeIds"]), {"g1", "c1", "c2"})
        self.assertEqual(body["changedNodeIds"], ["n1"])

        nodes = body["generation"]["ir"]["nodes"]
        ids = {node["id"] for node in nodes}
        self.assertEqual(ids, {"c3", "n1"}, "只剩兄弟节点和新节点")
        self.assertEqual(body["generation"]["mode"], "subgraph")
        provenance = body["generation"]["provenance"]
        self.assertEqual(provenance["kind"], "subgraph_edit")
        self.assertEqual(provenance["baseGenerationId"], "gen-base")

    def test_empty_replacement_deletes_subtree(self) -> None:
        self._register("sg_delete")
        self._seed_generation(self.user_id)
        response = self._edit("gen-base", {"rootNodeId": "g1", "replacementNodes": []})
        self.assertEqual(response.status_code, 201, response.text)
        ids = {n["id"] for n in response.json()["generation"]["ir"]["nodes"]}
        self.assertEqual(ids, {"c3"})
        self.assertEqual(response.json()["changedNodeIds"], [])

    def test_unknown_root_is_rejected(self) -> None:
        self._register("sg_missing")
        self._seed_generation(self.user_id)
        response = self._edit("gen-base", {"rootNodeId": "nope", "replacementNodes": []})
        self.assertEqual(response.status_code, 422, response.text)

    def test_id_conflict_outside_subtree_is_rejected(self) -> None:
        """新节点撞到子树外的现有节点 → 422，且报错点出冲突 ID。"""
        self._register("sg_conflict")
        self._seed_generation(self.user_id)
        response = self._edit("gen-base", {
            "rootNodeId": "g1",
            "replacementNodes": [self._replacement(node_id="c3")],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("冲突", response.json()["error"]["message"])

    def test_duplicate_ids_in_replacement_is_rejected(self) -> None:
        self._register("sg_dup")
        self._seed_generation(self.user_id)
        response = self._edit("gen-base", {
            "rootNodeId": "g1",
            "replacementNodes": [self._replacement("n1"), self._replacement("n1")],
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_dangling_parent_after_edit_is_rejected(self) -> None:
        """替换后新节点指向已被删掉的目标，必须拒掉而不是产出坏 IR。"""
        self._register("sg_dangling")
        self._seed_generation(self.user_id)
        orphan = self._replacement("n1")
        orphan["parentId"] = "g1"  # g1 正好被这次编辑删除
        response = self._edit("gen-base", {
            "rootNodeId": "g1",
            "replacementNodes": [orphan],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "IR_INVALID")

    def test_viewport_can_be_overridden(self) -> None:
        self._register("sg_viewport")
        self._seed_generation(self.user_id)
        response = self._edit("gen-base", {
            "rootNodeId": "g1",
            "replacementNodes": [self._replacement()],
            "viewport": {"x": 100, "y": 50, "width": 400, "height": 300},
        })
        self.assertEqual(response.status_code, 201, response.text)
        viewport = response.json()["generation"]["ir"]["viewport"]
        self.assertEqual(viewport["width"], 400)
        self.assertEqual(viewport["x"], 100)

    def test_other_users_generation_is_404(self) -> None:
        self._register("sg_real")
        self._seed_generation(self.user_id)
        self._logout()
        self._register("sg_other")
        response = self._edit("gen-base", {"rootNodeId": "g1", "replacementNodes": []})
        self.assertEqual(response.status_code, 404, response.text)

    def test_capability_flag_matches_endpoint(self) -> None:
        """声明 subgraphEditing=true 就必须真能用（BE-SUBGRAPH-CAPABILITY-001）。

        以前接口做好了、能力开关还是 false：前端按声明尊重能力，页面把子树
        入口关掉了，等于白做。这条断言把「声明」和「行为」钉在一起。
        """
        capabilities = self.client.get(f"{API}/engine/capabilities")
        self.assertEqual(capabilities.status_code, 200, capabilities.text)
        self.assertTrue(
            capabilities.json()["features"]["subgraphEditing"],
            "接口已实现，能力声明不能还是 false",
        )


class ReproduceTests(unittest.TestCase):
    """P1B-04：复现任务 + 像素比较。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""
        self.user_id = ""

    def _register(self, name: str) -> None:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.user_id = response.json()["id"]
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        self.csrf = login.json()["csrfToken"]

    def _job_with_frame(self, job_id: str, gen_id: str, png: bytes,
                        *, renderer: str = "codepoet-canvas-1.0.0") -> str:
        """造一个已完成的渲染任务 + 一帧真实产物。"""
        stored = uploads.store_frame(self.settings, self.user_id, png, 0)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend, execution_mode,"
            " status, phase, times_ms_json, policy_revision, renderer_version,"
            " client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, self.user_id, gen_id, "canvas2d", "browser_upload", "succeeded",
             "completed", "[0]", 1, renderer,
             '{"userAgent":"test/1.0","deviceScaleFactor":1}', 1, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO artifacts (id, owner_id, kind, content_type, byte_size,"
            " storage_path, sha256, width, height, time_ms, render_job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (stored.artifact_id, self.user_id, stored.kind, stored.content_type,
             stored.byte_size, stored.storage_path, stored.sha256,
             stored.width, stored.height, stored.time_ms, job_id, NOW),
        )
        connection.commit()
        connection.close()
        return stored.artifact_id

    def _seed_generation(self, gen_id: str = "gen-r") -> None:
        """只插 generation。work_versions 依赖 render_jobs，得等 job 建好。"""
        ir = json.dumps(nested_ir())
        provenance = json.dumps({
            "kind": "manual_edit", "baseGenerationId": None, "matchedEntities": [],
            "matchedRules": [], "selectedSymbols": [], "paletteDecision": None,
            "compositionDecision": None, "timelineDecision": None,
            "templateSelection": None, "conflicts": [], "warnings": [],
            "engineVersion": "test-1.0", "ontologyVersion": 1, "policyRevision": 1,
            "fragmentSource": None, "fragmentSourceHash": None,
        })
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gen_id, self.user_id, "ir", "canvas2d", None, "{}", 7, ir, "a" * 64,
             provenance,
             '{"parameterValid":true,"nodeCount":4,"outOfBoundsNodeCount":0,"issues":[]}',
             "[]", 0, "req-r", NOW),
        )
        connection.commit()
        connection.close()

    def _seed_work_row(self, original_job_id: str) -> None:
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, published_version_id,"
            " revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("wrk-r", self.user_id, "复现作品", "", "wv-r", 1, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("wv-r", "wrk-r", 1, self.user_id, "published", "复现作品", "",
             "gen-r", original_job_id, "", None, None, 1, NOW, NOW),
        )
        connection.commit()
        connection.close()

    def _seed_ready(self, png: bytes | None = None) -> None:
        """按依赖顺序造齐：generation → 原任务+帧 → 作品与版本。"""
        self._seed_generation()
        self._job_with_frame("job-orig", "gen-r", png or make_png())
        self._seed_work_row("job-orig")

    # ---- 用例 -----------------------------------------------------------
    def test_reproduce_creates_job_bound_to_original(self) -> None:
        self._register("rep_owner")
        self._seed_ready()

        response = self.client.post(
            f"{API}/work-versions/wv-r/reproduce",
            json={"rendererVersion": "codepoet-canvas-1.0.0",
                  "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1}},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["originalJobId"], "job-orig")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["phase"], "awaiting_frames")
        self.assertEqual(job["generationId"], "gen-r")
        self.assertIsNotNone(job["expiresAt"])

    def test_identical_frames_compare_exact(self) -> None:
        """同一张图 → exactMatch=true、mae=0。"""
        self._register("cmp_same")
        png = make_png()
        self._seed_ready(png)
        self._job_with_frame("job-rep", "gen-r", png)

        response = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["compatible"], body.get("reason"))
        self.assertTrue(body["exactMatch"])
        self.assertEqual(body["mae"], 0.0)
        self.assertEqual(body["differentPixelRatio"], 0.0)
        self.assertEqual(len(body["frames"]), 1)

    def test_different_frames_report_difference(self) -> None:
        self._register("cmp_diff")
        self._seed_ready(make_png(rgb=(200, 120, 90)))
        self._job_with_frame("job-rep", "gen-r", make_png(rgb=(20, 20, 20)))

        body = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        ).json()
        self.assertTrue(body["compatible"])
        self.assertFalse(body["exactMatch"])
        self.assertGreater(body["mae"], 0.0)
        self.assertEqual(body["differentPixelRatio"], 1.0, "整幅图都不同")

    def test_incompatible_returns_null_metrics(self) -> None:
        """渲染器版本不同 → 不可比，指标必须是 null 而不是 0。"""
        self._register("cmp_incompat")
        self._seed_ready(make_png())
        self._job_with_frame("job-rep", "gen-r", make_png(), renderer="canvas-2.0.0")

        body = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        ).json()
        self.assertFalse(body["compatible"])
        self.assertIn("渲染器版本", body["reason"])
        self.assertIsNone(body["exactMatch"], "不可比时不能给 0，会被读成完全一致")
        self.assertIsNone(body["mae"])
        self.assertIsNone(body["differentPixelRatio"])
        self.assertEqual(body["frames"], [])

    def test_comparison_requires_completed_jobs(self) -> None:
        self._register("cmp_pending")
        self._seed_ready()
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend, execution_mode,"
            " status, phase, times_ms_json, policy_revision, renderer_version,"
            " client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-pending", self.user_id, "gen-r", "canvas2d", "browser_upload",
             "running", "awaiting_frames", "[0]", 1, "codepoet-canvas-1.0.0",
             '{"userAgent":"test/1.0","deviceScaleFactor":1}', 1, NOW, NOW),
        )
        connection.commit()
        connection.close()

        response = self.client.get(
            f"{API}/render-jobs/job-pending/comparison?originalJobId=job-orig"
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_comparison_of_foreign_job_is_404(self) -> None:
        self._register("cmp_owner")
        self._seed_ready()
        self._job_with_frame("job-rep", "gen-r", make_png())
        self.client.post(f"{API}/auth/logout",
                         headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]})
        self._register("cmp_stranger")

        response = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ---- 复现他人的公开作品（评审 2026-11-10，两条 P2）------------------
    def _reproduce(self, version_id: str):
        return self.client.post(
            f"{API}/work-versions/{version_id}/reproduce",
            json={"rendererVersion": "codepoet-canvas-1.0.0",
                  "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1}},
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_reproduce_others_public_work_end_to_end(self) -> None:
        """B 复现 A 的公开作品：读快照、上传帧、比较像素都要通。

        评审 [P2]：新任务 owner 是 B、generation 属于 A，于是
        GET /generations 404、upload_frame 走 _require_owned_generation 404、
        comparison 按原任务 owner 判 404。契约明确允许复现他人的公开作品。
        """
        self._register("pub_owner")
        png = make_png()
        self._seed_ready(png)

        self._register("pub_viewer")  # 现在是 B 的身份
        reproduced = self._reproduce("wv-r")
        self.assertEqual(reproduced.status_code, 202, reproduced.text)
        job = reproduced.json()

        # 绘制要用的快照：B 读得到，因为引用它的版本是公开的
        generation = self.client.get(f"{API}/generations/gen-r")
        self.assertEqual(generation.status_code, 200, generation.text)
        self.assertEqual(generation.json()["id"], "gen-r")

        upload = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", png, "image/png")},
            data={"timeMs": "0", "irHash": "a" * 64},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["status"], "succeeded")

        comparison = self.client.get(
            f"{API}/render-jobs/{job['id']}/comparison?originalJobId=job-orig"
        )
        self.assertEqual(comparison.status_code, 200, comparison.text)
        body = comparison.json()
        self.assertTrue(body["compatible"], body.get("reason"))
        self.assertTrue(body["exactMatch"])
        self.assertEqual(body["mae"], 0.0)

    def test_generation_of_private_version_stays_hidden(self) -> None:
        """无关用户读不到私有版本引用的快照。

        V 权限放开的是「可读版本关联的快照」，不是所有快照。这条防的是
        把 404 放宽成 200 之后顺手泄露别人草稿的 IR。
        """
        self._register("priv_owner")
        self._seed_ready()
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("gen-priv", self.user_id, "ir", "canvas2d", None, "{}", 7,
             json.dumps(nested_ir()), "b" * 64,
             json.dumps({"kind": "manual_edit", "baseGenerationId": None,
                         "matchedEntities": [], "matchedRules": [], "selectedSymbols": [],
                         "paletteDecision": None, "compositionDecision": None,
                         "timelineDecision": None, "templateSelection": None,
                         "conflicts": [], "warnings": [], "engineVersion": "test-1.0",
                         "ontologyVersion": 1, "policyRevision": 1,
                         "fragmentSource": None, "fragmentSourceHash": None}),
             '{"parameterValid":true,"nodeCount":4,"outOfBoundsNodeCount":0,"issues":[]}',
             "[]", 0, "req-priv", NOW),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("wv-r-priv", "wrk-r", 2, self.user_id, "private", "草稿", "",
             "gen-priv", "job-orig", "", None, None, 1, NOW, NOW),
        )
        connection.commit()
        connection.close()

        self._register("priv_stranger")
        self.assertEqual(self.client.get(f"{API}/generations/gen-priv").status_code, 404)
        # 公开的是 v1，不该因此获得 v2 的复现权
        self.assertEqual(self._reproduce("wv-r-priv").status_code, 404)
        # 同作品的公开版本仍然可以复现
        self.assertEqual(self._reproduce("wv-r").status_code, 202)

    def test_alpha_difference_counts_as_difference(self) -> None:
        """只差 alpha 的两张图必须判为不同。

        评审 [P2]：(10,20,30,255) 与 (10,20,30,0) 之前被判成
        exactMatch=true / mae=0 / differentPixelRatio=0——实现只比 RGB，
        而契约要求 RGBA 四通道。透明和不透明在图上看就是两张图，
        判成"完全一致"是这类接口最坏的误导。
        """
        self._register("alpha_owner")
        opaque = make_rgba_png((10, 20, 30, 255))
        clear = make_rgba_png((10, 20, 30, 0))
        self._seed_ready(opaque)
        self._job_with_frame("job-rep", "gen-r", clear)

        response = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["compatible"], body.get("reason"))
        self.assertFalse(body["exactMatch"], "只差 alpha 不该判成完全一致")
        self.assertGreater(body["mae"], 0.0)
        self.assertGreater(body["differentPixelRatio"], 0.0)

    def test_identical_rgba_frames_stay_exact(self) -> None:
        """两张完全相同的 RGBA 图仍然要判 exactMatch=true。

        放开 alpha 比较之后要确认没把「没有差异」也算成差异——四通道
        MAE 取平均，稳定不透明的两张图 alpha 差恒为 0，不该放大成误报。
        """
        self._register("alpha_same")
        png = make_rgba_png((10, 20, 30, 128))
        self._seed_ready(png)
        self._job_with_frame("job-rep", "gen-r", png)

        body = self.client.get(
            f"{API}/render-jobs/job-rep/comparison?originalJobId=job-orig"
        ).json()
        self.assertTrue(body["exactMatch"], body)
        self.assertEqual(body["mae"], 0.0)

    def test_comparison_rejects_unbound_original_job(self) -> None:
        """只能跟任务绑定的那个原任务比较，不能拿同 generation 的别的任务比。

        评审 [P2]：授权放宽到"快照可读"之后，B 拿自己的复现任务去比该
        generation 下的私有任务会返回 200，并给出那张图的 MAE 与差异像素比
        ——而 B 直接 GET 那个任务和它的产物都是 404。快照公开不等于它下面的
        所有渲染输出都公开，契约 8.2 也要求两任务的原任务绑定一致。
        """
        self._register("bind_owner")
        self._seed_ready(make_png(rgb=(200, 120, 90)))
        # 同一个 generation 下另一个任务，产物 B 读不到，也没被任何版本引用
        self._job_with_frame("job-private", "gen-r", make_png(rgb=(10, 200, 10)))
        reproduced = self._reproduce("wv-r")
        self.assertEqual(reproduced.status_code, 202, reproduced.text)
        job_id = reproduced.json()["id"]

        # 绑定的原任务是 job-orig，换成 job-private 必须 404
        swapped = self.client.get(
            f"{API}/render-jobs/{job_id}/comparison?originalJobId=job-private"
        )
        self.assertEqual(swapped.status_code, 404, f"不该泄露别的任务：{swapped.text[:200]}")

        # 绑定一致的那条仍然可用（先补一帧让任务完成）
        upload = self.client.post(
            f"{API}/render-jobs/{job_id}/frames",
            files={"file": ("frame.png", make_png(rgb=(200, 120, 90)), "image/png")},
            data={"timeMs": "0", "irHash": "a" * 64},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        ok = self.client.get(
            f"{API}/render-jobs/{job_id}/comparison?originalJobId=job-orig"
        )
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_derive_generation_from_public_snapshot(self) -> None:
        """能从「当前公开版本可见」的快照派生自己的 generation。

        评审 [P2]：上一轮我把这里收紧成只允许本人，把契约 8.2 的流程
        （先 mode=ir 派生本人 generation，再建任务保存副本）挡死了：
        B 读得到 A 的公开快照，却派生 404。
        """
        self._register("derive_owner")
        self._seed_ready()
        body = {
            "mode": "ir",
            "baseGenerationId": "gen-r",
            "backend": "canvas2d",
            "ir": nested_ir(),
        }

        self._register("derive_viewer")  # 现在是 B
        derived = self.client.post(
            f"{API}/generations", json=body, headers={"X-CSRF-Token": self.csrf}
        )
        self.assertEqual(derived.status_code, 201, derived.text)
        generation = derived.json()
        self.assertEqual(generation["ownerId"], self.user_id, "新快照归操作者")
        self.assertEqual(
            generation["provenance"]["baseGenerationId"], "gen-r", "要记下来源"
        )
