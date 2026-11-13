"""P1B-05 质量检查测试。

验收原话是「参数/出界/空白统计」。参数与出界在 ``validate_ir`` 里早就有，
缺的是**空白检测**——一张全白的 PNG 能通过所有结构校验、能存成作品、
能公开，但它上面什么都没画。这种"技术上成功、事实上失败"的产物
只有看像素才发现，所以本文件重点验它。

第二个重点是采样时刻校验：静态图传多帧、动画帧数不对，都要在创建任务
时就拒掉，而不是等存下一个缺帧的"动画"。
"""

from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageDraw
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


def blank_png(width: int = 800, height: int = 600) -> bytes:
    """全白图：与默认背景色完全一致，覆盖率应为 0。"""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    buffer = __import__("io").BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def drawn_png(width: int = 800, height: int = 600) -> bytes:
    """白底画一个实心圆：有明显内容，覆盖率应 > 0。"""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse([300, 200, 500, 400], fill=(200, 80, 60))
    buffer = __import__("io").BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ir_doc(duration_ms: int = 0) -> dict:
    return {
        "schemaVersion": "1.0",
        "seed": 42,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": duration_ms,
        "nodes": [
            {
                "id": "c1", "parentId": None, "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 100},
                "style": {"fill": "#E8A0A0", "stroke": "#8B3A3A",
                          "strokeWidth": 2, "opacity": 1},
            }
        ],
        "animations": [],
        "narrative": [],
    }


class QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""
        self.gen_id = ""

    def _register(self, name: str) -> None:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        user_id = response.json()["id"]
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        self.csrf = login.json()["csrfToken"]
        self.user_id = user_id

    def _seed_generation(self, duration_ms: int = 0) -> str:
        ir = ir_doc(duration_ms)
        gen_id = f"gen-q-{duration_ms}"
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
            (gen_id, self.user_id, "ir", "canvas2d", None, "{}", 42,
             json.dumps(ir), "c" * 64, provenance,
             '{"parameterValid":true,"nodeCount":1,"outOfBoundNodeCount":0,"issues":[]}',
             "[]", 0, "req-q", "2026-11-10T00:00:00+00:00"),
        )
        connection.commit()
        connection.close()
        return gen_id

    def _create_job(self, times: list[int], gen_id: str):
        return self.client.post(
            f"{API}/render-jobs",
            json={"generationId": gen_id, "backend": "canvas2d", "timesMs": times,
                  "executionMode": "browser_upload", "rendererVersion": "codepoet-canvas-1.0.0",
                  "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1}},
            headers={"X-CSRF-Token": self.csrf},
        )

    def _upload(self, job_id: str, time_ms: int, png: bytes, ir_hash: str = "c" * 64):
        return self.client.post(
            f"{API}/render-jobs/{job_id}/frames",
            files={"file": ("frame.png", png, "image/png")},
            data={"timeMs": str(time_ms), "irHash": ir_hash},
            headers={"X-CSRF-Token": self.csrf},
        )

    # ---- 质量报告 -------------------------------------------------------
    def test_drawn_frame_has_coverage(self) -> None:
        self._register("q_drawn")
        gen_id = self._seed_generation()
        job = self._create_job([0], gen_id).json()
        response = self._upload(job["id"], 0, drawn_png())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        quality = body["qualityReport"]
        self.assertIsNotNone(quality, "任务完成时必须写入质量报告")
        self.assertGreater(quality["nonBackgroundPixelRatio"], 0.01,
                           "画了圆就该有明显的非背景像素")
        self.assertFalse(quality["emptyOutput"])
        self.assertEqual(quality["pixelDifferenceThreshold"], 8)
        self.assertEqual(len(quality["frames"]), 1)
        self.assertEqual(quality["frames"][0]["timeMs"], 0)

    def test_blank_frame_is_flagged(self) -> None:
        """全白图必须被判成空输出——它技术上合法，事实上什么都没画。"""
        self._register("q_blank")
        gen_id = self._seed_generation()
        job = self._create_job([0], gen_id).json()
        response = self._upload(job["id"], 0, blank_png())
        self.assertEqual(response.status_code, 200, response.text)
        quality = response.json()["qualityReport"]

        self.assertTrue(quality["emptyOutput"])
        self.assertLess(quality["nonBackgroundPixelRatio"], 0.001)
        codes = {issue["code"] for issue in quality["issues"]}
        self.assertIn("EMPTY_OUTPUT", codes, quality["issues"])

    # ---- 采样时刻校验 ---------------------------------------------------
    def test_static_ir_rejects_multi_frame(self) -> None:
        """静态图（durationMs=0）只能采样 [0]。"""
        self._register("q_static")
        gen_id = self._seed_generation(duration_ms=0)
        response = self._create_job([0, 1000], gen_id)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("静态图", response.json()["error"]["message"])

    def test_animation_requires_exact_three_frames(self) -> None:
        """动画完整保存必须恰好 [0, mid, duration]。"""
        self._register("q_anim")
        gen_id = self._seed_generation(duration_ms=2000)
        ok = self._create_job([0, 1000, 2000], gen_id)
        self.assertEqual(ok.status_code, 202, ok.text)

        wrong = self._create_job([0, 500, 2000], gen_id)
        self.assertEqual(wrong.status_code, 422, wrong.text)
        self.assertIn("完整动画", wrong.json()["error"]["message"])

    def test_sample_beyond_duration_is_rejected(self) -> None:
        self._register("q_over")
        gen_id = self._seed_generation(duration_ms=2000)
        response = self._create_job([0, 3000], gen_id)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("超出动画时长", response.json()["error"]["message"])

    def test_animation_preview_single_frame_allowed(self) -> None:
        """契约允许"普通预览只取某个合法时刻"。"""
        self._register("q_preview")
        gen_id = self._seed_generation(duration_ms=2000)
        response = self._create_job([1200], gen_id)
        self.assertEqual(response.status_code, 202, response.text)

    # ---- 三帧动画的质量报告 ---------------------------------------------
    def test_three_frame_animation_quality(self) -> None:
        self._register("q_three")
        gen_id = self._seed_generation(duration_ms=2000)
        job = self._create_job([0, 1000, 2000], gen_id).json()
        for time_ms in (0, 1000, 2000):
            response = self._upload(job["id"], time_ms, drawn_png())
            self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        quality = body["qualityReport"]
        self.assertEqual(len(quality["frames"]), 3, "三帧都该有质量数据")
        self.assertEqual([f["timeMs"] for f in quality["frames"]], [0, 1000, 2000])
        self.assertFalse(quality["emptyOutput"])

    def test_middle_frame_blank_marks_whole_animation_empty(self) -> None:
        """动画中间一段空白，观感上就是断的——整体判空。"""
        self._register("q_mid_blank")
        gen_id = self._seed_generation(duration_ms=2000)
        job = self._create_job([0, 1000, 2000], gen_id).json()
        self._upload(job["id"], 0, drawn_png())
        self._upload(job["id"], 1000, blank_png())
        response = self._upload(job["id"], 2000, drawn_png())
        self.assertEqual(response.status_code, 200, response.text)

        quality = response.json()["qualityReport"]
        self.assertTrue(quality["emptyOutput"], "有一帧空就该整体标空")
        empty_frames = [f["timeMs"] for f in quality["frames"] if f["emptyOutput"]]
        self.assertEqual(empty_frames, [1000])
