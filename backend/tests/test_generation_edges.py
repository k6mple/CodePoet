"""生成服务的边界路径回归（补 P1 漏测）。

起因：`services/generations.py` 里 `_WARNING_CODES` / `_PARAM_ADAPTER` /
`_CONSTRAINT_ADAPTER` 三个定义被误删，而 **350 个测试全绿**。原因是它们只在
两条窄路径上被求值：

1. IR 校验产生 issue 时——`_split_issues` 才去读 `_WARNING_CODES`，
   而当时唯一提到 `IR_OUT_OF_BOUNDS` 的测试直接调 `validate_ir`，从不走
   `create_generation`；
2. 模板版本带 `parameterSchema` / `constraints` 时——才去用两个 Adapter，
   而现有模板用例的 schema 都是空数组，列表推导一次都没迭代。

也就是说：**「跑通 350 项」和「这两条路径能用」是两件事。** 这里把它们钉住。

刻意不 mock 服务层：直接用真实 app + 真实 SQLite，走 HTTP。
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


def node(node_id: str, cx: float, cy: float, r: float = 20) -> dict:
    return {
        "id": node_id,
        "parentId": None,
        "type": "circle",
        "geometry": {"cx": cx, "cy": cy, "r": r},
        "style": {"fill": "#E8A0A0", "stroke": None, "strokeWidth": 1, "opacity": 1},
    }


def ir_doc(nodes: list[dict]) -> dict:
    return {
        "schemaVersion": "1.0",
        "seed": 1,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": nodes,
        "animations": [],
        "narrative": [],
    }


def metadata(name: str = "边界用例模板") -> dict:
    return {
        "name": name,
        "description": "",
        "entityIds": [],
        "tagIds": [],
        "symbols": [],
        "timeTheme": None,
    }


class _Base(unittest.TestCase):
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
        self.csrf = self._register("edge_user")

    def _register(self, name: str) -> str:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "y" * 12
        created = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(created.status_code, 201, created.text)
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return login.json()["csrfToken"]

    def _canvas_base(self) -> dict:
        """先建一份正常快照，作为 ir 派生的基底。"""
        created = self.client.post(
            f"{API}/templates",
            json={"metadata": metadata()},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        version = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={
                "irTemplate": ir_doc([node("c1", 400, 300)]),
                "parameterSchema": [],
                "bindings": [],
                "constraints": [],
                "supportedBackends": ["canvas2d"],
                "metadata": metadata(),
                "changeNote": "初版",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(version.status_code, (200, 201), version.text)
        base = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": version.json()["id"],
                "params": {},
                "backend": "canvas2d",
                "seed": 1,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(base.status_code, (200, 201), base.text)
        return base.json()


class OutOfBoundsTests(_Base):
    """越界只警告不阻断——这条路径以前没人走过。"""

    def test_out_of_bounds_ir_still_generates_with_warning(self) -> None:
        base = self._canvas_base()
        # 圆心在 5000，远超 800 宽画布：validate_ir 会给 IR_OUT_OF_BOUNDS
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": ir_doc([node("far", 5000, 300)]),
                "backend": "canvas2d",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(response.status_code, (200, 201), response.text)
        body = response.json()
        self.assertTrue(
            body["warnings"], "越界必须作为 warning 记下来，而不是悄悄放过"
        )
        codes = {item["code"] for item in body["warnings"]}
        self.assertIn("IR_OUT_OF_BOUNDS", codes)

    def test_missing_parent_is_blocking_not_warning(self) -> None:
        """反向确认：真正的结构错误仍然 422，没有被 warning 通道放过去。"""
        base = self._canvas_base()
        broken = node("child", 100, 100)
        broken["parentId"] = "ghost"
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": ir_doc([broken]),
                "backend": "canvas2d",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)


class TemplatePartsTests(_Base):
    """模板版本带参数定义与约束时才用到两个 Adapter。"""

    def test_template_with_parameter_schema_and_constraints(self) -> None:
        created = self.client.post(
            f"{API}/templates",
            json={"metadata": metadata()},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        version = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={
                "irTemplate": ir_doc([node("c1", 400, 300)]),
                # 至少各给一个：空数组的列表推导不会迭代，就测不到两个 Adapter
                "parameterSchema": [
                    {
                        "type": "integer",
                        "name": "radius",
                        "label": "半径",
                        "description": "",
                        "default": 20,
                        "min": 10,
                        "max": 200,
                        "step": 1,
                    },
                    {
                        "type": "integer",
                        "name": "limit",
                        "label": "上限",
                        "description": "",
                        "default": 100,
                        "min": 10,
                        "max": 200,
                        "step": 1,
                    },
                ],
                "bindings": [],
                "constraints": [{"type": "lessThan", "left": "radius", "right": "limit"}],
                "supportedBackends": ["canvas2d"],
                "metadata": metadata(),
                "changeNote": "带参数",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(version.status_code, (200, 201), version.text)

        generated = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": version.json()["id"],
                "params": {"radius": 50},
                "backend": "canvas2d",
                "seed": 1,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(generated.status_code, (200, 201), generated.text)
        self.assertTrue(generated.json()["ir"]["nodes"])
