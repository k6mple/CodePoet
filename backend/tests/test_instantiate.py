"""参数实例化的单元测试。

覆盖三类行为：参数取值（类型/范围/枚举/颜色）、参数间约束、
模板自身错误。每条都对着契约里的规则写，不是凭感觉造的用例。
"""

import unittest

from pydantic import TypeAdapter

from app.engine.instantiate import (
    TemplateDefinitionError,
    instantiate,
)
from app.schemas.generation import (
    Binding,
    Constraint,
    IntegerParameter,
    ParameterDefinition,
)
from app.schemas.ir import DrawingIR

PARAM_ADAPTER = TypeAdapter(ParameterDefinition)
CONSTRAINT_ADAPTER = TypeAdapter(Constraint)


def make_template_ir() -> DrawingIR:
    """一份最小可实例化模板：一个花瓣圆 + 一片叶子多边形。"""
    return DrawingIR.model_validate(
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
                },
                {
                    "id": "leaf",
                    "parentId": None,
                    "type": "polygon",
                    "geometry": {"points": [[400, 400], [460, 460], [340, 460]]},
                    "style": {
                        "fill": "#8FBF7F",
                        "stroke": None,
                        "strokeWidth": 1,
                        "opacity": 1,
                    },
                },
            ],
            "animations": [],
            "narrative": [],
        }
    )


def int_param(name: str, *, low: int, high: int, default: int, label: str = "") -> ParameterDefinition:
    return PARAM_ADAPTER.validate_python(
        {
            "name": name,
            "label": label or name,
            "description": "",
            "type": "integer",
            "default": default,
            "min": low,
            "max": high,
            "step": 1,
        }
    )


def color_param(name: str, default: str, label: str = "") -> ParameterDefinition:
    return PARAM_ADAPTER.validate_python(
        {
            "name": name,
            "label": label or name,
            "description": "",
            "type": "color",
            "default": default,
        }
    )


def base_schema() -> list[ParameterDefinition]:
    return [
        int_param("petalRadius", low=10, high=200, default=60, label="花瓣半径"),
        color_param("petalColor", "#E8A0A0", label="花瓣颜色"),
    ]


def base_bindings() -> list[Binding]:
    return [
        Binding(parameter="petalRadius", nodeId="petal", property="geometry.r"),
        Binding(parameter="petalColor", nodeId="petal", property="style.fill"),
    ]


class InstantiateSuccessTest(unittest.TestCase):
    """正常路径：参数正确写入，未提供的用默认值。"""

    def test_writes_provided_values(self):
        outcome = instantiate(
            make_template_ir(),
            base_schema(),
            base_bindings(),
            [],
            {"petalRadius": 120, "petalColor": "#3366CC"},
        )
        self.assertTrue(outcome.ok, outcome.issues)
        petal = outcome.ir.nodes[0]
        self.assertAlmostEqual(petal.geometry.r, 120)
        self.assertEqual(petal.style.fill, "#3366CC")

    def test_fills_defaults_when_not_provided(self):
        outcome = instantiate(make_template_ir(), base_schema(), base_bindings(), [], {})
        self.assertTrue(outcome.ok, outcome.issues)
        self.assertAlmostEqual(outcome.ir.nodes[0].geometry.r, 60)
        self.assertEqual(outcome.ir.nodes[0].style.fill, "#E8A0A0")
        self.assertEqual(outcome.params, {"petalRadius": 60, "petalColor": "#E8A0A0"})

    def test_float_integer_is_accepted(self):
        """JSON 里 8.0 很常见，不该因为不是 int 就拒绝。"""
        outcome = instantiate(
            make_template_ir(), base_schema(), base_bindings(), [], {"petalRadius": 80.0}
        )
        self.assertTrue(outcome.ok, outcome.issues)
        self.assertAlmostEqual(outcome.ir.nodes[0].geometry.r, 80)

    def test_partial_bindings_leave_other_properties(self):
        """只绑半径时，颜色必须保持模板原值，不能被默认值覆盖。"""
        schema = [int_param("petalRadius", low=10, high=200, default=60)]
        bindings = [Binding(parameter="petalRadius", nodeId="petal", property="geometry.r")]
        outcome = instantiate(make_template_ir(), schema, bindings, [], {"petalRadius": 50})
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.ir.nodes[0].style.fill, "#E8A0A0")


class ParameterValidationTest(unittest.TestCase):
    """参数本身的取值校验。"""

    def _issues(self, provided):
        outcome = instantiate(make_template_ir(), base_schema(), base_bindings(), [], provided)
        return outcome, [i.path for i in outcome.issues]

    def test_out_of_range_rejected(self):
        outcome, paths = self._issues({"petalRadius": 500})
        self.assertFalse(outcome.ok)
        self.assertIn("params.petalRadius", paths)
        self.assertIn("10", outcome.issues[0].message)
        self.assertIn("200", outcome.issues[0].message)

    def test_non_integer_rejected(self):
        outcome, paths = self._issues({"petalRadius": 60.5})
        self.assertFalse(outcome.ok)
        self.assertIn("params.petalRadius", paths)

    def test_boolean_rejected_for_integer(self):
        """True 是 int 的子类，必须显式拦住，不能当成 1。"""
        outcome, paths = self._issues({"petalRadius": True})
        self.assertFalse(outcome.ok)
        self.assertIn("params.petalRadius", paths)

    def test_unknown_parameter_rejected(self):
        outcome, paths = self._issues({"noSuchParam": 1})
        self.assertFalse(outcome.ok)
        self.assertIn("params.noSuchParam", paths)

    def test_bad_color_rejected(self):
        outcome, paths = self._issues({"petalColor": "#FFF"})
        self.assertFalse(outcome.ok)
        self.assertIn("params.petalColor", paths)

    def test_enum_outside_options_rejected(self):
        schema = [
            PARAM_ADAPTER.validate_python(
                {
                    "name": "mood",
                    "label": "气质",
                    "description": "",
                    "type": "enum",
                    "default": "清雅",
                    "options": ["清雅", "苍劲"],
                }
            )
        ]
        outcome = instantiate(make_template_ir(), schema, [], [], {"mood": "激昂"})
        self.assertFalse(outcome.ok)
        self.assertIn("清雅", outcome.issues[0].message)

    def test_no_ir_produced_when_invalid(self):
        """有参数问题时不能返回带脏值的 IR，只能原样返回模板。"""
        outcome = instantiate(
            make_template_ir(), base_schema(), base_bindings(), [], {"petalRadius": 999}
        )
        self.assertFalse(outcome.ok)
        # 模板原值是 60，未被改成 999
        self.assertAlmostEqual(outcome.ir.nodes[0].geometry.r, 60)


