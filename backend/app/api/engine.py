"""引擎能力查询接口。

前端拿这一个接口就能知道自己能画什么，不必去读 348K 的契约文件。

契约要求（接口文档 2.1）：权限 G，公共读，不需要登录。
未实现的后端必须 available=false 并给出 unavailableReason，
不能含糊其辞——文档里明确写了「不把未实现说成暂时渲染成功」。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas.generation import (
    Capabilities,
    Capability,
    CapabilityFeatureFlags,
)
from app.engine import capabilities
from app.settings import Settings
from app.services.generations import ENGINE_VERSION

router = APIRouter(tags=["引擎"])

# 视觉参数目前只接线性插值的四个属性；缩放动画不在当前范围内
_ANIMATION_PROPS = ["x", "y", "rotation", "opacity"]
_NODE_TYPES = ["group", "circle", "polygon", "polyline"]

_UNAVAILABLE_REASONS = {
    "turtle": "基础版只实现 Canvas 2D，Turtle 后端未实现",
    "webgl": "基础版不做 GLSL 与 WebGL，未实现",
    "css": "基础版不做 CSS 渲染，未实现",
}


def _unsupported(backend: str, reason: str | None = None) -> Capability:
    """不可用的后端：契约要求 maxNodes / maxFrames 至少为 1，填 1 占位。"""
    return Capability(
        backend=backend,  # type: ignore[arg-type]
        available=False,
        unavailableReason=reason or _UNAVAILABLE_REASONS[backend],
        rendererVersion="none",
        nodeTypes=[],
        shaderRefs=[],
        animationProperties=[],
        supportsContinuousPlayback=False,
        maxNodes=1,
        maxFrames=1,
        executionModes=[],
    )


def _availability(backend: str, enabled: frozenset[str]) -> str | None:
    """这个后端为什么可用/不可用。返回 None 表示可用。

    判据只有两条，缺一不可：

    1. 实例启用了它（``CODEPOET_ENABLED_BACKENDS``）；
    2. 注册表里有它的渲染器版本，也就是真的有可执行实现。

    只看第 1 条会对外宣称一个跑不起来的能力；只看第 2 条则无视实例配置，
    导致关掉 Canvas 之后接口还在说可用——两种都会让前端做出错误判断。
    """
    if backend not in enabled:
        return "当前实例未启用该后端"
    if capabilities.renderer_version(backend) is None:
        return _UNAVAILABLE_REASONS.get(backend, "渲染器尚未实现")
    return None


def build_capabilities(settings: Settings | None = None) -> Capabilities:
    enabled = capabilities.enabled_backends(settings)
    canvas_reason = _availability("canvas2d", enabled)
    return Capabilities(
        schemaVersion="1.0",
        backends=[
            Capability(
                backend="canvas2d",
                available=canvas_reason is None,
                unavailableReason=canvas_reason,
                # 渲染器版本与语义引擎版本是两件事：以前这里报 p0-03.1，
                # 而前端实际跑的是 codepoet-canvas-1.0.0，两边各说各话。
                rendererVersion=capabilities.renderer_version("canvas2d") or ENGINE_VERSION,
                nodeTypes=_NODE_TYPES,  # type: ignore[arg-type]
                shaderRefs=["flat"],
                animationProperties=_ANIMATION_PROPS,  # type: ignore[arg-type]
                supportsContinuousPlayback=True,
                maxNodes=200,
                # 帧采样属 P1B，这里给演示够用的保守值，不夸大成 12
                maxFrames=6,
                executionModes=["browser_upload"],
            ),
            _unsupported("turtle", _availability("turtle", enabled)),
            _unsupported("webgl", _availability("webgl", enabled)),
            _unsupported("css", _availability("css", enabled)),
        ],
        maxDurationMs=5000,
        maxCanvasSize=800,
        profile="core-demo",
        maxVertices=2000,
        maxDepth=4,
        maxParameters=16,
        features=CapabilityFeatureFlags(
            semanticGeneration=True,
            # POST /generations/{id}/subgraph 已实现并有 8 条回归覆盖（子树替换、
            # 空替换即删除、越界 ID 冲突、悬空父引用、viewport 覆盖、他人快照 404）。
            # 接口能用却声明 false，前端按声明尊重能力，页面就把入口关掉了。
            subgraphEditing=True,
            dslSandbox=False,
            advancedPolicies=False,
            headlessRendering=False,
            selectiveExport=False,
            workSnapshotImport=False,
        ),
    )


@router.get(
    "/engine/capabilities",
    response_model=Capabilities,
    summary="引擎能力与运行可用性",
    description="返回 profile、四个后端的可用性与限制、以及功能开关。公开可读。",
)
def read_capabilities(request: Request) -> Capabilities:
    # 能力声明必须与生成/建任务用的是同一份事实，否则前端会照着错的声明做判断
    return build_capabilities(request.app.state.settings)
