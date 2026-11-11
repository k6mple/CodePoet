"""P1B-01 语义规则测试。

看板验收原话是「莲与竹改变符号、色板、构图；可查规则」。所以核心用例是
**同一套接口下，不同输入产出结构不同的 IR** —— 只测"接口返回 200"证明不了
规则真的生效，必须比对节点类型、颜色和坐标。

另外两条容易漏的：
* 同优先级多条规则的排序必须确定，否则同一句话刷两次会画出两张不同的图
* 空规则不能命中任何输入，否则一条刚建好还没填条件的规则会静默覆盖一切
"""

from __future__ import annotations

import json
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


def rule_body(name: str, *, keywords, symbols, palette, composition="centered",
              motion="none", priority=10, enabled=True, entityIds=None,
              tagIds=None, operator="any", caption="") -> dict:
    return {
        "name": name,
        "enabled": enabled,
        "priority": priority,
        "match": {
            "keywords": list(keywords),
            "entityIds": entityIds or [],
            "conceptIds": [],
            "tagIds": tagIds or [],
            "operator": operator,
        },
        "effects": {
            "symbolIds": list(symbols),
            "palette": list(palette),
            "composition": composition,
            "motion": motion,
            "caption": caption,
        },
        "explanation": f"{name} 的说明",
    }


def semantic_body(text: str, entity_ids=None, seed: int = 42) -> dict:
    return {
        "mode": "semantic",
        "input": {"text": text, "entityIds": entity_ids or []},
        "backend": "canvas2d",
        "seed": seed,
    }


class RuleCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = ""

    def _register(self, name: str, role: str) -> str:
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
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        self.csrf = login.json()["csrfToken"]
        return user_id

    def _create(self, **kwargs) -> dict:
        response = self.client.post(
            f"{API}/rules", json=rule_body(**kwargs),
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ---- CRUD -----------------------------------------------------------
    def test_create_and_list(self) -> None:
        self._register("rule_admin", "admin")
        created = self._create(
            name="莲花规则", keywords=["莲"], symbols=["lotus"],
            palette=["#D85A30"], composition="radial",
        )
        self.assertEqual(created["version"], 1)
        self.assertEqual(created["revision"], 1)
        self.assertTrue(created["enabled"])
        self.assertEqual(created["effects"]["composition"], "radial")

        listed = self.client.get(f"{API}/rules").json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["id"], created["id"])

    def test_list_orders_by_priority_then_created(self) -> None:
        self._register("rule_order", "admin")
        low = self._create(name="低", keywords=["a"], symbols=["lotus"],
                           palette=["#111111"], priority=1)
        high = self._create(name="高", keywords=["b"], symbols=["bamboo"],
                            palette=["#222222"], priority=99)
        listed = self.client.get(f"{API}/rules").json()
        self.assertEqual([i["id"] for i in listed["items"]], [high["id"], low["id"]])

    def test_filter_by_enabled_and_q(self) -> None:
        self._register("rule_filter", "admin")
        self._create(name="启用规则", keywords=["a"], symbols=["lotus"], palette=["#111111"])
        self._create(name="停用规则", keywords=["b"], symbols=["bamboo"],
                     palette=["#222222"], enabled=False)

        only_enabled = self.client.get(f"{API}/rules?enabled=true").json()
        self.assertEqual(only_enabled["total"], 1)
        self.assertEqual(only_enabled["items"][0]["name"], "启用规则")

        searched = self.client.get(f"{API}/rules?q=%E5%81%9C%E7%94%A8").json()  # 停用
        self.assertEqual(searched["total"], 1)
        self.assertEqual(searched["items"][0]["name"], "停用规则")

    def test_update_bumps_both_counters(self) -> None:
        self._register("rule_update", "admin")
        created = self._create(name="待改", keywords=["a"], symbols=["lotus"],
                               palette=["#111111"])
        response = self.client.patch(
            f"{API}/rules/{created['id']}",
            json={"expectedRevision": created["revision"], "priority": 500,
                  "effects": {**created["effects"], "composition": "vertical"}},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["priority"], 500)
        self.assertEqual(updated["effects"]["composition"], "vertical")
        self.assertEqual(updated["revision"], 2, "乐观锁计数器 +1")
        self.assertEqual(updated["version"], 2, "内容版本也要 +1")
        # 未提交的字段保持原值
        self.assertEqual(updated["name"], "待改")

    def test_update_with_stale_revision_conflicts(self) -> None:
        self._register("rule_stale", "admin")
        created = self._create(name="并发", keywords=["a"], symbols=["lotus"],
                               palette=["#111111"])
        response = self.client.patch(
            f"{API}/rules/{created['id']}",
            json={"expectedRevision": 99, "priority": 1},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_write_requires_admin(self) -> None:
        self._register("rule_dev", "developer")
        response = self.client.post(
            f"{API}/rules",
            json=rule_body(name="越权", keywords=["a"], symbols=["lotus"],
                           palette=["#111111"]),
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_list_requires_login(self) -> None:
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]})
        self.assertEqual(self.client.get(f"{API}/rules").status_code, 401)

    def test_rejects_bad_palette(self) -> None:
        self._register("rule_color", "admin")
        body = rule_body(name="坏色", keywords=["a"], symbols=["lotus"], palette=["red"])
        response = self.client.post(f"{API}/rules", json=body,
                                    headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 422, response.text)

    def test_rejects_priority_out_of_range(self) -> None:
        self._register("rule_range", "admin")
        body = rule_body(name="越界", keywords=["a"], symbols=["lotus"],
                         palette=["#111111"], priority=5000)
        response = self.client.post(f"{API}/rules", json=body,
                                    headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 422, response.text)