class ConstraintTest(unittest.TestCase):
    """参数间约束。"""

    def test_less_than_violation(self):
        schema = [
            int_param("innerR", low=1, high=100, default=10),
            int_param("outerR", low=1, high=100, default=50),
        ]
        rule = CONSTRAINT_ADAPTER.validate_python(
            {"type": "lessThan", "left": "innerR", "right": "outerR"}
        )
        outcome = instantiate(
            make_template_ir(), schema, [], [rule], {"innerR": 80, "outerR": 50}
        )
        self.assertFalse(outcome.ok)
        self.assertIn("params.innerR", [i.path for i in outcome.issues])

    def test_less_than_satisfied(self):
        schema = [
            int_param("innerR", low=1, high=100, default=10),
            int_param("outerR", low=1, high=100, default=50),
        ]
        rule = CONSTRAINT_ADAPTER.validate_python(
            {"type": "lessThan", "left": "innerR", "right": "outerR"}
        )
        outcome = instantiate(
            make_template_ir(), schema, [], [rule], {"innerR": 20, "outerR": 50}
        )
        self.assertTrue(outcome.ok, outcome.issues)

    def test_mutually_exclusive_both_set(self):
        schema = [
            int_param("a", low=1, high=10, default=1),
            int_param("b", low=1, high=10, default=1),
        ]
        rule = CONSTRAINT_ADAPTER.validate_python(
            {"type": "mutuallyExclusive", "parameters": ["a", "b"]}
        )
        outcome = instantiate(make_template_ir(), schema, [], [rule], {"a": 5, "b": 6})
        self.assertFalse(outcome.ok)
        self.assertIn("互斥", outcome.issues[0].message)

    def test_mutually_exclusive_defaults_do_not_conflict(self):
        """只指定一个时，另一个的默认值不算「显式指定」。"""
        schema = [
            int_param("a", low=1, high=10, default=1),
            int_param("b", low=1, high=10, default=1),
        ]
        rule = CONSTRAINT_ADAPTER.validate_python(
            {"type": "mutuallyExclusive", "parameters": ["a", "b"]}
        )
        outcome = instantiate(make_template_ir(), schema, [], [rule], {"a": 5})
        self.assertTrue(outcome.ok, outcome.issues)


class TemplateIntegrityTest(unittest.TestCase):
    """模板写坏了要抛专门异常，不能伪装成参数问题。"""

    def test_binding_to_missing_node(self):
        bindings = [Binding(parameter="petalRadius", nodeId="ghost", property="geometry.r")]
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(
                make_template_ir(), base_schema(), bindings, [], {"petalRadius": 50}
            )
        self.assertIn("ghost", str(ctx.exception))

    def test_binding_to_undefined_parameter(self):
        bindings = [Binding(parameter="petalRadius", nodeId="petal", property="geometry.r")]
        schema = [color_param("petalColor", "#E8A0A0")]
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(make_template_ir(), schema, bindings, [], {})
        self.assertIn("petalRadius", str(ctx.exception))

    def test_binding_fill_on_group_node(self):
        """GroupStyle 没有 fill，绑上去必须报模板定义错误而不是 500 traceback。"""
        ir = make_template_ir()
        data = ir.model_dump(mode="python")
        data["nodes"].append(
            {
                "id": "grp",
                "parentId": None,
                "type": "group",
                "style": {"opacity": 1},
            }
        )
        ir_with_group = DrawingIR.model_validate(data)
        bindings = [Binding(parameter="petalColor", nodeId="grp", property="style.fill")]
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(ir_with_group, base_schema(), bindings, [], {})
        self.assertIn("style.fill", str(ctx.exception))

    def test_binding_circle_property_on_polygon(self):
        bindings = [Binding(parameter="petalRadius", nodeId="leaf", property="geometry.r")]
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(make_template_ir(), base_schema(), bindings, [], {})
        self.assertIn("geometry.r", str(ctx.exception))

    def test_binding_value_breaks_target_field(self):
        """参数范围 10~200 绑到 opacity（0~1），写入后必然不合法。"""
        schema = [int_param("radius", low=10, high=200, default=60)]
        bindings = [Binding(parameter="radius", nodeId="petal", property="style.opacity")]
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(make_template_ir(), schema, bindings, [], {"radius": 100})
        self.assertIn("opacity", str(ctx.exception))

    def test_constraint_referencing_unknown_parameter(self):
        rule = CONSTRAINT_ADAPTER.validate_python(
            {"type": "lessThan", "left": "petalRadius", "right": "nope"}
        )
        with self.assertRaises(TemplateDefinitionError) as ctx:
            instantiate(make_template_ir(), base_schema(), [], [rule], {})
        self.assertIn("nope", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
