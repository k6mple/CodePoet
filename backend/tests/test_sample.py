"""服务器采样与前端采样的一致性（Q1-A1）。

核心断言只有一条：**同一份 IR、同一个时刻，Python 和 TypeScript 算出的世界
坐标必须一致**。否则服务端算「两个主体重叠了多少」和浏览器实际画的画面会对
不上，而质量报告正是拿重叠率当判据——那比不报质量更糟。

期望值怎么来的（不是我自己编的）：

1. `shared/demo/sample-cases.json` 里是 9 组输入（多层变换、动画前/中/后、
   viewport 偏移、数组顺序、圆/多边形/折线混合）；
2. 用 `frontend/src/renderers/scene.ts` 的真实实现跑出坐标，存进
   `tests/fixtures/sample-expected.json`；
3. 本文件把 Python 结果与那份期望值逐点比对。

重新生成期望值的方式见 `scripts/gen_sample_expected.mjs` 头部说明（需要 Node
与 tsc，CI 里不跑，所以期望值是提交进仓库的）。

**负缩放构造不出来，旋转也不等价**：`Transform.scaleX/scaleY` 的 schema 是
`ge=0.1`，负值进不了 IR。`rotation=180` 是双轴同时取反，**不是单轴负缩放**
（镜像翻转的画序与绕向都不同）。本契约不支持负缩放；fixture 里那组旋转样例
只覆盖旋转，不能算作负缩放覆盖。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.engine.sample import (
    CIRCLE_SEGMENTS,
    IrSampleError,
    sample_scene,
    sample_times,
)
from app.schemas.ir import DrawingIR

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CASES = REPO / "shared" / "demo" / "sample-cases.json"
EXPECTED = BACKEND / "tests" / "fixtures" / "sample-expected.json"

# 浮点比较：两侧都是 IEEE754 double，同一公式的末位差异远小于这个量级
TOL = 1e-9


def load_fixture() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def load_expected() -> dict[tuple[str, float], dict]:
    items = json.loads(EXPECTED.read_text(encoding="utf-8"))
    return {(item["id"], round(item["timeMs"], 6)): item for item in items}


def simple_ir(**overrides) -> dict:
    base = {
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
                "geometry": {"cx": 10, "cy": 10, "r": 5},
                "style": {
                    "fill": "#FF0000",
                    "stroke": None,
                    "strokeWidth": 0,
                    "opacity": 1,
                },
            }
        ],
        "animations": [],
        "narrative": [],
    }
    base.update(overrides)
    return base


class CrossLanguageTests(unittest.TestCase):
    """与 TS 实现逐点比对。"""

    def test_fixture_exists(self) -> None:
        self.assertTrue(CASES.exists(), f"缺 fixture：{CASES}")
        self.assertTrue(EXPECTED.exists(), f"缺期望值：{EXPECTED}")

    def test_matches_typescript_implementation(self) -> None:
        fixture = load_fixture()
        expected = load_expected()
        checked = 0
        for case in fixture["cases"]:
            ir = DrawingIR.model_validate(case["ir"])
            times = sample_times(ir)
            for time_ms in case["times"]:
                scene = sample_scene(ir, time_ms)
                key = (case["id"], round(scene.time_ms, 6))
                self.assertIn(key, expected, f"{case['id']} t={time_ms} 缺少 TS 期望值")
                ref = expected[key]
                checked += 1

                self.assertEqual(ref["sampleTimes"], times, f"{case['id']} 采样时刻不一致")
                self.assertEqual(
                    len(ref["nodes"]), len(scene.nodes),
                    f"{case['id']} t={time_ms} 节点数不一致",
                )
                for got, want in zip(scene.nodes, ref["nodes"]):
                    self.assertEqual(want["id"], got.id, f"{case['id']} 节点顺序不一致")
                    self.assertAlmostEqual(
                        want["opacity"], got.opacity, delta=TOL,
                        msg=f"{case['id']}/{got.id} t={time_ms} 透明度不一致",
                    )
                    self.assertEqual(
                        len(want["points"]), len(got.points),
                        f"{case['id']}/{got.id} 顶点数不一致",
                    )
                    for index, (point, target) in enumerate(zip(got.points, want["points"])):
                        self.assertAlmostEqual(
                            target[0], point[0], delta=TOL,
                            msg=f"{case['id']}/{got.id} t={time_ms} 顶点{index} x 不一致",
                        )
                        self.assertAlmostEqual(
                            target[1], point[1], delta=TOL,
                            msg=f"{case['id']}/{got.id} t={time_ms} 顶点{index} y 不一致",
                        )
        self.assertGreaterEqual(checked, 9, "比对组数太少，fixture 没覆盖全")


class SamplingSemanticsTests(unittest.TestCase):
    """几条容易搞错、且不依赖 fixture 的语义。"""

    def test_circle_is_64_segments(self) -> None:
        scene = sample_scene(DrawingIR.model_validate(simple_ir()))
        self.assertEqual(len(scene.nodes[0].points), CIRCLE_SEGMENTS)

    def test_circle_first_point_on_positive_x(self) -> None:
        """角度从 0 开始：第一个点在 (cx + r, cy)，与 TS 的 Array.from 一致。"""
        scene = sample_scene(DrawingIR.model_validate(simple_ir()))
        x, y = scene.nodes[0].points[0]
        self.assertAlmostEqual(x, 15.0, places=9)
        self.assertAlmostEqual(y, 10.0, places=9)

    def test_time_is_clamped_to_duration(self) -> None:
        document = DrawingIR.model_validate(simple_ir(durationMs=1000))
        self.assertEqual(sample_scene(document, 99_999).time_ms, 1000)
        self.assertEqual(sample_scene(document, -5).time_ms, 0)

    def test_opacity_animation_overrides_style_not_multiplies(self) -> None:
        """动画轨道覆盖 style.opacity，只有父层才相乘。"""
        document = DrawingIR.model_validate(simple_ir(
            nodes=[
                {
                    "id": "g1", "parentId": None, "type": "group",
                    "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
                    "style": {"opacity": 0.5},
                },
                {
                    "id": "c1", "parentId": "g1", "type": "circle",
                    "geometry": {"cx": 0, "cy": 0, "r": 5},
                    "style": {"fill": "#FF0000", "stroke": None,
                              "strokeWidth": 0, "opacity": 0.3},
                },
            ],
            durationMs=1000,
            animations=[
                {"nodeId": "c1", "property": "opacity", "from": 1, "to": 0,
                 "startMs": 0, "endMs": 1000, "easing": "linear"},
            ],
        ))
        # t=0 动画值 1（覆盖 style 的 0.3），乘父层 0.5 → 0.5，而不是 0.3×0.5
        self.assertAlmostEqual(sample_scene(document, 0).nodes[0].opacity, 0.5, places=9)
        self.assertAlmostEqual(sample_scene(document, 1000).nodes[0].opacity, 0.0, places=9)

    def test_draw_order_follows_array_not_depth(self) -> None:
        document = DrawingIR.model_validate(simple_ir(
            nodes=[
                {
                    "id": "c-first", "parentId": None, "type": "circle",
                    "geometry": {"cx": 1, "cy": 1, "r": 1},
                    "style": {"fill": "#FF0000", "stroke": None,
                              "strokeWidth": 0, "opacity": 1},
                },
                {
                    "id": "g-late", "parentId": None, "type": "group",
                    "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
                    "style": {"opacity": 1},
                },
                {
                    "id": "c-under", "parentId": "g-late", "type": "circle",
                    "geometry": {"cx": 2, "cy": 2, "r": 1},
                    "style": {"fill": "#0000FF", "stroke": None,
                              "strokeWidth": 0, "opacity": 1},
                },
            ],
        ))
        ids = [node.id for node in sample_scene(document).nodes]
        self.assertEqual(ids, ["c-first", "c-under"], "必须按数组顺序，不是深度优先")

    def test_group_is_not_emitted(self) -> None:
        document = DrawingIR.model_validate(simple_ir(
            nodes=[
                {
                    "id": "g1", "parentId": None, "type": "group",
                    "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
                    "style": {"opacity": 1},
                },
            ],
        ))
        self.assertEqual(sample_scene(document).nodes, ())

    def test_sample_times_static_and_animated(self) -> None:
        static = DrawingIR.model_validate(simple_ir(durationMs=0))
        self.assertEqual(sample_times(static), [0])
        animated = DrawingIR.model_validate(simple_ir(durationMs=4000))
        self.assertEqual(sample_times(animated), [0, 2000, 4000])


class SampleRejectionTests(unittest.TestCase):
    """不满足前提就拒绝，不画错图。"""

    def test_too_many_nodes(self) -> None:
        nodes = [
            {
                "id": f"c{i}", "parentId": None, "type": "circle",
                "geometry": {"cx": 0, "cy": 0, "r": 1},
                "style": {"fill": "#FF0000", "stroke": None, "strokeWidth": 0, "opacity": 1},
            }
            for i in range(201)
        ]
        # schema 本身就限制 200，构造不进去；用 model_construct 绕过校验，
        # 专门验证采样层自己的闸门也在
        document = DrawingIR.model_construct(
            schemaVersion="1.0", seed=1,
            canvas=DrawingIR.model_validate(simple_ir()).canvas,
            viewport=DrawingIR.model_validate(simple_ir()).viewport,
            durationMs=0, nodes=nodes, animations=[], narrative=[],
        )
        with self.assertRaises(IrSampleError):
            sample_scene(document)

    def test_viewport_out_of_range(self) -> None:
        document = DrawingIR.model_validate(simple_ir())
        document.viewport.width = 1200
        with self.assertRaises(IrSampleError):
            sample_scene(document)

    def test_parent_must_be_group(self) -> None:
        document = DrawingIR.model_validate(simple_ir(
            nodes=[
                {
                    "id": "c1", "parentId": None, "type": "circle",
                    "geometry": {"cx": 0, "cy": 0, "r": 1},
                    "style": {"fill": "#FF0000", "stroke": None,
                              "strokeWidth": 0, "opacity": 1},
                },
                {
                    "id": "c2", "parentId": "c1", "type": "circle",
                    "geometry": {"cx": 0, "cy": 0, "r": 1},
                    "style": {"fill": "#00FF00", "stroke": None,
                              "strokeWidth": 0, "opacity": 1},
                },
            ],
        ))
        with self.assertRaises(IrSampleError):
            sample_scene(document)

    def test_missing_parent(self) -> None:
        document = DrawingIR.model_validate(simple_ir(
            nodes=[
                {
                    "id": "c2", "parentId": "ghost", "type": "circle",
                    "geometry": {"cx": 0, "cy": 0, "r": 1},
                    "style": {"fill": "#00FF00", "stroke": None,
                              "strokeWidth": 0, "opacity": 1},
                },
            ],
        ))
        with self.assertRaises(IrSampleError):
            sample_scene(document)

    def test_parent_chain_too_deep(self) -> None:
        """边界在 4 层：TS 的判据是「进入下一个节点时 visiting.size >= 4」，
        所以 4 层 group 合法、第 5 层才抛。这里同时钉住两侧边界，避免以后
        有人「顺手改成 > 4」把合法嵌套拒掉。
        """
        def chain(depth: int) -> list[dict]:
            return [
                {
                    "id": f"g{index}",
                    "parentId": None if index == 1 else f"g{index - 1}",
                    "type": "group",
                    "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
                    "style": {"opacity": 1},
                }
                for index in range(1, depth + 1)
            ]

        # 4 层是合法上限
        sample_scene(DrawingIR.model_validate(simple_ir(nodes=chain(4))))

        # 第 5 层必须拒绝。
        # **必须倒序（子先于父）才会触发**：父先于子时，第一个节点无父、
        # 立刻算出并进缓存，后面的子节点只递归一层，`visiting` 涨不到 4。
        # 这个不对称是 TS 侧同样的行为（同样的缓存 + 同样的判据），不是这里
        # 的实现偏差；正常流程也碰不到——`validate_ir` 已经先挡了 4 层，
        # 采样层这条只是兜底。
        with self.assertRaises(IrSampleError):
            sample_scene(DrawingIR.model_validate(simple_ir(nodes=list(reversed(chain(5))))))

    def test_non_flat_material_rejected(self) -> None:
        """schema 现在只有 flat，这里验证采样层也认这条，不靠上游兜底。"""
        document = DrawingIR.model_validate(simple_ir())
        object.__setattr__(
            document.nodes[0], "material", type("M", (), {"shaderRef": "noise"})()
        )
        with self.assertRaises(IrSampleError):
            sample_scene(document)