class SemanticGenerationTests(unittest.TestCase):
    """看板验收：莲与竹改变符号、色板、构图。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, data_dir=Path(self.tmp.name) / "runtime")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.admin_csrf = ""
        self.creator_csrf = ""

    def _register(self, name: str, role: str) -> None:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        response = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        user_id = response.json()["id"]
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()
        connection.close()
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={
                "X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
            },
        )
        return login.json()["csrfToken"]

    def _switch(self, csrf: str) -> None:
        self.client.post(f"{API}/auth/logout",
                         headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]})

    def _setup_rules(self) -> None:
        self.admin_csrf = self._register("sem_admin", "admin")
        for body in [
            rule_body("莲花语义", keywords=["莲"], symbols=["lotus"],
                      palette=["#D85A30", "#FAC775"], composition="centered",
                      priority=50, caption="莲"),
            rule_body("竹节语义", keywords=["竹"], symbols=["bamboo"],
                      palette=["#639922", "#27500A"], composition="vertical",
                      priority=50, motion="sway", caption="竹"),
        ]:
            response = self.client.post(f"{API}/rules", json=body,
                                        headers={"X-CSRF-Token": self.admin_csrf})
            self.assertEqual(response.status_code, 201, response.text)
        self._switch(self.admin_csrf)
        self.creator_csrf = self._register("sem_creator", "creator")

    def _generate(self, text: str) -> dict:
        response = self.client.post(
            f"{API}/generations",
            json=semantic_body(text),
            headers={"X-CSRF-Token": self.creator_csrf},
        )
        return response

    def test_lotus_and_bamboo_produce_different_ir(self) -> None:
        """看板核心验收：两种输入返回不同 IR。"""
        self._setup_rules()

        lotus = self._generate("莲花清正")
        self.assertEqual(lotus.status_code, 201, lotus.text)
        bamboo = self._generate("竹节守正")
        self.assertEqual(bamboo.status_code, 201, bamboo.text)

        lotus_body = lotus.json()
        bamboo_body = bamboo.json()

        self.assertEqual(lotus_body["mode"], "semantic")
        self.assertEqual(bamboo_body["mode"], "semantic")
        self.assertNotEqual(lotus_body["irHash"], bamboo_body["irHash"],
                            "莲与竹必须产出不同的 IR 哈希")

        lotus_nodes = lotus_body["ir"]["nodes"]
        bamboo_nodes = bamboo_body["ir"]["nodes"]

        # 莲：6 个花瓣圆 + 1 个花心 = 7 个圆
        self.assertEqual({n["type"] for n in lotus_nodes}, {"circle"}, "莲应全是圆")
        self.assertEqual(len(lotus_nodes), 7)

        # 竹：3 段竖条 = 3 个多边形
        self.assertEqual({n["type"] for n in bamboo_nodes}, {"polygon"}, "竹应全是多边形")
        self.assertEqual(len(bamboo_nodes), 3)

        # 色板不同
        lotus_fill = lotus_nodes[0]["style"]["fill"]
        bamboo_fill = bamboo_nodes[0]["style"]["fill"]
        self.assertEqual(lotus_fill, "#D85A30")
        self.assertEqual(bamboo_fill, "#639922")
        self.assertNotEqual(lotus_fill, bamboo_fill)

    def test_composition_changes_coordinates(self) -> None:
        """构图真的改变坐标，不只是记在 provenance 里。"""
        self._setup_rules()
        lotus = self._generate("莲花").json()
        bamboo = self._generate("竹节").json()

        # Point 是 tuple[float, float]，取 [0]=x、[1]=y
        bamboo_xs = {n["geometry"]["points"][0][0] for n in bamboo["ir"]["nodes"]}
        bamboo_ys = [n["geometry"]["points"][0][1] for n in bamboo["ir"]["nodes"]]
        self.assertEqual(len(bamboo_xs), 1, "竖排的竹各段左侧 x 应相同")
        self.assertEqual(bamboo_ys, sorted(bamboo_ys), "竖排的竹各段应自上而下递增")

        # 莲是环形排布：各花瓣圆心 x 与 y 都不相同
        lotus_ys = {n["geometry"]["cy"] for n in lotus["ir"]["nodes"]}
        lotus_xs = {n["geometry"]["cx"] for n in lotus["ir"]["nodes"]}
        self.assertGreater(len(lotus_ys), 1)
        self.assertGreater(len(lotus_xs), 1)

    def test_motion_produces_animations(self) -> None:
        self._setup_rules()
        bamboo = self._generate("竹节守正").json()
        self.assertEqual(bamboo["ir"]["durationMs"], 2000, "sway 应给出动画时长")
        self.assertTrue(bamboo["ir"]["animations"], "sway 应生成动画轨道")
        self.assertEqual(bamboo["ir"]["animations"][0]["property"], "rotation")

        lotus = self._generate("莲花").json()
        self.assertEqual(lotus["ir"]["durationMs"], 0, "未设 motion 保持静态")
        self.assertEqual(lotus["ir"]["animations"], [])

    def test_provenance_records_rule_snapshot(self) -> None:
        self._setup_rules()
        body = self._generate("莲花清正").json()
        provenance = body["provenance"]
        self.assertEqual(provenance["kind"], "semantic")
        self.assertEqual(len(provenance["matchedRules"]), 1)
        snapshot = provenance["matchedRules"][0]
        self.assertIn("ruleId", snapshot)
        self.assertEqual(snapshot["version"], 1)
        # 规则内容整份存进快照：规则以后改了，旧作品溯源仍然准确
        self.assertEqual(snapshot["content"]["name"], "莲花语义")
        self.assertEqual(provenance["selectedSymbols"], ["lotus"])
        self.assertIsNotNone(provenance["paletteDecision"])
        self.assertIsNotNone(provenance["compositionDecision"])

    def test_unmatched_input_is_rejected(self) -> None:
        """没有规则命中要报 422，不画兜底图。"""
        self._setup_rules()
        response = self._generate("完全无关的输入")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "NO_SEMANTIC_MATCH")

    def test_empty_rule_never_matches(self) -> None:
        """四组条件全空的规则不该匹配一切。"""
        self.admin_csrf = self._register("empty_admin", "admin")
        empty = rule_body("空规则", keywords=[], symbols=["lotus"],
                          palette=["#111111"], priority=1000)
        response = self.client.post(f"{API}/rules", json=empty,
                                    headers={"X-CSRF-Token": self.admin_csrf})
        self.assertEqual(response.status_code, 201, response.text)

        self._switch(self.admin_csrf)
        self.creator_csrf = self._register("empty_creator", "creator")
        result = self._generate("任何东西")
        self.assertEqual(result.status_code, 422, "空规则不能兜住所有输入")
        self.assertEqual(result.json()["error"]["code"], "NO_SEMANTIC_MATCH")

    def test_priority_wins_on_composition(self) -> None:
        """高优先级规则决定构图，低优先级的只补符号。"""
        self.admin_csrf = self._register("prio_admin", "admin")
        for body in [
            rule_body("低优先", keywords=["莲"], symbols=["water"],
                      palette=["#378ADD"], composition="horizontal", priority=1),
            rule_body("高优先", keywords=["莲"], symbols=["lotus"],
                      palette=["#BA7517"], composition="radial", priority=999),
        ]:
            self.client.post(f"{API}/rules", json=body,
                             headers={"X-CSRF-Token": self.admin_csrf})
        self._switch(self.admin_csrf)
        self.creator_csrf = self._register("prio_creator", "creator")

        body = self._generate("莲花").json()
        self.assertEqual(body["provenance"]["compositionDecision"]["value"], "radial")
        # 符号取并集：两条规则都要出现
        self.assertEqual(set(body["provenance"]["selectedSymbols"]), {"lotus", "water"})

    def test_disabled_rule_is_ignored(self) -> None:
        self.admin_csrf = self._register("off_admin", "admin")
        body = rule_body("停用规则", keywords=["莲"], symbols=["lotus"],
                         palette=["#111111"], enabled=False)
        self.client.post(f"{API}/rules", json=body,
                         headers={"X-CSRF-Token": self.admin_csrf})
        self._switch(self.admin_csrf)
        self.creator_csrf = self._register("off_creator", "creator")
        self.assertEqual(self._generate("莲花").status_code, 422)

    def test_generation_is_deterministic(self) -> None:
        """同一个输入两次生成，IR 哈希必须一致。"""
        self._setup_rules()
        first = self._generate("莲花清正").json()
        second = self._generate("莲花清正").json()
        self.assertEqual(first["irHash"], second["irHash"])
        self.assertEqual(first["ir"], second["ir"])
