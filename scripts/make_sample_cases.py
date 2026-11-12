"""生成跨语言采样 fixture（Q1-A1）。

每一条都是「同一份 IR + 同一组时刻」：先由 Python `sample_scene` 校验能算，
再交给 Node 跑 `frontend/src/renderers/scene.ts` 的 `sampleScene`，两边坐标
必须一致。fixture 是这份一致性的唯一凭据，所以覆盖细则点名的场景：

* 多层变换（2 层 / 3 层 group）
* 动画前 / 中 / 后（含越界截断）
* viewport 非零偏移
* 数组顺序（group 出现在子节点之后，验证不是深度优先）
* 圆（64 段）/ 多边形 / 折线三种图元

**负缩放构造不出来**：`Transform.scaleX/scaleY` 在 schema 上就是 `ge=0.1`，
负值进不了 IR。用 `rotation=180` 表达翻转是等价效果，单列一组并在文档里说明。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.engine.sample import sample_scene  # noqa: E402
from app.schemas.ir import DrawingIR  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "shared" / "demo" / "sample-cases.json"

CANVAS = {"width": 800, "height": 600, "background": "#FFFFFF"}
VIEWPORT = {"x": 0, "y": 0, "width": 800, "height": 600}


def node(node_id: str, node_type: str, parent: str | None = None, **kwargs) -> dict:
    base = {"id": node_id, "parentId": parent, "type": node_type}
    base.update(kwargs)
    return base


def leaf_style(opacity: float = 1.0, fill: str = "#E8A0A0") -> dict:
    return {"fill": fill, "stroke": "#8B3A3A", "strokeWidth": 2, "opacity": opacity}


def ir(nodes: list[dict], animations: list[dict] | None = None,
       duration: int = 0, viewport: dict | None = None, seed: int = 7) -> dict:
    return {
        "schemaVersion": "1.0",
        "seed": seed,
        "canvas": dict(CANVAS),
        "viewport": dict(viewport or VIEWPORT),
        "durationMs": duration,
        "nodes": nodes,
        "animations": animations or [],
        "narrative": [],
    }


CASES: list[dict] = [
    {
        "id": "translate-only",
        "note": "单节点纯平移：世界坐标 = 局部坐标 + 位移",
        "ir": ir([
            node("c1", "circle",
                 geometry={"cx": 10, "cy": 20, "r": 15},
                 style=leaf_style(),
                 transform={"x": 100, "y": 50, "rotation": 0, "scaleX": 1, "scaleY": 1}),
        ]),
        "times": [0],
    },
    {
        "id": "two-level-group",
        "note": "两层 group：外层旋转 90°，内层平移，验证矩阵连乘顺序",
        "ir": ir([
            node("g1", "group", style={"opacity": 1.0},
                 transform={"x": 400, "y": 300, "rotation": 90, "scaleX": 1, "scaleY": 1}),
            node("g2", "group", "g1", style={"opacity": 1.0},
                 transform={"x": 50, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1}),
            node("p1", "polygon", "g2",
                 geometry={"points": [[0, 0], [20, 0], [20, 20]]},
                 style=leaf_style()),
        ]),
        "times": [0],
    },
    {
        "id": "three-level-opacity",
        "note": "三层嵌套 + 透明度累积：0.5 × 0.8 × 0.5 = 0.2",
        "ir": ir([
            node("g1", "group", style={"opacity": 0.5},
                 transform={"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1}),
            node("g2", "group", "g1", style={"opacity": 0.8},
                 transform={"x": 10, "y": 10, "rotation": 0, "scaleX": 1, "scaleY": 1}),
            node("c1", "circle", "g2",
                 geometry={"cx": 0, "cy": 0, "r": 5},
                 style=leaf_style(opacity=0.5)),
        ]),
        "times": [0],
    },
    {
        "id": "animation-before-mid-after",
        "note": "x 轨道 0→200（0~4000ms）：0/1000/2000/4000/9999 五个时刻，末尾越界截断",
        "ir": ir([
            node("p1", "polygon",
                 geometry={"points": [[0, 0], [30, 0], [30, 30]]},
                 style=leaf_style()),
        ], animations=[
            {"nodeId": "p1", "property": "x", "from": 0, "to": 200,
             "startMs": 0, "endMs": 4000, "easing": "linear"},
        ], duration=4000),
        "times": [0, 1000, 2000, 4000, 9999],
    },
    {
        "id": "opacity-animation-overrides-style",
        "note": "透明度动画覆盖 style.opacity（不是相乘），再乘父层 0.5",
        "ir": ir([
            node("g1", "group", style={"opacity": 0.5},
                 transform={"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1}),
            node("c1", "circle", "g1",
                 geometry={"cx": 0, "cy": 0, "r": 8},
                 style=leaf_style(opacity=0.3)),
        ], animations=[
            {"nodeId": "c1", "property": "opacity", "from": 1, "to": 0,
             "startMs": 2000, "endMs": 4000, "easing": "linear"},
        ], duration=4000),
        "times": [0, 2000, 3000, 4000],
    },
    {
        "id": "array-order-not-depth-first",
        "note": "group 写在子节点之后：输出顺序必须是数组顺序，不是深度优先",
        "ir": ir([
            node("c-first", "circle",
                 geometry={"cx": 100, "cy": 100, "r": 10},
                 style=leaf_style(fill="#FF0000")),
            node("g-late", "group", style={"opacity": 1.0},
                 transform={"x": 20, "y": 20, "rotation": 0, "scaleX": 1, "scaleY": 1}),
            node("c-under-late-group", "circle", "g-late",
                 geometry={"cx": 0, "cy": 0, "r": 10},
                 style=leaf_style(fill="#0000FF")),
        ]),
        "times": [0],
    },
    {
        "id": "viewport-offset",
        "note": "viewport 非零偏移只被原样带出，不参与世界坐标计算",
        "ir": ir([
            node("l1", "polyline",
                 geometry={"points": [[0, 0], [40, 40], [80, 0]]},
                 style={"fill": None, "stroke": "#333333", "strokeWidth": 3, "opacity": 1}),
        ], viewport={"x": 120, "y": 80, "width": 600, "height": 400}),
        "times": [0],
    },
    {
        "id": "rotation-180-as-flip",
        "note": "负缩放 schema 不允许（scaleX ge=0.1），用 180° 旋转表达翻转",
        "ir": ir([
            node("p1", "polygon",
                 geometry={"points": [[0, 0], [40, 0], [40, 10]]},
                 style=leaf_style(),
                 transform={"x": 200, "y": 200, "rotation": 180, "scaleX": 1, "scaleY": 1}),
        ]),
        "times": [0],
    },
    {
        "id": "scale-and-mixed-geometry",
        "note": "非 1 缩放 + 圆/多边形/折线混合，验证 64 段圆在缩放下的顶点",
        "ir": ir([
            node("c1", "circle",
                 geometry={"cx": 50, "cy": 50, "r": 20},
                 style=leaf_style(),
                 transform={"x": 0, "y": 0, "rotation": 0, "scaleX": 1.5, "scaleY": 0.5}),
            node("p1", "polygon",
                 geometry={"points": [[0, 0], [20, 0], [10, 20]]},
                 style=leaf_style(fill="#00AA00"),
                 transform={"x": 100, "y": 0, "rotation": 30, "scaleX": 1, "scaleY": 1}),
            node("l1", "polyline",
                 geometry={"points": [[0, 0], [15, 15]]},
                 style={"fill": None, "stroke": "#000000", "strokeWidth": 2, "opacity": 1}),
        ]),
        "times": [0],
    },
]


def main() -> int:
    payload = []
    for case in CASES:
        document = DrawingIR.model_validate(case["ir"])
        # 先自己算一遍：算不出来说明 fixture 本身不合法，别提交
        for time_ms in case["times"]:
            sample_scene(document, time_ms)
        payload.append({
            "id": case["id"],
            "note": case["note"],
            "ir": case["ir"],
            "times": case["times"],
        })
        print(f"  OK {case['id']}：{len(case['times'])} 个时刻")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"version": 1, "cases": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n已写 {OUT}（{len(payload)} 组）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
