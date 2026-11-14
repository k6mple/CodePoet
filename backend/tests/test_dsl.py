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

from pydantic import TypeAdapter

from app.engine import dsl
from app.schemas.generation import Binding, ParameterDefinition
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
        """polygon 的 3 点下限必须在 **compile** 阶段就拒。

        以前这里只卡 2~512，3 点要求拖到 run 阶段，与「所有不合规都在 compile
        阶段拒绝」的约定不符——同一类错误在不同阶段报出来，调用方要写两套处理。
        """
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.compile_program(
                source({"op": "polygon", "points": [[0, 0], [10, 0]], "fill": "#112233"})
            )
        self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED")
        self.assertEqual(ctx.exception.rule, dsl.RULE_NUMERIC_RANGE)
        self.assertIn("3~512", ctx.exception.message)

    def test_polyline_still_accepts_two_points(self) -> None:
        """下限按 op 区分：折线 2 点是合法的最小线段，不能跟着 polygon 一起卡 3。"""
        program = dsl.compile_program(
            source({"op": "polyline", "points": [[0, 0], [10, 0]], "stroke": "#112233"})
        )
        outcome = dsl.run(program, base_ir(), run_id="run-abc")
        self.assertEqual(outcome.draw_calls, 1)

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
    """set_param 只有在模板上下文齐备时才允许。

    这一组覆盖的是**参数真正生效**的路径，而不只是「没上下文就拒」。
    上一版只有后者，结果 ``issue.code`` 那处 AttributeError 从缺口里漏了出去——
    未知参数名、越界值、约束冲突都会走到同一行。
    """

    def context(self) -> dsl.ParamContext:
        """一份最小但真实的模板上下文：一个半径可调的花瓣圆。"""
        template = DrawingIR.model_validate(
            {
                "schemaVersion": "1.0",
                "seed": 42,
                "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
                "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
                "durationMs": 0,
                "nodes": [
                    {
                        "id": "petal",
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
        )
        schema = [
            TypeAdapter(ParameterDefinition).validate_python(
                {
                    "name": "petalRadius",
                    "label": "花瓣半径",
                    "description": "",
                    "type": "integer",
                    "default": 60,
                    "min": 10,
                    "max": 200,
                    "step": 1,
                }
            )
        ]
        bindings = [
            Binding(parameter="petalRadius", nodeId="petal", property="geometry.r")
        ]
        return dsl.ParamContext(
            template_ir=template,
            parameter_schema=schema,
            bindings=bindings,
            constraints=[],
            provided={},
        )

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
        self.assertEqual(ctx.exception.rule, dsl.RULE_SET_PARAM_BASE)

    def test_override_takes_effect(self) -> None:
        """成功路径：覆盖值必须真的改写绑定的那个属性。"""
        program = dsl.compile_program(
            source(
                {"op": "set_param", "name": "petalRadius", "value": 120},
                {"op": "circle", "cx": 100, "cy": 100, "r": 10},
            )
        )
        outcome = dsl.run(
            program, base_ir(), run_id="run-abc", param_context=self.context()
        )
        petal = next(n for n in outcome.ir.nodes if n.id == "petal")
        self.assertEqual(petal.geometry.r, 120)
        # 追加的图元也在
        self.assertEqual(outcome.draw_calls, 1)

    def test_unknown_param_is_invalid_base(self) -> None:
        """未知参数名：必须是 DSL_INVALID_BASE，不能是 AttributeError。

        这条就是评审指出的 P1。``ParameterIssue`` 只有 path / message，
        早期代码读了 ``issue.code``，于是这整条分支抛 AttributeError，
        被 worker 的通用 except 兜成 DSL_EXECUTION_FAILED——用户看到
        「沙箱内部错误」，而真实原因是「参数名写错了」。
        """
        program = dsl.compile_program(
            source({"op": "set_param", "name": "nope", "value": 1})
        )
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, base_ir(), run_id="run-abc", param_context=self.context())
        self.assertEqual(ctx.exception.code, "DSL_INVALID_BASE")
        self.assertEqual(ctx.exception.rule, dsl.RULE_SET_PARAM_BASE)
        issues = ctx.exception.details["issues"]
        self.assertTrue(issues, "必须给出参数级问题")
        # 只有 path / message 两个字段，没有 code
        self.assertEqual(set(issues[0]), {"path", "message"})
        self.assertIn("nope", json.dumps(issues, ensure_ascii=False))

    def test_out_of_range_value_is_invalid_base(self) -> None:
        """越界值走同一条分支，同样不能被吞成内部错误。"""
        program = dsl.compile_program(
            source({"op": "set_param", "name": "petalRadius", "value": 9999})
        )
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.run(program, base_ir(), run_id="run-abc", param_context=self.context())
        self.assertEqual(ctx.exception.code, "DSL_INVALID_BASE")
        self.assertEqual(set(ctx.exception.details["issues"][0]), {"path", "message"})

    def test_set_param_after_draw_is_rejected(self) -> None:
        """set_param 必须在绘图之前：换顺序就是另一张图，不能事后改。"""
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.compile_program(
                source(
                    {"op": "circle", "cx": 10, "cy": 10, "r": 5},
                    {"op": "set_param", "name": "petalRadius", "value": 80},
                )
            )
        self.assertEqual(ctx.exception.rule, dsl.RULE_SET_PARAM_BASE)


class RuleIdTests(unittest.TestCase):
    """每条拒绝都要能说出「是哪条规则拒的」，且与策略清单同一套名字。"""

    def test_rule_ids_match_policy_catalogue(self) -> None:
        from app.engine import dsl as engine_dsl
        from app.services import policy

        listed = {rule.ruleId for rule in policy.AUDIT_RULES}
        for name in (
            "RULE_DOCUMENT_SHAPE",
            "RULE_OP_WHITELIST",
            "RULE_FIELD_WHITELIST",
            "RULE_NUMERIC_RANGE",
            "RULE_NODE_ID_UNIQUE",
            "RULE_BASE_UNMODIFIED",
            "RULE_SET_PARAM_BASE",
            "RULE_BUDGET_LIMIT",
        ):
            self.assertIn(getattr(engine_dsl, name), listed, name)

    def test_rejections_carry_distinct_rules(self) -> None:
        """不同类型的问题要报不同的规则，而不是全都 DSL_AUDIT_REJECTED。"""
        cases = {
            "unknown op": (
                source({"op": "shell", "cmd": "ls"}),
                dsl.RULE_OP_WHITELIST,
            ),
            "unknown field": (
                source({"op": "circle", "cx": 1, "cy": 1, "r": 1, "class": "x"}),
                dsl.RULE_FIELD_WHITELIST,
            ),
            "bad radius": (
                source({"op": "circle", "cx": 1, "cy": 1, "r": 99999}),
                dsl.RULE_NUMERIC_RANGE,
            ),
            "bad document": ("[]", dsl.RULE_DOCUMENT_SHAPE),
        }
        for label, (text, expected) in cases.items():
            with self.assertRaises(dsl.DslError) as ctx:
                dsl.compile_program(text)
            self.assertEqual(ctx.exception.rule, expected, label)
            # code 仍然是契约里的那个，不受 rule 影响
            self.assertEqual(ctx.exception.code, "DSL_AUDIT_REJECTED", label)

    def test_error_location_points_at_the_op(self) -> None:
        """details.index 要能换算成指令序号：0 基下标 → 1 基行号。"""
        with self.assertRaises(dsl.DslError) as ctx:
            dsl.compile_program(
                source(
                    {"op": "circle", "cx": 10, "cy": 10, "r": 5},
                    {"op": "bogus"},
                )
            )
        self.assertEqual(ctx.exception.location, 1)


if __name__ == "__main__":
    unittest.main()
