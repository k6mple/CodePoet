"""模板检索与模板版本读取的行为测试。

覆盖两类硬性约定：

* 可见性 —— ``public`` 只出已发布模板，``mine``/``all`` 必须登录（``all`` 还要
  管理员），未发布的模板版本对非作者一律 404，不能靠 ID 试探草稿是否存在；
* 元数据来源 —— 公开列表里的 ``metadata`` 来自发布版快照而不是容器工作元数据，
  ``workingMetadata`` 只对作者/管理员有值。

夹具直接写 SQLite 造状态，和 test_auth.py 一样：测的是真实迁移文件建出来的表，
不是另起一套模型。
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
# 夹具用户用低轮数哈希：登录走的是真实校验路径，只是不想让几十次 PBKDF2
# 把测试拖慢十几秒。轮数存在哈希串里，验证时按串里的值算，行为一致。
FAST_ITERATIONS = 1000

NOW = "2026-11-08T00:00:00+00:00"
LATER = "2026-11-08T01:00:00+00:00"

DEMO_TEMPLATE = PROJECT_ROOT / "shared" / "demo" / "templates" / "lotus.template.json"


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            connection.executescript(path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


class TemplateReadTests(unittest.TestCase):
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
        self._seed()

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
            (user_id, username, hash_password(PASSWORD, FAST_ITERATIONS), username, role, NOW, NOW),
        )

    def _template(
        self,
        template_id: str,
        *,
        owner_id: str,
        name: str,
        status: str,
        working_metadata: dict | None,
        published_version_id: str | None,
        latest_version_id: str | None,
        supported_backends: str = '["canvas2d"]',
        updated_at: str = NOW,
    ) -> None:
        self._write(
            "INSERT INTO templates (id, owner_id, name, description, status,"
            " published_version_id, latest_version_id, working_metadata_json,"
            " supported_backends_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,'',?,?,?,?,?,1,?,?)",
            (
                template_id,
                owner_id,
                name,
                status,
                published_version_id,
                latest_version_id,
                json.dumps(working_metadata, ensure_ascii=False) if working_metadata else None,
                supported_backends,
                NOW,
                updated_at,
            ),
        )

    def _version(
        self,
        version_id: str,
        *,
        template_id: str,
        created_by: str,
        status: str,
        metadata: dict,
        version_number: int = 1,
        submitted_by: str | None = None,
        submitted_at: str | None = None,
        published_at: str | None = None,
    ) -> None:
        ir = self.demo["irTemplate"]
        self._write(
            "INSERT INTO template_versions (id, template_id, version_number, created_by,"
            " status, metadata_json, ir_template_json, parameter_schema_json,"
            " bindings_json, constraints_json, supported_backends_json,"
            " ontology_version, change_note, submitted_by, submitted_at, published_at,"
            " revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'[\"canvas2d\"]',1,'',?,?,?,1,?,?)",
            (
                version_id,
                template_id,
                version_number,
                created_by,
                status,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(ir, ensure_ascii=False),
                json.dumps(self.demo["parameterSchema"], ensure_ascii=False),
                json.dumps(self.demo["bindings"], ensure_ascii=False),
                json.dumps(self.demo["constraints"], ensure_ascii=False),
                submitted_by,
                submitted_at,
                published_at,
                NOW,
                NOW,
            ),
        )

    def _seed(self) -> None:
        self._user("user-owner", "owner", "creator")
        self._user("user-other", "other", "creator")
        self._user("user-admin", "admin", "admin")
        self._user("user-expert", "expert", "expert")

        # 已发布的莲模板：工作元数据故意改成另一个名字，用来证明公开列表
        # 读的是发布版快照而不是工作元数据
        self._template(
            "tpl-lotus",
            owner_id="user-owner",
            name="莲",
            status="published",
            working_metadata={"name": "莲（改名未发布）"},
            published_version_id="tv-lotus-1",
            latest_version_id="tv-lotus-1",
            updated_at=LATER,
        )
        self._version(
            "tv-lotus-1",
            template_id="tpl-lotus",
            created_by="user-owner",
            status="published",
            metadata={
                "name": "莲",
                "description": "六瓣莲花",
                "entityIds": ["ent-lotus"],
                "tagIds": ["tag-flower"],
                "symbols": ["lotus"],
                "timeTheme": "夏",
            },
            submitted_by="user-owner",
            published_at=LATER,
        )

        # 已发布的竹模板：用来验证 symbols / entityIds / timeTheme 过滤
        self._template(
            "tpl-bamboo",
            owner_id="user-other",
            name="竹",
            status="published",
            working_metadata={"name": "竹"},
            published_version_id="tv-bamboo-1",
            latest_version_id="tv-bamboo-1",
        )
        self._version(
            "tv-bamboo-1",
            template_id="tpl-bamboo",
            created_by="user-other",
            status="published",
            metadata={
                "name": "竹",
                "description": "劲节",
                "entityIds": ["ent-bamboo"],
                "tagIds": ["tag-plant"],
                "symbols": ["bamboo"],
                "timeTheme": "冬",
            },
            published_at=LATER,
        )

        # 作者的草稿：只在 mine 里出现
        self._template(
            "tpl-draft",
            owner_id="user-owner",
            name="草稿叙事",
            status="draft",
            working_metadata={
                "name": "草稿叙事",
                "symbols": ["narrative"],
                "timeTheme": "秋",
            },
            published_version_id=None,
            latest_version_id="tv-draft-1",
        )
        self._version(
            "tv-draft-1",
            template_id="tpl-draft",
            created_by="user-owner",
            status="draft",
            metadata={"name": "草稿叙事", "symbols": ["narrative"]},
        )

        # 别人的待审版本：审核人可读，其他创作者不可读
        self._template(
            "tpl-review",
            owner_id="user-other",
            name="待审水纹",
            status="submitted",
            working_metadata={"name": "待审水纹"},
            published_version_id=None,
            latest_version_id="tv-review-1",
        )
        self._version(
            "tv-review-1",
            template_id="tpl-review",
            created_by="user-other",
            status="submitted",
            metadata={"name": "待审水纹", "symbols": ["water"]},
            submitted_by="user-other",
            submitted_at=LATER,
        )

        # 历史默认值 '["canvas"]' 不合法，读取时应回落到 canvas2d
        self._template(
            "tpl-legacy",
            owner_id="user-owner",
            name="历史模板",
            status="draft",
            working_metadata={"name": "历史模板"},
            published_version_id=None,
            latest_version_id=None,
            supported_backends='["canvas"]',
        )

    # ---- 登录辅助 -------------------------------------------------------
    def _csrf(self) -> str:
        response = self.client.get(f"{API}/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrfToken"]

    def login(self, username: str) -> None:
        token = self._csrf()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ---- GET /templates --------------------------------------------------
    def test_public_scope_lists_published_templates_anonymously(self):
        response = self.client.get(f"{API}/templates")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["id"] for item in body["items"]], ["tpl-lotus", "tpl-bamboo"])
        self.assertEqual(body["total"], 2)
        # 默认分页
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["pageSize"], 20)

    def test_public_metadata_comes_from_published_version(self):
        body = self.client.get(f"{API}/templates").json()
        lotus = next(item for item in body["items"] if item["id"] == "tpl-lotus")
        self.assertEqual(lotus["metadata"]["name"], "莲")
        self.assertEqual(lotus["metadata"]["entityIds"], ["ent-lotus"])
        self.assertEqual(lotus["metadata"]["symbols"], ["lotus"])
        self.assertEqual(lotus["metadata"]["timeTheme"], "夏")
        # 匿名读者看不到容器的工作元数据，也没有任何写权限
        self.assertIsNone(lotus["workingMetadata"])
        self.assertEqual(lotus["permissions"], [])
        self.assertEqual(lotus["publishedVersionId"], "tv-lotus-1")
        self.assertEqual(lotus["supportedBackends"], ["canvas2d"])

    def test_mine_requires_login(self):
        response = self.client.get(f"{API}/templates", params={"scope": "mine"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_mine_returns_own_templates_with_working_metadata(self):
        self.login("owner")
        response = self.client.get(f"{API}/templates", params={"scope": "mine"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        ids = {item["id"] for item in body["items"]}
        # 三个本人模板都在，别人的模板不在
        self.assertEqual(ids, {"tpl-lotus", "tpl-draft", "tpl-legacy"})
        lotus = next(item for item in body["items"] if item["id"] == "tpl-lotus")
        self.assertEqual(lotus["workingMetadata"]["name"], "莲（改名未发布）")
        self.assertIn("templates.editOwn", lotus["permissions"])
        self.assertIn("templates.submitOwn", lotus["permissions"])
        # 历史默认值 '["canvas"]' 不在契约枚举里，读取时要回落
        legacy = next(item for item in body["items"] if item["id"] == "tpl-legacy")
        self.assertEqual(legacy["supportedBackends"], ["canvas2d"])

    def test_scope_all_requires_admin(self):
        self.login("owner")
        response = self.client.get(f"{API}/templates", params={"scope": "all"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")

    def test_scope_all_for_admin_sees_every_template(self):
        self.login("admin")
        response = self.client.get(f"{API}/templates", params={"scope": "all"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(
            {item["id"] for item in body["items"]},
            {"tpl-lotus", "tpl-bamboo", "tpl-draft", "tpl-review", "tpl-legacy"},
        )
        lotus = next(item for item in body["items"] if item["id"] == "tpl-lotus")
        self.assertIn("templates.editAny", lotus["permissions"])

    def test_filters_are_and_between_dimensions_or_within(self):
        def ids(params: dict) -> list[str]:
            response = self.client.get(f"{API}/templates", params=params)
            self.assertEqual(response.status_code, 200, response.text)
            return [item["id"] for item in response.json()["items"]]

        self.assertEqual(ids({"symbols": "lotus"}), ["tpl-lotus"])
        self.assertEqual(ids({"entityIds": "ent-bamboo"}), ["tpl-bamboo"])
        self.assertEqual(ids({"tagIds": "tag-plant"}), ["tpl-bamboo"])
        self.assertEqual(ids({"timeTheme": "夏"}), ["tpl-lotus"])
        self.assertEqual(ids({"q": "竹"}), ["tpl-bamboo"])
        # 同维度 OR：两个 entityId 都给，两个模板都命中
        self.assertEqual(
            sorted(ids({"entityIds": ["ent-lotus", "ent-bamboo"]})),
            ["tpl-bamboo", "tpl-lotus"],
        )
        # 不同维度 AND：竹 + 莲的实体不会同时命中
        self.assertEqual(ids({"entityIds": "ent-bamboo", "symbols": "lotus"}), [])
        # 不支持的后端过滤出空集，而不是忽略参数
        self.assertEqual(ids({"backend": "turtle"}), [])

    def test_pagination_slices_and_reports_total(self):
        body = self.client.get(
            f"{API}/templates", params={"page": 2, "pageSize": 1}
        ).json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["pageSize"], 1)
        self.assertEqual([item["id"] for item in body["items"]], ["tpl-bamboo"])

    def test_out_of_range_query_params_are_validation_errors(self):
        for params in ({"page": 0}, {"pageSize": 101}, {"scope": "world"}):
            response = self.client.get(f"{API}/templates", params=params)
            self.assertEqual(response.status_code, 422, params)
            self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    # ---- GET /template-versions/{id} -------------------------------------
    def test_published_version_readable_anonymously(self):
        response = self.client.get(f"{API}/template-versions/tv-lotus-1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "tv-lotus-1")
        self.assertEqual(body["templateId"], "tpl-lotus")
        self.assertEqual(body["status"], "published")
        self.assertEqual(body["versionNumber"], 1)
        self.assertEqual(body["createdBy"], "user-owner")
        # 契约把这三个字段钉死：generatorRef=ir、fragmentSource=null、后端只有 canvas2d
        self.assertEqual(body["generatorRef"], "ir")
        self.assertIsNone(body["fragmentSource"])
        self.assertEqual(body["supportedBackends"], ["canvas2d"])
        self.assertEqual(len(body["parameterSchema"]), len(self.demo["parameterSchema"]))
        self.assertEqual(len(body["bindings"]), len(self.demo["bindings"]))
        self.assertTrue(body["irTemplate"]["nodes"])
        self.assertIsNone(body["lastReview"])

    def test_draft_version_hidden_from_anonymous(self):
        response = self.client.get(f"{API}/template-versions/tv-draft-1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_draft_version_hidden_from_other_creator(self):
        self.login("other")
        response = self.client.get(f"{API}/template-versions/tv-draft-1")
        # 无权访问必须和「不存在」一样是 404，不能是 403
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_draft_version_readable_by_owner(self):
        self.login("owner")
        response = self.client.get(f"{API}/template-versions/tv-draft-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "draft")

    def test_draft_version_readable_by_admin(self):
        self.login("admin")
        response = self.client.get(f"{API}/template-versions/tv-draft-1")
        self.assertEqual(response.status_code, 200)

    def test_submitted_version_readable_by_reviewer_only(self):
        self.login("expert")
        self.assertEqual(
            self.client.get(f"{API}/template-versions/tv-review-1").status_code, 200
        )
        # 审核人只看得到待审的那一份，看不到作者的草稿
        self.assertEqual(
            self.client.get(f"{API}/template-versions/tv-draft-1").status_code, 404
        )

    def test_unknown_version_is_404(self):
        response = self.client.get(f"{API}/template-versions/tv-nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_invalid_version_id_is_validation_error(self):
        response = self.client.get(f"{API}/template-versions/bad id")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
