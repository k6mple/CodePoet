"""能力与渲染版本注册表（S0-A1）。

存在的理由只有一个：**同一份事实不能有两份声明**。改之前，渲染器版本散在
三处且互相不一致——

* ``api/engine.py`` 把语义引擎版本 ``p0-03.1`` 当成 ``rendererVersion`` 报出去；
* 前端 ``renderers/canvas/renderScene.ts`` 真实用的是 ``codepoet-canvas-1.0.0``；
* ``works.create_render_job`` 直接把客户端上报的版本记下来，从不比对。

于是能力接口对外声明的版本、前端实际运行的版本、库里存下来的版本三者可以
各不相同，而任何一处都不会报错。

这里的边界很窄：**只放「哪个后端可跑、它的渲染器是什么版本」**。
节点上限、参数上限这些 core-demo 硬限额仍留在各自 schema 里，不往这里搬，
免得改一个限额要动注册表。

同时刻意不让它 import ``app.api``：服务层也要读这份注册表，反过来依赖 API
会形成循环。
"""

from __future__ import annotations

from app.settings import Settings

__all__ = [
    "ALL_BACKENDS",
    "require_enabled",
    "CANVAS_RENDERER_VERSION",
    "DEFAULT_ENABLED_BACKENDS",
    "SEMANTIC_ENGINE_VERSION",
    "enabled_backends",
    "renderer_version",
]

# 契约里的四个后端，顺序即文档顺序
ALL_BACKENDS: tuple[str, ...] = ("turtle", "canvas2d", "webgl", "css")

# Canvas 的真实渲染器版本。以前 ``api/engine.py`` 报的是语义引擎版本
# ``p0-03.1``，前端实际跑的是这个；旧作业的 manifest 里存着当时客户端上报的
# 真实值，不在这里回写。
CANVAS_RENDERER_VERSION = "codepoet-canvas-1.0.0"

# 语义引擎版本（规则合成 + IR 生成），与渲染器版本是两件事，不要合并
SEMANTIC_ENGINE_VERSION = "p0-03.1"

# 每个后端的渲染器版本。没登记的等于「还没有可执行实现」。
RENDERER_VERSIONS: dict[str, str] = {
    "canvas2d": CANVAS_RENDERER_VERSION,
}

# 默认只开 Canvas。CSS/WebGL 要等 R1/R2 的真实渲染验收，Turtle 要等 R3，
# 在此之前把它们声明成可用等于对外承诺一个跑不起来的能力。
DEFAULT_ENABLED_BACKENDS: tuple[str, ...] = ("canvas2d",)


def enabled_backends(settings: Settings | None = None) -> frozenset[str]:
    """当前实例启用了哪些后端。

    隔离联调环境可以显式打开候选后端（``CODEPOET_ENABLED_BACKENDS=canvas2d,css``），
    公共部署保持默认。只接受契约里的四个名字，写错的名字一律忽略——
    静默容忍拼写错误会让「我明明开了 CSS」变成一场排查。
    """
    raw = getattr(settings, "enabled_backends", "") or ""
    if not raw.strip():
        return frozenset(DEFAULT_ENABLED_BACKENDS)
    wanted = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return frozenset(name for name in wanted if name in ALL_BACKENDS)


def renderer_version(backend: str) -> str | None:
    """该后端的渲染器版本；没有可执行实现时返回 None。"""
    return RENDERER_VERSIONS.get(backend)


def require_enabled(backend: str, enabled: frozenset[str]) -> None:
    """实例级能力闸门：没启用的后端一律 422。

    生成和建渲染任务两条路都要过这一关。**绝不静默回落到 Canvas**——
    用户以为在跑 CSS、结果拿到一张 Canvas 图，这种「成功」比报错难查得多。

    ``details`` 按契约带 ``backend`` 与 ``unsupportedProperties``，前端据此
    禁用入口，而不是逐次试错。
    """
    if backend in enabled:
        return
    from app import errors  # 函数内导入：errors 不能反过来依赖本模块

    raise errors.unsupported_capability(
        f"当前实例未启用后端 {backend}"
        f"（已启用：{'、'.join(sorted(enabled)) or '无'}）",
        details={
            "backend": backend,
            "unsupportedProperties": sorted(set(ALL_BACKENDS) - enabled),
        },
    )
