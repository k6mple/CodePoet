"""P1A-06 组合检索测试。

这个文件存在的理由是：**过滤代码写对了不等于过滤真的生效**。
参数都在，但「同维度 OR、跨维度 AND」这条契约约定极易写反——
把 tagIds=[a,b] 写成 AND，单维度查询仍然能用，只有叠加两个维度时
才露馅。所以每个维度都先单独验一次，再验叠加。

看板的验收标准原话是「两个维度叠加结果改变」，这里就照着写。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

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


def template_ir() -> dict:
    return {
        "schemaVersion": "1.0",
        "seed": 1,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": [
            {
                "id": "c1",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 80},
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


class TemplateSearchTests(unittest.TestCase):
    """模板：q / 实体 / 标签 / 符号 / 后端 / 时间主题。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""

    def _register(self, name: str, role: str = "developer") -> str:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertIn(response.status_code, (201, 409), response.text)
        user_id = response.json()["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()
        connection.close()
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]},
        )
        self.csrf = login.json()["csrfToken"]
        return user_id

    def _create_template(
        self,
        name: str,
        *,
        symbols: list[str] | None = None,
        tag_ids: list[str] | None = None,
        entity_ids: list[str] | None = None,
        time_theme: str | None = None,
        backends: list[str] | None = None,
    ) -> str:
        metadata = {
            "name": name,
            "description": f"{name} 的说明",
            "entityIds": entity_ids or [],
            "tagIds": tag_ids or [],
            "symbols": symbols or [],
            "timeTheme": time_theme,
        }
        response = self.client.post(
            f"{API}/templates",
            json={"metadata": metadata},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        template_id = response.json()["id"]
        # 建一个版本，让 supportedBackends 可控
        version = self.client.post(
            f"{API}/templates/{template_id}/versions",
            json={
                "irTemplate": template_ir(),
                "parameterSchema": [],
                "bindings": [],
                "constraints": [],
                "supportedBackends": backends or ["canvas2d"],
                "metadata": metadata,
                "changeNote": "初版",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(version.status_code, 201, version.text)
        return template_id

    def _ids(self, query: str) -> set[str]:
        response = self.client.get(f"{API}/templates?scope=mine&pageSize=100&{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def test_each_dimension_alone(self) -> None:
        self._register("search_dev")
        a = self._create_template(
            "莲花模板", symbols=["lotus"], tag_ids=["tag-a"],
            entity_ids=["ent-1"], time_theme="春",
        )
        b = self._create_template(
            "竹子模板", symbols=["bamboo"], tag_ids=["tag-b"],
            entity_ids=["ent-2"], time_theme="夏",
        )
        c = self._create_template(
            "莲花夏景", symbols=["lotus"], tag_ids=["tag-b"],
            entity_ids=["ent-1"], time_theme="夏",
        )

        self.assertEqual(self._ids("symbols=lotus"), {a, c})
        self.assertEqual(self._ids("symbols=bamboo"), {b})
        self.assertEqual(self._ids("tagIds=tag-a"), {a})
        self.assertEqual(self._ids("tagIds=tag-b"), {b, c})
        self.assertEqual(self._ids("entityIds=ent-1"), {a, c})
        self.assertEqual(self._ids("timeTheme=%E5%A4%8F"), {b, c})  # 夏
        self.assertEqual(self._ids("q=%E8%8E%B2%E8%8A%B1"), {a, c})  # 莲花

        # core-demo 只启用 canvas2d（契约 2：未实现的后端不算完成），
        # 所以 canvas2d 命中全部、webgl 命中零个——后者正是过滤生效的证明
        self.assertEqual(self._ids("backend=canvas2d"), {a, b, c})
        self.assertEqual(self._ids("backend=webgl"), set())

    def test_same_dimension_is_or(self) -> None:
        """tagIds=a&tagIds=b 是「或」，不是「且」。"""
        self._register("search_or")
        a = self._create_template("A 模板", tag_ids=["tag-a"])
        b = self._create_template("B 模板", tag_ids=["tag-b"])
        c = self._create_template("C 模板", tag_ids=["tag-c"])

        result = self._ids("tagIds=tag-a&tagIds=tag-b")
        self.assertEqual(result, {a, b}, f"同维度应为 OR，实际 {result}")
        self.assertNotIn(c, result)

    def test_cross_dimension_is_and(self) -> None:
        """看板验收：两个维度叠加后结果改变。"""
        self._register("search_and")
        a = self._create_template("莲花春", symbols=["lotus"], tag_ids=["tag-a"])
        b = self._create_template("竹子春", symbols=["bamboo"], tag_ids=["tag-a"])
        c = self._create_template("莲花夏", symbols=["lotus"], tag_ids=["tag-b"])

        only_symbol = self._ids("symbols=lotus")
        self.assertEqual(only_symbol, {a, c})

        only_tag = self._ids("tagIds=tag-a")
        self.assertEqual(only_tag, {a, b})

        both = self._ids("symbols=lotus&tagIds=tag-a")
        self.assertEqual(both, {a}, f"两个维度叠加应取交集，实际 {both}")
        self.assertLess(len(both), len(only_symbol), "叠加后结果必须变少")
        self.assertLess(len(both), len(only_tag))

    def test_three_dimensions_stack(self) -> None:
        self._register("search_three")
        hit = self._create_template(
            "命中", symbols=["lotus"], tag_ids=["tag-a"],
            entity_ids=["ent-1"], time_theme="春",
        )
        self._create_template(
            "差时间主题", symbols=["lotus"], tag_ids=["tag-a"],
            entity_ids=["ent-1"], time_theme="秋",
        )
        self._create_template(
            "差实体", symbols=["lotus"], tag_ids=["tag-a"],
            entity_ids=["ent-9"], time_theme="春",
        )

        result = self._ids("symbols=lotus&tagIds=tag-a&entityIds=ent-1&timeTheme=%E6%98%A5")
        self.assertEqual(result, {hit})

    def test_empty_result_is_not_error(self) -> None:
        """过滤到空要给空列表 + total=0，不能报错。"""
        self._register("search_empty")
        self._create_template("只有莲花", symbols=["lotus"])
        response = self.client.get(
            f"{API}/templates?scope=mine&symbols=bamboo&tagIds=nope"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])

    def test_q_is_case_insensitive_substring(self) -> None:
        self._register("search_q")
        target = self._create_template("Lotus Blossom")
        self._create_template("Bamboo Grove")
        self.assertEqual(self._ids("q=lotus"), {target})
        self.assertEqual(self._ids("q=LOTUS"), {target})
        self.assertEqual(self._ids("q=blos"), {target})


class KnowledgeSearchTests(unittest.TestCase):
    """知识条目：q / 类型 / 标签 / 实体 / 时间主题。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""

    def _login_dev(self) -> None:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": "kdev", "password": password, "nickname": "kdev"},
            headers={"X-CSRF-Token": token},
        )
        user_id = response.json()["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = 'developer' WHERE id = ?", (user_id,))
        connection.commit()
        connection.close()
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": "kdev", "password": password},
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        self.csrf = login.json()["csrfToken"]

    def _tag(self, name: str) -> str:
        response = self.client.post(
            f"{API}/tags", json={"name": name}, headers={"X-CSRF-Token": self.csrf}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _entity(
        self, name: str, *, kind: str = "concept", tag_ids: list[str] | None = None,
        time_theme: str | None = None,
    ) -> str:
        response = self.client.post(
            f"{API}/knowledge",
            json={
                "type": kind,
                "name": name,
                "aliases": [],
                "summary": f"{name} 摘要",
                "content": f"{name} 正文",
                "tagIds": tag_ids or [],
                "conceptIds": [],
                "relations": [],
                "timeTheme": time_theme,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _ids(self, query: str) -> set[str]:
        response = self.client.get(
            f"{API}/knowledge?scope=manage&pageSize=100&{query}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def test_dimension_filters(self) -> None:
        self._login_dev()
        t_spring = self._tag("春")
        t_summer = self._tag("夏")
        a = self._entity("莲花", kind="symbol", tag_ids=[t_spring], time_theme="春")
        b = self._entity("竹节", kind="symbol", tag_ids=[t_summer], time_theme="夏")
        c = self._entity("清正", kind="concept", tag_ids=[t_spring], time_theme="夏")

        self.assertEqual(self._ids("type=symbol"), {a, b})
        self.assertEqual(self._ids("type=concept"), {c})
        self.assertEqual(self._ids(f"tagIds={t_spring}"), {a, c})
        self.assertEqual(self._ids("timeTheme=%E5%A4%8F"), {b, c})

    def test_same_dimension_or_and_cross_and(self) -> None:
        self._login_dev()
        t1 = self._tag("标签一")
        t2 = self._tag("标签二")
        jia = self._entity("甲", kind="symbol", tag_ids=[t1])
        yi = self._entity("乙", kind="concept", tag_ids=[t2])
        bing = self._entity("丙", kind="symbol", tag_ids=[t2])

        # 同维度 OR：两个标签取并集
        self.assertEqual(self._ids(f"tagIds={t1}&tagIds={t2}"), {jia, yi, bing})

        # 跨维度 AND：标签并集再与 type 取交集
        both = self._ids(f"tagIds={t1}&tagIds={t2}&type=symbol")
        self.assertEqual(both, {jia, bing}, f"叠加 type 后应剩甲丙，实际 {both}")
        self.assertLess(len(both), 3, "叠加后结果必须变少")

    def test_q_matches_name_and_body(self) -> None:
        self._login_dev()
        target = self._entity("莲花")
        other = self._entity("竹节")

        # 按名称命中
        self.assertEqual(self._ids("q=%E8%8E%B2%E8%8A%B1"), {target})
        # 按正文命中（每条实体的 content 都是「<名称> 正文」）
        hit_body = self._ids("q=%E6%AD%A3%E6%96%87")
        self.assertEqual(hit_body, {target, other}, "正文匹配应命中全部")
        # 摘要也命中
        self.assertEqual(self._ids("q=%E6%91%98%E8%A6%81"), {target, other})
