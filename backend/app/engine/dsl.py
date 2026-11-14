"""DSL v1：纯 AST 解释器。

这个模块存在的理由只有一个：**让用户能程序化地画图，又不给它执行代码的机会**。
所以这里没有 ``eval``、没有 ``exec``、不 import 用户字符串——只解释一个受限的
JSON AST。

安全边界（每一条都有对应的测试）：

1. **op 白名单**：circle / polygon / polyline / set_param，别的一律拒绝；
2. **字段白名单**：每个 op 允许哪些字段是固定的，多一个就整份拒绝——防止把
   别的东西夹带进来（比如 ``"class"``、``"__proto__"``）；
3. **数值有界**：坐标、半径、透明度、颜色都要落在 IR schema 允许的范围内，
   NaN / Infinity 直接拒绝；
4. **预算**：步数、绘图调用次数、AST 节点数都有上限，超了整次拒绝，
   **不产出半成品 IR**；
5. **不改 base**：既有节点、动画、叙事原样保留，新增图元只挂在末尾一个
   确定性的 root group 下。

## 追加语义（细则第 7 节）

DSL v1 **不替换任意子树**：从 base 的完整 IR 复制，在确定性新 root group 下
追加 circle/polygon/polyline 节点，ID 由 run + 调用序号生成。

``set_param`` 是唯一的例外：它会先用模板既有绑定重新实例化，再追加图元。
但它只允许用于「有模板来源、且未被手改/子图派生」的 base——否则会丢掉用户
的编辑。不满足就返回 ``DSL_INVALID_BASE``，不尝试修补。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.engine.instantiate import InstantiateOutcome, instantiate
from app.schemas.generation import (
    Binding,
    Constraint,
    ParameterDefinition,
    ParameterValue,
)
from app.schemas.ir import DrawingIR

__all__ = [
    "DEFAULT_LIMITS",
    "DSL_VERSION",
    "MAX_SOURCE_BYTES",
    "DslError",
    "DslLimits",
    "DslOutcome",
    "DslProgram",
    "ParamContext",
    "compile_program",
    "run",
]

#: 源码上限，与契约 ``SandboxCreate.source`` 的 maxLength 一致
MAX_SOURCE_BYTES = 16384

DSL_VERSION = 1

#: 契约里 ``dslMaxAstNodes`` 的上限是 2000；默认按细则的「步数 10000」另算
DEFAULT_LIMITS = None  # 在类定义之后赋值，见文件末尾

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NODE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

#: 绘图 op。set_param 单独处理，因为它影响的是「实例化」而不是「追加」。
_DRAW_OPS = ("circle", "polygon", "polyline")

_COMMON_STYLE_FIELDS = {"fill", "stroke", "strokeWidth", "opacity"}

_OP_FIELDS: dict[str, set[str]] = {
    "circle": {"op", "cx", "cy", "r"} | _COMMON_STYLE_FIELDS,
    "polygon": {"op", "points"} | _COMMON_STYLE_FIELDS,
    "polyline": {"op", "points"} | _COMMON_STYLE_FIELDS,
    "set_param": {"op", "name", "value"},
}

#: 与 IR schema 对齐的数值边界。这里再写一遍不是重复劳动：
#: schema 校验发生在**构造模型时**，而我们要在构造之前就把不合规的输入拒掉，
#: 否则 IllegalValue 会以 500 的形式冒出去，而不是一个可解释的审计拒绝。
_COORD_LIMIT = 8192.0
_RADIUS_MIN = 0.1
_RADIUS_MAX = 2048.0
_STROKE_MIN = 0.0
_STROKE_MAX = 20.0

_ROOT_GROUP_PREFIX = "g"
_NODE_PREFIX = "n"


class DslError(Exception):
    """DSL 阶段的失败。``code`` 直接对应契约里的 run.error.code。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class DslLimits:
    """执行预算。数值来自 policy 快照，不在请求里给（客户端不能自己放宽）。"""

    max_draw_calls: int = 1000
    max_steps: int = 10000
    max_ast_nodes: int = 2000


