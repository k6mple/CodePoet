"""IR 跨字段校验的行为测试。

每条规则都构造一份「只有这一处不合法」的 IR，确认它能被单独抓出来。
这样以后改动校验逻辑时，能立刻知道破坏了哪条规则。
"""

import math
import unittest

from app.engine.validate import validate_ir
from app.schemas.ir import DrawingIR


def base_ir(nodes=None, animations=None, narrative=None):
    """构造一份最小合法 IR，各用例只替换自己关心的部分。"""
    return {
        "schemaVersion": "1.0",
        "seed": 7,
        "canvas": {"width": 800, "height": 600, "background": "#FFFDF7"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": nodes
        if nodes is not None
        else [
            {
                "id": "petal-1",
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
        "animations": animations or [],
        "narrative": narrative or [],
    }


def ngon(cx, cy, radius, count):
    """生成逆时针正多边形，用于顶点数测试。"""
    return [
        [
            cx + radius * math.cos(2 * math.pi * index / count),
            cy + radius * math.sin(2 * math.pi * index / count),
        ]
        for index in range(count)
    ]


def codes(report):
    return {issue.code for issue in report.issues}


class IrValidationTests(unittest.TestCase):
    def test_valid_ir_has_no_issues(self):
        report = validate_ir(DrawingIR.model_validate(base_ir()))
        self.assertEqual(report.issues, [])
        self.assertEqual(report.nodeCount, 1)
        self.assertEqual(report.outOfBoundsNodeCount, 0)
        self.assertTrue(report.parameterValid)

    def test_duplicate_node_id(self):
        node = base_ir()["nodes"][0]
        report = validate_ir(
            DrawingIR.model_validate(base_ir(nodes=[node, dict(node)]))
        )
        self.assertIn("IR_DUPLICATE_NODE_ID", codes(report))

    def test_missing_parent(self):
        node = dict(base_ir()["nodes"][0], parentId="ghost")
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[node])))
        self.assertIn("IR_MISSING_PARENT", codes(report))

    def test_parent_cycle(self):
        first = dict(base_ir()["nodes"][0], id="a", parentId="b")
        second = dict(base_ir()["nodes"][0], id="b", parentId="a")
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[first, second])))
        self.assertIn("IR_PARENT_CYCLE", codes(report))

    def test_too_deep(self):
        # 5 层（超过 4 层上限）：g1 -> g2 -> g3 -> g4 -> leaf
        nodes = [
            {"id": "g1", "parentId": None, "type": "group", "style": {"opacity": 1}},
            {"id": "g2", "parentId": "g1", "type": "group", "style": {"opacity": 1}},
            {"id": "g3", "parentId": "g2", "type": "group", "style": {"opacity": 1}},
            {"id": "g4", "parentId": "g3", "type": "group", "style": {"opacity": 1}},
            {
                "id": "leaf",
                "parentId": "g4",
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 20},
                "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
            },
        ]
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=nodes)))
        self.assertIn("IR_NODE_TOO_DEEP", codes(report))

    def test_four_levels_is_allowed(self):
        # 正好 4 层，不应该报深度错误
        nodes = [
            {"id": "g1", "parentId": None, "type": "group", "style": {"opacity": 1}},
            {"id": "g2", "parentId": "g1", "type": "group", "style": {"opacity": 1}},
            {"id": "g3", "parentId": "g2", "type": "group", "style": {"opacity": 1}},
            {
                "id": "leaf",
                "parentId": "g3",
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 20},
                "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
            },
        ]
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=nodes)))
        self.assertNotIn("IR_NODE_TOO_DEEP", codes(report))

    def test_too_many_vertices(self):
        # 200 个 11 边形 = 2200 个顶点，超过 2000
        nodes = [
            {
                "id": f"p{index}",
                "parentId": None,
                "type": "polygon",
                "geometry": {"points": ngon(400, 300, 40, 11)},
                "style": {"fill": "#88AA88", "stroke": None, "strokeWidth": 0, "opacity": 1},
            }
            for index in range(200)
        ]
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=nodes)))
        self.assertIn("IR_TOO_MANY_VERTICES", codes(report))

    def test_degenerate_polygon(self):
        node = {
            "id": "flat",
            "parentId": None,
            "type": "polygon",
            "geometry": {"points": [[100, 100], [200, 200], [300, 300]]},
            "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
        }
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[node])))
        self.assertIn("IR_POLYGON_DEGENERATE", codes(report))

    def test_self_intersecting_polygon(self):
        # 蝴蝶结形状：两条对角线交叉
        node = {
            "id": "bowtie",
            "parentId": None,
            "type": "polygon",
            "geometry": {"points": [[100, 100], [300, 300], [300, 100], [100, 300]]},
            "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
        }
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[node])))
        self.assertIn("IR_POLYGON_SELF_INTERSECT", codes(report))

    def test_convex_polygon_passes_geometry(self):
        node = {
            "id": "hex",
            "parentId": None,
            "type": "polygon",
            "geometry": {"points": ngon(400, 300, 80, 6)},
            "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
        }
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[node])))
        self.assertNotIn("IR_POLYGON_SELF_INTERSECT", codes(report))
        self.assertNotIn("IR_POLYGON_DEGENERATE", codes(report))

    def test_animation_with_missing_node(self):
        animation = {
            "nodeId": "nobody",
            "property": "opacity",
            "from": 0,
            "to": 1,
            "startMs": 0,
            "endMs": 1000,
            "easing": "linear",
        }
        report = validate_ir(DrawingIR.model_validate(base_ir(animations=[animation])))
        self.assertIn("IR_ANIMATION_NODE_MISSING", codes(report))

    def test_animation_bad_time_range(self):
        animation = {
            "nodeId": "petal-1",
            "property": "opacity",
            "from": 0,
            "to": 1,
            "startMs": 2000,
            "endMs": 1000,
            "easing": "linear",
        }
        report = validate_ir(DrawingIR.model_validate(base_ir(animations=[animation])))
        self.assertIn("IR_ANIMATION_TIME_RANGE", codes(report))

    def test_out_of_bounds_detected(self):
        # 圆心在 x=780，半径 60，右边缘到 840，超出 800
        node = dict(
            base_ir()["nodes"][0],
            geometry={"cx": 780, "cy": 300, "r": 60},
        )
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=[node])))
        self.assertEqual(report.outOfBoundsNodeCount, 1)
        self.assertIn("IR_OUT_OF_BOUNDS", codes(report))

    def test_parent_transform_accumulates(self):
        # 父分组整体右移 700，子圆局部 x=200，实际落在 900 → 越界
        nodes = [
            {
                "id": "shifted",
                "parentId": None,
                "type": "group",
                "transform": {"x": 700, "y": 0},
                "style": {"opacity": 1},
            },
            {
                "id": "child",
                "parentId": "shifted",
                "type": "circle",
                "transform": {"x": 200, "y": 300},
                "geometry": {"cx": 0, "cy": 0, "r": 50},
                "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
            },
        ]
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=nodes)))
        self.assertEqual(report.outOfBoundsNodeCount, 1)

    def test_parent_transform_within_canvas(self):
        # 父右移 100，子局部 x=300、半径 50 → 实际 400，完全在画布内
        nodes = [
            {
                "id": "shifted",
                "parentId": None,
                "type": "group",
                "transform": {"x": 100, "y": 0},
                "style": {"opacity": 1},
            },
            {
                "id": "child",
                "parentId": "shifted",
                "type": "circle",
                "transform": {"x": 300, "y": 300},
                "geometry": {"cx": 0, "cy": 0, "r": 50},
                "style": {"fill": "#000000", "stroke": None, "strokeWidth": 0, "opacity": 1},
            },
        ]
        report = validate_ir(DrawingIR.model_validate(base_ir(nodes=nodes)))
        self.assertEqual(report.outOfBoundsNodeCount, 0)


if __name__ == "__main__":
    unittest.main()
