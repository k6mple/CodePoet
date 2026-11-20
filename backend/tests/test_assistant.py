"""X1 LLM 智能辅助提案的集成与边界测试。

测试覆盖：
1. 迁移 014（新库 + 旧库副本升级且历史作品可读）；
2. 未配置 API Key 返回 503 ASSISTANT_UNAVAILABLE 且 features.llmAssistant=false；
3. 客户端禁止传模型 URL/密钥（422）；
4. 跨用户读/应用 404；非模板基底请求 parameter_schema 返回 422 ASSISTANT_UNSUPPORTED_BASE；
5. 第 1 次非法输出 -> 隔离校验检出 -> 自动反馈修复第 2 次成功（attempts=2）；
6. 两次均失败返回 status=failed + ASSISTANT_VALIDATION_FAILED，apply 返回 409 INVALID_STATE；
7. parameter_schema 临时模板实例化且不修改原 TemplateVersion；
8. 重复 apply 事务幂等返回同一 generationId；
9. 基底权限收回后 apply 被拒（404）；
10. provenance.assistant 经 ir / subgraph / sandbox(fragment) 派生继承及纳入 dependencySnapshotHash；
11. 孤儿提案重启清理 recover_orphans。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import create_database, create_session_factory
from app.engine.llm_client import LlmResponse
from app.main import create_app
from app.models.assistant import AssistantProposal as AssistantProposalModel
from app.models.creations import Generation as GenerationModel
from app.models.templates import Template, TemplateVersion
from app.models.works import RenderJob, Work, WorkVersion
from app.services import assistant as assistant_service
from app.services import sandbox as sandbox_service
from app.services import works as works_service
from app.settings import Settings

API = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
BACKUP_DB = REPO_ROOT / "backups" / "f1-backup-20261119" / "codepoet.sqlite3"

TEMPLATE_IR = {
    "schemaVersion": "1.0",
    "seed": 42,
    "canvas": {"width": 800, "height": 600, "background": "#F6F3EA"},
    "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
    "durationMs": 0,
    "nodes": [
        {
            "id": "scene_root",
            "parentId": None,
            "type": "group",
            "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
            "style": {"opacity": 1},
        },
        {
            "id": "lotus_core",
            "parentId": "scene_root",
            "type": "circle",
            "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
            "geometry": {"cx": 400, "cy": 300, "r": 60},
            "style": {
                "fill": "#E6B8AF",
                "stroke": "#8B3A3A",
                "strokeWidth": 2,
                "opacity": 1,
            },
        },
        {
            "id": "outer_ring",
            "parentId": "scene_root",
            "type": "circle",
            "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
            "geometry": {"cx": 400, "cy": 300, "r": 120},
            "style": {
                "fill": None,
                "stroke": "#4A6B5D",
                "strokeWidth": 3,
                "opacity": 0.9,
            },
        },
    ],
    "animations": [],
    "narrative": [],
}

TEMPLATE_SCHEMA = [
    {
        "name": "coreRadius",
        "label": "莲心半径",
        "description": "控制中心圆半径",
        "type": "number",
        "default": 60,
        "min": 20,
        "max": 120,
        "step": 2,
    }
]

TEMPLATE_BINDINGS = [
    {"parameter": "coreRadius", "nodeId": "lotus_core", "property": "geometry.r"}
]


def apply_migrations(database_path: Path, *, max_version: int | None = None) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if max_version is not None and version > max_version:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, path.name, "2026-11-20T00:00:00+00:00"),
        )
    connection.commit()
    connection.close()


class AssistantTestBase(unittest.TestCase):
    llm_api_key = "test-placeholder-not-real-key"
    llm_base_url = "https://api.deepseek.com"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None,
            data_dir=Path(self.tmp.name) / "runtime",
            llm_api_key=self.llm_api_key,
            llm_base_url=self.llm_base_url,
            llm_model="deepseek-chat",
            llm_timeout_seconds=15.0,
        )
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(self.settings.database_path)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

        self.engine = create_database(self.settings)
        self.addCleanup(self.engine.dispose)
        self.sessions = create_session_factory(self.engine)

    def _csrf(self) -> str:
        res = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(res.status_code, 200)
        return res.json()["csrfToken"]

    def _register_and_login(self, username: str) -> tuple[str, str]:
        password = "Aa1!" + "x" * 12
        reg = self.client.post(
            f"{API}/auth/register",
            json={"username": username, "password": password, "nickname": username},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertIn(reg.status_code, (201, 409), reg.text)
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": self._csrf()},
        )
        self.assertEqual(login.status_code, 200, login.text)
        body = login.json()
        return body["csrfToken"], body["user"]["id"]

    def _seed_template_and_generation(
        self, owner_id: str, csrf: str
    ) -> tuple[str, str]:
        with self.sessions() as db:
            tpl = Template(
                id="tpl-x1-lotus",
                owner_id=owner_id,
                name="清莲图式",
                description="用于 X1 测试的清莲模板",
                status="published",
                supported_backends_json='["canvas2d"]',
                published_version_id=None,
                latest_version_id=None,
                revision=1,
                created_at="2026-11-20T00:00:00+00:00",
                updated_at="2026-11-20T00:00:00+00:00",
            )
            db.add(tpl)
            db.flush()
            ver = TemplateVersion(
                id="tplv-x1-lotus-1",
                template_id=tpl.id,
                version_number=1,
                created_by=owner_id,
                status="published",
                metadata_json=json.dumps(
                    {"name": "清莲图式", "description": "用于 X1 测试"}, ensure_ascii=False
                ),
                ir_template_json=json.dumps(TEMPLATE_IR, ensure_ascii=False),
                parameter_schema_json=json.dumps(TEMPLATE_SCHEMA, ensure_ascii=False),
                bindings_json=json.dumps(TEMPLATE_BINDINGS, ensure_ascii=False),
                constraints_json="[]",
                supported_backends_json='["canvas2d"]',
                ontology_version=1,
                change_note="init",
                published_at="2026-11-20T00:00:00+00:00",
                revision=1,
                created_at="2026-11-20T00:00:00+00:00",
                updated_at="2026-11-20T00:00:00+00:00",
            )
            db.add(ver)
            db.flush()
            tpl.published_version_id = ver.id
            tpl.latest_version_id = ver.id
            db.commit()

        gen_res = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": "tplv-x1-lotus-1",
                "params": {"coreRadius": 60},
                "backend": "canvas2d",
                "seed": 42,
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(gen_res.status_code, 201, gen_res.text)
        return "tplv-x1-lotus-1", gen_res.json()["id"]


class UnconfiguredAssistantTests(AssistantTestBase):
    llm_api_key = ""

    def test_disabled_when_api_key_empty(self) -> None:
        caps = self.client.get(f"{API}/engine/capabilities")
        self.assertEqual(caps.status_code, 200)
        self.assertFalse(caps.json()["features"]["llmAssistant"])

        csrf, uid = self._register_and_login("x1_off_user")
        _, gen_id = self._seed_template_and_generation(uid, csrf)
        res = self.client.post(
            f"{API}/assistant/proposals",
            json={
                "baseGenerationId": gen_id,
                "prompt": "给莲心增加清雅花瓣",
                "target": "ir_fragment",
                "rootNodeId": "lotus_core",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "ASSISTANT_UNAVAILABLE")


class AssistantFlowTests(AssistantTestBase):
    def test_migration_014_upgrades_legacy_backup_copy(self) -> None:
        import subprocess
        import sys

        copy_dir = Path(self.tmp.name) / "legacy_upgrade"
        copy_dir.mkdir(parents=True, exist_ok=True)
        target_db = copy_dir / "codepoet.sqlite3"
        if BACKUP_DB.exists():
            shutil.copy2(BACKUP_DB, target_db)
        else:
            apply_migrations(target_db, max_version=13)

        env = {"CODEPOET_DATA_DIR": str(copy_dir)}
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "migrate.py")],
            cwd=str(REPO_ROOT),
            env={**__import__("os").environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        conn = sqlite3.connect(target_db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("assistant_proposals", tables)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        if BACKUP_DB.exists():
            gen_count = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
            self.assertGreater(gen_count, 0)
        conn.close()

    def test_capabilities_enabled_and_forbids_client_secrets(self) -> None:
        caps = self.client.get(f"{API}/engine/capabilities")
        self.assertEqual(caps.status_code, 200)
        self.assertTrue(caps.json()["features"]["llmAssistant"])

        csrf, uid = self._register_and_login("x1_user_sec")
        _, gen_id = self._seed_template_and_generation(uid, csrf)
        bad = self.client.post(
            f"{API}/assistant/proposals",
            json={
                "baseGenerationId": gen_id,
                "prompt": "优化构图",
                "target": "ir_fragment",
                "rootNodeId": "lotus_core",
                "apiKey": "forbidden",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(bad.status_code, 422)

    def test_validation_and_unsupported_base(self) -> None:
        csrf, uid = self._register_and_login("x1_user_val")
        _, gen_id = self._seed_template_and_generation(uid, csrf)

        # ir_fragment 缺少 rootNodeId -> 422
        r1 = self.client.post(
            f"{API}/assistant/proposals",
            json={
                "baseGenerationId": gen_id,
                "prompt": "替换莲心",
                "target": "ir_fragment",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(r1.status_code, 422)

        # parameter_schema 传 rootNodeId -> 422
        r2 = self.client.post(
            f"{API}/assistant/proposals",
            json={
                "baseGenerationId": gen_id,
                "prompt": "新增颜色参数",
                "target": "parameter_schema",
                "rootNodeId": "lotus_core",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(r2.status_code, 422)

        # 手工 IR 派生后再请求 parameter_schema -> 422 ASSISTANT_UNSUPPORTED_BASE
        manual_gen = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": gen_id,
                "ir": TEMPLATE_IR,
                "backend": "canvas2d",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(manual_gen.status_code, 201)
        r3 = self.client.post(
            f"{API}/assistant/proposals",
            json={
                "baseGenerationId": manual_gen.json()["id"],
                "prompt": "新增颜色参数",
                "target": "parameter_schema",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(r3.status_code, 422)
        self.assertEqual(r3.json()["error"]["code"], "ASSISTANT_UNSUPPORTED_BASE")

    def test_auto_repair_attempt_2_succeeds_and_idempotent_apply(self) -> None:
        csrf, uid = self._register_and_login("x1_user_repair")
        _, gen_id = self._seed_template_and_generation(uid, csrf)

        # 第 1 次故意输出节点 ID 冲突 + 越界坐标；第 2 次修复为合法子树
        attempt1_bad = {
            "rootNodeId": "lotus_core",
            "replacementNodes": [
                {
                    "id": "outer_ring",  # 故意与子树外节点冲突
                    "parentId": "scene_root",
                    "type": "circle",
                    "geometry": {"cx": 960, "cy": 300, "r": 80},
                    "style": {
                        "fill": "#D98C8C",
                        "stroke": "#6A2E2E",
                        "strokeWidth": 2,
                        "opacity": 0.95,
                    },
                }
            ],
        }
        attempt2_fixed = {
            "rootNodeId": "lotus_core",
            "replacementNodes": [
                {
                    "id": "lotus_core_group",
                    "parentId": "scene_root",
                    "type": "group",
                    "style": {"opacity": 1},
                },
                {
                    "id": "lotus_petal_inner",
                    "parentId": "lotus_core_group",
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 52},
                    "style": {
                        "fill": "#E8B4B8",
                        "stroke": "#8B3A3A",
                        "strokeWidth": 2,
                        "opacity": 0.95,
                    },
                },
                {
                    "id": "lotus_petal_top",
                    "parentId": "lotus_core_group",
                    "type": "polygon",
                    "geometry": {"points": [[400, 220], [426, 268], [374, 268]]},
                    "style": {
                        "fill": "#F3D7DA",
                        "stroke": "#8B3A3A",
                        "strokeWidth": 1.5,
                        "opacity": 0.9,
                    },
                },
            ],
        }
        responses = iter(
            [
                LlmResponse(
                    content=json.dumps(attempt1_bad, ensure_ascii=False),
                    model="deepseek-chat",
                ),
                LlmResponse(
                    content=json.dumps(attempt2_fixed, ensure_ascii=False),
                    model="deepseek-chat",
                ),
            ]
        )

        with patch(
            "app.engine.llm_client.chat_json",
            side_effect=lambda **kwargs: next(responses),
        ):
            created = self.client.post(
                f"{API}/assistant/proposals",
                json={
                    "baseGenerationId": gen_id,
                    "prompt": "把莲心改造成双层清雅莲瓣",
                    "target": "ir_fragment",
                    "rootNodeId": "lotus_core",
                },
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(created.status_code, 202, created.text)
            body = created.json()
            self.assertIsNone(body["proposal"])
            proposal_id = body["id"]

            with self.sessions() as db:
                assistant_service.wait_for(db, proposal_id, timeout=15.0)

        fetched = self.client.get(f"{API}/assistant/proposals/{proposal_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        prop = fetched.json()
        self.assertEqual(prop["status"], "ready")
        self.assertEqual(prop["attempts"], 2)
        self.assertTrue(prop["validation"]["isolatedValidationPassed"])
        self.assertGreater(len(prop["validation"]["repairedIssues"]), 0)
        self.assertIsNotNone(prop["proposal"])
        self.assertIsNotNone(prop["proposalHash"])

        # 显式 apply + 重复 apply 幂等返回同一 generationId
        app1 = self.client.post(
            f"{API}/assistant/proposals/{proposal_id}/apply",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(app1.status_code, 201, app1.text)
        gen1 = app1.json()
        self.assertEqual(gen1["provenance"]["assistant"]["proposalId"], proposal_id)
        self.assertEqual(gen1["provenance"]["assistant"]["attempts"], 2)

        app2 = self.client.post(
            f"{API}/assistant/proposals/{proposal_id}/apply",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(app2.status_code, 201, app2.text)
        self.assertEqual(app2.json()["id"], gen1["id"])

        # 派生继承：IR 编辑、子树替换、DSL 沙箱都保留 provenance.assistant 且纳入 dependencySnapshotHash
        derived_ir = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": gen1["id"],
                "ir": gen1["ir"],
                "backend": "canvas2d",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(derived_ir.status_code, 201, derived_ir.text)
        self.assertEqual(
            derived_ir.json()["provenance"]["assistant"]["proposalId"], proposal_id
        )

        derived_sub = self.client.post(
            f"{API}/generations/{gen1['id']}/subgraph",
            json={
                "rootNodeId": "outer_ring",
                "replacementNodes": [
                    {
                        "id": "outer_ring_v2",
                        "parentId": "scene_root",
                        "type": "circle",
                        "geometry": {"cx": 400, "cy": 300, "r": 115},
                        "style": {
                            "fill": None,
                            "stroke": "#2E5B4C",
                            "strokeWidth": 3,
                            "opacity": 0.9,
                        },
                    }
                ],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(derived_sub.status_code, 201, derived_sub.text)
        self.assertEqual(
            derived_sub.json()["generation"]["provenance"]["assistant"]["proposalId"],
            proposal_id,
        )

        # DSL 沙箱派生也保留 assistant
        run_res = self.client.post(
            f"{API}/sandbox-runs",
            json={
                "baseGenerationId": gen1["id"],
                "source": json.dumps(
                    {
                        "version": 1,
                        "ops": [{"op": "circle", "cx": 200, "cy": 150, "r": 12, "fill": "#4A6B5D"}],
                    }
                ),
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(run_res.status_code, 202, run_res.text)
        with self.sessions() as db:
            run_row = sandbox_service.wait_for(db, run_res.json()["id"], timeout=15.0)
            self.assertEqual(run_row.status, "succeeded")
            frag_gen = db.get(GenerationModel, run_row.generation_id)
            assert frag_gen is not None
            frag_prov = json.loads(frag_gen.provenance_json)
            self.assertEqual(frag_prov["assistant"]["proposalId"], proposal_id)

            # dependencySnapshotHash 包含 assistant 来源
            base_row = db.get(GenerationModel, gen_id)
            applied_row = db.get(GenerationModel, gen1["id"])
            assert base_row is not None and applied_row is not None
            h_without = works_service._dependency_snapshot_hash(base_row, "codepoet-canvas-1.0.0")  # noqa: SLF001
            h_with = works_service._dependency_snapshot_hash(applied_row, "codepoet-canvas-1.0.0")  # noqa: SLF001
            self.assertNotEqual(h_without, h_with)

    def test_both_attempts_failed_returns_validation_failed_and_apply_409(self) -> None:
        csrf, uid = self._register_and_login("x1_user_fail")
        _, gen_id = self._seed_template_and_generation(uid, csrf)

        bad_payload = json.dumps(
            {
                "rootNodeId": "lotus_core",
                "replacementNodes": [
                    {
                        "id": "outer_ring",
                        "parentId": "missing_parent",
                        "type": "circle",
                        "geometry": {"cx": 999, "cy": 999, "r": 20},
                        "style": {"fill": "not-a-color", "stroke": None, "strokeWidth": 0, "opacity": 2},
                    }
                ],
            }
        )
        with patch(
            "app.engine.llm_client.chat_json",
            return_value=LlmResponse(content=bad_payload, model="deepseek-chat"),
        ):
            created = self.client.post(
                f"{API}/assistant/proposals",
                json={
                    "baseGenerationId": gen_id,
                    "prompt": "测试两次校验均失败",
                    "target": "ir_fragment",
                    "rootNodeId": "lotus_core",
                },
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(created.status_code, 202)
            proposal_id = created.json()["id"]
            with self.sessions() as db:
                assistant_service.wait_for(db, proposal_id, timeout=15.0)

        fetched = self.client.get(f"{API}/assistant/proposals/{proposal_id}").json()
        self.assertEqual(fetched["status"], "failed")
        self.assertEqual(fetched["attempts"], 2)
        self.assertIsNone(fetched["proposal"])
        self.assertEqual(fetched["error"]["code"], "ASSISTANT_VALIDATION_FAILED")

        apply_res = self.client.post(
            f"{API}/assistant/proposals/{proposal_id}/apply",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(apply_res.status_code, 409)
        self.assertEqual(apply_res.json()["error"]["code"], "INVALID_STATE")

    def test_parameter_schema_proposal_does_not_mutate_template(self) -> None:
        csrf, uid = self._register_and_login("x1_user_ps")
        tplv_id, gen_id = self._seed_template_and_generation(uid, csrf)

        ps_proposal = {
            "parameterSchema": [
                {
                    "name": "coreRadius",
                    "label": "莲心半径",
                    "description": "中心莲心半径大小",
                    "type": "number",
                    "default": 48,
                    "min": 20,
                    "max": 100,
                    "step": 2,
                },
                {
                    "name": "ringColor",
                    "label": "外环青墨色",
                    "description": "外环描边颜色",
                    "type": "color",
                    "default": "#2F5D50",
                },
            ],
            "bindings": [
                {"parameter": "coreRadius", "nodeId": "lotus_core", "property": "geometry.r"},
                {"parameter": "ringColor", "nodeId": "outer_ring", "property": "style.stroke"},
            ],
            "defaults": {"coreRadius": 48, "ringColor": "#2F5D50"},
        }

        with patch(
            "app.engine.llm_client.chat_json",
            return_value=LlmResponse(
                content=json.dumps(ps_proposal, ensure_ascii=False),
                model="deepseek-chat",
            ),
        ):
            created = self.client.post(
                f"{API}/assistant/proposals",
                json={
                    "baseGenerationId": gen_id,
                    "prompt": "扩展外环颜色与莲心半径参数空间",
                    "target": "parameter_schema",
                },
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(created.status_code, 202, created.text)
            proposal_id = created.json()["id"]
            with self.sessions() as db:
                assistant_service.wait_for(db, proposal_id, timeout=15.0)

        applied = self.client.post(
            f"{API}/assistant/proposals/{proposal_id}/apply",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(applied.status_code, 201, applied.text)
        gen = applied.json()
        self.assertEqual(gen["params"]["coreRadius"], 48.0)
        self.assertEqual(gen["params"]["ringColor"], "#2F5D50")
        self.assertEqual(gen["provenance"]["assistant"]["proposalId"], proposal_id)

        # 确认数据库中的原 TemplateVersion 未被修改
        with self.sessions() as db:
            ver = db.get(TemplateVersion, tplv_id)
            assert ver is not None
            self.assertEqual(json.loads(ver.parameter_schema_json), TEMPLATE_SCHEMA)
            self.assertEqual(json.loads(ver.bindings_json), TEMPLATE_BINDINGS)

    def test_cross_user_404_and_permission_revocation_on_apply(self) -> None:
        csrf_a, uid_a = self._register_and_login("x1_owner_a")
        _, gen_a = self._seed_template_and_generation(uid_a, csrf_a)

        # 给用户 A 建一件已公开作品，使 B 暂时可以读取 gen_a
        with self.sessions() as db:
            job = RenderJob(
                id="job-x1-pub",
                owner_id=uid_a,
                generation_id=gen_a,
                backend="canvas2d",
                execution_mode="browser_upload",
                status="succeeded",
                phase="completed",
                times_ms_json="[0]",
                policy_revision=1,
                renderer_version="codepoet-canvas-1.0.0",
                client_environment_json='{"userAgent":"test","deviceScaleFactor":1}',
                revision=1,
                created_at="2026-11-20T00:00:00+00:00",
                updated_at="2026-11-20T00:00:00+00:00",
            )
            work = Work(
                id="work-x1-pub",
                owner_id=uid_a,
                title="公开清莲",
                description="",
                published_version_id="wv-x1-pub-1",
                latest_version_id="wv-x1-pub-1",
                revision=1,
                created_at="2026-11-20T00:00:00+00:00",
                updated_at="2026-11-20T00:00:00+00:00",
            )
            wv = WorkVersion(
                id="wv-x1-pub-1",
                work_id=work.id,
                version_number=1,
                status="published",
                title="公开清莲",
                description="",
                generation_id=gen_a,
                render_job_id=job.id,
                created_by=uid_a,
                change_note="",
                published_at="2026-11-20T00:00:00+00:00",
                public_revoked_at=None,
                revision=1,
                created_at="2026-11-20T00:00:00+00:00",
                updated_at="2026-11-20T00:00:00+00:00",
            )
            db.add_all([job, work, wv])
            db.commit()

        # 切换到用户 B：基于公开快照 gen_a 创建提案成功
        csrf_b, _ = self._register_and_login("x1_reader_b")
        ok_proposal = {
            "rootNodeId": "lotus_core",
            "replacementNodes": [
                {
                    "id": "lotus_core_b",
                    "parentId": "scene_root",
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 50},
                    "style": {"fill": "#E8B4B8", "stroke": "#8B3A3A", "strokeWidth": 2, "opacity": 1},
                }
            ],
        }
        with patch(
            "app.engine.llm_client.chat_json",
            return_value=LlmResponse(content=json.dumps(ok_proposal), model="deepseek-chat"),
        ):
            res_b = self.client.post(
                f"{API}/assistant/proposals",
                json={
                    "baseGenerationId": gen_a,
                    "prompt": "缩小莲心半径",
                    "target": "ir_fragment",
                    "rootNodeId": "lotus_core",
                },
                headers={"X-CSRF-Token": csrf_b},
            )
            self.assertEqual(res_b.status_code, 202, res_b.text)
            prop_b = res_b.json()["id"]
            with self.sessions() as db:
                assistant_service.wait_for(db, prop_b, timeout=15.0)

        # 用户 A 读/应用 B 的提案 -> 404
        csrf_a2, _ = self._register_and_login("x1_owner_a")
        self.assertEqual(self.client.get(f"{API}/assistant/proposals/{prop_b}").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"{API}/assistant/proposals/{prop_b}/apply",
                headers={"X-CSRF-Token": csrf_a2},
            ).status_code,
            404,
        )

        # 撤回公开作品的可见性，再切回 B 尝试 apply -> 404 拒绝越权
        with self.sessions() as db:
            wv_row = db.get(WorkVersion, "wv-x1-pub-1")
            w_row = db.get(Work, "work-x1-pub")
            assert wv_row is not None and w_row is not None
            wv_row.public_revoked_at = "2026-11-20T01:00:00+00:00"
            w_row.published_version_id = None
            db.commit()

        csrf_b2, _ = self._register_and_login("x1_reader_b")
        revoked_apply = self.client.post(
            f"{API}/assistant/proposals/{prop_b}/apply",
            headers={"X-CSRF-Token": csrf_b2},
        )
        self.assertEqual(revoked_apply.status_code, 404)

    def test_recover_orphans(self) -> None:
        csrf, uid = self._register_and_login("x1_user_orphan")
        _, gen_id = self._seed_template_and_generation(uid, csrf)
        with self.sessions() as db:
            orphan = AssistantProposalModel(
                id="prop-orphan-1",
                owner_id=uid,
                base_generation_id=gen_id,
                target="ir_fragment",
                root_node_id="lotus_core",
                prompt="test orphan",
                prompt_hash="a" * 64,
                status="running",
                model="deepseek-chat",
                attempts=1,
                policy_revision=1,
                request_id="req-orphan",
                created_at="2026-11-20T00:00:00+00:00",
                finished_at=None,
            )
            db.add(orphan)
            db.commit()
            recovered = assistant_service.recover_orphans(db)
            self.assertEqual(recovered, 1)
            db.refresh(orphan)
            self.assertEqual(orphan.status, "failed")
            self.assertIsNotNone(orphan.finished_at)
            self.assertEqual(json.loads(orphan.error_json)["code"], "ASSISTANT_INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