DEFAULT_LIMITS = DslLimits()


@dataclass(frozen=True)
class ParamContext:
    """``set_param`` 需要的模板上下文。

    没有它就代表「这个 base 不支持改参数」——调用方（服务层）负责按
    「有模板来源、未手改、未子图派生」判断，判断不通过就别传。
    """

    template_ir: DrawingIR
    parameter_schema: list[ParameterDefinition]
    bindings: list[Binding]
    constraints: list[Constraint]
    provided: dict[str, ParameterValue]


@dataclass(frozen=True)
class DslProgram:
    """审计通过的程序。构造它就意味着「静态检查全过」。"""

    ops: tuple[dict[str, Any], ...]
    set_param: tuple[dict[str, Any], ...]


@dataclass
class DslOutcome:
    """执行结果。``metrics`` 直接对应契约里的 SandboxRun.metrics。"""

    ir: DrawingIR
    appended_node_ids: list[str] = field(default_factory=list)
    draw_calls: int = 0
    steps: int = 0
    wall_time_ms: int = 0
    cpu_time_ms: int = 0

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "wallTimeMs": self.wall_time_ms,
            "cpuTimeMs": self.cpu_time_ms,
            "drawCalls": self.draw_calls,
            "steps": self.steps,
        }


# ---------------------------------------------------------------------------
# 静态审计
# ---------------------------------------------------------------------------


def compile_program(source: str, limits: DslLimits | None = None) -> DslProgram:
    """把源码解析成已审计的程序。

    所有不合规都在这里拒绝，执行阶段只做「已知合法」的构造——这样执行代码里
    不需要再塞一堆 assert，也不会出现「跑到一半才发现输入有问题」。
    """
    limits = limits or DEFAULT_LIMITS

    if not isinstance(source, str) or not source.strip():
        raise DslError("DSL_AUDIT_REJECTED", "源码为空")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"源码超过 {MAX_SOURCE_BYTES} 字节上限",
            {"maxBytes": MAX_SOURCE_BYTES},
        )

    try:
        document = json.loads(source)
    except json.JSONDecodeError as exc:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"源码不是合法 JSON：{exc.msg}",
            {"line": exc.lineno, "column": exc.colno},
        ) from exc

    if not isinstance(document, dict):
        raise DslError("DSL_AUDIT_REJECTED", "顶层必须是对象")
    extra = set(document) - {"version", "ops"}
    if extra:
        # 多余字段一律拒绝：这是「夹带」最容易被忽略的入口
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"顶层出现未支持字段：{sorted(extra)}",
            {"unknownFields": sorted(extra)},
        )
    if document.get("version") != DSL_VERSION:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"只支持 version={DSL_VERSION}",
            {"version": document.get("version"), "supported": DSL_VERSION},
        )

    ops = document.get("ops")
    if not isinstance(ops, list) or not ops:
        raise DslError("DSL_AUDIT_REJECTED", "ops 必须是非空数组")
    if len(ops) > limits.max_ast_nodes:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops 数量 {len(ops)} 超过上限 {limits.max_ast_nodes}",
            {"count": len(ops), "max": limits.max_ast_nodes},
        )

    audited: list[dict[str, Any]] = []
    set_param: list[dict[str, Any]] = []
    seen_draw = False

    for index, raw in enumerate(ops):
        op = _audit_op(index, raw)
        if op["op"] == "set_param":
            if seen_draw:
                # 细则：set_param 必须在绘图调用之前。放在后面会让「用哪套参数
                # 画的」变得不可解释——同一份源码换顺序就是另一张图。
                raise DslError(
                    "DSL_AUDIT_REJECTED",
                    f"ops[{index}] set_param 出现在绘图调用之后",
                    {"index": index},
                )
            set_param.append(op)
        else:
            seen_draw = True
            audited.append(op)

    return DslProgram(ops=tuple(audited), set_param=tuple(set_param))


