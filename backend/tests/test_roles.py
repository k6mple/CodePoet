"""角色审批流程的测试。

关注的是「提权路径有没有被堵住」，不是接口能不能返回 200：

* 非管理员一律 403——这是整个权限体系里唯一能把人往上提的操作；
* 禁止本人审批；
* 批准的角色必须等于用户申请的角色，否则一次误操作就能直接给 admin；
* 拒绝不降级；
* 已经处理过的申请不能再批一次，否则结果会被静默覆盖。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.security import hash_password
from app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
API = "/api/v1"
PASSWORD = "PasswordOf10"
FAST_ITERATIONS = 1000
NOW = "2026-11-09T00:00:00+00:00"


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            connection.executescript(path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


class RoleDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self._user("usr-admin", "adminone", "admin")
        self._user("usr-dev", "devone", "developer")

    # ---- 夹具 -----------------------------------------------------------
    def _write(self, sql: str, params: tuple) -> None:
        connection = sqlite3.connect(self.settings.database_path)
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def _user(self, user_id: str, username: str, role: str) -> None:
        self._write(
            "INSERT INTO users (id, username, password_hash, nickname, role,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,'active',?,?)",
            (
                user_id,
                username,
                hash_password(PASSWORD, FAST_ITERATIONS),
                username,
                role,
                NOW,
                NOW,
            ),
        )

    def _csrf(self) -> str:
        response = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def _login(self, username: str) -> str:
        token = self._csrf()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def _register_applicant(self, csrf: str, username: str, role: str = "developer") -> dict:
        """走真实注册接口，顺带验证触发器会把状态补成 pending。"""
        token = self._csrf() if csrf is None else csrf
        response = self.client.post(
            f"{API}/auth/register",
            json={
                "username": username,
                "password": PASSWORD,
                "nickname": username,
                "requestedRole": role,
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201, response.text)
        # 注册接口直接返回 User 对象（response_model=UserSchema），不是包在 user 里
        return response.json()

    # ---- 用例 -----------------------------------------------------------

    def test_register_creates_pending_application(self) -> None:
        csrf = self._csrf()
        user = self._register_applicant(csrf, "pendingone", "developer")
        self.assertEqual(user["role"], "creator")
        self.assertEqual(user["roleApplication"]["status"], "pending")
        self.assertEqual(user["roleApplication"]["requestedRole"], "developer")
        self.assertIsNone(user["roleApplication"]["decidedAt"])

    def test_non_admin_cannot_list_or_decide(self) -> None:
        csrf = self._login("devone")
        listing = self.client.get(f"{API}/admin/users")
        self.assertEqual(listing.status_code, 403, listing.text)

        decision = self.client.post(
            f"{API}/admin/users/usr-admin/role-decision",
            json={
                "expectedRevision": 1,
                "approved": True,
                "role": "expert",
                "reason": "越权尝试",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(decision.status_code, 403, decision.text)

    def test_admin_lists_and_filters_applications(self) -> None:
        csrf = self._csrf()
        self._register_applicant(csrf, "candone", "developer")
        self._register_applicant(csrf, "candtwo", "expert")

        token = self._login("adminone")
        page = self.client.get(f"{API}/admin/users?applicationStatus=pending")
        self.assertEqual(page.status_code, 200, page.text)
        body = page.json()
        self.assertEqual(body["total"], 2)
        names = {item["username"] for item in body["items"]}
        self.assertEqual(names, {"candone", "candtwo"})

        by_role = self.client.get(f"{API}/admin/users?role=admin").json()
        self.assertEqual(by_role["total"], 1)
        self.assertEqual(by_role["items"][0]["username"], "adminone")

        searched = self.client.get(f"{API}/admin/users?q=candone").json()
        self.assertEqual(searched["total"], 1)
        _ = token

    def test_approve_grants_role(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "grantee", "developer")
        token = self._login("adminone")

        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": True,
                "role": "developer",
                "reason": "材料齐全",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["role"], "developer")
        self.assertEqual(body["roleApplication"]["status"], "approved")
        self.assertEqual(body["roleApplication"]["reason"], "材料齐全")
        self.assertIsNotNone(body["roleApplication"]["decidedAt"])
        # 权限跟着角色走，不存库
        self.assertIn("knowledge.write", body["permissions"])

    def test_role_must_match_applied_role(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "escalate", "developer")
        token = self._login("adminone")

        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": True,
                "role": "admin",
                "reason": "手滑提权",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"]["fieldErrors"][0]["path"], "role"
        )

    def test_cannot_decide_own_application(self) -> None:
        csrf = self._csrf()
        # 管理员自己发一次申请，然后试图自批
        applicant = self._register_applicant(csrf, "selfadmin", "expert")
        self._write(
            "UPDATE users SET role='admin' WHERE id=?", (applicant["id"],)
        )
        token = self._login("selfadmin")

        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": True,
                "role": "expert",
                "reason": "自己批自己",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_reject_keeps_current_role(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "rejectedone", "expert")
        token = self._login("adminone")

        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": False,
                "role": "expert",
                "reason": "作品数量不足",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["role"], "creator")
        self.assertEqual(body["roleApplication"]["status"], "rejected")
        self.assertEqual(body["roleApplication"]["reason"], "作品数量不足")

    def test_cannot_decide_twice(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "twiceone", "developer")
        token = self._login("adminone")
        first = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": True,
                "role": "developer",
                "reason": "首次",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(first.status_code, 200, first.text)

        again = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": first.json()["revision"],
                "approved": False,
                "role": "developer",
                "reason": "想反悔",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(again.json()["error"]["code"], "INVALID_STATE")

    def test_stale_revision_rejected(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "staleone", "developer")
        token = self._login("adminone")
        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": 99,
                "approved": True,
                "role": "developer",
                "reason": "版本号过时",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_reason_is_required(self) -> None:
        csrf = self._csrf()
        applicant = self._register_applicant(csrf, "noreason", "developer")
        token = self._login("adminone")
        response = self.client.post(
            f"{API}/admin/users/{applicant['id']}/role-decision",
            json={
                "expectedRevision": applicant["revision"],
                "approved": True,
                "role": "developer",
                "reason": "",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
