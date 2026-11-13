"""引擎能力查询接口。

前端拿这一个接口就能知道自己能画什么，不必去读 348K 的契约文件。

契约要求（接口文档 2.1）：权限 G，公共读，不需要登录。
未实现的后端必须 available=false 并给出 unavailableReason，
不能含糊其辞——文档里明确写了「不把未实现说成暂时渲染成功」。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from app.settings import Settings

from app.schemas.generation import (
    Capabilities,
    Capability,
    CapabilityFeatureFlags,
)
from app.engine import capabilities

router = APIRouter(tags=["引擎"])

# 视觉参数目前只接线性插值的四个属性；缩放动画不在当前范围内
_ANIMATION_PROPS = ["x", "y", "rotation", "opacity"]
_NODE_TYPES = ["group", "circle", "polygon", "polyline"]

_UNAVAILABLE_REASONS = {
    "canvas2d": "当前实例未启用 Canvas 2D",
    "turtle": "Turtle 后端尚未实现",
    "webgl": "基础版不做 GLSL 与 WebGL，未实现",
    "css": "当前实例未启用 CSS",
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


def build_capabilities(settings: Settings | None = None) -> Capabilities:
    enabled = capabilities.enabled_backends(settings)

    def entry(backend: str) -> Capability:
        version = capabilities.renderer_version(backend)
        if backend not in enabled or version is None:
            item = _unsupported(backend)
            if version is not None:
                item.rendererVersion = version
                item.unavailableReason = "当前实例未启用此后端"
            return item
        return Capability(
            backend=backend, available=True, unavailableReason=None,
            rendererVersion=version, nodeTypes=_NODE_TYPES, shaderRefs=["flat"],
            animationProperties=_ANIMATION_PROPS, supportsContinuousPlayback=True,
            maxNodes=200, maxFrames=3, executionModes=["browser_upload"],
        )

    return Capabilities(
        schemaVersion="1.0",
        backends=[
            entry("canvas2d"), entry("turtle"), entry("webgl"), entry("css"),
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
            qualityAssessment=True,
            dslSandbox=False,
            advancedPolicies=False,
            headlessRendering=False,
            selectiveExport=False,
            workSnapshotImport=False,
            # 来源从部署配置读：配了才对外宣称可用，否则前端会显示一个
            # 点进去就 503 的入口
            knowledgeGraph=bool(
                (settings.knowledge_graph_sources if settings else "").strip()
            ),
        ),
    )


@router.get(
    "/engine/capabilities",
    response_model=Capabilities,
    summary="引擎能力与运行可用性",
    description="返回 profile、四个后端的可用性与限制、以及功能开关。公开可读。",
)
def read_capabilities(request: Request) -> Capabilities:
    return build_capabilities(request.app.state.settings)
