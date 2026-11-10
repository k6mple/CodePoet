"""P0-04 作品与渲染任务的集成测试。

用真实 SQLite（临时目录）跑迁移，不用 mock——这几条链路的价值就在
「文件真的落盘了、状态真的流转了」，mock 掉就测不出任何东西。
"""

from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import threading
import unittest
import zlib
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db import create_database, create_session_factory
from app.errors import ApiError
from app.main import create_app
from app.models.users import User as UserModel
from app.schemas.works import RenderCreate
from app.services import uploads
from app.services import works as works_service
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


def make_png(width: int = 800, height: int = 600, rgb: tuple[int, int, int] = (200, 120, 90)) -> bytes:
    """构造纯色合法 PNG，不依赖 Pillow。"""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class PngParsingTests(unittest.TestCase):
    """纯函数测试，不用起应用。"""

    def test_reads_real_size_from_ihdr(self):
        width, height = uploads.read_png_size(make_png(40, 30))
        self.assertEqual((width, height), (40, 30))

    def test_rejects_truncated_file(self):
        with self.assertRaises(Exception):
            uploads.read_png_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)

    def test_rejects_absurd_size(self):
        # 把 IHDR 里的宽度改成 99999，应被上限拦住
        data = bytearray(make_png(10, 10))
        data[16:20] = (99999).to_bytes(4, "big")
        with self.assertRaises(Exception):
            uploads.read_png_size(bytes(data))

    def test_store_rejects_non_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, data_dir=Path(tmp) / "runtime")
            with self.assertRaises(Exception):
                uploads.store_frame(settings, "usr-1", b"GIF89a fake", 0)

    def test_store_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, data_dir=Path(tmp) / "runtime")
            with self.assertRaises(Exception):
                uploads.store_frame(settings, "usr-1", b"", 0)

    def test_store_writes_file_and_returns_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, data_dir=Path(tmp) / "runtime")
            png = make_png(64, 48)
            stored = uploads.store_frame(settings, "usr-1", png, 250)
            self.assertEqual((stored.width, stored.height), (64, 48))
            self.assertEqual(stored.time_ms, 250)
            self.assertEqual(len(stored.sha256), 64)
            path = uploads.artifact_path(settings, stored.storage_path)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), png)

    def test_artifact_path_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, data_dir=Path(tmp) / "runtime")
            with self.assertRaises(Exception):
                uploads.artifact_path(settings, "../../../etc/passwd")


