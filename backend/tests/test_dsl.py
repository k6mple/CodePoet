"""DSL v1 解释器：追加语义、审计拒绝、预算熔断。

测试分四组，对应模块 docstring 里写下的四条边界：

* ``AuditTests``      —— 白名单与数值校验（不执行任何东西就拒绝）；
* ``AppendTests``     —— 追加语义：base 原样保留、新图元挂在末尾 root 下；
* ``BudgetTests``     —— 步数/调用/节点/顶点上限，超限整次拒绝；
* ``SetParamTests``   —— set_param 只在有模板上下文时可用，否则 DSL_INVALID_BASE。

一条刻意的取舍：这些用例**不连数据库、不连网络**。``engine/dsl.py`` 是纯函数，
它的正确性不该依赖服务层——那部分留给 S1-A2 的集成测试。
"""

from __future__ import annotations

import json
import unittest

from app.engine import dsl
from app.schemas.ir import DrawingIR


def base_ir(nodes: list[dict] | None = None, *, duration: int = 0) -> DrawingIR:
    """一份最小的合法 IR。默认只有一个圆。"""
    return DrawingIR.model_validate(
        {
            "schemaVersion": "1.0",
            "seed": 7,
            "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
            "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
            "durationMs": duration,
            "nodes": nodes
            or [
                {
                    "id": "base-circle",
                    "parentId": None,
                    "type": "circle",
                    "geometry": {"cx": 400, "cy": 300, "r": 60},
                    "style": {
                        "fill": "#E8A0A0",
                        "stroke": None,
                        "strokeWidth": 0,
                        "opacity": 1,
                    },
                }
            ],
            "animations": [],
            "narrative": [],
        }
    )


def source(*ops: dict, version: int = 1) -> str:
    return json.dumps({"version": version, "ops": list(ops)}, ensure_ascii=False)


class AuditTests(unittest.TestCase):
    """静态审计：不合规的源码必须在执行之前被拒绝。"""

    def assertRejected(self, text: str, *, code: str = "DSL_AUDIT_REJECTED") -> dsl.DslError:
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.compile_program(text)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception.details))
        return ctx.exception

    def test_empty_source(self) -> None:
        self.assertRejected("")

    def test_not_json(self) -> None:
        error = self.assertRejected("{not json")
        self.assertIn("JSON", error.message)

    def test_top_level_must_be_object(self) -> None:
        self.assertRejected("[]")

    def test_unknown_top_level_field(self) -> None:
        # 夹带最容易被忽略的入口：顶层多一个字段
        self.assertRejected(json.dumps({"version": 1, "ops": [], "extra": 1}))

    def test_version_required(self) -> None:
        self.assertRejected(json.dumps({"ops": []}))
        self.assertRejected(json.dumps({"version": 2, "ops": [{"op": "circle"}]}))

    def test_unknown_op(self) -> None:
        error = self.assertRejected(source({"op": "rect", "cx": 1}))
        self.assertIn("rect", error.message)
        self.assertIn("circle", error.details["supported"])

    def test_unknown_field_is_rejected(self) -> None:
        """多余字段整份拒绝，不做「忽略未知字段」的宽容。"""
        error = self.assertRejected(
            source({"op": "circle", "cx": 400, "cy": 300, "r": 10, "onclick": "alert(1)"})
        )
        self.assertEqual(error.details["unknownFields"], ["onclick"])

    def test_prototype_pollution_style_key(self) -> None:
        self.assertRejected(source({"op": "circle", "cx": 1, "cy": 1, "r": 1, "__proto__": {}}))

    def test_numeric_range(self) -> None:
        self.assertRejected(source({"op": "circle", "cx": 99999, "cy": 0, "r": 10}))
        self.assertRejected(source({"op": "circle", "cx": 0, "cy": 0, "r": 0}))  # r 最小 0.1
        self.assertRejected(source({"op": "circle", "cx": 0, "cy": 0, "r": 10, "opacity": 1.5}))

    def test_non_finite_numbers(self) -> None:
        # 双重保险：直接构造字符串（json.loads 默认接受 Infinity）
        self.assertRejected('{"version":1,"ops":[{"op":"circle","cx":Infinity,"cy":0,"r":1}]}')
        self.assertRejected('{"version":1,"ops":[{"op":"circle","cx":NaN,"cy":0,"r":1}]}')

    def test_boolean_is_not_a_number(self) -> None:
        """Python 里 bool 是 int 的子类，很容易被 `isinstance(x, (int, float))` 放过去。"""
        self.assertRejected(source({"op": "circle", "cx": True, "cy": 0, "r": 10}))

    def test_color_format(self) -> None:
        self.assertRejected(source({"op": "circle", "cx": 0, "cy": 0, "r": 10, "fill": "red"}))
        self.assertRejected(source({"op": "circle", "cx": 0, "cy": 0, "r": 10, "stroke": "#FFF"}))

    def test_polygon_needs_three_points(self) -> None:
        # 审计阶段只卡 2~512，3 点的要求留给构造时——但这里要确认 2 点也进不来
        program = dsl.compile_program(
            source({"op": "polygon", "points": [[0, 0], [10, 0]], "fill": "#112233"})
        )
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, base_ir(), run_id="run-abc")
        self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED")

    def test_points_must_be_pairs(self) -> None:
        self.assertRejected(source({"op": "polyline", "points": [[0, 0, 0], [1, 1]]}))
        self.assertRejected(source({"op": "polyline", "points": [["a", "b"]]}))

    def test_set_param_after_draw_is_rejected(self) -> None:
        """set_param 必须在绘图之前：换顺序就是另一张图，不能含糊。"""
        error = self.assertRejected(
            source(
                {"op": "circle", "cx": 0, "cy": 0, "r": 5},
                {"op": "set_param", "name": "radius", "value": 10},
            )
        )
        self.assertEqual(error.details["index"], 1)


