"""账号与认证接口的行为测试。

覆盖契约 2.1 的每条硬性约定：CSRF 必须带、登录不区分账号是否存在、
注销后会话立刻失效、乐观并发冲突返回 409、停用账号不能登录。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
API = "/api/v1"
PASSWORD = "PasswordOf10"  # 契约要求至少 10 位


def apply_migrations(database_path: Path) -> None:
    """测试库直接跑真实迁移文件，保证测的是线上那套表结构。"""
    connection = sqlite3.connect(database_path)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            connection.executescript(path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None, data_dir=Path(self.tmp.name) / "runtime"
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)

    # ---- 辅助方法 -------------------------------------------------------
    def csrf_token(self) -> str:
        response = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def register(self, username: str = "alice", password: str = PASSWORD):
        token = self.csrf_token()
        return self.client.post(
            f"{API}/auth/register",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": token},
        )

    def login(self, username: str = "alice", password: str = PASSWORD):
        token = self.csrf_token()
        return self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": token},
        )

    # ---- CSRF -----------------------------------------------------------
    def test_csrf_issues_token_and_sets_cookie(self):
        response = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # 契约要求 32~256 字符
        self.assertGreaterEqual(len(body["csrfToken"]), 32)
        self.assertIn("codepoet_session", response.cookies)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_register_without_csrf_rejected(self):
        # 没有先拿 CSRF token 就注册，属于契约禁止的行为
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": "bob", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "CSRF_INVALID")

    def test_csrf_round_trip_is_idempotent(self):
        first = self.csrf_token()
        second = self.csrf_token()
        # 已有有效会话时复用，不该每次发新 token
        self.assertEqual(first, second)

    # ---- 注册 -----------------------------------------------------------
    def test_register_creates_creator(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "creator")
        self.assertEqual(body["revision"], 1)
        self.assertIn("profile.edit", body["permissions"])
        # 绝不返回密码哈希
        self.assertNotIn("password", str(body).lower())
        self.assertIsNone(body["roleApplication"])

    def test_register_with_requested_role_only_records_application(self):
        token = self.csrf_token()
        response = self.client.post(
            f"{API}/auth/register",
            json={
                "username": "wannabe",
                "password": PASSWORD,
                "requestedRole": "admin",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        # 关键：申请 admin 不等于成为 admin
        self.assertEqual(body["role"], "creator")
        self.assertEqual(body["roleApplication"]["status"], "pending")
        self.assertEqual(body["roleApplication"]["requestedRole"], "admin")

    def test_register_duplicate_username_case_insensitive(self):
        self.register("alice")
        response = self.register("ALICE")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "USERNAME_TAKEN")

    def test_register_short_password_is_validation_error(self):
        response = self.register(password="short")
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        # 前端要能定位到具体字段
        self.assertTrue(any("password" in item["path"] for item in error["fieldErrors"]))

    # ---- 登录 -----------------------------------------------------------
    def test_login_success_returns_session_and_rotates_cookie(self):
        self.register()
        before = self.client.cookies.get("codepoet_session")
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["username"], "alice")
        self.assertGreaterEqual(len(body["csrfToken"]), 32)
        self.assertIn("expiresAt", body)
        # 登录必须轮换 session，防会话固定攻击
        after = self.client.cookies.get("codepoet_session")
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_login_wrong_password(self):
        self.register()
        response = self.login(password="WrongPassword1")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_unknown_user_returns_same_error(self):
        token = self.csrf_token()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": "nobody", "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        # 不区分「账号不存在」和「密码错误」，避免账号枚举
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_disabled_user_cannot_login(self):
        self.register()
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET status='disabled' WHERE username='alice'")
        connection.commit()
        connection.close()

        response = self.login()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")

    # ---- /me ------------------------------------------------------------
    def test_me_requires_login(self):
        self.csrf_token()  # 只有匿名会话
        response = self.client.get(f"{API}/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_me_returns_current_user_after_login(self):
        self.register()
        self.login()
        response = self.client.get(f"{API}/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "alice")

    def test_logout_invalidates_session_immediately(self):
        self.register()
        self.login()
        csrf = self.client.get(f"{API}/me").json()["csrfToken"]

        response = self.client.post(
            f"{API}/auth/logout", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 204)

        # 注销后再访问必须 401，且服务端记录已删
        self.assertEqual(self.client.get(f"{API}/me").status_code, 401)
        connection = sqlite3.connect(self.settings.database_path)
        remaining = connection.execute(
            "SELECT count(*) FROM auth_sessions WHERE kind='authenticated'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(remaining, 0)

    # ---- PATCH /me -------------------------------------------------------
    def test_patch_me_updates_and_bumps_revision(self):
        self.register()
        self.login()
        csrf = self.client.get(f"{API}/me").json()["csrfToken"]

        response = self.client.patch(
            f"{API}/me",
            json={"expectedRevision": 1, "nickname": "爱丽丝", "bio": "喜欢莲"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["user"]
        self.assertEqual(body["nickname"], "爱丽丝")
        self.assertEqual(body["bio"], "喜欢莲")
        self.assertEqual(body["revision"], 2)

    def test_patch_me_stale_revision_conflicts(self):
        self.register()
        self.login()
        csrf = self.client.get(f"{API}/me").json()["csrfToken"]

        response = self.client.patch(
            f"{API}/me",
            json={"expectedRevision": 99, "nickname": "别人改过了"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_patch_me_requires_a_field_besides_revision(self):
        self.register()
        self.login()
        csrf = self.client.get(f"{API}/me").json()["csrfToken"]

        response = self.client.patch(
            f"{API}/me",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": csrf},
        )
        # 契约 minProperties=2，只给版本号不算修改
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
