"""执行策略（/admin/policies）的测试。

这一节的重点不是「能读能写」，而是四条不肯让步的边界：

1. 迁移里插入的初始值必须等于代码常量（手抄两份一定会漂）；
2. 本期只读的字段被改就 422 ``POLICY_FIELD_READ_ONLY``；
3. 任何字段都不能被 PATCH 到超过 core-demo 硬上限；
4. revision 是乐观锁，不是装饰。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.sandbox import PolicyValues
from app.services import policy as policy_service
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


class PolicyTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None, data_dir=Path(self.tmp.name) / "runtime"
        )
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.app = create_app(self.settings)
        apply_migrations(self.settings.database_path)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _csrf(self) -> str:
        return self.client.get(f"{API}/auth/csrf").json()["csrfToken"]

    def _new_user(self, name: str) -> None:
        token = self._csrf()
        self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": "Aa1!" + "x" * 12, "nickname": name},
            headers={"X-CSRF-Token": token},
        )

    def _login(self, name: str) -> str:
        token = self._csrf()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": "Aa1!" + "x" * 12},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def _set_admin(self, name: str) -> None:
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE users SET role = 'admin' WHERE username = ?", (name,)
        )
        connection.commit()
        connection.close()

    def _as_admin(self, name: str = "boss") -> str:
        self._new_user(name)
        self._login(name)
        self._set_admin(name)
        return self._login(name)

    def _policies(self) -> dict:
        response = self.client.get(f"{API}/admin/policies")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _patch(self, csrf: str, values: dict, revision: int | None = None):
        body = {
            "expectedRevision": revision
            if revision is not None
            else self._policies()["revision"],
            "values": values,
        }
        return self.client.patch(
            f"{API}/admin/policies", json=body, headers={"X-CSRF-Token": csrf}
        )

    def _values_with(self, **overrides) -> dict:
        values = dict(self._policies()["values"])
        values.update(overrides)
        return values


class DefaultsTests(PolicyTestBase):
    def test_migration_seeded_values_match_code_defaults(self) -> None:
        """迁移里手写的 JSON 必须与 ``DEFAULT_VALUES`` 完全一致。

        不一致的后果很隐蔽：新库里是迁移那份、被 `_load` 兜底创建的是代码那份，
        同一份代码在不同库上跑出不同的限额，还查不出为什么。
        """
        connection = sqlite3.connect(self.settings.database_path)
        row = connection.execute(
            "SELECT revision, values_json FROM policies WHERE id = 1"
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row, "迁移没有插入初始策略行")
        revision, values_json = row
        self.assertEqual(revision, 1)
        seeded = json.loads(values_json)
        self.assertEqual(
            seeded, policy_service.DEFAULT_VALUES.model_dump(mode="json")
        )

    def test_api_returns_hard_limits_and_audit_rules(self) -> None:
        csrf = self._as_admin()
        body = self._policies()
        self.assertEqual(body["revision"], 1)
        self.assertEqual(body["hardLimits"], policy_service.HARD_LIMITS.model_dump(mode="json"))
        rules = body["auditRules"]
        self.assertGreaterEqual(len(rules), 5)
        # 必需规则不可关闭：全部 enabled
        self.assertTrue(all(rule["enabled"] for rule in rules))
        ids = [rule["ruleId"] for rule in rules]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("DSL_OP_WHITELIST", ids)

    def test_hard_limits_fit_contract_ranges(self) -> None:
        """硬上限自己也得是契约允许的值，否则 PATCH 的校验基准就不合法。"""
        hard = policy_service.HARD_LIMITS
        self.assertIsInstance(hard, PolicyValues)
        self.assertEqual(hard.maxIRNodes, 200)
        self.assertEqual(hard.maxVertices, 2000)
        self.assertEqual(hard.dslWallTimeMs, 2000)
        self.assertEqual(hard.dslCpuTimeSeconds, 1)
        self.assertEqual(hard.dslMemoryMiB, 256)


class AdminOnlyTests(PolicyTestBase):
    def test_creator_cannot_read(self) -> None:
        self._new_user("plain")
        self._login("plain")
        response = self.client.get(f"{API}/admin/policies")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")

    def test_creator_cannot_write(self) -> None:
        self._new_user("plain2")
        csrf = self._login("plain2")
        response = self.client.patch(
            f"{API}/admin/policies",
            json={
                "expectedRevision": 1,
                "values": policy_service.DEFAULT_VALUES.model_dump(mode="json"),
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_requires_login(self) -> None:
        response = self.client.get(f"{API}/admin/policies")
        self.assertEqual(response.status_code, 401, response.text)


class WriteTests(PolicyTestBase):
    def test_writable_field_updates_and_bumps_revision(self) -> None:
        csrf = self._as_admin("boss1")
        response = self._patch(csrf, self._values_with(dslMaxDrawCalls=500))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["values"]["dslMaxDrawCalls"], 500)
        # 硬上限不跟着动
        self.assertEqual(body["hardLimits"]["dslMaxDrawCalls"], 1000)

    def test_enabled_functions_can_be_narrowed(self) -> None:
        csrf = self._as_admin("boss2")
        response = self._patch(
            csrf, self._values_with(enabledDslFunctions=["circle"])
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["values"]["enabledDslFunctions"], ["circle"])

    def test_read_only_field_is_rejected(self) -> None:
        """改只读字段必须报 POLICY_FIELD_READ_ONLY，而不是笼统的 422。"""
        csrf = self._as_admin("boss3")
        response = self._patch(csrf, self._values_with(maxIRNodes=300))
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "POLICY_FIELD_READ_ONLY")
        self.assertIn("maxIRNodes", error["details"]["readOnlyFields"])
        paths = [item["path"] for item in error["fieldErrors"]]
        self.assertIn("values.maxIRNodes", paths)

    def test_reserved_render_fields_are_read_only(self) -> None:
        """render/turtle/browser 三项属于 R3，本期不许配。"""
        csrf = self._as_admin("boss4")
        response = self._patch(csrf, self._values_with(renderWallTimeMs=60000))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "POLICY_FIELD_READ_ONLY"
        )

    def test_over_hard_limit_is_rejected(self) -> None:
        """可写字段也不能超过硬上限：策略只能收紧，不能放松。"""
        csrf = self._as_admin("boss5")
        response = self._patch(csrf, self._values_with(dslWallTimeMs=5000))
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        self.assertIn(
            "values.dslWallTimeMs",
            [item["path"] for item in error["fieldErrors"]],
        )

    def test_memory_limit_cannot_exceed_hard_limit(self) -> None:
        csrf = self._as_admin("boss6")
        response = self._patch(csrf, self._values_with(dslMemoryMiB=512))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_no_change_keeps_revision(self) -> None:
        """提交一份与现状相同的策略：不递增 revision，也不写审计。"""
        csrf = self._as_admin("boss7")
        before = self._policies()
        response = self._patch(csrf, before["values"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], before["revision"])

    def test_stale_revision_is_409(self) -> None:
        csrf = self._as_admin("boss8")
        response = self._patch(
            csrf, self._values_with(dslMaxDrawCalls=300), revision=99
        )
        self.assertEqual(response.status_code, 409, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "REVISION_CONFLICT")

        # 并且真的没改
        self.assertEqual(self._policies()["values"]["dslMaxDrawCalls"], 1000)

    def test_change_is_audited_without_values_leak(self) -> None:
        csrf = self._as_admin("boss9")
        response = self._patch(csrf, self._values_with(dslMaxAstNodes=500))
        self.assertEqual(response.status_code, 200, response.text)

        connection = sqlite3.connect(self.settings.database_path)
        rows = connection.execute(
            "SELECT action, input_summary_json, before_json, after_json "
            "FROM audit_logs WHERE action = 'policy.update'"
        ).fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        _, summary_json, before_json, after_json = rows[0]
        summary = json.loads(summary_json)
        self.assertEqual(summary["changedFields"], ["dslMaxAstNodes"])
        self.assertEqual(summary["fromRevision"], 1)
        self.assertEqual(summary["toRevision"], 2)
        self.assertEqual(json.loads(before_json)["values"]["dslMaxAstNodes"], 2000)
        self.assertEqual(json.loads(after_json)["values"]["dslMaxAstNodes"], 500)


if __name__ == "__main__":
    unittest.main()
