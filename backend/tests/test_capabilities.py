"""能力闸门与渲染版本注册表（S0-A1）。

改之前的三个毛病，每条对应一组用例：

1. 能力接口报的 ``rendererVersion`` 是语义引擎版本 ``p0-03.1``，而前端实际
   跑的是 ``codepoet-canvas-1.0.0``——两边各说各话，谁都不报错。
2. 请求体把 ``backend`` 写死成 ``canvas2d``，"支持四后端"只停留在契约文字上。
3. 没有实例级闸门：就算请求能表达 CSS，也没有任何地方判断这台机器跑不跑得了。

这里全部走真实 HTTP，因为要看的是「控制器 → 服务 → 注册表」这条链，
纯函数单测证明不了闸门装在了对的位置。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engine import capabilities
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
        "seed": 7,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": [
            {
                "id": "c1",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 60},
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


def metadata(name: str = "能力测试模板") -> dict:
    return {
        "name": name,
        "description": "",
        "entityIds": [],
        "tagIds": [],
        "symbols": [],
        "timeTheme": None,
    }


class _Base(unittest.TestCase):
    """两种实例配置共用一套骨架：只有 enabled_backends 不同。"""

    enabled_backends = "canvas2d"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None,
            data_dir=Path(self.tmp.name) / "runtime",
            enabled_backends=self.enabled_backends,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf = self._register("cap_user")

    # ---- 辅助 -----------------------------------------------------------
    def _register(self, name: str) -> str:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        created = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(created.status_code, 201, created.text)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "UPDATE users SET role = 'creator' WHERE id = ?", (created.json()["id"],)
        )
        connection.commit()
        connection.close()
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return login.json()["csrfToken"]

    def _template_version(self, supported: list[str] | None = None) -> str:
        created = self.client.post(
            f"{API}/templates",
            json={"metadata": metadata()},
            headers={"X-CSRF-Token": self.csrf},
        ).json()
        version = self.client.post(
            f"{API}/templates/{created['id']}/versions",
            json={
                "irTemplate": template_ir(),
                "parameterSchema": [],
                "bindings": [],
                "constraints": [],
                "supportedBackends": supported or ["canvas2d"],
                "metadata": metadata(),
                "changeNote": "初版",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(version.status_code, (200, 201), version.text)
        return version.json()["id"]

    def _generate_canvas(self) -> dict:
        version_id = self._template_version()
        created = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": version_id,
                "params": {},
                "backend": "canvas2d",
                "seed": 7,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(created.status_code, (200, 201), created.text)
        return created.json()


class CapabilityReportTests(_Base):
    """能力接口声明的版本必须是渲染器真实版本。"""

    def test_renderer_version_is_the_real_canvas_version(self) -> None:
        """以前报 p0-03.1（语义引擎版本），前端实际跑 codepoet-canvas-1.0.0。"""
        caps = self.client.get(f"{API}/engine/capabilities")
        self.assertEqual(caps.status_code, 200, caps.text)
        canvas = next(
            item for item in caps.json()["backends"] if item["backend"] == "canvas2d"
        )
        self.assertEqual(canvas["rendererVersion"], "codepoet-canvas-1.0.0")
        self.assertTrue(canvas["available"])


class CanvasOnlyGateTests(_Base):
    """默认实例只开 Canvas：另外三个后端必须明确拒绝。"""

    enabled_backends = "canvas2d"

    def test_ir_branch_rejects_disabled_backend(self) -> None:
        base = self._generate_canvas()
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": template_ir(),
                "backend": "css",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(error["details"]["backend"], "css")
        self.assertIn("css", error["details"]["unsupportedProperties"])

    def test_template_branch_rejects_disabled_backend(self) -> None:
        version_id = self._template_version(["canvas2d", "css"])
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": version_id,
                "params": {},
                "backend": "css",
                "seed": 7,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_CAPABILITY")

    def test_render_job_rejects_disabled_backend(self) -> None:
        """快照是 canvas2d，请求也是 canvas2d，但如果实例连 canvas2d 都没开，
        就不能再发起渲染——启用集合是运维可调的，快照可读不等于能渲染。"""
        base = self._generate_canvas()
        # 直接改实例配置再发请求，模拟「运行期把 canvas2d 关掉」
        self.app.state.settings.enabled_backends = "css"
        response = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": base["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "codepoet-canvas-1.0.0",
                "clientEnvironment": {"userAgent": "t", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_CAPABILITY")

    def test_canvas_chain_unaffected(self) -> None:
        """旧 Canvas 链路一个字段都没多，行为不变。"""
        base = self._generate_canvas()
        self.assertEqual(base["backend"], "canvas2d")
        job = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": base["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "codepoet-canvas-1.0.0",
                "clientEnvironment": {"userAgent": "t", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(job.status_code, 202, job.text)


class CssEnabledTests(_Base):
    """显式打开 CSS 之后，契约层的门要真的打开（渲染器仍归 R1-B）。

    **只把 css 放进启用集合还不够**：注册表里必须有它的渲染器版本，否则闸门会
    按「没有可执行实现」拒绝——这正是评审指出的「能力声明与闸门不一致」。
    这里临时给 css 注册一个候选版本，模拟 R1-B 的真实渲染器接入；
    「启用但没注册」那一半由 ``UnregisteredBackendTests`` 单独覆盖。
    """

    enabled_backends = "canvas2d,css"

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.dict(
            capabilities.RENDERER_VERSIONS, {"css": "codepoet-css-1.0.0"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ir_branch_accepts_enabled_backend(self) -> None:
        base = self._generate_canvas()
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": template_ir(),
                "backend": "css",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(response.status_code, (200, 201), response.text)
        self.assertEqual(response.json()["backend"], "css")

    def test_template_supported_backends_still_enforced(self) -> None:
        """实例开了 CSS，不代表每个模板都能跑 CSS：版本自己的声明仍然生效。"""
        version_id = self._template_version(["canvas2d"])
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "template",
                "templateVersionId": version_id,
                "params": {},
                "backend": "css",
                "seed": 7,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_CAPABILITY")
        self.assertIn("不支持后端 css", response.json()["error"]["message"])

    def test_version_can_declare_candidate_backend(self) -> None:
        """模板草稿可以声明候选后端，但「声明」不等于「能用」——生成时仍要过闸门。"""
        version_id = self._template_version(["canvas2d", "css"])
        detail = self.client.get(f"{API}/template-versions/{version_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["supportedBackends"], ["canvas2d", "css"])

    def test_unknown_backend_rejected_by_schema(self) -> None:
        base = self._generate_canvas()
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": template_ir(),
                "backend": "flash",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)


class RegistryTests(unittest.TestCase):
    """注册表本身的纯函数行为。"""

    def test_unknown_names_are_filtered(self) -> None:
        from app.engine import capabilities

        settings = Settings(_env_file=None, enabled_backends="canvas2d,flash")
        self.assertEqual(capabilities.enabled_backends(settings), frozenset({"canvas2d"}))

    def test_empty_config_falls_back_to_default(self) -> None:
        from app.engine import capabilities

        settings = Settings(_env_file=None, enabled_backends="")
        self.assertEqual(
            capabilities.enabled_backends(settings),
            frozenset(capabilities.DEFAULT_ENABLED_BACKENDS),
        )

    def test_only_canvas_has_renderer(self) -> None:
        from app.engine import capabilities

        self.assertEqual(
            capabilities.renderer_version("canvas2d"), capabilities.CANVAS_RENDERER_VERSION
        )
        for backend in ("css", "webgl", "turtle"):
            self.assertIsNone(
                capabilities.renderer_version(backend),
                f"{backend} 还没有可执行实现，不该有渲染器版本",
            )

class CapabilityAvailabilityTests(_Base):
    """能力声明必须与实际闸门同源（评审 P2）。

    改之前 `/engine/capabilities` 硬编码 Canvas 可用、其余不可用，完全无视
    Settings；而生成/建任务接口却按 enabled_backends 放行。两套说法不一致，
    前端照着能力接口做判断就会做错。
    """

    enabled_backends = "canvas2d"

    def _canvas(self) -> dict:
        caps = self.client.get(f"{API}/engine/capabilities").json()
        return next(item for item in caps["backends"] if item["backend"] == "canvas2d")

    def test_canvas_available_when_enabled(self) -> None:
        canvas = self._canvas()
        self.assertTrue(canvas["available"])
        self.assertIsNone(canvas["unavailableReason"])
        self.assertEqual(canvas["rendererVersion"], "codepoet-canvas-1.0.0")

    def test_canvas_unavailable_when_disabled(self) -> None:
        # 明确只开 css：canvas2d 就该报不可用，而不是继续宣称可用
        self.app.state.settings.enabled_backends = "css"
        canvas = self._canvas()
        self.assertFalse(canvas["available"], "关掉的后端不能还说自己可用")
        self.assertIn("未启用", canvas["unavailableReason"])

    def test_declared_backend_matches_gate(self) -> None:
        """能力说不可用的后端，请求它就一定被拒；反之亦然。"""
        self.app.state.settings.enabled_backends = "css"
        base = self._generate_canvas_before_disable()
        response = self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": base["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": "codepoet-canvas-1.0.0",
                "clientEnvironment": {"userAgent": "t", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_CAPABILITY")

    def _generate_canvas_before_disable(self) -> dict:
        """先在 Canvas 启用时建好快照，之后再关掉——模拟运维中途调整。"""
        self.app.state.settings.enabled_backends = "canvas2d"
        base = self._generate_canvas()
        self.app.state.settings.enabled_backends = "css"
        return base


class RenderVersionTests(_Base):
    """建任务时校验渲染器版本（评审 P2）。"""

    enabled_backends = "canvas2d"

    def _job(self, version: str):
        base = self._generate_canvas()
        return self.client.post(
            f"{API}/render-jobs",
            json={
                "generationId": base["id"],
                "backend": "canvas2d",
                "timesMs": [0],
                "executionMode": "browser_upload",
                "rendererVersion": version,
                "clientEnvironment": {"userAgent": "t", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_stale_renderer_version_rejected(self) -> None:
        response = self._job("p0-03.1")
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        # 与注册表的统一入口一致：版本不兼容报 UNSUPPORTED_CAPABILITY，
        # 详请字段用 rendererVersion / expectedRendererVersion
        self.assertEqual(error["code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(error["details"]["rendererVersion"], "p0-03.1")
        self.assertEqual(error["details"]["expectedRendererVersion"], "codepoet-canvas-1.0.0")

    def test_matching_renderer_version_accepted(self) -> None:
        response = self._job("codepoet-canvas-1.0.0")
        self.assertEqual(response.status_code, 202, response.text)


class UnregisteredBackendTests(_Base):
    """启用了但**没有可执行渲染器**的后端：声明与闸门必须给同一个答案（评审 P2）。

    改之前是两套判据：
    * `/engine/capabilities` 看「有没有渲染器版本」→ 说 css 不可用；
    * 生成/建任务的闸门只看 `enabled_backends` → 放行。

    于是前端按能力声明把入口关掉，直接调 API 却能成功——反过来也一样糟。
    """

    enabled_backends = "canvas2d,css"

    def _css_capability(self) -> dict:
        caps = self.client.get(f"{API}/engine/capabilities").json()
        return next(item for item in caps["backends"] if item["backend"] == "css")

    def test_declaration_says_unavailable(self) -> None:
        css = self._css_capability()
        self.assertFalse(css["available"], "没有注册渲染器却声明可用")
        self.assertTrue(css["unavailableReason"])

    def test_generation_gate_agrees_with_declaration(self) -> None:
        """声明说不可用，生成就必须拒绝同一后端。"""
        base = self._generate_canvas()
        response = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": template_ir(),
                "backend": "css",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(response.json()["error"]["details"]["backend"], "css")

    def test_canvas_still_works(self) -> None:
        """闸门收紧不能误伤已注册的 canvas2d。"""
        self.assertTrue(self._css_capability() is not None)
        self.assertIn("id", self._generate_canvas())
