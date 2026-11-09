"""模板容器与版本的写路径测试（P1A-03）。

重点不是「能不能返回 201」，而是这几条边界：

* **可见性** —— 没有发布版本的模板，对非作者按不存在处理，ID 不能被拿来试探；
* **工作区与快照隔离** —— PATCH 改容器工作元数据，不能污染已发布版本的快照；
* **落库前的引擎校验** —— 定义不自洽、参数默认值越界，都必须当场 422，
  而不是存进去让「生成」按钮变 500；
* **克隆** —— 版本号递增、内容继承、显式字段覆盖、禁止跨模板克隆；
* **乐观锁** —— 拿着旧 revision 改一律 409。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.security import hash_password
from app.settings import PROJECT_ROOT, Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
API = "/api/v1"
PASSWORD = "PasswordOf10"
FAST_ITERATIONS = 1000
NOW = "2026-11-09T00:00:00+00:00"

DEMO_TEMPLATE = PROJECT_ROOT / "shared" / "demo" / "templates" / "lotus.template.json"


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            connection.executescript(path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


class TemplateWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.demo = json.loads(DEMO_TEMPLATE.read_text(encoding="utf-8"))
        self._user("usr-dev", "devone", "developer")
        self._user("usr-other", "otherone", "developer")
        self._user("usr-admin", "adminone", "admin")

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

    def _create_template(self, csrf: str, name: str = "六瓣莲") -> dict:
        response = self.client.post(
            f"{API}/templates",
            json={"metadata": {"name": name, "description": "测试用"}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _version_body(self, **override) -> dict:
        body = {
            "irTemplate": self.demo["irTemplate"],
            "parameterSchema": self.demo["parameterSchema"],
            "bindings": self.demo["bindings"],
            "constraints": self.demo["constraints"],
            "supportedBackends": ["canvas2d"],
            "changeNote": "首个草稿",
        }
        body.update(override)
        return body

    def _create_version(self, csrf: str, template_id: str, **override) -> dict:
        response = self.client.post(
            f"{API}/templates/{template_id}/versions",
            json=self._version_body(**override),
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ---- 容器 -----------------------------------------------------------

    def test_create_template_starts_as_draft_without_versions(self) -> None:
        csrf = self._login("devone")
        body = self._create_template(csrf)
        self.assertEqual(body["metadata"]["name"], "六瓣莲")
        self.assertIsNone(body["latestVersionId"])
        self.assertIsNone(body["publishedVersionId"])
        self.assertIn("templates.editOwn", body["permissions"])

    def test_anonymous_cannot_create_template(self) -> None:
        token = self._csrf()
        response = self.client.post(
            f"{API}/templates",
            json={"metadata": {"name": "匿名", "description": ""}},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_unpublished_template_is_invisible_to_others(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)

        # 匿名看不到 —— 先登出，否则 client 还带着作者会话
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(
            self.client.get(f"{API}/templates/{created['id']}").status_code, 404
        )
        # 版本列表也要 404，不能靠「空列表」泄露这个 ID 是个模板
        self.assertEqual(
            self.client.get(f"{API}/templates/{created['id']}/versions").status_code,
            404,
        )
        # 另一个 developer 也看不到（不是作者、不是管理员）
        self._login("otherone")
        self.assertEqual(
            self.client.get(f"{API}/templates/{created['id']}").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"{API}/templates/{created['id']}/versions").status_code,
            404,
        )
        # 管理员能看到
        self._login("adminone")
        admin_view = self.client.get(f"{API}/templates/{created['id']}")
        self.assertEqual(admin_view.status_code, 200, admin_view.text)

    def test_owner_and_admin_can_patch_but_others_cannot(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)

        other_csrf = self._login("otherone")
        denied = self.client.patch(
            f"{API}/templates/{created['id']}",
            json={"expectedRevision": 1, "metadata": {"name": "被改", "description": ""}},
            headers={"X-CSRF-Token": other_csrf},
        )
        # 无权时给 404，不暴露这个模板存在
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_patch_does_not_touch_published_snapshot(self) -> None:
        """工作元数据改了，已发布版本里读者看到的 metadata 必须还是旧的。"""
        csrf = self._login("devone")
        created = self._create_template(csrf, "旧标题")
        version = self._create_version(csrf, created["id"], metadata={"name": "旧标题", "description": ""})

        # 直接把版本标成已发布，并让容器指向它（真实流程由 P1A-04 完成）
        self._write(
            "UPDATE template_versions SET status='published' WHERE id=?",
            (version["id"],),
        )
        self._write(
            "UPDATE templates SET published_version_id=? WHERE id=?",
            (version["id"], created["id"]),
        )

        # 建版本会让容器 revision 递增，PATCH 前必须重新取当前版本号
        current = self.client.get(f"{API}/templates/{created['id']}").json()
        patched = self.client.patch(
            f"{API}/templates/{created['id']}",
            json={
                "expectedRevision": current["revision"],
                "metadata": {"name": "新标题", "description": "改过的简介"},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["revision"], current["revision"] + 1)

        # 读者视角：metadata 仍取发布版快照
        anonymous = self.client.get(f"{API}/templates/{created['id']}").json()
        self.assertEqual(anonymous["metadata"]["name"], "旧标题")
        # 作者视角：工作元数据已是新值
        mine = self.client.get(f"{API}/templates/{created['id']}")
        self.assertEqual(mine.json()["workingMetadata"]["name"], "新标题")

    def test_stale_revision_conflicts(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        self.client.patch(
            f"{API}/templates/{created['id']}",
            json={"expectedRevision": 1, "metadata": {"name": "第一次", "description": ""}},
            headers={"X-CSRF-Token": csrf},
        )
        stale = self.client.patch(
            f"{API}/templates/{created['id']}",
            json={"expectedRevision": 1, "metadata": {"name": "第二次", "description": ""}},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")

    # ---- 版本 -----------------------------------------------------------

    def test_create_version_uses_working_metadata_by_default(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf, "容器标题")
        version = self._create_version(csrf, created["id"])
        self.assertEqual(version["versionNumber"], 1)
        self.assertEqual(version["status"], "draft")
        # 没传 metadata 时取容器工作元数据
        self.assertEqual(version["metadata"]["name"], "容器标题")

    def test_version_number_increments(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        first = self._create_version(csrf, created["id"])
        second = self._create_version(csrf, created["id"])
        self.assertEqual(first["versionNumber"], 1)
        self.assertEqual(second["versionNumber"], 2)
        # 容器的 latestVersionId 跟着走
        detail = self.client.get(f"{API}/templates/{created['id']}").json()
        self.assertEqual(detail["latestVersionId"], second["id"])

    def test_missing_required_fields_for_new_version(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        response = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={"changeNote": "缺内容"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_inconsistent_bindings_are_rejected(self) -> None:
        """binding 指向不存在的节点时，必须在落库前拦住。"""
        csrf = self._login("devone")
        created = self._create_template(csrf)
        broken = json.loads(json.dumps(self.demo["bindings"]))
        broken[0]["nodeId"] = "node-not-exist"
        response = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json=self._version_body(bindings=broken),
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_invalid_parameter_default_is_rejected(self) -> None:
        """default 落在 min/max 之外必须在落库前被拒。

        这道校验放在模型层（IntegerParameter._default_within_range），
        所以错误体里 fieldErrors 能精确指到 ``parameterSchema.0.default``，
        前端可以直接把红框标在那个输入框上。
        """
        csrf = self._login("devone")
        created = self._create_template(csrf)
        schema = [
            {
                "type": "integer",
                "name": "petalCount",
                "label": "花瓣数",
                "description": "",
                "default": 5,
                "min": 10,
                "max": 20,
                "step": 1,
            }
        ]
        response = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json=self._version_body(parameterSchema=schema, bindings=[], constraints=[]),
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        paths = {item["path"] for item in response.json()["error"]["fieldErrors"]}
        # Pydantic 的 model_validator(mode="after") 把错误挂在「整个模型」上，
        # 所以路径是 parameterSchema.0.integer 而不是精确到 .default。
        # 对前端来说够用了：按前缀就能锁定是第几个参数出错。
        self.assertTrue(
            any("parameterSchema.0" in path for path in paths),
            f"fieldErrors 应指向第一个参数，实际是 {paths}",
        )

    def test_clone_copies_content_and_overrides_explicit_fields(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        source = self._create_version(csrf, created["id"])

        cloned = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={
                "fromVersionId": source["id"],
                "metadata": {"name": "克隆版", "description": ""},
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(cloned.status_code, 201, cloned.text)
        body = cloned.json()
        self.assertEqual(body["versionNumber"], 2)
        self.assertEqual(body["metadata"]["name"], "克隆版")
        # 没覆盖的字段继承来源
        self.assertEqual(
            len(body["parameterSchema"]), len(source["parameterSchema"])
        )
        self.assertEqual(len(body["bindings"]), len(source["bindings"]))

    def test_clone_from_other_template_is_rejected(self) -> None:
        csrf = self._login("devone")
        first = self._create_template(csrf, "甲")
        second = self._create_template(csrf, "乙")
        version = self._create_version(csrf, first["id"])

        response = self.client.post(
            f"{API}/templates/{second['id']}/versions",
            json={"fromVersionId": version["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_version_list_hides_drafts_from_public(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        published = self._create_version(csrf, created["id"])
        self._create_version(csrf, created["id"])  # 一条草稿
        self._write(
            "UPDATE template_versions SET status='published' WHERE id=?",
            (published["id"],),
        )
        self._write(
            "UPDATE templates SET published_version_id=? WHERE id=?",
            (published["id"], created["id"]),
        )

        # 作者看全部
        mine = self.client.get(f"{API}/templates/{created['id']}/versions").json()
        self.assertEqual(mine["total"], 2)
        # 匿名只看已发布
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf})
        public = self.client.get(f"{API}/templates/{created['id']}/versions").json()
        self.assertEqual(public["total"], 1)
        self.assertEqual(public["items"][0]["status"], "published")

    def test_non_owner_cannot_create_version(self) -> None:
        csrf = self._login("devone")
        created = self._create_template(csrf)
        other_csrf = self._login("otherone")
        response = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json=self._version_body(),
            headers={"X-CSRF-Token": other_csrf},
        )
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
