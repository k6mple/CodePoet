"""IR 跨字段校验。

Pydantic 模型（``app.schemas.ir``）已经保证了单个字段合法：类型、范围、
枚举、数组长度、拒绝未知字段。这里补的是 JSON Schema 表达不了的规则：

1. 节点 id 在同一份 IR 内唯一；
2. ``parentId`` 必须指向真实存在的节点，父子链不能成环；
3. 嵌套深度不超过 4 层；
4. 所有多边形/折线的顶点总数不超过 2000；
5. 多边形必须是非零面积、非自交的简单多边形；
6. 动画引用的 ``nodeId`` 必须存在；
7. 统计几何包围盒超出 800×600 画布的节点数。

返回契约里的 ``ValidationReport``，前端直接拿去做错误提示。

已知简化：坐标变换只精确累加平移和缩放。带 rotation 的节点改用外接正方形
做保守估计（宁可多报越界，也不漏报），精确的旋转后包围盒交给渲染端处理。
"""

from __future__ import annotations

from app.schemas.ir import (
    CircleNode,
    DrawingIR,
    IRNode,
    PolygonNode,
    PolylineNode,
)
from app.schemas.validation import Issue, ValidationReport

CANVAS_WIDTH = 800
CANVAS_HEIGHT = 600
MAX_NODE_DEPTH = 4
MAX_TOTAL_VERTICES = 2000
MAX_ISSUES = 100

# 浮点容差，避免 799.99999999 被误判成越界
EPSILON = 1e-6
# 判定面积为 0 的阈值
AREA_EPSILON = 1e-9


def _is_leaf(node: IRNode) -> bool:
    """分组节点没有几何形状，不参与越界判断。"""
    return isinstance(node, (CircleNode, PolygonNode, PolylineNode))


def _ancestor_chain(node: IRNode, by_id: dict[str, IRNode]) -> list[IRNode]:
    """从节点自身到根节点的链；遇到不存在的父节点或成环就停。"""
    chain = [node]
    seen = {node.id}
    current = node
    while current.parentId is not None:
        parent = by_id.get(current.parentId)
        if parent is None or parent.id in seen:
            break
        seen.add(parent.id)
        chain.append(parent)
        current = parent
    return chain


def _has_cycle(node: IRNode, by_id: dict[str, IRNode]) -> bool:
    """沿 parentId 向上走，再次遇到走过的节点即成环。"""
    seen: set[str] = set()
    current: IRNode | None = node
    while current is not None:
        if current.id in seen:
            return True
        seen.add(current.id)
        if current.parentId is None:
            return False
        current = by_id.get(current.parentId)
    return False


def _to_world(
    points: list[tuple[float, float]], chain: list[IRNode]
) -> tuple[list[tuple[float, float]], bool]:
    """把节点局部坐标逐层变换到画布坐标。

    chain 的顺序是「自身 → 父 → 祖父 … → 根」，变换也必须按这个顺序
    依次应用：先过自己的 transform，再过父的，最后到根。
    """
    rotated = False
    result: list[tuple[float, float]] = []
    for px, py in points:
        x, y = px, py
        for item in chain:
            transform = item.transform
            if transform.rotation:
                rotated = True
            x = x * transform.scaleX + transform.x
            y = y * transform.scaleY + transform.y
        result.append((x, y))
    return result, rotated


def _scaled_radius(radius: float, chain: list[IRNode]) -> float:
    """圆半径在父级缩放后也会变，取两个轴里较大的缩放比保证不低估。"""
    for item in chain:
        radius *= max(abs(item.transform.scaleX), abs(item.transform.scaleY))
    return radius


def _bounds(node: IRNode, chain: list[IRNode]) -> tuple[float, float, float, float]:
    """返回节点在画布坐标系下的包围盒 (minx, miny, maxx, maxy)。"""
    if isinstance(node, CircleNode):
        centers, _ = _to_world([(node.geometry.cx, node.geometry.cy)], chain)
        cx, cy = centers[0]
        radius = _scaled_radius(node.geometry.r, chain)
        # 圆旋转后仍是圆，包围盒不变
        return cx - radius, cy - radius, cx + radius, cy + radius

    points, rotated = _to_world(list(node.geometry.points), chain)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if rotated:
        # 有旋转时用外接正方形，宁可保守多算
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        half = (((max_x - min_x) ** 2 + (max_y - min_y) ** 2) ** 0.5) / 2
        return center_x - half, center_y - half, center_x + half, center_y + half
    return min_x, min_y, max_x, max_y


def _polygon_area(points: list[tuple[float, float]]) -> float:
    """鞋带公式求面积绝对值。"""
    count = len(points)
    total = 0.0
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def _cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """判断两条线段是否严格相交（端点接触不算，避免相邻边误报）。"""
    d1 = _cross(p3, p4, p1)
    d2 = _cross(p3, p4, p2)
    d3 = _cross(p1, p2, p3)
    d4 = _cross(p1, p2, p4)
    return ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4))


def _self_intersects(points: list[tuple[float, float]]) -> bool:
    """检查简单多边形自交。跳过相邻边和首尾边。"""
    count = len(points)
    for i in range(count):
        seg_a = (points[i], points[(i + 1) % count])
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            seg_b = (points[j], points[(j + 1) % count])
            if _segments_cross(seg_a[0], seg_a[1], seg_b[0], seg_b[1]):
                return True
    return False