def _audit_op(index: int, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DslError("DSL_AUDIT_REJECTED", f"ops[{index}] 必须是对象", {"index": index})

    name = raw.get("op")
    if not isinstance(name, str) or name not in _OP_FIELDS:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] 的 op 不受支持：{name!r}",
            {"index": index, "op": name, "supported": sorted(_OP_FIELDS)},
        )

    allowed = _OP_FIELDS[name]
    unknown = set(raw) - allowed
    if unknown:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}]（{name}）出现未支持字段：{sorted(unknown)}",
            {"index": index, "unknownFields": sorted(unknown)},
        )

    if name == "set_param":
        return _audit_set_param(index, raw)

    if name == "circle":
        _require_number(index, raw, "cx", -_COORD_LIMIT, _COORD_LIMIT)
        _require_number(index, raw, "cy", -_COORD_LIMIT, _COORD_LIMIT)
        _require_number(index, raw, "r", _RADIUS_MIN, _RADIUS_MAX)
    else:
        _audit_points(index, raw.get("points"))

    _audit_style(index, raw)
    return dict(raw)


def _audit_set_param(index: int, raw: dict[str, Any]) -> dict[str, Any]:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise DslError("DSL_AUDIT_REJECTED", f"ops[{index}] set_param 缺 name", {"index": index})
    value = raw.get("value")
    if not isinstance(value, (int, float, str, bool)) or isinstance(value, bool) and not isinstance(value, bool):
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] set_param 的 value 只能是数字/字符串/布尔",
            {"index": index},
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise DslError("DSL_AUDIT_REJECTED", f"ops[{index}] set_param 的 value 不是有限数", {"index": index})
    return dict(raw)


def _audit_points(index: int, points: Any) -> None:
    if not isinstance(points, list):
        raise DslError("DSL_AUDIT_REJECTED", f"ops[{index}] 的 points 必须是数组", {"index": index})
    # polygon 至少 3 点、polyline 至少 2 点；上限与 IR schema 一致为 512
    if len(points) < 2 or len(points) > 512:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] 的 points 数量 {len(points)} 不在 2~512",
            {"index": index, "count": len(points)},
        )
    for point_index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise DslError(
                "DSL_AUDIT_REJECTED",
                f"ops[{index}] 的 points[{point_index}] 必须是 [x, y]",
                {"index": index, "pointIndex": point_index},
            )
        for axis, value in enumerate(point):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DslError(
                    "DSL_AUDIT_REJECTED",
                    f"ops[{index}] 的 points[{point_index}][{axis}] 不是数字",
                    {"index": index, "pointIndex": point_index},
                )
            if not math.isfinite(float(value)) or abs(float(value)) > _COORD_LIMIT:
                raise DslError(
                    "DSL_AUDIT_REJECTED",
                    f"ops[{index}] 的 points[{point_index}][{axis}] 超出 ±{_COORD_LIMIT}",
                    {"index": index, "pointIndex": point_index, "value": value},
                )


