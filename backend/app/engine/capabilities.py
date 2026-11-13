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
    "require_renderer_version",
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
    # 两个条件缺一不可，且必须与 `/engine/capabilities` 的声明同源：
    #   a) 实例启用了它（CODEPOET_ENABLED_BACKENDS）；
    #   b) 注册表里有它的渲染器版本，也就是**真有可执行实现**。
    # 只看 a) 会出现「能力接口说 css 不可用，生成闸门却放行」——评审指出的那种。
    if backend in enabled and renderer_version(backend) is not None:
        return
    from app import errors  # 函数内导入：errors 不能反过来依赖本模块

    executable = {name for name in enabled if renderer_version(name) is not None}
    raise errors.unsupported_capability(
        f"当前实例未启用后端 {backend}"
        f"（已启用：{'、'.join(sorted(enabled)) or '无'}）",
        details={
            "backend": backend,
            "unsupportedProperties": sorted(set(ALL_BACKENDS) - executable),
        },
    )


def require_renderer_version(backend: str, version: str) -> None:
    """客户端上报的渲染器版本必须是本实例此刻能执行的那个。

    与能力声明同源：`/engine/capabilities` 报什么版本，这里就只接受什么版本。
    历史任务里存的旧版本**不回写、不篡改**——那是当时的记录；但**新建**的任务
    必须用可执行版本，否则 manifest 里的 rendererVersion 和实际跑的东西对不上。

    与 ``require_enabled`` 分开是因为两者的失败语义不同：前者是「这个后端不让跑」，
    这里是「后端能跑，但你报的版本不是它」。前端要分别提示。
    """
    expected = renderer_version(backend)
    if expected is not None and version == expected:
        return
    from app import errors

    raise errors.unsupported_capability(
        f"渲染器版本不兼容：能力声明 {expected or '无可执行实现'}，请求 {version}",
        details={
            "backend": backend,
            "rendererVersion": version,
            "expectedRendererVersion": expected,
            "unsupportedProperties": ["rendererVersion"],
        },
    )