class AppendTests(unittest.TestCase):
    """追加语义：base 不动，新图元挂在末尾一个确定性 root 下。"""

    def test_appends_nodes_and_root_group(self) -> None:
        program = dsl.compile_program(
            source(
                {"op": "circle", "cx": 200, "cy": 200, "r": 30, "fill": "#D4537E"},
                {"op": "polygon", "points": [[0, 0], [100, 0], [50, 80]], "fill": "#EF9F27"},
            )
        )
        original = base_ir()
        outcome = dsl.run(program, original, run_id="run-abc")

        # base 的节点原样在前
        self.assertEqual(outcome.ir.nodes[0].id, "base-circle")
        # 新节点 + 一个 root group
        self.assertEqual(len(outcome.ir.nodes), 1 + 2 + 1)
        root = outcome.ir.nodes[-1]
        self.assertEqual(root.type, "group")
        self.assertEqual(root.id, "g-run-abc")
        self.assertIsNone(root.parentId)

        appended = [n for n in outcome.ir.nodes if n.parentId == "g-run-abc"]
        self.assertEqual(len(appended), 2)
        self.assertEqual([n.type for n in appended], ["circle", "polygon"])
        self.assertEqual(outcome.appended_node_ids, [n.id for n in appended])

    def test_base_is_not_mutated(self) -> None:
        program = dsl.compile_program(source({"op": "circle", "cx": 1, "cy": 1, "r": 5}))
        original = base_ir()
        before = original.model_dump(mode="json")
        dsl.run(program, original, run_id="run-abc")
        self.assertEqual(original.model_dump(mode="json"), before, "base 被就地改了")

    def test_node_ids_are_deterministic(self) -> None:
        text = source({"op": "circle", "cx": 1, "cy": 1, "r": 5})
        first = dsl.run(dsl.compile_program(text), base_ir(), run_id="run-abc")
        second = dsl.run(dsl.compile_program(text), base_ir(), run_id="run-abc")
        self.assertEqual(first.appended_node_ids, second.appended_node_ids)
        self.assertEqual(
            first.ir.model_dump(mode="json"), second.ir.model_dump(mode="json")
        )

    def test_different_runs_get_different_ids(self) -> None:
        text = source({"op": "circle", "cx": 1, "cy": 1, "r": 5})
        a = dsl.run(dsl.compile_program(text), base_ir(), run_id="run-aaa")
        b = dsl.run(dsl.compile_program(text), base_ir(), run_id="run-bbb")
        self.assertNotEqual(a.appended_node_ids, b.appended_node_ids)

    def test_preserves_base_animations_and_metadata(self) -> None:
        """动画、viewport、seed、duration 都要原样保留——DSL 只追加图元。"""
        original = base_ir(duration=4000)
        original = DrawingIR.model_validate(
            {
                **original.model_dump(mode="json"),
                "animations": [
                    {
                        "nodeId": "base-circle",
                        "property": "x",
                        "from": 0,
                        "to": 100,
                        "startMs": 0,
                        "endMs": 4000,
                        "easing": "linear",
                    }
                ],
                "narrative": [
                    {"timeMs": 0, "entityIds": [], "caption": "原来的说明"}
                ],
            }
        )
        outcome = dsl.run(
            dsl.compile_program(source({"op": "circle", "cx": 1, "cy": 1, "r": 5})),
            original,
            run_id="run-abc",
        )
        self.assertEqual(len(outcome.ir.animations), 1)
        self.assertEqual(outcome.ir.narrative[0].caption, "原来的说明")
        self.assertEqual(outcome.ir.durationMs, 4000)
        self.assertEqual(outcome.ir.seed, original.seed)

    def test_empty_ops_rejected(self) -> None:
        with self.assertRaises(dsl.DslError):
            dsl.compile_program(json.dumps({"version": 1, "ops": []}))

    def test_metrics_shape(self) -> None:
        outcome = dsl.run(
            dsl.compile_program(source({"op": "circle", "cx": 1, "cy": 1, "r": 5})),
            base_ir(),
            run_id="run-abc",
        )
        self.assertEqual(set(outcome.metrics), {"wallTimeMs", "cpuTimeMs", "drawCalls", "steps"})
        self.assertEqual(outcome.metrics["drawCalls"], 1)
        self.assertGreaterEqual(outcome.metrics["steps"], 1)