def validate_ir(ir: DrawingIR) -> ValidationReport:
    """执行全部跨字段校验，返回契约规定的报告结构。"""
    issues: list[Issue] = []

    # ---- 1. id 唯一 -----------------------------------------------------
    by_id: dict[str, IRNode] = {}
    for index, node in enumerate(ir.nodes):
        if node.id in by_id:
            issues.append(
                Issue(
                    code="IR_DUPLICATE_NODE_ID",
                    message=f"节点 id「{node.id}」重复出现，同一份 IR 内必须唯一",
                    path=f"nodes[{index}].id",
                    nodeIds=[node.id],
                )
            )
        else:
            by_id[node.id] = node

    # ---- 2. parentId 必须存在 -------------------------------------------
    for index, node in enumerate(ir.nodes):
        if node.parentId is not None and node.parentId not in by_id:
            issues.append(
                Issue(
                    code="IR_MISSING_PARENT",
                    message=f"节点「{node.id}」的 parentId「{node.parentId}」不存在",
                    path=f"nodes[{index}].parentId",
                    nodeIds=[node.id],
                )
            )

    # ---- 3. 成环与深度 --------------------------------------------------
    reported_cycle: set[str] = set()
    for index, node in enumerate(ir.nodes):
        if _has_cycle(node, by_id):
            if node.id not in reported_cycle:
                issues.append(
                    Issue(
                        code="IR_PARENT_CYCLE",
                        message=f"节点「{node.id}」的父级链形成环，无法确定层级",
                        path=f"nodes[{index}].parentId",
                        nodeIds=[node.id],
                    )
                )
                reported_cycle.add(node.id)
            continue
        depth = len(_ancestor_chain(node, by_id))
        if depth > MAX_NODE_DEPTH:
            issues.append(
                Issue(
                    code="IR_NODE_TOO_DEEP",
                    message=f"节点「{node.id}」嵌套 {depth} 层，超过上限 {MAX_NODE_DEPTH} 层",
                    path=f"nodes[{index}].parentId",
                    nodeIds=[node.id],
                )
            )

    # ---- 4. 顶点总数 ----------------------------------------------------
    total_vertices = sum(
        len(node.geometry.points)
        for node in ir.nodes
        if isinstance(node, (PolygonNode, PolylineNode))
    )
    if total_vertices > MAX_TOTAL_VERTICES:
        issues.append(
            Issue(
                code="IR_TOO_MANY_VERTICES",
                message=f"顶点总数 {total_vertices} 超过上限 {MAX_TOTAL_VERTICES}",
                path="nodes",
                nodeIds=[],
            )
        )

    # ---- 5. 多边形几何 --------------------------------------------------
    for index, node in enumerate(ir.nodes):
        if not isinstance(node, PolygonNode):
            continue
        points = list(node.geometry.points)
        # 必须先判自交再判面积：自交多边形的鞋带面积可能恰好为 0
        # （例如蝴蝶结形的两个三角形互相抵消），若先判面积就会把
        # 真正的病因「自交」误报成「退化」，用户按提示改不出来。
        if _self_intersects(points):
            issues.append(
                Issue(
                    code="IR_POLYGON_SELF_INTERSECT",
                    message=f"多边形「{node.id}」存在自交，只接受简单多边形",
                    path=f"nodes[{index}].geometry.points",
                    nodeIds=[node.id],
                )
            )
        elif _polygon_area(points) <= AREA_EPSILON:
            issues.append(
                Issue(
                    code="IR_POLYGON_DEGENERATE",
                    message=f"多边形「{node.id}」面积为 0，所有点共线或重合",
                    path=f"nodes[{index}].geometry.points",
                    nodeIds=[node.id],
                )
            )

    # ---- 6. 动画引用与时间区间 ------------------------------------------
    for index, animation in enumerate(ir.animations):
        if animation.nodeId not in by_id:
            issues.append(
                Issue(
                    code="IR_ANIMATION_NODE_MISSING",
                    message=f"第 {index} 条动画引用的节点「{animation.nodeId}」不存在",
                    path=f"animations[{index}].nodeId",
                    nodeIds=[],
                )
            )
        elif animation.endMs <= animation.startMs:
            issues.append(
                Issue(
                    code="IR_ANIMATION_TIME_RANGE",
                    message=(
                        f"第 {index} 条动画的 endMs({animation.endMs}) "
                        f"必须大于 startMs({animation.startMs})"
                    ),
                    path=f"animations[{index}].endMs",
                    nodeIds=[animation.nodeId],
                )
            )

    # ---- 7. 越界统计 ----------------------------------------------------
    out_of_bounds: list[str] = []
    for node in ir.nodes:
        if not _is_leaf(node) or _has_cycle(node, by_id):
            continue
        chain = _ancestor_chain(node, by_id)
        min_x, min_y, max_x, max_y = _bounds(node, chain)
        if (
            min_x < -EPSILON
            or min_y < -EPSILON
            or max_x > CANVAS_WIDTH + EPSILON
            or max_y > CANVAS_HEIGHT + EPSILON
        ):
            out_of_bounds.append(node.id)

    if out_of_bounds:
        issues.append(
            Issue(
                code="IR_OUT_OF_BOUNDS",
                message=(
                    f"{len(out_of_bounds)} 个节点的包围盒超出 "
                    f"{CANVAS_WIDTH}×{CANVAS_HEIGHT} 画布，渲染时会被裁掉一部分"
                ),
                path="nodes",
                nodeIds=out_of_bounds[:MAX_ISSUES],
            )
        )

    return ValidationReport(
        # 这一层还没有参数可校验，参数校验在模板生成阶段做
        parameterValid=True,
        nodeCount=len(ir.nodes),
        outOfBoundsNodeCount=len(out_of_bounds),
        issues=issues[:MAX_ISSUES],
    )