def _audit_style(index: int, raw: dict[str, Any]) -> None:
    for key in ("fill", "stroke"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not _COLOR.match(value):
            raise DslError(
                "DSL_AUDIT_REJECTED",
                f"ops[{index}] 的 {key} 必须是 #RRGGBB",
                {"index": index, "field": key, "value": value},
            )
    if "strokeWidth" in raw:
        _require_number(index, raw, "strokeWidth", _STROKE_MIN, _STROKE_MAX)
    if "opacity" in raw:
        _require_number(index, raw, "opacity", 0.0, 1.0)


def _require_number(index: int, raw: dict[str, Any], key: str, low: float, high: float) -> None:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] 的 {key} 必须是数字",
            {"index": index, "field": key},
        )
    number = float(value)
    if not math.isfinite(number):
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] 的 {key} 不是有限数",
            {"index": index, "field": key, "value": str(value)},
        )
    if number < low or number > high:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"ops[{index}] 的 {key}={number} 超出 {low}~{high}",
            {"index": index, "field": key, "value": number, "min": low, "max": high},
        )


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def run(
    program: DslProgram,
    base_ir: DrawingIR,
    *,
    run_id: str,
    limits: DslLimits | None = None,
    param_context: ParamContext | None = None,
) -> DslOutcome:
    """在 base 上追加图元，返回新的 IR 与度量。

    ``base_ir`` 不会被修改——所有改动都在副本上做，调用方拿到的是一份新的
    不可变快照的原料。任何一步超限都抛 ``DslError``，**不返回半成品**。
    """
    limits = limits or DEFAULT_LIMITS
    started_wall = time.perf_counter()
    started_cpu = time.process_time()

    working = base_ir
    if program.set_param:
        working = _instantiate_for_params(program.set_param, param_context, base_ir)

    # 深拷贝是**防御性**的，不是当前必需：下面最后会用 ``DrawingIR.model_validate``
    # 从 dict 重建，本来就会产生新对象。保留它是为了「以后有人在这里就地改节点
    # 样式」时不会污染 base——但请知道：现有测试只能证明「base 不被修改」，
    # 证明不了「这个 deep=True 有必要」。别把它当成已被验证的安全网。
    nodes = [node.model_copy(deep=True) for node in working.nodes]
    existing_ids = {node.id for node in nodes}

    root_id = _root_group_id(run_id)
    if root_id in existing_ids:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"新 root group 的 ID「{root_id}」与 base 节点冲突",
            {"runId": run_id, "nodeId": root_id},
        )

    appended: list[str] = []
    draw_calls = 0
    steps = 0

    # 顶点总量按「base + 本次追加」一起算：契约的 2000 是全图上限，不是增量上限。
    total_vertices = _count_vertices(working)

    for index, op in enumerate(program.ops):
        steps += 1
        if steps > limits.max_steps:
            raise DslError(
                "DSL_STEP_LIMIT",
                f"解释步数超过 {limits.max_steps}",
                {"steps": steps, "max": limits.max_steps},
            )

        node = _build_node(op, run_id, index, root_id)
        if node["type"] in ("polygon", "polyline"):
            point_count = len(node["geometry"]["points"])
            total_vertices += point_count
            # 每个点也要计一步：否则一个大 polygon 用 1 步就画完了，
            # 步数上限形同虚设。
            steps += point_count
            if steps > limits.max_steps:
                raise DslError(
                    "DSL_STEP_LIMIT",
                    f"解释步数超过 {limits.max_steps}",
                    {"steps": steps, "max": limits.max_steps},
                )
            if total_vertices > 2000:
                raise DslError(
                    "DSL_AUDIT_REJECTED",
                    f"顶点总数 {total_vertices} 超过全图上限 2000",
                    {"vertices": total_vertices, "max": 2000},
                )

        draw_calls += 1
        if draw_calls > limits.max_draw_calls:
            raise DslError(
                "DSL_CALL_LIMIT",
                f"绘图调用 {draw_calls} 超过上限 {limits.max_draw_calls}",
                {"drawCalls": draw_calls, "max": limits.max_draw_calls},
            )

        node_id = node["id"]
        if node_id in existing_ids:
            raise DslError(
                "DSL_AUDIT_REJECTED",
                f"节点 ID「{node_id}」重复",
                {"nodeId": node_id},
            )
        existing_ids.add(node_id)
        appended.append(node_id)
        nodes.append(node)

    if appended:
        if len(nodes) > 200:
            raise DslError(
                "DSL_AUDIT_REJECTED",
                f"节点总数 {len(nodes)} 超过全图上限 200",
                {"nodes": len(nodes), "max": 200},
            )
        nodes.append(
            {
                "id": root_id,
                "parentId": None,
                "type": "group",
                "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
                "style": {"opacity": 1},
            }
        )

    ir = DrawingIR.model_validate(
        {
            **working.model_dump(mode="json"),
            "nodes": nodes,
        }
    )

    wall_ms = int((time.perf_counter() - started_wall) * 1000)
    cpu_ms = int((time.process_time() - started_cpu) * 1000)
    return DslOutcome(
        ir=ir,
        appended_node_ids=appended,
        draw_calls=draw_calls,
        steps=steps,
        wall_time_ms=wall_ms,
        cpu_time_ms=cpu_ms,
    )