class BudgetTests(unittest.TestCase):
    """预算熔断：超限整次拒绝，不产出半成品。"""

    def test_call_limit(self) -> None:
        ops = [{"op": "circle", "cx": i, "cy": 0, "r": 1} for i in range(5)]
        program = dsl.compile_program(source(*ops))
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(
                program,
                base_ir(),
                run_id="run-abc",
                limits=dsl.DslLimits(max_draw_calls=3),
            )
        self.assertEqual(ctx.exception.code, "DSL_CALL_LIMIT")
        self.assertEqual(ctx.exception.details["drawCalls"], 4)

    def test_step_limit(self) -> None:
        # 每个点算一步，polyline 的 points 会推高步数
        points = [[i, i] for i in range(50)]
        program = dsl.compile_program(source({"op": "polyline", "points": points}))
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(
                program,
                base_ir(),
                run_id="run-abc",
                limits=dsl.DslLimits(max_steps=20),
            )
        self.assertEqual(ctx.exception.code, "DSL_STEP_LIMIT")

    def test_ast_node_limit(self) -> None:
        ops = [{"op": "circle", "cx": i, "cy": 0, "r": 1} for i in range(10)]
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.compile_program(source(*ops), dsl.DslLimits(max_ast_nodes=5))
        self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED")
        self.assertEqual(ctx.exception.details["max"], 5)

    def test_total_node_limit_includes_base(self) -> None:
        """200 是**全图**上限，不是增量上限：base 占用的名额也要算。"""
        base_nodes = [
            {
                "id": f"base-{i}",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": i, "cy": 0, "r": 1},
                "style": {"fill": "#112233", "stroke": None, "strokeWidth": 0, "opacity": 1},
            }
            for i in range(195)
        ]
        original = base_ir(base_nodes)
        ops = [{"op": "circle", "cx": i, "cy": 0, "r": 1} for i in range(10)]
        program = dsl.compile_program(source(*ops))
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, original, run_id="run-abc")
        self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED")
        self.assertIn("节点总数", ctx.exception.message)

    def test_total_vertex_limit_includes_base(self) -> None:
        base_nodes = [
            {
                "id": f"base-{i}",
                "parentId": None,
                "type": "polygon",
                "geometry": {"points": [[j, j] for j in range(400)]},
                "style": {"fill": "#112233", "stroke": None, "strokeWidth": 0, "opacity": 1},
            }
            for i in range(5)
        ]
        original = base_ir(base_nodes)  # 2000 顶点，已经顶满
        program = dsl.compile_program(
            source({"op": "polygon", "points": [[0, 0], [1, 0], [1, 1]]})
        )
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, original, run_id="run-abc")
        self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED")
        self.assertIn("顶点总数", ctx.exception.message)

    def test_root_id_collision_rejected(self) -> None:
        """run id 生成的 root 与 base 节点撞名时必须拒绝，不能覆盖。"""
        nodes = [
            {
                "id": "g-run-abc",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": 0, "cy": 0, "r": 1},
                "style": {"fill": "#112233", "stroke": None, "strokeWidth": 0, "opacity": 1},
            }
        ]
        program = dsl.compile_program(source({"op": "circle", "cx": 1, "cy": 1, "r": 1}))
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, base_ir(nodes), run_id="run-abc")
        self.assertIn("冲突", ctx.exception.message)


class SetParamTests(unittest.TestCase):
    """set_param 只有在模板上下文齐备时才允许。"""

    def test_without_context_is_invalid_base(self) -> None:
        program = dsl.compile_program(
            source(
                {"op": "set_param", "name": "radius", "value": 80},
                {"op": "circle", "cx": 400, "cy": 300, "r": 10},
            )
        )
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, base_ir(), run_id="run-abc")
        self.assertEqual(ctx.exception.code, "DSL_INVALID_BASE")
        self.assertEqual(ctx.exception.details["reason"], "no-template-context")


if __name__ == "__main__":
    unittest.main()
