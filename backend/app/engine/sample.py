"""固定时刻的世界几何采样（Q1-A1）。

这是 ``frontend/src/renderers/scene.ts`` 的 Python 侧对等实现。**语义必须逐行
对齐**，因为两边会拿同一份 fixture 比对坐标：任何一处取值差异都会让「服务器
算出来的重叠率」和「浏览器看到的画面」对不上，而质量报告正是拿它当判据。

对齐点（改任何一处都要同步改 TS，否则跨语言比对会红）：

1. 画布限制 ``nodes ≤ 200``、``1 ≤ viewport.width ≤ 800``、``1 ≤ viewport.height ≤ 600``；
2. 时间先截断到 ``[0, durationMs]``，动画用线性插值并再夹一次 ``[0, 1]``；
3. 父链最多 4 层（含自身），循环立刻报错；
4. ``parentId`` 只能指向 ``group``；
5. ``opacity`` **包含祖先**，渲染方不得再乘一次 ``style.opacity``；
6. 圆按 64 段折线采样，角度 ``index * 2π / 64``；
7. **保持 ``ir.nodes`` 的数组顺序**，不是深度优先——旧画序依赖这一点；
8. ``sampleTimes``：静态图 ``[0]``，动画 ``[0, ⌊d/2⌋, d]``。

刻意不做的事：不碰 DOM、不碰 Canvas、不读数据库。纯函数才好在服务端跑。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.schemas.ir import DrawingIR, IRNode

__all__ = [
    "CIRCLE_SEGMENTS",
    "IrSampleError",
    "SampledNode",
    "SampledScene",
    "sample_scene",
    "sample_times",
]

# 与 TS 侧 `Array.from({ length: 64 })` 一致：圆永远 64 段折线
CIRCLE_SEGMENTS = 64
MAX_NODES = 200
MAX_VIEWPORT_WIDTH = 800
MAX_VIEWPORT_HEIGHT = 600
MAX_PARENT_DEPTH = 4

Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class IrSampleError(ValueError):
    """IR 不满足采样前提。服务端据此拒绝，而不是画一张错图。"""


@dataclass(frozen=True)
class SampledNode:
    """一个叶节点在某一时刻的**世界坐标**。"""

    id: str
    type: str
    #: 世界坐标下的顶点序列（圆已展开成 64 边形）
    points: tuple[tuple[float, float], ...]
    #: 已包含祖先透明度
    opacity: float
    style: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SampledScene:
    time_ms: float
    viewport: dict[str, Any]
    background: str | None
    nodes: tuple[SampledNode, ...]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    """2D 仿射 6 元组相乘，与 TS 的 ``multiply`` 完全同式。"""
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def _apply(matrix: Matrix, point: tuple[float, float]) -> tuple[float, float]:
    x, y = point
    return (
        matrix[0] * x + matrix[2] * y + matrix[4],
        matrix[1] * x + matrix[3] * y + matrix[5],
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sample_times(ir: DrawingIR) -> list[int]:
    """保存时要采的帧时刻。静态图只有 0；动画取首/中/尾。"""
    if not ir.durationMs:
        return [0]
    return [0, ir.durationMs // 2, ir.durationMs]


def sample_scene(ir: DrawingIR, time_ms: float = 0) -> SampledScene:
    """把 IR 在 ``time_ms`` 采样成世界坐标下的叶节点序列。"""
    viewport = ir.viewport
    if (
        len(ir.nodes) > MAX_NODES
        or viewport.width < 1
        or viewport.height < 1
        or viewport.width > MAX_VIEWPORT_WIDTH
        or viewport.height > MAX_VIEWPORT_HEIGHT
    ):
        raise IrSampleError("IR 超出 core-demo 画布/节点限制")

    time = _clamp(float(time_ms), 0.0, float(ir.durationMs or 0))
    nodes = {node.id: node for node in ir.nodes}
    states: dict[str, tuple[Matrix, float]] = {}
    visiting: set[str] = set()

    def state(node_id: str) -> tuple[Matrix, float]:
        cached = states.get(node_id)
        if cached is not None:
            return cached
        node = nodes.get(node_id)
        if node is None or node_id in visiting or len(visiting) >= MAX_PARENT_DEPTH:
            raise IrSampleError("IR 父节点不存在、循环或超过4层")
        visiting.add(node_id)
        try:
            transform = _resolve_transform(ir, node, time)
            opacity = _resolve_opacity(ir, node, time)
            angle = math.radians(transform["rotation"])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            local: Matrix = (
                cos_a * transform["scaleX"],
                sin_a * transform["scaleX"],
                -sin_a * transform["scaleY"],
                cos_a * transform["scaleY"],
                transform["x"],
                transform["y"],
            )
            if node.parentId is not None:
                parent_node = nodes.get(node.parentId)
                if parent_node is None or parent_node.type != "group":
                    raise IrSampleError("IR parentId 必须指向 group")
                parent_matrix, parent_opacity = state(node.parentId)
            else:
                parent_matrix, parent_opacity = _IDENTITY, 1.0
            result = (_multiply(parent_matrix, local), parent_opacity * opacity)
            states[node_id] = result
            return result
        finally:
            visiting.discard(node_id)

    sampled: list[SampledNode] = []
    # 必须按数组顺序遍历，不能按父链深度优先：旧画序依赖这个顺序
    for node in ir.nodes:
        matrix, opacity = state(node.id)
        if node.type == "group":
            continue
        if node.material is not None and node.material.shaderRef != "flat":
            raise IrSampleError("仅支持 flat 材质")
        points = _local_points(node)
        sampled.append(
            SampledNode(
                id=node.id,
                type=node.type,
                points=tuple(_apply(matrix, point) for point in points),
                opacity=opacity,
                style=node.style.model_dump() if node.style is not None else {},
            )
        )

    return SampledScene(
        time_ms=time,
        viewport={
            "x": viewport.x,
            "y": viewport.y,
            "width": viewport.width,
            "height": viewport.height,
        },
        background=getattr(ir.canvas, "background", None),
        nodes=tuple(sampled),
    )


def _resolve_transform(ir: DrawingIR, node: IRNode, time: float) -> dict[str, float]:
    transform = {"x": 0.0, "y": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0}
    if node.transform is not None:
        for key in transform:
            value = getattr(node.transform, key, None)
            if value is not None:
                transform[key] = float(value)
    for track in ir.animations:
        if track.nodeId != node.id or track.property == "opacity":
            continue
        if track.property in transform:
            transform[track.property] = _track_value(track, time)
    return transform


def _resolve_opacity(ir: DrawingIR, node: IRNode, time: float) -> float:
    opacity = float(node.style.opacity) if node.style is not None else 1.0
    for track in ir.animations:
        if track.nodeId == node.id and track.property == "opacity":
            opacity = _track_value(track, time)
    return opacity


def _track_value(track, time: float) -> float:
    span = track.endMs - track.startMs
    # span 为 0 时 TS 会得到 NaN；这里显式夹到终点，避免除零
    fraction = 1.0 if span == 0 else _clamp((time - track.startMs) / span, 0.0, 1.0)
    return float(track.from_ + (track.to - track.from_) * fraction)


def _local_points(node: IRNode) -> list[tuple[float, float]]:
    geometry = node.geometry
    if node.type == "circle":
        radius = getattr(geometry, "r", None)
        if radius is None:
            raise IrSampleError("圆缺少半径")
        cx = float(getattr(geometry, "cx", 0.0) or 0.0)
        cy = float(getattr(geometry, "cy", 0.0) or 0.0)
        return [
            (
                cx + float(radius) * math.cos(index * math.pi * 2 / CIRCLE_SEGMENTS),
                cy + float(radius) * math.sin(index * math.pi * 2 / CIRCLE_SEGMENTS),
            )
            for index in range(CIRCLE_SEGMENTS)
        ]
    raw = getattr(geometry, "points", None) or []
    points = [(float(item[0]), float(item[1])) for item in raw]
    if not points:
        raise IrSampleError("IR 图元缺少顶点")
    return points