class WorkFlowTests(unittest.TestCase):
    """端到端：注册 → 生成 → 上传帧 → 存作品 → 重开 → 下载。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)

    # ---- 辅助 -----------------------------------------------------------
    def _csrf(self) -> str:
        response = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def _new_user(self, name: str) -> None:
        token = self._csrf()
        body = {"username": name, "password": "Aa1!" + "x" * 12, "nickname": name}
        response = self.client.post(
            f"{API}/auth/register", json=body, headers={"X-CSRF-Token": token}
        )
        self.assertIn(response.status_code, (201, 409), response.text)

    def _login(self, name: str) -> str:
        token = self._csrf()
        body = {"username": name, "password": "Aa1!" + "x" * 12}
        response = self.client.post(
            f"{API}/auth/login", json=body, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.__dict__["_login_body"] = body
        return body["csrfToken"]

    def _template_version(self) -> str | None:
        """测试库里没有种子数据，模板可能为空。没有就跳过生成类用例。"""
        response = self.client.get(f"{API}/templates?backend=canvas2d&pageSize=10")
        if response.status_code != 200:
            return None
        for item in response.json().get("items") or []:
            if item.get("publishedVersionId"):
                return item["publishedVersionId"]
        return None

    def _seed_generation(self, csrf: str) -> dict:
        """直接往库里插一条生成快照，避免依赖模板种子。"""
        from app.models.creations import Generation as GenerationModel

        ir = {
            "schemaVersion": "1.0",
            "seed": 42,
            "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
            "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
            "durationMs": 0,
            # DrawingIR 要求至少 1 个节点，空数组会被模型拒绝
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
        ir_hash = "a" * 64
        row = GenerationModel(
            id="gen-test-1",
            owner_id=self._user_id,
            mode="ir",
            backend="canvas2d",
            template_version_id=None,
            params_json="{}",
            seed=42,
            ir_json=json.dumps(ir),
            ir_hash=ir_hash,
            provenance_json=json.dumps(
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
            ),
            validation_report_json=(
                '{"parameterValid":true,"nodeCount":1,'
                '"outOfBoundsNodeCount":0,"issues":[]}'
            ),
            warnings_json="[]",
            unpublished_source=0,
            request_id="req-test",
            created_at="2026-11-08T00:00:00+00:00",
        )
        engine = create_database(self.settings)
        try:
            factory = create_session_factory(engine)
            with factory() as db:
                db.add(row)
                db.commit()
        finally:
            engine.dispose()
        return {"id": "gen-test-1", "irHash": ir_hash}
        return {"id": "gen-test-1", "irHash": ir_hash}

    def _whoami(self) -> str:
        """从登录响应里取 userId，避免依赖 /me 的包装结构。"""
        body = self.__dict__.get("_login_body") or {}
        user_id = (body.get("user") or {}).get("id")
        self.assertIsNotNone(user_id, f"登录响应里没有 user.id：{list(body)}")
        return user_id

    def _make_job(self, csrf: str, generation_id: str, times: list[int]) -> dict:
        response = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": generation_id,
                "backend": "canvas2d",
                "timesMs": times,
                "executionMode": "browser_upload",
                "rendererVersion": "test-1.0",
                "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _force_expire(self, job_id: str) -> None:
        """把到期时间挪到过去，模拟「五分钟真的过去了」。

        直接改库而不是 sleep：等五分钟会让测试慢到没人愿意跑，而这里要
        验的是到期后的行为，不是时钟本身。
        """
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE render_jobs SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", job_id),
        )
        connection.commit()
        connection.close()

    # ---- 用例 -----------------------------------------------------------
    def test_render_job_starts_awaiting_frames(self):
        """创建即 running/awaiting_frames，并带 5 分钟到期时间。

        契约 7.1 对 browser_upload 就是这么写的。早期实现返回 queued 且
        expiresAt=null，前端据此判定「不可上传」，G0 保存链断在第一跳
        （问题单 BE-G0-001）。
        """
        self._new_user("owner1")
        csrf = self._login("owner1")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        self.assertEqual(job["phase"], "awaiting_frames")
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["manifest"])
        self.assertIsNone(job["finishedAt"])
        # expiresAt 必须是创建时间 + 5 分钟，不能是 null
        self.assertIsNotNone(job["expiresAt"])
        created = datetime.fromisoformat(job["createdAt"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(job["expiresAt"].replace("Z", "+00:00"))
        self.assertEqual(int((expires - created).total_seconds()), 300)

    def test_third_open_job_is_rejected_with_429(self):
        """契约 7.1：每用户最多两个未完成上传任务。"""
        self._new_user("owner_cap")
        csrf = self._login("owner_cap")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        self._make_job(csrf, gen["id"], [0])
        self._make_job(csrf, gen["id"], [0])

        response = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": gen["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "test-1.0",
                "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 429, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "EXECUTION_BUSY")
        self.assertEqual(error.get("details", {}).get("maxOpenUploads"), 2)

    def test_expired_job_reports_timed_out(self):
        """过期任务读到时标 timed_out，并带 UPLOAD_EXPIRED。

        契约写明「过期时检查并标 timed_out，不需常驻 worker」，
        所以这个判断必须发生在读路径上，而不是等某个后台进程。
        """
        self._new_user("owner_expire")
        csrf = self._login("owner_expire")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        self._force_expire(job["id"])

        response = self.client.get(f"{API}/render-jobs/{job['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "timed_out")
        self.assertEqual((body.get("error") or {}).get("code"), "UPLOAD_EXPIRED")
        self.assertIsNotNone(body["finishedAt"])

    def test_expired_job_rejects_frame_upload(self):
        """过期任务不能再收帧，否则会产出「图是旧的、状态是新的」的成品。"""
        self._new_user("owner_late")
        csrf = self._login("owner_late")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        self._force_expire(job["id"])

        response = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "0", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_STATE")

    def test_expired_slot_is_released(self):
        """过期任务不该继续占额度，否则用户永远建不出新任务。"""
        self._new_user("owner_release")
        csrf = self._login("owner_release")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        first = self._make_job(csrf, gen["id"], [0])
        second = self._make_job(csrf, gen["id"], [0])
        self._force_expire(first["id"])
        self._force_expire(second["id"])

        # 两个都过期了，额度应全部释放：还能再建两个
        third = self._make_job(csrf, gen["id"], [0])
        self.assertEqual(third["status"], "running")
        fourth = self._make_job(csrf, gen["id"], [0])
        self.assertEqual(fourth["status"], "running")

        # 此刻未完成的是 third + fourth，共两个，第五个就该撞上限
        response = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": gen["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "test-1.0",
                "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 429, response.text)

    def test_frame_upload_completes_job(self):
        self._new_user("owner2")
        csrf = self._login("owner2")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        png = make_png(800, 600)
        response = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", png, "image/png")},
            data={"timeMs": "0", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["phase"], "completed")
        self.assertEqual(body["status"], "succeeded")
        frames = (body["manifest"] or {}).get("frames") or []
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["width"], 800)
        self.assertEqual(frames[0]["sha256"], frames[0]["sha256"].lower())

    def test_rejects_undeclared_time(self):
        self._new_user("owner3")
        csrf = self._login("owner3")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        response = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "500", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_rejects_mismatched_ir_hash(self):
        self._new_user("owner4")
        csrf = self._login("owner4")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        response = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "0", "irHash": "f" * 64},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_cannot_save_work_before_frames_uploaded(self):
        self._new_user("owner5")
        csrf = self._login("owner5")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        response = self.client.post(
            f"{API}/works",
            json={
                "title": "未完成",
                "generationId": gen["id"],
                "renderJobId": job["id"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_full_flow_and_artifact_download(self):
        self._new_user("owner6")
        csrf = self._login("owner6")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0, 1000])
        png = make_png(800, 600)

        for t in (0, 1000):
            response = self.client.post(
                f"{API}/render-jobs/{job['id']}/frames",
                files={"file": ("frame.png", png, "image/png")},
                data={"timeMs": str(t), "irHash": gen["irHash"]},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(response.status_code, 200, response.text)

        created = self.client.post(
            f"{API}/works",
            json={
                "title": "莲花",
                "description": "",
                "generationId": gen["id"],
                "renderJobId": job["id"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        work, version = body["work"], body["version"]
        self.assertEqual(version["versionNumber"], 1)
        self.assertEqual(version["status"], "private")
        self.assertEqual(work["latestVersionId"], version["id"])
        self.assertIsNotNone(work["thumbnailArtifactId"])

        mine = self.client.get(f"{API}/works?scope=mine")
        self.assertEqual(mine.status_code, 200)
        self.assertGreaterEqual(mine.json()["total"], 1)

        detail = self.client.get(f"{API}/work-versions/{version['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        detail_body = detail.json()
        self.assertIn("generation", detail_body)
        self.assertIn("renderJob", detail_body)
        manifest = detail_body["renderJob"]["manifest"]
        self.assertEqual(len(manifest["frames"]), 2)
        self.assertEqual(manifest["timesMs"], [0, 1000])

        artifact_id = manifest["frames"][0]["artifactId"]
        blob = self.client.get(f"{API}/artifacts/{artifact_id}")
        self.assertEqual(blob.status_code, 200, blob.text)
        self.assertEqual(blob.headers["content-type"], "image/png")
        self.assertEqual(blob.content, png)

    def test_other_user_cannot_read_work(self):
        self._new_user("owner7")
        csrf = self._login("owner7")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "0", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        created = self.client.post(
            f"{API}/works",
            json={"title": "私有作品", "generationId": gen["id"], "renderJobId": job["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(created.status_code, 201, created.text)
        version_id = created.json()["version"]["id"]
        artifact_id = created.json()["work"]["thumbnailArtifactId"]

        # 换成另一个用户
        self.client.cookies.clear()
        self._new_user("intruder")
        self._login("intruder")

        # 私有作品对外必须 404，不能 403
        self.assertEqual(self.client.get(f"{API}/work-versions/{version_id}").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/artifacts/{artifact_id}").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/render-jobs/{job['id']}").status_code, 404)

    def test_anonymous_cannot_list_mine(self):
        self.client.cookies.clear()
        response = self.client.get(f"{API}/works?scope=mine")
        self.assertIn(response.status_code, (401, 404))

    def test_public_scope_excludes_private_works(self):
        self._new_user("owner8")
        csrf = self._login("owner8")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "0", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.client.post(
            f"{API}/works",
            json={"title": "未发布", "generationId": gen["id"], "renderJobId": job["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.client.cookies.clear()
        page = self.client.get(f"{API}/works?scope=public")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 0)


    # ---- PR #10 评审回归（2026-11-10）-----------------------------------
    def _insert_legacy_jobs(self, count: int, created_at: str) -> list[str]:
        """造出旧版本遗留的 queued / expires_at=NULL 任务。

        当前实现写不出这种行（创建即 running + 5 分钟到期），所以只能直接
        插库来模拟升级前的数据。评审的复现也是这么做的。
        """
        ids: list[str] = []
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        for index in range(count):
            job_id = f"job-legacy-{index}"
            connection.execute(
                "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
                " execution_mode, status, phase, times_ms_json, policy_revision,"
                " revision, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    self._user_id,
                    self._gen_id,
                    "canvas2d",
                    "browser_upload",
                    "queued",
                    "awaiting_frames",
                    "[0]",
                    1,
                    1,
                    created_at,
                    created_at,
                ),
            )
            ids.append(job_id)
        connection.commit()
        connection.close()
        return ids

    def _open_job_count(self) -> int:
        connection = sqlite3.connect(self.settings.database_path)
        total = connection.execute(
            "SELECT COUNT(*) FROM render_jobs WHERE owner_id = ?"
            " AND status IN ('queued', 'running')",
            (self._user_id,),
        ).fetchone()[0]
        connection.close()
        return total

    def test_succeeded_job_is_not_rewritten_by_expiry(self):
        """已成功的任务超过上传期限后仍是 succeeded。

        评审 [P1]：``_mark_expired_if_due`` 只看 expires_at 不看 status，而
        ``get_render_job`` 对任意状态都调它——把 expires_at 拨到过去后，
        一次已经成功上传完的任务被读成 timed_out，还多挂一个 UPLOAD_EXPIRED，
        manifest 却还留着。读一次改一次，保存重试也会据此判失败。
        """
        self._new_user("owner9")
        csrf = self._login("owner9")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        job = self._make_job(csrf, gen["id"], [0])
        upload = self.client.post(
            f"{API}/render-jobs/{job['id']}/frames",
            files={"file": ("frame.png", make_png(), "image/png")},
            data={"timeMs": "0", "irHash": gen["irHash"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["status"], "succeeded")

        # 五分钟真的过去了
        self._force_expire(job["id"])

        for attempt in range(2):  # 读两次，确认不是"第一次侥幸没改"
            got = self.client.get(f"{API}/render-jobs/{job['id']}")
            self.assertEqual(got.status_code, 200)
            body = got.json()
            self.assertEqual(
                body["status"], "succeeded", f"第 {attempt + 1} 次读取被时钟改写了状态"
            )
            self.assertEqual(body["phase"], "completed")
            self.assertIsNone(body["error"], "成功任务不该带 UPLOAD_EXPIRED")
            self.assertIsNotNone(body["manifest"], "成功任务的清单要还在")
            self.assertEqual(len(body["manifest"]["frames"]), 1)

    def test_legacy_queued_job_without_expiry_releases_quota(self):
        """旧版本 queued / expiresAt=null 的任务要能被清理，不能永久占名额。

        评审 [P2]：``deadline is None`` 直接 return False，而额度检查又把
        queued 算作未完成——两条遗留任务就能把用户永久锁死：建不出新任务，
        GET 它们也还是 queued/null，用户只能手工取消。
        """
        self._new_user("owner10")
        csrf = self._login("owner10")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        self._gen_id = gen["id"]
        legacy = self._insert_legacy_jobs(2, "2026-11-09T00:00:00+00:00")

        first = self.client.get(f"{API}/render-jobs/{legacy[0]}")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertEqual(body["status"], "timed_out", "旧任务应当按 created_at+TTL 判过期")
        self.assertEqual((body["error"] or {}).get("code"), "UPLOAD_EXPIRED")
        self.assertIsNotNone(
            body["expiresAt"], "补算出来的到期时刻要能读回来，否则前端不知道凭什么叫过期"
        )

        # 另一条还挂着 queued，新建时应当先清理再放行，而不是 429
        created = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": gen["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "test-1.0",
                "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(self._open_job_count(), 1, "旧的应当被清掉，只剩新建的这一条")

    def test_legacy_job_inside_ttl_is_not_expired(self):
        """没写到期时刻但还在五分钟内的旧任务不能被误杀。

        补算用的是 created_at + TTL，刚建出来还没到期的必须保持原状——
        否则升级瞬间会把所有进行中的上传一起判死。
        """
        self._new_user("owner11")
        csrf = self._login("owner11")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        self._gen_id = gen["id"]
        fresh = datetime.now().astimezone().isoformat(timespec="seconds")
        legacy = self._insert_legacy_jobs(1, fresh)

        got = self.client.get(f"{API}/render-jobs/{legacy[0]}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["status"], "queued", "还在有效期内不该被判过期")
        self.assertIsNone(got.json()["error"])

    def test_migration_backfills_legacy_expiry(self):
        """迁移 010 要把遗留任务的 expires_at 真的补成 created_at + 300s。

        惰性补算已经能兜住，但 GET 返回 ``expiresAt: null`` 而状态是
        timed_out 会让前端困惑。迁移负责把列填上，所以这条 SQL 必须真的
        算对——它一旦写错，所有人启动时迁移就失败。
        """
        self._new_user("owner13")
        csrf = self._login("owner13")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        self._gen_id = gen["id"]
        legacy = self._insert_legacy_jobs(1, "2026-11-09T10:00:00+00:00")

        migration = (
            Path(__file__).resolve().parents[1] / "migrations" / "010_backfill_upload_expiry.sql"
        )
        connection = sqlite3.connect(self.settings.database_path)
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.commit()
        value = connection.execute(
            "SELECT expires_at FROM render_jobs WHERE id = ?", (legacy[0],)
        ).fetchone()[0]
        connection.close()

        self.assertEqual(value, "2026-11-09T10:05:00+00:00")

    def test_concurrent_create_cannot_exceed_limit(self):
        """并发创建不能突破「每用户两个未完成上传」。

        评审 [P2]：COUNT 与 INSERT 之间没有原子保护，两个并发 POST 都返回
        202，库里变成 3 个未完成任务。

        这里用真实引擎 + 两个线程各自一个 Session 直调服务层（控制器只是
        透传，竞态在服务层）。为了让"读-改-写交错"成为必然而不是偶然，在
        额度查询语句处挂 before_cursor_execute 撮合两个线程：修好之后只有
        先拿到写锁的那个能走到 COUNT，另一个卡在 BEGIN IMMEDIATE 上，栅栏
        超时后它才会发现名额已满。
        """
        self._new_user("owner12")
        csrf = self._login("owner12")
        self._user_id = self._whoami()
        gen = self._seed_generation(csrf)
        self._make_job(csrf, gen["id"], [0])  # 先占 1 个名额，上限 2

        payload = RenderCreate(
            generationId=gen["id"],
            backend="canvas2d",
            timesMs=[0],
            executionMode="browser_upload",
            rendererVersion="test-1.0",
            clientEnvironment={"userAgent": "test/1.0", "deviceScaleFactor": 1},
        )
        gate = threading.Barrier(2, timeout=4)
        results: list[tuple] = []

        def worker() -> None:
            engine = create_database(self.settings)

            @event.listens_for(engine, "before_cursor_execute")
            def _meet_at_quota_check(conn, cursor, statement, params, context, executemany):
                lowered = statement.lower()
                if "count(" in lowered and "render_jobs" in lowered:
                    try:
                        gate.wait()
                    except threading.BrokenBarrierError:
                        pass  # 修好之后另一个线程压根到不了这里

            factory = create_session_factory(engine)
            try:
                with factory() as db:
                    user = db.get(UserModel, self._user_id)
                    try:
                        job = works_service.create_render_job(db, user, payload)
                        results.append(("created", job.id))
                    except ApiError as exc:
                        results.append((exc.status_code, exc.code))
            finally:
                engine.dispose()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "线程没有结束")

        created = [item for item in results if item[0] == "created"]
        busy = [item for item in results if item[0] == 429]
        self.assertEqual(len(results), 2, f"两个请求都要有结果：{results}")
        self.assertEqual(len(created), 1, f"并发创建只能成功一个：{results}")
        self.assertEqual(len(busy), 1, f"另一个必须是 429：{results}")
        self.assertEqual(busy[0][1], "EXECUTION_BUSY", results)
        self.assertEqual(self._open_job_count(), 2, "上限是 2，并发不该突破")


if __name__ == "__main__":
    unittest.main()