def _instantiate_for_params(
    set_param_ops: tuple[dict[str, Any], ...],
    context: ParamContext | None,
    base_ir: DrawingIR,
) -> DrawingIR:
    """用模板既有绑定重新实例化，再在其上追加。

    不满足条件就 ``DSL_INVALID_BASE``，**不尝试修补**：如果为了「让它跑起来」
    而拿 base 的 IR 硬改样式，那等于悄悄重建了模板，用户的编辑就没了。
    """
    if context is None:
        raise DslError(
            "DSL_INVALID_BASE",
            "该 base 不支持 set_param：需要「有模板来源、未手改、未子图派生」的快照",
            {"reason": "no-template-context"},
        )

    provided = dict(context.provided)
    for op in set_param_ops:
        provided[op["name"]] = op["value"]

    outcome: InstantiateOutcome = instantiate(
        context.template_ir,
        context.parameter_schema,
        context.bindings,
        context.constraints,
        provided,
    )
    if not outcome.ok:
        raise DslError(
            "DSL_INVALID_BASE",
            "参数无法在模板既有绑定下实例化",
            {
                "issues": [
                    {"code": issue.code, "message": issue.message, "path": issue.path}
                    for issue in outcome.issues
                ]
            },
        )
    return outcome.ir


def _build_node(op: dict[str, Any], run_id: str, index: int, root_id: str) -> dict[str, Any]:
    """把审计过的 op 变成 IR 节点字典。调用前输入已保证合法。"""
    style = {
        "fill": op.get("fill"),
        "stroke": op.get("stroke"),
        "strokeWidth": float(op.get("strokeWidth", 0)),
        "opacity": float(op.get("opacity", 1)),
    }
    base = {
        "id": _node_id(run_id, index),
        "parentId": root_id,
        "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
        "style": style,
    }
    if op["op"] == "circle":
        return {
            **base,
            "type": "circle",
            "geometry": {
                "cx": float(op["cx"]),
                "cy": float(op["cy"]),
                "r": float(op["r"]),
            },
        }
    points = [[float(point[0]), float(point[1])] for point in op["points"]]
    if op["op"] == "polygon" and len(points) < 3:
        raise DslError(
            "DSL_AUDIT_REJECTED",
            f"polygon 至少需要 3 个点，实际 {len(points)}",
            {"index": index},
        )
    return {
        **base,
        "type": op["op"],
        "geometry": {"points": points},
    }


def _root_group_id(run_id: str) -> str:
    safe = _sanitize(run_id)
    return f"{_ROOT_GROUP_PREFIX}-{safe}"


def _node_id(run_id: str, index: int) -> str:
    safe = _sanitize(run_id)
    # 序号放前面，方便按生成顺序读；总长控制在 80 以内
    return f"{_NODE_PREFIX}{index}-{safe}"[:80]


def _sanitize(raw: str) -> str:
    """把 run id 压成合法 NodeId 片段。

    run id 由服务层生成（约定是 ``run-<hex>``），这里仍做一次防御：
    合法性不能依赖「调用方一定守规矩」。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", str(raw))
    if not _NODE_ID.match(cleaned):
        raise DslError("DSL_AUDIT_REJECTED", f"runId 不合法：{raw!r}", {"runId": str(raw)})
    return cleaned[:40]


def _count_vertices(ir: DrawingIR) -> int:
    total = 0
    for node in ir.nodes:
        geometry = getattr(node, "geometry", None)
        points = getattr(geometry, "points", None) if geometry is not None else None
        if points:
            total += len(points)
    return total
