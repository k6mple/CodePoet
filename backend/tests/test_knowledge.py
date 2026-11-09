"""标签树、本体树与知识条目的行为测试。

测的是「规则」而不是「返回 200」：

* **可见性** —— ``public`` 看不到停用项，``manage`` 要写权限才看得到全量；
* **树约束** —— 自引用、间接环、停用前必须先清空子节点；
* **乐观锁** —— ``expectedRevision`` 不匹配一律 409，不静默覆盖；
* **标签继承** —— ``effectiveTagIds = 直接 ∪ 祖先``，且检索按有效标签命中；
* **互斥** —— 同时选中互斥标签被拒，单向声明也按双向判。

夹具直接写 SQLite 造用户，和 test_templates.py 一致：测的是真实迁移文件
建出来的表，不另起一套模型。登录走真实接口，CSRF 也真实轮换。
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
# 夹具用户用低轮数哈希：登录仍走真实校验路径，只是不想让 PBKDF2 拖慢测试
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


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self._user("usr-dev", "devone", "developer")
        self._user("usr-cre", "creatorone", "creator")

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
        """登录并返回轮换后的 CSRF token；后续写操作都要带它。"""
        token = self._csrf()
        response = self.client.post(
            f"{API}/auth/login",
            json={"username": username, "password": PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def _create_tag(self, csrf: str, name: str, **extra) -> dict:
        response = self.client.post(
            f"{API}/tags",
            json={"name": name, **extra},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ---- 权限与可见性 ---------------------------------------------------

    def test_anonymous_can_read_public_lists(self) -> None:
        for url in ("/tags", "/ontology/concepts", "/knowledge"):
            response = self.client.get(f"{API}{url}")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["items"], [])
            self.assertEqual(body["total"], 0)

    def test_anonymous_cannot_create(self) -> None:
        token = self._csrf()
        response = self.client.post(
            f"{API}/tags", json={"name": "匿名"}, headers={"X-CSRF-Token": token}
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_write_requires_csrf(self) -> None:
        self._login("devone")
        response = self.client.post(f"{API}/tags", json={"name": "无令牌"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "CSRF_INVALID")

    def test_creator_lacks_taxonomy_write(self) -> None:
        csrf = self._login("creatorone")
        response = self.client.post(
            f"{API}/tags", json={"name": "创作者标签"}, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")

    def test_manage_scope_needs_permission(self) -> None:
        csrf = self._login("creatorone")
        response = self.client.get(f"{API}/tags?scope=manage")
        self.assertEqual(response.status_code, 403, response.text)
        # developer 有 tags.write，能看管理视图
        self._login("devone")
        response = self.client.get(f"{API}/tags?scope=manage")
        self.assertEqual(response.status_code, 200, response.text)
        _ = csrf

    # ---- 标签树约束 -----------------------------------------------------

    def test_duplicate_active_name_is_rejected(self) -> None:
        csrf = self._login("devone")
        self._create_tag(csrf, "清正")
        response = self.client.post(
            f"{API}/tags", json={"name": "清正"}, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        self.assertEqual(error["fieldErrors"][0]["path"], "name")

    def test_inactive_name_can_be_reused(self) -> None:
        """停用后名字要能重新占用，否则改错一次名字就永久占坑。"""
        csrf = self._login("devone")
        first = self._create_tag(csrf, "旧名")
        response = self.client.delete(
            f"{API}/tags/{first['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 204, response.text)
        # 同名再建一次应当成功
        second = self._create_tag(csrf, "旧名")
        self.assertNotEqual(first["id"], second["id"])

    def test_parent_must_exist(self) -> None:
        csrf = self._login("devone")
        response = self.client.post(
            f"{API}/tags",
            json={"name": "孤儿", "parentId": "tag-missing"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["fieldErrors"][0]["path"], "parentId")

    def test_indirect_cycle_is_rejected(self) -> None:
        csrf = self._login("devone")
        parent = self._create_tag(csrf, "父级")
        child = self._create_tag(csrf, "子级", parentId=parent["id"])
        # 把父级挂到自己的子级下面 → A→B→A
        response = self.client.patch(
            f"{API}/tags/{parent['id']}",
            json={"expectedRevision": parent["revision"], "parentId": child["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_deactivate_blocked_by_active_child(self) -> None:
        csrf = self._login("devone")
        parent = self._create_tag(csrf, "有子级")
        self._create_tag(csrf, "子级甲", parentId=parent["id"])
        response = self.client.delete(
            f"{API}/tags/{parent['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 409, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "DEPENDENCY_CONFLICT")
        self.assertEqual(error["details"]["activeChildren"], 1)

    def test_deactivate_hides_from_public_but_not_manage(self) -> None:
        csrf = self._login("devone")
        tag = self._create_tag(csrf, "将停用")
        response = self.client.delete(
            f"{API}/tags/{tag['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 204, response.text)

        public = self.client.get(f"{API}/tags")
        self.assertEqual(public.json()["total"], 0)
        manage = self.client.get(f"{API}/tags?scope=manage")
        self.assertEqual(manage.json()["total"], 1)
        self.assertEqual(manage.json()["items"][0]["status"], "inactive")

    def test_deactivate_is_idempotent(self) -> None:
        """已经停用再删一次直接 204，客户端重发不该炸。"""
        csrf = self._login("devone")
        tag = self._create_tag(csrf, "幂等")
        self.client.delete(
            f"{API}/tags/{tag['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        again = self.client.delete(
            f"{API}/tags/{tag['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(again.status_code, 204, again.text)

    def test_stale_revision_conflicts(self) -> None:
        csrf = self._login("devone")
        tag = self._create_tag(csrf, "并发")
        self.client.patch(
            f"{API}/tags/{tag['id']}",
            json={"expectedRevision": 1, "description": "第一次改"},
            headers={"X-CSRF-Token": csrf},
        )
        stale = self.client.patch(
            f"{API}/tags/{tag['id']}",
            json={"expectedRevision": 1, "description": "拿着旧版本再改"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")

    def test_update_requires_a_business_field(self) -> None:
        """契约是 minProperties: 2，只给 expectedRevision 不算一次有效的更新。"""
        csrf = self._login("devone")
        tag = self._create_tag(csrf, "空更新")
        response = self.client.patch(
            f"{API}/tags/{tag['id']}",
            json={"expectedRevision": 1},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_inherited_tags_walk_the_parent_chain(self) -> None:
        csrf = self._login("devone")
        root = self._create_tag(csrf, "根")
        mid = self._create_tag(csrf, "中", parentId=root["id"])
        leaf = self._create_tag(csrf, "叶", parentId=mid["id"])
        detail = self.client.get(f"{API}/tags?scope=manage").json()["items"]
        leaf_row = next(item for item in detail if item["id"] == leaf["id"])
        # 从近到远：先中、后根
        self.assertEqual(leaf_row["inheritedTagIds"], [mid["id"], root["id"]])

    def test_conflict_is_symmetric(self) -> None:
        """A 声明与 B 互斥，B 的响应里也要看到 A。"""
        csrf = self._login("devone")
        first = self._create_tag(csrf, "清廉")
        second = self._create_tag(csrf, "奢靡", conflictsWith=[first["id"]])
        rows = {
            item["id"]: item
            for item in self.client.get(f"{API}/tags").json()["items"]
        }
        self.assertIn(second["id"], rows[first["id"]]["conflictsWith"])

    # ---- 本体 -----------------------------------------------------------

    def test_concept_cycle_uses_ontology_code(self) -> None:
        csrf = self._login("devone")
        resp = self.client.post(
            f"{API}/ontology/concepts",
            json={"name": "本体甲"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        parent = resp.json()
        resp = self.client.post(
            f"{API}/ontology/concepts",
            json={"name": "本体乙", "parentId": parent["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        child = resp.json()
        cycle = self.client.patch(
            f"{API}/ontology/concepts/{parent['id']}",
            json={"expectedRevision": 1, "parentId": child["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(cycle.status_code, 422, cycle.text)
        self.assertEqual(cycle.json()["error"]["code"], "ONTOLOGY_CYCLE")

    def test_concept_deactivate_blocked_by_child(self) -> None:
        csrf = self._login("devone")
        parent = self.client.post(
            f"{API}/ontology/concepts",
            json={"name": "根概念"},
            headers={"X-CSRF-Token": csrf},
        ).json()
        self.client.post(
            f"{API}/ontology/concepts",
            json={"name": "子概念", "parentId": parent["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        response = self.client.delete(
            f"{API}/ontology/concepts/{parent['id']}?expectedRevision=1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "DEPENDENCY_CONFLICT")

    # ---- 知识条目 -------------------------------------------------------

    def _create_knowledge(self, csrf: str, name: str, **extra) -> dict:
        payload = {"type": "concept", "name": name, "summary": "摘要", "content": "正文"}
        payload.update(extra)
        response = self.client.post(
            f"{API}/knowledge", json=payload, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_knowledge_requires_summary_and_content(self) -> None:
        """契约 required 含 summary/content，省略字段应当被拒。"""
        csrf = self._login("devone")
        response = self.client.post(
            f"{API}/knowledge",
            json={"type": "concept", "name": "缺字段"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        paths = {item["path"] for item in response.json()["error"]["fieldErrors"]}
        self.assertIn("summary", paths)
        self.assertIn("content", paths)

    def test_knowledge_tag_inheritance_and_filtering(self) -> None:
        csrf = self._login("devone")
        root = self._create_tag(csrf, "廉洁")
        leaf = self._create_tag(csrf, "清正", parentId=root["id"])
        entity = self._create_knowledge(csrf, "包拯", tagIds=[leaf["id"]])
        self.assertEqual(entity["tagIds"], [leaf["id"]])
        self.assertEqual(entity["inheritedTagIds"], [root["id"]])
        self.assertIn(root["id"], entity["effectiveTagIds"])

        # 用父标签筛选，要能命中只打了子标签的条目
        hit = self.client.get(f"{API}/knowledge?tagIds={root['id']}")
        self.assertEqual(hit.json()["total"], 1)
        miss = self.client.get(f"{API}/knowledge?tagIds=tag-not-there")
        self.assertEqual(miss.json()["total"], 0)

    def test_knowledge_rejects_conflicting_tags(self) -> None:
        csrf = self._login("devone")
        clean = self._create_tag(csrf, "清廉")
        waste = self._create_tag(csrf, "奢靡", conflictsWith=[clean["id"]])
        response = self.client.post(
            f"{API}/knowledge",
            json={
                "type": "person",
                "name": "冲突者",
                "summary": "",
                "content": "",
                "tagIds": [clean["id"], waste["id"]],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "TAG_CONFLICT")

    def test_knowledge_reference_must_exist(self) -> None:
        csrf = self._login("devone")
        response = self.client.post(
            f"{API}/knowledge",
            json={
                "type": "person",
                "name": "空引用",
                "summary": "",
                "content": "",
                "tagIds": ["tag-ghost"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["fieldErrors"][0]["path"], "tagIds")

    def test_knowledge_update_and_soft_delete(self) -> None:
        csrf = self._login("devone")
        entity = self._create_knowledge(csrf, "于谦")
        patched = self.client.patch(
            f"{API}/knowledge/{entity['id']}",
            json={"expectedRevision": 1, "summary": "两袖清风"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["summary"], "两袖清风")
        self.assertEqual(patched.json()["revision"], 2)

        removed = self.client.delete(
            f"{API}/knowledge/{entity['id']}?expectedRevision=2",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(removed.status_code, 204, removed.text)
        # 停用后匿名看不到，按不存在处理
        self.assertEqual(self.client.get(f"{API}/knowledge").json()["total"], 0)

        # 维护者仍能读到停用条目（要能把它救回来），所以断言 404 得先登出
        logout = self.client.post(
            f"{API}/auth/logout", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(logout.status_code, 204, logout.text)
        self.assertEqual(
            self.client.get(f"{API}/knowledge/{entity['id']}").status_code, 404
        )

    def test_knowledge_relation_target_must_exist(self) -> None:
        csrf = self._login("devone")
        response = self.client.post(
            f"{API}/knowledge",
            json={
                "type": "story",
                "name": "断链典故",
                "summary": "",
                "content": "",
                "relations": [
                    {"predicate": "symbolizes", "targetEntityId": "kn-nowhere"}
                ],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"]["fieldErrors"][0]["path"], "relations"
        )

    def test_knowledge_relation_works_when_target_exists(self) -> None:
        csrf = self._login("devone")
        target = self._create_knowledge(csrf, "目标实体")
        source = self._create_knowledge(
            csrf,
            "来源实体",
            relations=[
                {"predicate": "derived_from", "targetEntityId": target["id"]}
            ],
        )
        self.assertEqual(source["relations"][0]["targetEntityId"], target["id"])


if __name__ == "__main__":
    unittest.main()
