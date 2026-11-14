"""S1-A2 沙箱执行的集成测试。

用真实 SQLite + 真实子进程，不 mock 执行体——这一节的验收点全在
「隔离进程真的跑起来了」「超限真的被熔断了」「失败真的留了可追溯的 run」，
把 worker 换成假的就什么都测不出来。

跑得慢一点是有意的：每个成功用例都要起一个 Python 子进程。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_database, create_session_factory
from app.main import create_app
from app.models.creations import Generation as GenerationModel
from app.models.sandbox import SandboxRun as SandboxRunModel
from app.schemas.sandbox import SandboxCreate
from app.services import policy as policy_service
from app.services import sandbox as sandbox_service
from app.services.sandbox import WorkerResult
from app.settings import Settings

API = "/api/v1"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

BASE_IR = {
    "schemaVersion": "1.0",
    "seed": 42,
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


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


def source(*ops: dict) -> str:
    return json.dumps({"version": 1, "ops": list(ops)}, ensure_ascii=False)


def circle(cx: float = 100, cy: float = 100, r: float = 20) -> dict:
    return {"op": "circle", "cx": cx, "cy": cy, "r": r, "fill": "#3366CC"}


class SandboxTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None, data_dir=Path(self.tmp.name) / "runtime"
        )
        # 先迁移再进 lifespan：lifespan 里的孤儿清理要查 sandbox_runs
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.app = create_app(self.settings)
        apply_migrations(self.settings.database_path)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

        self.engine = create_database(self.settings)
        self.addCleanup(self.engine.dispose)
        self.sessions = create_session_factory(self.engine)

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
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": "Aa1!" + "x" * 12},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self._login_body = body
        return body["csrfToken"]

    def _user_id(self) -> str:
        return self._login_body["user"]["id"]

    def _seed_generation(self, owner_id: str, gen_id: str = "gen-base") -> str:
        with self.sessions() as db:
            db.add(
                GenerationModel(
                    id=gen_id,
                    owner_id=owner_id,
                    mode="ir",
                    backend="canvas2d",
                    template_version_id=None,
                    params_json="{}",
                    seed=42,
                    ir_json=json.dumps(BASE_IR),
                    ir_hash="a" * 64,
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
                    created_at="2026-11-14T00:00:00+00:00",
                )
            )
            db.commit()
        return gen_id

    def _post_run(self, csrf: str, base_id: str, src: str):
        return self.client.post(
            f"{API}/sandbox-runs",
            json={"baseGenerationId": base_id, "source": src},
            headers={"X-CSRF-Token": csrf},
        )

    def _wait(self, run_id: str, timeout: float = 20.0):
        with self.sessions() as db:
            return sandbox_service.wait_for(db, run_id, timeout=timeout)

    def _run_and_wait(self, csrf: str, base_id: str, src: str) -> dict:
        response = self._post_run(csrf, base_id, src)
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["id"]
        row = self._wait(run_id)
        body = self.client.get(f"{API}/sandbox-runs/{run_id}")
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(body.json()["status"], row.status)
        return body.json()

    def _as_admin(self, username: str) -> str:
        """建号 → 改 role → 重新登录，返回新的 CSRF。

        重新登录是必须的：登录会换一个新会话，旧 token 立刻失效。
        """
        self._new_user(username)
        self._login(username)
        self._set_admin(username)
        return self._login(username)

    def _set_admin(self, username: str) -> None:
        """直接改库把角色提成 admin：测试不该依赖审批流。"""
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE users SET role = 'admin' WHERE username = ?", (username,)
        )
        connection.commit()
        connection.close()

    def _patch_policy(self, csrf: str, **overrides):
        current = self.client.get(f"{API}/admin/policies").json()
        values = dict(current["values"])
        values.update(overrides)
        return self.client.patch(
            f"{API}/admin/policies",
            json={"expectedRevision": current["revision"], "values": values},
            headers={"X-CSRF-Token": csrf},
        )


class AcceptTests(SandboxTestBase):
    """受理：202 + queued，且响应里不该有源码或未发生的指标。"""

    def test_create_returns_202_with_queued_run(self) -> None:
        self._new_user("owner1")
        csrf = self._login("owner1")
        base = self._seed_generation(self._user_id())

        response = self._post_run(csrf, base, source(circle()))
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertIsNone(body["generationId"])
        self.assertIsNone(body["metrics"])
        self.assertIsNone(body["error"])
        self.assertEqual(body["baseGenerationId"], base)
        self.assertEqual(len(body["sourceHash"]), 64)
        self.assertRegex(body["sourceHash"], r"^[0-9a-f]{64}$")
        # 源码不许出现在响应里
        self.assertNotIn("source", body)
        # 策略快照要说清本次生效的限额
        snapshot = body["policySnapshot"]
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(snapshot["dslWallTimeMs"], 2000)
        self.assertEqual(
            snapshot["enabledDslFunctions"],
            ["circle", "polygon", "polyline", "set_param"],
        )

        # 让它跑完，别把执行器留到下一个测试
        self._wait(body["id"])

    def test_unknown_base_is_404(self) -> None:
        self._new_user("owner2")
        csrf = self._login("owner2")
        response = self._post_run(csrf, "gen-does-not-exist", source(circle()))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_other_users_base_is_404(self) -> None:
        """别人的 base 与不存在返回同一个 404：不暴露它存在。"""
        self._new_user("ownerA")
        self._login("ownerA")
        base = self._seed_generation(self._user_id(), gen_id="gen-private")

        self._new_user("ownerB")
        csrf_b = self._login("ownerB")
        response = self._post_run(csrf_b, base, source(circle()))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_source_over_16kib_is_422(self) -> None:
        self._new_user("owner3")
        csrf = self._login("owner3")
        base = self._seed_generation(self._user_id())
        # Pydantic 的 max_length 是按字符算的，这里给足 16385 个 ASCII 字符
        response = self._post_run(csrf, base, "x" * 16385)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_empty_source_is_422(self) -> None:
        self._new_user("owner4")
        csrf = self._login("owner4")
        base = self._seed_generation(self._user_id())
        response = self._post_run(csrf, base, "")
        self.assertEqual(response.status_code, 422, response.text)

    def test_requires_login(self) -> None:
        response = self.client.post(
            f"{API}/sandbox-runs",
            json={"baseGenerationId": "gen-x", "source": source(circle())},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_requires_csrf(self) -> None:
        self._new_user("owner5")
        self._login("owner5")
        base = self._seed_generation(self._user_id())
        response = self.client.post(
            f"{API}/sandbox-runs",
            json={"baseGenerationId": base, "source": source(circle())},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "CSRF_INVALID")


class ExecuteTests(SandboxTestBase):
    """执行成功：产出不可变 fragment generation，base 原样保留。"""

    def test_success_creates_fragment_generation(self) -> None:
        self._new_user("runner1")
        csrf = self._login("runner1")
        base = self._seed_generation(self._user_id())

        body = self._run_and_wait(csrf, base, source(circle(120, 140, 30)))
        self.assertEqual(body["status"], "succeeded", body.get("error"))
        generation_id = body["generationId"]
        self.assertIsNotNone(generation_id)
        self.assertIsNone(body["error"])
        self.assertIsNotNone(body["finishedAt"])
        self.assertIsNotNone(body["startedAt"])

        # 指标必须来自真实执行：至少要画了一次、走了若干步
        metrics = body["metrics"]
        self.assertEqual(metrics["drawCalls"], 1)
        self.assertGreater(metrics["steps"], 0)
        # 峰值内存取不到时必须是 null，不能填 0 装作「没占内存」
        self.assertIn("peakMemoryMiB", metrics)

        generation = self.client.get(f"{API}/generations/{generation_id}")
        self.assertEqual(generation.status_code, 200, generation.text)
        gen = generation.json()
        self.assertEqual(gen["mode"], "fragment")
        self.assertEqual(gen["backend"], "canvas2d")
        self.assertEqual(gen["provenance"]["kind"], "fragment")
        self.assertEqual(gen["provenance"]["baseGenerationId"], base)
        self.assertEqual(gen["provenance"]["fragmentSourceHash"], body["sourceHash"])
        self.assertEqual(gen["provenance"]["policyRevision"], 1)

        # base 的节点原样保留，新图元挂在末尾 root group 下
        ids = [node["id"] for node in gen["ir"]["nodes"]]
        self.assertIn("c1", ids)
        self.assertEqual(len(ids), 3)  # c1 + 新圆 + root group
        root_id = f"g-{body['id']}"
        self.assertIn(root_id, ids)
        appended = gen["ir"]["nodes"][1]
        self.assertEqual(appended["parentId"], root_id)

    def test_static_rejection_still_leaves_a_run(self) -> None:
        """AST 违规：run 必须留痕，generationId 为 null。"""
        self._new_user("runner2")
        csrf = self._login("runner2")
        base = self._seed_generation(self._user_id())

        body = self._run_and_wait(
            csrf, base, source({"op": "read_file", "path": "/etc/passwd"})
        )
        self.assertEqual(body["status"], "failed")
        self.assertIsNone(body["generationId"])
        self.assertEqual(body["error"]["code"], "DSL_AUDIT_REJECTED")
        self.assertIsNotNone(body["finishedAt"])
        # 静态拒绝也要给一条可定位的审计发现，且 ruleId 是**规则 ID**
        # 而不是错误码——两者语义不同，前端要据 rule 才知道该改哪里
        self.assertGreaterEqual(len(body["auditFindings"]), 1)
        finding = body["auditFindings"][0]
        self.assertEqual(finding["ruleId"], "DSL_OP_WHITELIST")
        self.assertNotEqual(finding["ruleId"], "DSL_AUDIT_REJECTED")

    def test_invalid_json_source_is_audit_rejected(self) -> None:
        self._new_user("runner3")
        csrf = self._login("runner3")
        base = self._seed_generation(self._user_id())
        body = self._run_and_wait(csrf, base, "{not json")
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "DSL_AUDIT_REJECTED")
        self.assertIsNone(body["generationId"])

    def test_call_limit_is_terminal(self) -> None:
        """策略把绘图调用上限压到 1，提交两个圆必须被拒。"""
        self._new_user("runner4")
        csrf = self._login("runner4")
        base = self._seed_generation(self._user_id())
        self._set_admin("runner4")
        # 重新登录会换一个新会话，旧的 CSRF 立刻失效——必须换回新 token，
        # 否则这里会先撞 403，把真正要验的「策略生效」挡在后面。
        csrf = self._login("runner4")

        patched = self._patch_policy(csrf, dslMaxDrawCalls=1)
        self.assertEqual(patched.status_code, 200, patched.text)

        body = self._run_and_wait(
            csrf, base, source(circle(100, 100, 10), circle(200, 200, 10))
        )
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "DSL_CALL_LIMIT")
        self.assertIsNone(body["generationId"])

    def test_disabled_function_is_rejected(self) -> None:
        """策略关掉 polygon 之后再用它，属于策略拒绝而不是静默降级。"""
        self._new_user("runner5")
        csrf = self._login("runner5")
        base = self._seed_generation(self._user_id())
        self._set_admin("runner5")
        csrf = self._login("runner5")

        patched = self._patch_policy(
            csrf, enabledDslFunctions=["circle", "polyline", "set_param"]
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        polygon = {
            "op": "polygon",
            "points": [[0, 0], [10, 0], [10, 10]],
            "fill": "#112233",
        }
        body = self._run_and_wait(csrf, base, source(polygon))
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "DSL_AUDIT_REJECTED")
        self.assertIn("polygon", body["error"]["message"])


class OwnershipTests(SandboxTestBase):
    def test_other_user_cannot_read_run(self) -> None:
        self._new_user("mine")
        csrf = self._login("mine")
        base = self._seed_generation(self._user_id())
        run = self._run_and_wait(csrf, base, source(circle()))
        self._wait(run["id"])

        self._new_user("theirs")
        self._login("theirs")
        response = self.client.get(f"{API}/sandbox-runs/{run['id']}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_base_must_be_owned_not_merely_readable(self) -> None:
        """administrator 能读别人的快照，但不该能拿它跑 DSL。

        OpenAPI 的 POST /sandbox-runs 写的是「基础 generation 必须本人拥有」。
        上一版用 ``can_read``，管理员（以及任何能读公开作品的人）都能拿别人的
        快照当 base——产出的 fragment 归自己，来源链却写成「我改了别人的图」。
        """
        self._new_user("author")
        self._login("author")
        base = self._seed_generation(self._user_id(), gen_id="gen-author")

        self._new_user("boss")
        csrf_boss = self._login("boss")
        self._set_admin("boss")
        csrf_boss = self._login("boss")

        response = self._post_run(csrf_boss, base, source(circle()))
        self.assertEqual(response.status_code, 404, response.text)
        # 不是 403：不告诉管理员「这条快照存在但不是你的」
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_source_never_leaks_through_generation_provenance(self) -> None:
        """源码不能经 provenance 暴露给公开读者。

        provenance 会随 ``GET /generations/{id}`` 返回，而作品一旦发布，
        ``GET /work-versions/{id}`` 是匿名可读的、详情里原样带着 provenance。
        把 source 写进 provenance，等于作品公开的一刻源码就对所有人可见。
        """
        self._new_user("leak")
        csrf = self._login("leak")
        base = self._seed_generation(self._user_id())

        # 用一段特征明显的源码，便于断言「它没有出现在任何响应里」
        marker = "#LEAKCANARY"
        src = source({"op": "circle", "cx": 10, "cy": 10, "r": 5, "fill": "#ABCDEF"})
        body = self._run_and_wait(csrf, base, src)
        self.assertEqual(body["status"], "succeeded", body.get("error"))

        generation = self.client.get(f"{API}/generations/{body['generationId']}")
        self.assertEqual(generation.status_code, 200, generation.text)
        provenance = generation.json()["provenance"]
        self.assertIsNone(
            provenance["fragmentSource"],
            "provenance 不该带源码，公开作品会连带把源码公开",
        )
        # 但要留得住溯源：hash 还在，且与 run 的一致
        self.assertEqual(provenance["fragmentSourceHash"], body["sourceHash"])

        # 响应里也不该出现源码文本本身
        self.assertNotIn(marker, generation.text)
        run_response = self.client.get(f"{API}/sandbox-runs/{body['id']}")
        self.assertNotIn("source", run_response.json())


class BudgetTests(SandboxTestBase):
    """三档预算与退出码映射。不靠运气：直接验判据本身。"""

    def _payload(self) -> dict:
        return {
            "protocol": 1,
            "runId": "run-budget",
            "source": source(circle()),
            "baseIr": BASE_IR,
            "limits": {"maxDrawCalls": 1000, "maxSteps": 10000, "maxAstNodes": 2000},
            "cpuMs": 1000,
            "memoryMiB": 256,
            "wallMs": 2000,
            "enabledFunctions": ["circle", "polygon", "polyline", "set_param"],
            "paramContext": None,
        }

    def test_memory_watchdog_kills_worker(self) -> None:
        """把内存上限压到 1MiB：Python 本体就超了，必须被立刻终止。

        这条比「构造一段吃内存的源码」更可靠——它验的是守门机制本身，
        而不是某段特定代码碰巧吃了多少内存。

        退出码可能是 worker 看门狗的 71，也可能是 Job Object 内存上限触发后
        内核给的 0xC0000017；两者都算内存超限，判据统一在 ``is_memory_exit``。
        """
        payload = self._payload()
        # worker 看门狗用的是 payload 里的 memoryMiB（= 策略值）；
        # _run_worker 的参数只管 Job Object 那层内核兜底。
        payload["memoryMiB"] = 1
        result = sandbox_service._run_worker(
            payload, wall_seconds=20.0, job_memory_mib=64
        )
        self.assertTrue(
            sandbox_service.is_memory_exit(result.returncode),
            f"returncode={result.returncode} stderr={result.stderr[:300]!r}",
        )
        self.assertFalse(result.timed_out)

    def test_parent_wall_deadline_reclaims_process(self) -> None:
        """父进程 wall 兜底：给一个不可能完成的期限，必须收回并标记超时。"""
        result = sandbox_service._run_worker(
            self._payload(), wall_seconds=0.001, job_memory_mib=256
        )
        self.assertTrue(result.timed_out)

    def test_cpu_exit_code_maps_to_cpu_limit(self) -> None:
        """退出码 70 → DSL_CPU_LIMIT。用打桩的 WorkerResult 精确验映射。"""
        self._new_user("cpu")
        csrf = self._login("cpu")
        base = self._seed_generation(self._user_id())

        run_id = self._create_queued_run(csrf, base)
        original = sandbox_service._run_worker

        def fake(*_args, **_kwargs):
            return WorkerResult(70, b"", b"", timed_out=False)

        sandbox_service._run_worker = fake
        try:
            with self.sessions() as db:
                sandbox_service.execute_run(db, run_id)
        finally:
            sandbox_service._run_worker = original

        body = self.client.get(f"{API}/sandbox-runs/{run_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "DSL_CPU_LIMIT")

    def test_memory_exit_code_maps_to_memory_limit(self) -> None:
        self._new_user("mem")
        csrf = self._login("mem")
        base = self._seed_generation(self._user_id())

        run_id = self._create_queued_run(csrf, base)
        original = sandbox_service._run_worker

        def fake(*_args, **_kwargs):
            return WorkerResult(71, b"", b"", timed_out=False)

        sandbox_service._run_worker = fake
        try:
            with self.sessions() as db:
                sandbox_service.execute_run(db, run_id)
        finally:
            sandbox_service._run_worker = original

        body = self.client.get(f"{API}/sandbox-runs/{run_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "DSL_MEMORY_LIMIT")

    def _create_queued_run(self, csrf: str, base_id: str) -> str:
        """直接建一条 queued 的 run，绕开执行器——这些用例要自己决定怎么跑。"""
        with self.sessions() as db:
            from app.models.users import User as UserModel

            user = db.get(UserModel, self._user_id())
            run = sandbox_service.create_run(
                db,
                user,
                SandboxCreate(baseGenerationId=base_id, source=source(circle())),
                "req-test",
            )
        return run.id


class QuotaTests(SandboxTestBase):
    def test_third_active_run_is_429(self) -> None:
        self._new_user("quota")
        csrf = self._login("quota")
        base = self._seed_generation(self._user_id())

        # 手工造两条未完成的 run：靠真实执行很难让它们停在 queued，
        # 而这里要验的正是「配额计数」这条判据。
        with self.sessions() as db:
            from app.models.users import User as UserModel

            user = db.get(UserModel, self._user_id())
            for _ in range(sandbox_service.MAX_ACTIVE_RUNS_PER_USER):
                sandbox_service.create_run(
                    db,
                    user,
                    SandboxCreate(baseGenerationId=base, source=source(circle())),
                    "req-test",
                )

        response = self._post_run(csrf, base, source(circle()))
        self.assertEqual(response.status_code, 429, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "EXECUTION_BUSY")
        self.assertEqual(error["details"]["maxActiveRuns"], 2)

    def test_orphans_are_recovered_on_restart(self) -> None:
        """进程重启后遗留的 queued/running 必须变成 failed，配额随之释放。"""
        self._new_user("orphan")
        csrf = self._login("orphan")
        base = self._seed_generation(self._user_id())

        with self.sessions() as db:
            from app.models.users import User as UserModel

            user = db.get(UserModel, self._user_id())
            run = sandbox_service.create_run(
                db,
                user,
                SandboxCreate(baseGenerationId=base, source=source(circle())),
                "req-test",
            )
            run_id = run.id

        with self.sessions() as db:
            recovered = sandbox_service.recover_orphans(db)
            self.assertEqual(recovered, 1)
            row = db.get(SandboxRunModel, run_id)
            self.assertEqual(row.status, "failed")
            self.assertIsNotNone(row.finished_at)
            self.assertEqual(json.loads(row.error_json)["code"], "DSL_EXECUTION_FAILED")
            self.assertIsNone(row.generation_id)

        # 配额应该空出来了
        response = self._post_run(csrf, base, source(circle()))
        self.assertEqual(response.status_code, 202, response.text)
        self._wait(response.json()["id"])


class ConcurrencyTests(SandboxTestBase):
    """两处并发防护。

    配额那条是**概率性**验证：串行执行时也会得到「2 成功 2 拒绝」，
    所以它只能证明不超限，证明不了原子性——原子性由 ``BEGIN IMMEDIATE``
    保证，见 ``test_create_run_opens_exclusive_transaction``。
    乐观锁那条是确定性的，见 ``test_policy_patch_rejects_stale_identity_map``。
    """

    def test_create_run_opens_exclusive_transaction(self) -> None:
        """计数与插入必须在同一个 IMMEDIATE 事务里。

        这里的判据是「create_run 期间出现了一次 BEGIN IMMEDIATE」，
        而不是「并发时没超限」——后者要复现竞态窗口，单机测试不可靠。
        """
        self._new_user("txn")
        csrf = self._login("txn")
        base = self._seed_generation(self._user_id())
        statements: list[str] = []

        with self.sessions() as db:
            from sqlalchemy import event

            from app.models.users import User as UserModel

            def record(conn, cursor, statement, params, context, executemany):
                statements.append(statement)

            event.listen(self.engine, "before_cursor_execute", record)
            try:
                user = db.get(UserModel, self._user_id())
                sandbox_service.create_run(
                    db,
                    user,
                    SandboxCreate(baseGenerationId=base, source=source(circle())),
                    "req-test",
                )
            finally:
                event.remove(self.engine, "before_cursor_execute", record)

        self.assertTrue(
            any("BEGIN IMMEDIATE" in item for item in statements),
            "配额检查没有开独占写事务，并发时会突破上限",
        )
        # 顺序也要对：先拿锁、再 COUNT、最后 INSERT
        lock_at = next(i for i, s in enumerate(statements) if "BEGIN IMMEDIATE" in s)
        count_at = next(i for i, s in enumerate(statements) if "count(" in s.lower())
        insert_at = next(
            i for i, s in enumerate(statements) if s.lstrip().upper().startswith("INSERT")
        )
        self.assertLess(lock_at, count_at)
        self.assertLess(count_at, insert_at)

    def test_concurrent_submissions_do_not_exceed_quota(self) -> None:
        self._new_user("race")
        self._login("race")
        base = self._seed_generation(self._user_id())
        owner = self._user_id()

        from typing import Any

        from app.errors import ApiError
        from app.models.users import User as UserModel

        outcomes: list[Any] = []
        guard = threading.Lock()
        start = threading.Barrier(4)

        def submit() -> None:
            db = self.sessions()
            try:
                user = db.get(UserModel, owner)
                start.wait(timeout=10)
                run = sandbox_service.create_run(
                    db,
                    user,
                    SandboxCreate(baseGenerationId=base, source=source(circle())),
                    "req-test",
                )
                with guard:
                    outcomes.append(("ok", run.id))
            except ApiError as exc:
                with guard:
                    outcomes.append((exc.code, None))
            except Exception as exc:  # noqa: BLE001 - 锁等待超时也要让测试说清楚
                with guard:
                    outcomes.append(("error", type(exc).__name__))
            finally:
                db.close()

        threads = [threading.Thread(target=submit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        accepted = [item for item in outcomes if item[0] == "ok"]
        rejected = [item for item in outcomes if item[0] == "EXECUTION_BUSY"]
        self.assertEqual(len(accepted) + len(rejected), 4, outcomes)
        self.assertLessEqual(len(accepted), sandbox_service.MAX_ACTIVE_RUNS_PER_USER)
        # 库里未完成的数量不能超过上限——这才是真正要保的不变式
        with self.sessions() as db:
            active = db.scalar(
                text(
                    "SELECT COUNT(*) FROM sandbox_runs "
                    "WHERE status IN ('queued','running')"
                )
            )
        self.assertLessEqual(active, sandbox_service.MAX_ACTIVE_RUNS_PER_USER)

    def test_policy_patch_rejects_stale_identity_map(self) -> None:
        """乐观锁条件必须在 UPDATE 的 WHERE 里，不能只在 Python 里比较。

        这个用例必须直接调服务层、并且**复用同一个 Session**，否则测不出东西：

        上一版走 HTTP，PATCH 用的是全新 Session，``db.get`` 会从库里读到
        revision=2，于是前置的「1 != 2」比较就返回 409 了——退化掉 WHERE 条件
        测试照样通过，等于什么都没验。

        真正需要区分的是「本 Session 已经缓存了旧值」这种情况：

        * 条件放进 WHERE：UPDATE ... WHERE revision=1 影响 0 行 → 409；
        * 「先比较再赋值」：缓存里还是 1，比较能过，接着把别人的改动覆盖掉
          并返回 200——客户端以为自己的改动生效了，实际抹掉了别人那一版。
        """
        from app.errors import ApiError
        from app.models.sandbox import Policy as PolicyModel
        from app.models.users import User as UserModel
        from app.schemas.sandbox import PolicyUpdate

        self._as_admin("locker")
        admin_id = self._login_body["user"]["id"]

        with self.sessions() as stale:
            row = stale.get(PolicyModel, 1)
            self.assertEqual(row.revision, 1)

            # 另一条连接先把 revision 推到 2（模拟别人抢先改了一版）
            with self.sessions() as other:
                other.execute(text("UPDATE policies SET revision = 2 WHERE id = 1"))
                other.commit()

            # 本 Session 的 identity map 里仍然是 revision=1
            self.assertEqual(stale.get(PolicyModel, 1).revision, 1)

            actor = stale.get(UserModel, admin_id)
            payload = PolicyUpdate(
                expectedRevision=1,
                values=policy_service.DEFAULT_VALUES.model_copy(
                    update={"dslMaxDrawCalls": 500}
                ),
            )
            with self.assertRaises(ApiError) as ctx:
                policy_service.update_policies(stale, actor, payload, "req-test")

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "REVISION_CONFLICT")
        # 库里仍是 2，且值没被这次提交覆盖
        current = self.client.get(f"{API}/admin/policies").json()
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["values"]["dslMaxDrawCalls"], 1000)


class SnapshotBindingTests(SandboxTestBase):
    def test_run_keeps_policy_snapshot_taken_at_submission(self) -> None:
        """受理后改策略，已经排队的 run 必须还用提交时那一版。"""
        self._new_user("snap")
        csrf = self._login("snap")
        base = self._seed_generation(self._user_id())
        self._set_admin("snap")
        csrf = self._login("snap")

        with self.sessions() as db:
            from app.models.users import User as UserModel

            user = db.get(UserModel, self._user_id())
            run = sandbox_service.create_run(
                db,
                user,
                SandboxCreate(
                    baseGenerationId=base,
                    source=source(circle(100, 100, 10), circle(200, 200, 10)),
                ),
                "req-test",
            )
            run_id = run.id

        # 受理之后再收紧到 1 次调用
        patched = self._patch_policy(csrf, dslMaxDrawCalls=1)
        self.assertEqual(patched.status_code, 200, patched.text)

        with self.sessions() as db:
            sandbox_service.execute_run(db, run_id)

        body = self.client.get(f"{API}/sandbox-runs/{run_id}").json()
        # 用旧快照（1000 次）就能画完两个圆；用新快照的话会 DSL_CALL_LIMIT
        self.assertEqual(body["status"], "succeeded", body.get("error"))
        self.assertEqual(body["policyRevision"], 1)
        self.assertEqual(body["policySnapshot"]["dslMaxDrawCalls"], 1000)
        self.assertEqual(body["metrics"]["drawCalls"], 2)


class CapabilityTests(SandboxTestBase):
    def test_capabilities_declare_dsl_sandbox(self) -> None:
        """声明为 true 的前提是真能拉起 worker，而不只是代码写了。"""
        response = self.client.get(f"{API}/engine/capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        features = response.json()["features"]
        self.assertTrue(features["dslSandbox"])
        self.assertTrue(features["advancedPolicies"])

    def test_probe_reports_reason_when_worker_is_broken(self) -> None:
        """自检失败时 503，而不是受理一个永远跑不了的任务。"""
        original = sandbox_service._probe_once
        sandbox_service._probe_once = lambda: (False, "模拟：解释器不可用")
        sandbox_service._probe_result = None
        try:
            self._new_user("probe")
            csrf = self._login("probe")
            base = self._seed_generation(self._user_id())
            response = self._post_run(csrf, base, source(circle()))
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(
                response.json()["error"]["code"], "SANDBOX_UNAVAILABLE"
            )
            # 没有受理就不该留 run
            with self.sessions() as db:
                count = db.scalar(text("SELECT COUNT(*) FROM sandbox_runs"))
            self.assertEqual(count, 0)
        finally:
            sandbox_service._probe_once = original
            sandbox_service._probe_result = None


if __name__ == "__main__":
    unittest.main()
