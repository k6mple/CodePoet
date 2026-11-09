"""参数实例化：把「模板 IR + 参数值」变成「可渲染的完整 IR」。

这是纯计算模块，不依赖 HTTP、不碰数据库。输入输出都是 Pydantic 模型。
错误分两类，因为它们该给不同的 HTTP 状态码：

1. ``ParameterIssue`` —— 用户传的参数不对（越界、类型错、约束冲突）。
   服务层转成 422 VALIDATION_ERROR，带 fieldErrors 让前端定位到表单字段。
2. ``TemplateDefinitionError`` —— 模板自己写坏了（绑定了不存在的节点、
   把颜色参数绑到半径上）。这不是用户能修的，服务层记日志后返回 500。

几个刻意的设计决定，写在前面：

* **不做 step 整除校验。** 契约给了 step，但它的用途是前端滑条步长，
  不是数据约束。硬性要求「值必须是 step 的整数倍」会误拒合法输入，
  比如用户手输 8.5 给一个 step=1 的 number 参数。
* **绑定通过 dump → 改 dict → 重新 validate。** 直接 setattr 会绕过
  Pydantic 的字段校验，写出一个字段类型错误但能序列化的对象，
  问题会推迟到前端渲染才暴露。重新 validate 能在服务端立刻拦住。
* **integer 参数接受 8.0。** JSON 里没有整型概念，前端滑条很容易传成
  8.0。只接受 ``isinstance(v, int)`` 会误拒，所以额外放行「值等于整数」的
  float，但 bool 必须排除（Python 里 True 也是 int）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas.generation import (
    Binding,
    BooleanParameter,
    ColorParameter,
    Constraint,
    EnumParameter,
    IntegerParameter,
    LessThanConstraint,
    MutuallyExclusiveConstraint,
    NumberParameter,
    ParameterDefinition,
    ParameterValue,
)
from app.schemas.ir import DrawingIR, IRNode

COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"

# 每种节点实际拥有的可绑定属性。模板里写了不存在的组合就是模板的错，
# 必须在这里显式列出而不是靠 hasattr 兜底——hasattr 会把「GroupStyle
# 没有 fill」这种问题漏到 Pydantic 的 extra="forbid" 上，报错信息对
# 模板作者毫无指向性。
_TRANSFORM_PROPS = frozenset(
    {
        "transform.x",
        "transform.y",
        "transform.rotation",
        "transform.scaleX",
        "transform.scaleY",
    }
)
_STYLE_LEAF_PROPS = frozenset({"style.fill", "style.stroke", "style.strokeWidth"})
_STYLE_ANY_PROPS = frozenset({"style.opacity"})
_CIRCLE_PROPS = frozenset({"geometry.cx", "geometry.cy", "geometry.r"})

_COMPATIBLE: dict[str, frozenset[str]] = {
    "group": _TRANSFORM_PROPS | _STYLE_ANY_PROPS,
    "circle": _TRANSFORM_PROPS | _STYLE_ANY_PROPS | _STYLE_LEAF_PROPS | _CIRCLE_PROPS,
    "polygon": _TRANSFORM_PROPS | _STYLE_ANY_PROPS | _STYLE_LEAF_PROPS,
    "polyline": _TRANSFORM_PROPS | _STYLE_ANY_PROPS | _STYLE_LEAF_PROPS,
}


@dataclass(frozen=True)
class ParameterIssue:
    """一个参数级问题。path 直接对应前端表单字段，如 params.petalCount。"""

    path: str
    message: str


class TemplateDefinitionError(Exception):
    """模板自身定义有误，属服务端数据缺陷，不是客户端能修的问题。"""


@dataclass
class InstantiateOutcome:
    """实例化结果。issues 非空时 ir 仍是模板原样，不会带着脏参数返回。"""

    ir: DrawingIR
    params: dict[str, ParameterValue]
    issues: list[ParameterIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


# ---- 模板完整性 ----------------------------------------------------------


def check_template_integrity(
    ir_template: DrawingIR,
    parameter_schema: list[ParameterDefinition],
    bindings: list[Binding],
    constraints: list[Constraint],
) -> None:
    """校验模板自身是否自洽。不自洽就抛错，不要带着坏数据往下走。

    三类问题都会在这里拦住：参数名悬空、节点不存在、属性与节点类型不兼容。
    """
    names = {param.name for param in parameter_schema}
    nodes: dict[str, IRNode] = {node.id: node for node in ir_template.nodes}

    for index, binding in enumerate(bindings):
        if binding.parameter not in names:
            raise TemplateDefinitionError(
                f"bindings[{index}] 引用的参数「{binding.parameter}」未在 parameterSchema 中定义"
            )
        node = nodes.get(binding.nodeId)
        if node is None:
            raise TemplateDefinitionError(
                f"bindings[{index}] 引用的节点「{binding.nodeId}」不存在于 irTemplate"
            )
        allowed = _COMPATIBLE[node.type]
        if binding.property not in allowed:
            raise TemplateDefinitionError(
                f"bindings[{index}] 把参数「{binding.parameter}」绑定到"
                f"{node.type} 节点的「{binding.property}」，该属性在此类节点上不存在"
            )

    for index, rule in enumerate(constraints):
        if isinstance(rule, LessThanConstraint):
            for side in (rule.left, rule.right):
                if side not in names:
                    raise TemplateDefinitionError(
                        f"constraints[{index}] 引用的参数「{side}」未在 parameterSchema 中定义"
                    )
        elif isinstance(rule, MutuallyExclusiveConstraint):
            for name in rule.parameters:
                if name not in names:
                    raise TemplateDefinitionError(
                        f"constraints[{index}] 引用的参数「{name}」未在 parameterSchema 中定义"
                    )


# ---- 单个参数取值 --------------------------------------------------------


def _coerce_value(param: ParameterDefinition, value: Any) -> tuple[bool, Any, str]:
    """把用户给的值按参数定义转成规范值。返回 (是否合法, 规范值, 错误说明)。"""
    label = param.label

    if isinstance(param, BooleanParameter):
        if not isinstance(value, bool):
            return False, None, f"参数「{label}」必须是布尔值"
        return True, value, ""

    if isinstance(param, (IntegerParameter, NumberParameter)):
        # bool 是 int 的子类，必须显式排除，否则 True 会被当成 1
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, f"参数「{label}」必须是数字"
        if isinstance(param, IntegerParameter):
            if isinstance(value, float) and not value.is_integer():
                return False, None, f"参数「{label}」必须是整数"
            number: float | int = int(value)
        else:
            number = float(value)
        if number < param.min or number > param.max:
            return (
                False,
                None,
                f"参数「{label}」必须在 {param.min} 到 {param.max} 之间，当前为 {value}",
            )
        return True, number, ""

    if isinstance(param, ColorParameter):
        if not isinstance(value, str):
            return False, None, f"参数「{label}」必须是 #RRGGBB 形式的颜色"
        if not re.match(COLOR_PATTERN, value):
            return False, None, f"参数「{label}」必须是 #RRGGBB 形式，当前为 {value}"
        return True, value, ""

    if isinstance(param, EnumParameter):
        if not isinstance(value, str):
            return False, None, f"参数「{label}」必须是字符串选项"
        if value not in param.options:
            options = " / ".join(param.options)
            return False, None, f"参数「{label}」只能取 {options}，当前为 {value}"
        return True, value, ""

    return False, None, f"参数「{label}」的类型不受支持"


def _resolve_parameters(
    parameter_schema: list[ParameterDefinition],
    provided: dict[str, ParameterValue],
) -> tuple[dict[str, ParameterValue], list[ParameterIssue]]:
    """补齐默认值并校验。返回完整参数表与问题列表。"""
    issues: list[ParameterIssue] = []
    resolved: dict[str, ParameterValue] = {}
    known = {param.name for param in parameter_schema}

    for name in provided:
        if name not in known:
            issues.append(
                ParameterIssue(
                    path=f"params.{name}",
                    message=f"模板未定义参数「{name}」",
                )
            )

    for param in parameter_schema:
        if param.name in provided:
            ok, value, message = _coerce_value(param, provided[param.name])
            if not ok:
                issues.append(
                    ParameterIssue(path=f"params.{param.name}", message=message)
                )
                continue
            resolved[param.name] = value
        else:
            # 未提供就用默认值。默认值在模型层已校验过，这里直接采用。
            resolved[param.name] = param.default

    return resolved, issues


# ---- 参数间约束 ----------------------------------------------------------


def _check_constraints(
    constraints: list[Constraint],
    resolved: dict[str, ParameterValue],
    explicitly_set: set[str],
) -> list[ParameterIssue]:
    issues: list[ParameterIssue] = []

    for rule in constraints:
        if isinstance(rule, LessThanConstraint):
            left, right = resolved.get(rule.left), resolved.get(rule.right)
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                # 非数字参数之间比大小没有意义，模板定义本身有问题
                raise TemplateDefinitionError(
                    f"lessThan 约束（{rule.left} < {rule.right}）只能用于数字参数"
                )
            if not left < right:
                issues.append(
                    ParameterIssue(
                        path=f"params.{rule.left}",
                        message=f"参数「{rule.left}」必须小于「{rule.right}」",
                    )
                )
        elif isinstance(rule, MutuallyExclusiveConstraint):
            # 只看用户显式传的，补齐出来的默认值不算
            chosen = [name for name in rule.parameters if name in explicitly_set]
            if len(chosen) > 1:
                issues.append(
                    ParameterIssue(
                        path=f"params.{chosen[1]}",
                        message=f"参数 {'、'.join(rule.parameters)} 互斥，只能指定其中一个",
                    )
                )

    return issues


# ---- 写入绑定 ------------------------------------------------------------


def _apply_bindings(
    ir_template: DrawingIR,
    bindings: list[Binding],
    params: dict[str, ParameterValue],
) -> DrawingIR:
    """按绑定把参数值写进 IR，然后整份重新校验一次。"""
    data = ir_template.model_dump(mode="python")
    nodes_by_id = {node["id"]: node for node in data["nodes"]}

    for binding in bindings:
        node = nodes_by_id[binding.nodeId]
        section, _, attribute = binding.property.partition(".")
        node[section][attribute] = params[binding.parameter]

    try:
        return DrawingIR.model_validate(data)
    except ValidationError as exc:
        # 走到这里说明参数值写进属性后不合法，例如把 0~100 的整数绑到
        # opacity（只允许 0~1）。这是绑定设计的问题，报成参数问题更好定位。
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise TemplateDefinitionError(
            f"绑定写入后 IR 不合法（{location}）：{first.get('msg', '')}"
        ) from exc


# ---- 入口 ----------------------------------------------------------------


def instantiate(
    ir_template: DrawingIR,
    parameter_schema: list[ParameterDefinition],
    bindings: list[Binding],
    constraints: list[Constraint],
    provided: dict[str, ParameterValue],
) -> InstantiateOutcome:
    """实例化入口。参数有问题就返回问题集，不产出半成品 IR。"""
    check_template_integrity(ir_template, parameter_schema, bindings, constraints)

    resolved, issues = _resolve_parameters(parameter_schema, provided)

    # 参数本身不合法时不再跑约束——否则会在错值上二次报错，噪音大于信息
    if not issues:
        issues.extend(_check_constraints(constraints, resolved, set(provided)))

    if issues:
        return InstantiateOutcome(ir=ir_template, params=resolved, issues=issues)

    return InstantiateOutcome(
        ir=_apply_bindings(ir_template, bindings, resolved),
        params=resolved,
    )
