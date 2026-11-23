"""生成三套高精度新中式国画参数化艺术模板（严格满足 Drawing IR 1.0 契约）。

输出路径：
  shared/demo/templates/art/art-lotus.json   —— 《清莲映月·水墨工笔》
  shared/demo/templates/art/art-bamboo.json  —— 《劲节清风·墨竹听泉》
  shared/demo/templates/art/art-plum.json    —— 《疏影寒梅·雪月清辉》

所有模板均经过后端 ``DrawingIR``、``validate_ir``、``instantiate`` 与 ``sample_scene``
逐项校验，确保节点数 <= 185、总顶点数 <= 1900、零自交、零越界，并同时声明支持
``["canvas2d", "css", "webgl"]`` 三套浏览器渲染器。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
OUT_DIR = PROJECT_ROOT / "shared" / "demo" / "templates" / "art"

sys.path.insert(0, str(BACKEND_DIR))

from app.engine.instantiate import instantiate  # noqa: E402
from app.engine.sample import sample_scene  # noqa: E402
from app.engine.validate import validate_ir  # noqa: E402
from app.schemas.generation import Binding, Constraint, ParameterDefinition  # noqa: E402
from app.schemas.ir import DrawingIR  # noqa: E402
from app.schemas.templates import TemplateMetadata  # noqa: E402

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# 几何曲线与有机造型工具
# ---------------------------------------------------------------------------


def r2(v: float) -> float:
    return round(float(v), 2)


def pt(x: float, y: float) -> list[float]:
    return [r2(x), r2(y)]


def cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, steps: int) -> list[Point]:
    out: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
        y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
        out.append((x, y))
    return out


def catmull_rom(ctrl: list[Point], seg_steps: int = 4) -> list[Point]:
    """平滑样条插值，适合苍劲蜿蜒的古梅老干和山石曲线。"""
    if len(ctrl) < 2:
        return ctrl
    pts = [ctrl[0]] + ctrl + [ctrl[-1]]
    out: list[Point] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(seg_steps):
            t = s / seg_steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(ctrl[-1])
    return out


def clean_ring(points: list[Point], eps: float = 0.25) -> list[list[float]]:
    """去重相邻近点，避免退化边和 WebGL 耳切法卡死。"""
    cleaned: list[Point] = []
    for x, y in points:
        if not cleaned or math.hypot(x - cleaned[-1][0], y - cleaned[-1][1]) > eps:
            cleaned.append((x, y))
    if len(cleaned) > 2 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) <= eps:
        cleaned.pop()
    return [pt(x, y) for x, y in cleaned]


def rotate_translate(points: list[Point], cx: float, cy: float, angle_deg: float) -> list[Point]:
    rad = math.radians(angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in points]


def make_group(
    node_id: str,
    parent_id: str | None = None,
    *,
    x: float = 0,
    y: float = 0,
    rotation: float = 0,
    scale_x: float = 1,
    scale_y: float = 1,
    opacity: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "parentId": parent_id,
        "type": "group",
        "transform": {
            "x": r2(x),
            "y": r2(y),
            "rotation": r2(rotation),
            "scaleX": r2(scale_x),
            "scaleY": r2(scale_y),
        },
        "style": {"opacity": r2(opacity)},
    }


def make_polygon(
    node_id: str,
    parent_id: str | None,
    points: list[Point],
    *,
    fill: str | None,
    stroke: str | None = None,
    stroke_width: float = 0.0,
    opacity: float = 1.0,
    x: float = 0,
    y: float = 0,
    rotation: float = 0,
    scale_x: float = 1,
    scale_y: float = 1,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "parentId": parent_id,
        "type": "polygon",
        "transform": {
            "x": r2(x),
            "y": r2(y),
            "rotation": r2(rotation),
            "scaleX": r2(scale_x),
            "scaleY": r2(scale_y),
        },
        "geometry": {"points": clean_ring(points)},
        "style": {
            "fill": fill,
            "stroke": stroke,
            "strokeWidth": r2(stroke_width),
            "opacity": r2(opacity),
        },
    }


def make_polyline(
    node_id: str,
    parent_id: str | None,
    points: list[Point],
    *,
    stroke: str,
    stroke_width: float = 1.5,
    opacity: float = 1.0,
    x: float = 0,
    y: float = 0,
) -> dict[str, Any]:
    cleaned: list[list[float]] = []
    for px, py in points:
        if not cleaned or math.hypot(px - cleaned[-1][0], py - cleaned[-1][1]) > 0.2:
            cleaned.append(pt(px, py))
    return {
        "id": node_id,
        "parentId": parent_id,
        "type": "polyline",
        "transform": {"x": r2(x), "y": r2(y), "rotation": 0, "scaleX": 1, "scaleY": 1},
        "geometry": {"points": cleaned},
        "style": {
            "fill": None,
            "stroke": stroke,
            "strokeWidth": r2(stroke_width),
            "opacity": r2(opacity),
        },
    }


def make_circle(
    node_id: str,
    parent_id: str | None,
    cx: float,
    cy: float,
    radius: float,
    *,
    fill: str | None,
    stroke: str | None = None,
    stroke_width: float = 0.0,
    opacity: float = 1.0,
    x: float = 0,
    y: float = 0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "parentId": parent_id,
        "type": "circle",
        "transform": {"x": r2(x), "y": r2(y), "rotation": 0, "scaleX": 1, "scaleY": 1},
        "geometry": {"cx": r2(cx), "cy": r2(cy), "r": r2(radius)},
        "style": {
            "fill": fill,
            "stroke": stroke,
            "strokeWidth": r2(stroke_width),
            "opacity": r2(opacity),
        },
    }


def make_ellipse_pts(cx: float, cy: float, rx: float, ry: float, angle_deg: float = 0.0, steps: int = 24) -> list[Point]:
    pts = [(rx * math.cos(2 * math.pi * i / steps), ry * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return rotate_translate(pts, cx, cy, angle_deg)


def make_ribbon(spine: list[Point], half_widths: list[float]) -> list[Point]:
    """沿中心折线生成变宽笔触多边形（用于树干、花茎、弯枝），保证不自交。"""
    n = len(spine)
    assert n >= 2 and len(half_widths) == n
    left: list[Point] = []
    right: list[Point] = []
    for i in range(n):
        if i == 0:
            dx, dy = spine[1][0] - spine[0][0], spine[1][1] - spine[0][1]
        elif i == n - 1:
            dx, dy = spine[-1][0] - spine[-2][0], spine[-1][1] - spine[-2][1]
        else:
            dx, dy = spine[i + 1][0] - spine[i - 1][0], spine[i + 1][1] - spine[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        hw = half_widths[i]
        left.append((spine[i][0] + nx * hw, spine[i][1] + ny * hw))
        right.append((spine[i][0] - nx * hw, spine[i][1] - ny * hw))
    return left + list(reversed(right))


def make_lotus_petal_pts(
    cx: float,
    cy: float,
    length: float,
    width: float,
    angle_deg: float,
    *,
    tip_sharpness: float = 0.22,
    belly_pos: float = 0.44,
    curl: float = 0.0,
    steps: int = 9,
) -> list[Point]:
    """工笔尖瓣莲花花瓣：底部略收、中部饱满、顶部轻尖并带柔和曲线。"""
    hw = width / 2.0
    left_curve = cubic_bezier(
        (0.0, 0.0),
        (length * 0.22, -hw * 0.94 + curl * 0.25),
        (length * (belly_pos + 0.28), -hw * (1.0 - tip_sharpness) + curl * 0.6),
        (length, curl),
        steps,
    )
    right_curve = cubic_bezier(
        (length, curl),
        (length * (belly_pos + 0.28), hw * (1.0 - tip_sharpness) + curl * 0.6),
        (length * 0.22, hw * 0.94 + curl * 0.25),
        (0.0, 0.0),
        steps,
    )
    ring = left_curve[:-1] + right_curve[:-1]
    return rotate_translate(ring, cx, cy, angle_deg)


def make_lotus_leaf_pts(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    angle_deg: float,
    *,
    notch_deg: float = -75.0,
    wave_amp: float = 0.06,
    waves: int = 7,
    steps: int = 52,
) -> list[Point]:
    """写意大荷叶：高密度平滑椭圆轮廓 + 波浪叶缘 + 经典荷叶 V 形缺口。"""
    pts: list[Point] = []
    notch_rad = math.radians(notch_deg)
    for i in range(steps):
        a = 2.0 * math.pi * i / steps
        diff = abs(math.atan2(math.sin(a - notch_rad), math.cos(a - notch_rad)))
        notch_dip = 0.38 * math.exp(-((diff / 0.24) ** 2))
        wave = wave_amp * math.sin(waves * a + 0.6) * (1.0 - notch_dip)
        scale = 1.0 + wave - notch_dip
        pts.append((rx * scale * math.cos(a), ry * scale * math.sin(a)))
    return rotate_translate(pts, cx, cy, angle_deg)


def make_bamboo_segment_pts(p0: Point, p1: Point, w_end: float, w_mid: float) -> list[Point]:
    """真实竹节：两端微膨、中腰略收的平滑节段。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length

    def lerp(t: float) -> Point:
        return (p0[0] + dx * t, p0[1] + dy * t)

    ts = [0.0, 0.16, 0.5, 0.84, 1.0]
    ws = [w_end, w_mid * 1.03, w_mid, w_mid * 1.03, w_end]
    left = [(lerp(t)[0] + nx * w, lerp(t)[1] + ny * w) for t, w in zip(ts, ws)]
    right = [(lerp(t)[0] - nx * w, lerp(t)[1] - ny * w) for t, w in zip(reversed(ts), reversed(ws))]
    return left + right


def make_bamboo_leaf_pts(
    x: float,
    y: float,
    length: float,
    width: float,
    angle_deg: float,
    *,
    droop: float = 0.24,
    steps: int = 6,
) -> list[Point]:
    """柳叶形竹叶：叶柄纤细、前中段微丰、叶尖锋利并带自然垂弧。"""
    hw = width / 2.0
    tip_y = length * droop
    upper = cubic_bezier(
        (0.0, 0.0),
        (length * 0.26, -hw * 1.15 + tip_y * 0.08),
        (length * 0.68, -hw * 0.35 + tip_y * 0.52),
        (length, tip_y),
        steps,
    )
    lower = cubic_bezier(
        (length, tip_y),
        (length * 0.68, hw * 0.55 + tip_y * 0.65),
        (length * 0.26, hw * 1.10 + tip_y * 0.16),
        (0.0, 0.0),
        steps,
    )
    ring = upper[:-1] + lower[:-1]
    return rotate_translate(ring, x, y, angle_deg)


def make_plum_petal_pts(
    cx: float,
    cy: float,
    offset: float,
    radius_x: float,
    radius_y: float,
    angle_deg: float,
    steps: int = 14,
) -> list[Point]:
    """工笔梅花圆润花瓣：14 点平滑曲线卵圆形，瓣基略敛、瓣端丰润饱满。"""
    pts: list[Point] = []
    for i in range(steps):
        a = 2.0 * math.pi * i / steps
        taper = 0.74 if math.cos(a) < 0 else 1.0
        px = offset + radius_x * (0.86 + 0.14 * taper) * math.cos(a)
        py = radius_y * taper * math.sin(a)
        pts.append((px, py))
    return rotate_translate(pts, cx, cy, angle_deg)


def make_mountain_pts(
    x0: float,
    x1: float,
    base_y: float,
    peaks: list[tuple[float, float, float]],
    steps: int = 34,
) -> list[Point]:
    """多层平滑水墨远山轮廓：高斯峰叠加，两端自然贴底。"""
    pts: list[Point] = [(x0, base_y)]
    for i in range(steps + 1):
        x = x0 + (x1 - x0) * (i / steps)
        h = 0.0
        for px, height, spread in peaks:
            h += height * math.exp(-(((x - px) / spread) ** 2))
        edge = math.sin(math.pi * (i / steps)) ** 0.55
        y = max(14.0, base_y - h * edge)
        pts.append((x, y))
    pts.append((x1, base_y))
    return pts


def make_mist_cloud_pts(cx: float, cy: float, rx: float, ry: float, steps: int = 24) -> list[Point]:
    """柔和扁云雾带，消除矩形横切硬边。"""
    pts: list[Point] = []
    for i in range(steps):
        a = 2 * math.pi * i / steps
        wobble = 1.0 + 0.12 * math.sin(3 * a + 0.5) + 0.06 * math.cos(5 * a)
        pts.append((cx + rx * math.cos(a), cy + ry * wobble * math.sin(a)))
    return pts


def make_water_wave_pts(
    x0: float, x1: float, y: float, amp: float, cycles: float, phase: float = 0.0, steps: int = 18
) -> list[Point]:
    pts: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        env = math.sin(math.pi * t)
        pts.append((x, y + amp * env * math.sin(2.0 * math.pi * cycles * t + phase)))
    return pts


def make_seal_nodes(group_id: str, x: float, y: float, size: float = 28.0) -> list[dict[str, Any]]:
    """独立顶层传统朱文闲章（四角回纹印框，避免误读为特定姓氏字）。"""
    s = size
    m = 5.5
    c = s / 2.0
    return [
        make_group(group_id, None, x=x, y=y, opacity=0.92),
        make_polygon(
            f"{group_id}-bg",
            group_id,
            [(0, 0), (s, 0), (s, s), (0, s)],
            fill="#B53A2E",
            stroke="#87261D",
            stroke_width=1.0,
            opacity=0.94,
        ),
        make_polygon(
            f"{group_id}-inner",
            group_id,
            [(3.0, 3.0), (s - 3.0, 3.0), (s - 3.0, s - 3.0), (3.0, s - 3.0)],
            fill=None,
            stroke="#F6EFE2",
            stroke_width=1.0,
            opacity=0.88,
        ),
        # 传统回纹菱心印饰（雅致、对称且不产生歧义字形）
        make_polygon(
            f"{group_id}-diamond",
            group_id,
            [(c, m), (s - m, c), (c, s - m), (m, c)],
            fill=None,
            stroke="#F6EFE2",
            stroke_width=1.2,
            opacity=0.9,
        ),
        make_polygon(
            f"{group_id}-core",
            group_id,
            [(c, m + 4.2), (s - m - 4.2, c), (c, s - m - 4.2), (m + 4.2, c)],
            fill="#F6EFE2",
            opacity=0.88,
        ),
    ]


def make_frame_nodes(parent_id: str) -> list[dict[str, Any]]:
    """典雅新中式宣纸画卷双细线装裱。"""
    return [
        make_polygon(
            "scroll-frame-outer",
            parent_id,
            [(14, 14), (786, 14), (786, 586), (14, 586)],
            fill=None,
            stroke="#C9BEA8",
            stroke_width=1.2,
            opacity=0.75,
        ),
        make_polygon(
            "scroll-frame-inner",
            parent_id,
            [(22, 22), (778, 22), (778, 578), (22, 578)],
            fill=None,
            stroke="#D9D0BE",
            stroke_width=0.8,
            opacity=0.58,
        ),
    ]


# ---------------------------------------------------------------------------
# 模板 1：《清莲映月·水墨工笔》
# ---------------------------------------------------------------------------


def build_art_lotus() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 明月清晕（外层 moon-user 承接 moonOpacity 参数，内层 moon-group 承接 4s 渐明动画，互不覆盖）
    nodes.append(make_group("moon-user", "painting", x=594, y=130, opacity=0.95))
    nodes.append(make_group("moon-group", "moon-user", x=0, y=0, opacity=1.0))
    nodes.append(make_circle("moon-halo-outer", "moon-group", 0, 0, 92, fill="#EFE2C6", opacity=0.22))
    nodes.append(make_circle("moon-halo-mid", "moon-group", 0, 0, 66, fill="#F3E5C2", opacity=0.38))
    nodes.append(make_circle("moon-disc", "moon-group", 0, 0, 42, fill="#FBF5E6", stroke="#E5D5B4", stroke_width=0.9, opacity=0.96))

    # 2. 远景水墨青山 + 柔和云岚（自然融化山脚，无生硬矩形带）
    nodes.append(
        make_polygon(
            "mtn-far",
            "painting",
            make_mountain_pts(28, 772, 372, [(190, 128, 125), (420, 86, 135), (655, 102, 118)], steps=32),
            fill="#C7D0CA",
            opacity=0.34,
        )
    )
    nodes.append(
        make_polygon(
            "mtn-mid",
            "painting",
            make_mountain_pts(28, 772, 396, [(135, 92, 98), (335, 68, 115), (545, 76, 108), (710, 88, 86)], steps=32),
            fill="#98AEA4",
            opacity=0.26,
        )
    )
    nodes.append(make_polygon("mtn-mist-l", "painting", make_mist_cloud_pts(220, 386, 190, 30, steps=22), fill="#F6F1E7", opacity=0.68))
    nodes.append(make_polygon("mtn-mist-r", "painting", make_mist_cloud_pts(580, 390, 185, 28, steps=22), fill="#F6F1E7", opacity=0.66))

    # 3. 池塘柔和水色与倒影
    nodes.append(
        make_polygon(
            "pond-wash",
            "painting",
            [(28, 414), (772, 414), (772, 574), (28, 574)],
            fill="#DCE4DF",
            opacity=0.34,
        )
    )
    nodes.append(
        make_polygon(
            "pond-reflect-leaf",
            "painting",
            make_ellipse_pts(265, 500, 122, 22, -3, steps=24),
            fill="#6E8B7A",
            opacity=0.14,
        )
    )
    nodes.append(
        make_polygon(
            "pond-reflect-lotus",
            "painting",
            make_ellipse_pts(422, 514, 76, 17, 2, steps=20),
            fill="#CC8896",
            opacity=0.16,
        )
    )

    # 水波组（水平轻漾 + 茎入水涟漪）
    nodes.append(make_group("water-group", "painting", x=0, y=0, opacity=0.88))
    water_specs = [
        ("wave-1", 66, 370, 436, 3.8, 2.2, 0.0, "#758E82", 1.3, 0.42),
        ("wave-2", 435, 732, 452, 3.6, 2.0, 1.1, "#758E82", 1.3, 0.38),
        ("wave-3", 96, 516, 488, 4.4, 2.4, 0.6, "#678074", 1.5, 0.42),
        ("wave-4", 275, 718, 526, 3.8, 2.2, 2.0, "#678074", 1.3, 0.36),
        ("wave-5", 142, 608, 556, 3.0, 2.4, 0.4, "#859C91", 1.1, 0.32),
    ]
    for wid, x0, x1, wy, amp, cyc, ph, col, sw, op in water_specs:
        nodes.append(
            make_polyline(
                wid, "water-group", make_water_wave_pts(x0, x1, wy, amp, cyc, ph, steps=16),
                stroke=col, stroke_width=sw, opacity=op
            )
        )
    # 茎入水细小涟漪圈
    for ridx, (rx, ry, rw, rh) in enumerate([(402, 538, 28, 6.5), (344, 524, 20, 4.8), (274, 522, 22, 5.2)]):
        nodes.append(
            make_polygon(
                f"stem-ripple-{ridx}", "water-group", make_ellipse_pts(rx, ry, rw, rh, 0, steps=18),
                fill=None, stroke="#678275", stroke_width=1.0, opacity=0.45
            )
        )

    # 4. 荷茎（先画在荷叶下方，绝不穿帮！）+ 写意大荷叶
    nodes.append(make_group("foliage-group", "painting", x=0, y=0, opacity=1.0))

    # 左侧荷叶茎（藏在荷叶背后）
    leaf_stem_spine = cubic_bezier((256, 392), (262, 444), (268, 488), (274, 522), 7)
    nodes.append(
        make_polygon(
            "stem-leaf-left", "foliage-group",
            make_ribbon(leaf_stem_spine, [3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2, 4.4]),
            fill="#34543F", stroke="#243B2C", stroke_width=0.7, opacity=0.9
        )
    )
    # 右侧荷叶茎
    right_leaf_stem = cubic_bezier((548, 424), (542, 466), (534, 498), (528, 528), 6)
    nodes.append(
        make_polygon(
            "stem-leaf-right", "foliage-group",
            make_ribbon(right_leaf_stem, [2.8, 3.0, 3.3, 3.6, 3.9, 4.1, 4.3]),
            fill="#32503D", opacity=0.84
        )
    )

    # 左侧后景舒展大荷叶（高密度 48 点曲线轮廓）
    nodes.append(
        make_polygon(
            "leaf-back-shadow", "foliage-group",
            make_lotus_leaf_pts(260, 386, 146, 56, -10, notch_deg=-52, wave_amp=0.055, waves=7, steps=36),
            fill="#2B483B", opacity=0.2
        )
    )
    nodes.append(
        make_polygon(
            "leaf-back", "foliage-group",
            make_lotus_leaf_pts(256, 378, 150, 60, -10, notch_deg=-52, wave_amp=0.06, waves=7, steps=48),
            fill="#47725B", stroke="#2C4E3D", stroke_width=1.3, opacity=0.94
        )
    )
    nodes.append(
        make_polygon(
            "leaf-back-rim", "foliage-group",
            make_lotus_leaf_pts(252, 372, 120, 44, -10, notch_deg=-52, wave_amp=0.04, waves=6, steps=32),
            fill="#6C997F", opacity=0.35
        )
    )
    for idx, ang in enumerate([-162, -128, -95, -18, 22, 66, 118, 156]):
        rad = math.radians(ang - 10)
        vx = 256 + 112 * math.cos(rad)
        vy = 378 + 42 * math.sin(rad)
        mx = 256 + 58 * math.cos(rad + 0.05)
        my = 378 + 22 * math.sin(rad + 0.05)
        nodes.append(
            make_polyline(f"leaf-back-vein-{idx}", "foliage-group", [(256, 378), (mx, my), (vx, vy)], stroke="#8FB8A0", stroke_width=0.95, opacity=0.48)
        )
    nodes.append(make_circle("leaf-back-hub", "foliage-group", 256, 378, 4.2, fill="#D8CB9A", opacity=0.78))

    # 右侧中景舒展荷叶（44 点曲线轮廓）
    nodes.append(
        make_polygon(
            "leaf-right", "foliage-group",
            make_lotus_leaf_pts(552, 412, 134, 50, 8, notch_deg=-130, wave_amp=0.06, waves=7, steps=44),
            fill="#3B6651", stroke="#254435", stroke_width=1.25, opacity=0.92
        )
    )
    nodes.append(
        make_polygon(
            "leaf-right-highlight", "foliage-group",
            make_lotus_leaf_pts(556, 408, 102, 35, 8, notch_deg=-130, wave_amp=0.04, waves=6, steps=28),
            fill="#66937A", opacity=0.32
        )
    )
    for idx, ang in enumerate([-170, -82, -32, 14, 62, 120, 162]):
        rad = math.radians(ang + 8)
        vx = 552 + 98 * math.cos(rad)
        vy = 412 + 34 * math.sin(rad)
        nodes.append(
            make_polyline(f"leaf-right-vein-{idx}", "foliage-group", [(552, 412), (vx, vy)], stroke="#88B29A", stroke_width=0.9, opacity=0.44)
        )
    nodes.append(make_circle("leaf-right-hub", "foliage-group", 552, 412, 3.6, fill="#D4C796", opacity=0.72))

    # 花蕾茎与主莲花茎（优雅微弧）
    bud_stem_spine = cubic_bezier((344, 524), (318, 456), (296, 370), (288, 286), 9)
    nodes.append(
        make_polygon(
            "stem-bud", "foliage-group",
            make_ribbon(bud_stem_spine, [3.8 - 1.2 * (i / 9) for i in range(10)]),
            fill="#476B50", stroke="#2C4834", stroke_width=0.85, opacity=0.94
        )
    )
    main_stem_spine = cubic_bezier((402, 538), (395, 466), (408, 386), (416, 314), 10)
    nodes.append(
        make_polygon(
            "stem-main", "foliage-group",
            make_ribbon(main_stem_spine, [5.2 - 1.5 * (i / 10) for i in range(11)]),
            fill="#3D6046", stroke="#27412F", stroke_width=0.95, opacity=0.96
        )
    )

    # 5. 含苞荷花蕾
    nodes.append(make_group("bud-group", "painting", x=288, y=284, rotation=-14, opacity=1.0))
    nodes.append(make_polygon("bud-sepal-l", "bud-group", make_lotus_petal_pts(0, 6, 44, 24, -108, tip_sharpness=0.26, steps=7), fill="#567C5E", stroke="#35533C", stroke_width=0.9, opacity=0.92))
    nodes.append(make_polygon("bud-sepal-r", "bud-group", make_lotus_petal_pts(0, 6, 44, 24, -72, tip_sharpness=0.26, steps=7), fill="#567C5E", stroke="#35533C", stroke_width=0.9, opacity=0.92))
    nodes.append(make_polygon("bud-petal-back", "bud-group", make_lotus_petal_pts(0, 2, 76, 38, -90, tip_sharpness=0.18, steps=8), fill="#E69AA8", stroke="#AE5468", stroke_width=1.05, opacity=0.95))
    nodes.append(make_polygon("bud-petal-left", "bud-group", make_lotus_petal_pts(-3, 4, 68, 30, -102, tip_sharpness=0.2, curl=4, steps=7), fill="#D87D90", stroke="#9F4559", stroke_width=0.95, opacity=0.92))
    nodes.append(make_polygon("bud-petal-right", "bud-group", make_lotus_petal_pts(3, 4, 68, 30, -78, tip_sharpness=0.2, curl=-4, steps=7), fill="#F0B7C1", stroke="#B66274", stroke_width=0.95, opacity=0.92))

    # 6. 视觉焦点：工笔重瓣盛放莲花（外层 lotus-user 承接缩放/倾角参数，内层 lotus-bloom 承接微风动画）
    nodes.append(make_group("lotus-user", "painting", x=416, y=308, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("lotus-bloom", "lotus-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    for idx, (ang, ln, wd) in enumerate([(-156, 68, 28), (-24, 68, 28), (-132, 62, 26), (-48, 62, 26)]):
        nodes.append(make_polygon(f"lotus-sepal-{idx}", "lotus-bloom", make_lotus_petal_pts(0, 12, ln, wd, ang, tip_sharpness=0.28, steps=6), fill="#5A8364", stroke="#365740", stroke_width=0.85, opacity=0.88))

    outer_specs = [
        (-168, 130, 48, 8.0),
        (-146, 140, 52, 5.0),
        (-120, 148, 54, 2.0),
        (-90, 154, 58, 0.0),
        (-60, 148, 54, -2.0),
        (-34, 140, 52, -5.0),
        (-12, 130, 48, -8.0),
    ]
    for idx, (ang, ln, wd, crl) in enumerate(outer_specs):
        nodes.append(make_polygon(f"petal-outer-{idx}", "lotus-bloom", make_lotus_petal_pts(0, 6, ln, wd, ang, tip_sharpness=0.18, curl=crl, steps=8), fill="#E9A3B1", stroke="#B65B6F", stroke_width=1.15, opacity=0.94))

    mid_specs = [
        (-154, 110, 46, 6.0),
        (-128, 122, 50, 3.0),
        (-90, 128, 52, 0.0),
        (-52, 122, 50, -3.0),
        (-26, 110, 46, -6.0),
    ]
    for idx, (ang, ln, wd, crl) in enumerate(mid_specs):
        nodes.append(make_polygon(f"petal-mid-{idx}", "lotus-bloom", make_lotus_petal_pts(0, 4, ln, wd, ang, tip_sharpness=0.2, curl=crl, steps=7), fill="#D97388", stroke="#9C4156", stroke_width=1.05, opacity=0.9))

    inner_specs = [
        (-140, 84, 38, 4.0),
        (-112, 94, 42, 2.0),
        (-90, 98, 44, 0.0),
        (-68, 94, 42, -2.0),
        (-40, 84, 38, -4.0),
    ]
    for idx, (ang, ln, wd, crl) in enumerate(inner_specs):
        nodes.append(make_polygon(f"petal-inner-{idx}", "lotus-bloom", make_lotus_petal_pts(0, 4, ln, wd, ang, tip_sharpness=0.22, curl=crl, steps=7), fill="#F5CAD3", stroke="#C67486", stroke_width=0.9, opacity=0.92))

    nodes.append(make_polygon("lotus-pod", "lotus-bloom", [(-22, -4), (-16, -22), (0, -28), (16, -22), (22, -4), (12, 8), (-12, 8)], fill="#8DA65B", stroke="#5D7434", stroke_width=1.05, opacity=0.96))
    nodes.append(make_polygon("lotus-pod-top", "lotus-bloom", [(-15, -18), (-7, -25), (7, -25), (15, -18), (9, -11), (-9, -11)], fill="#BFD088", opacity=0.88))
    for idx, (sx, sy) in enumerate([(-28, -14), (-22, -28), (-10, -34), (0, -36), (10, -34), (22, -28), (28, -14), (-16, -10), (16, -10)]):
        nodes.append(make_polyline(f"stamen-line-{idx}", "lotus-bloom", [(sx * 0.45, -6), (sx, sy)], stroke="#D99B29", stroke_width=1.0, opacity=0.84))
        nodes.append(make_circle(f"stamen-dot-{idx}", "lotus-bloom", sx, sy, 2.6, fill="#F3C048", stroke="#A67115", stroke_width=0.6, opacity=0.98))

    for idx, (ang, ln, wd, crl) in enumerate([(-124, 58, 34, 5.0), (-90, 52, 36, 0.0), (-56, 58, 34, -5.0)]):
        nodes.append(make_polygon(f"petal-front-{idx}", "lotus-bloom", make_lotus_petal_pts(0, 12, ln, wd, ang, tip_sharpness=0.24, curl=crl, steps=7), fill="#F8D7DE", stroke="#C47486", stroke_width=0.95, opacity=0.88))

    # 7. 左上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 56, 48, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 101,
        "canvas": {"width": 800, "height": 600, "background": "#F6F1E7"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "moon-group", "property": "opacity", "from": 0.55, "to": 1.0, "startMs": 0, "endMs": 2200, "easing": "linear"},
            {"nodeId": "lotus-bloom", "property": "rotation", "from": -2.5, "to": 2.5, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "bud-group", "property": "rotation", "from": -16.5, "to": -11.5, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "water-group", "property": "x", "from": -5, "to": 5, "startMs": 0, "endMs": 4000, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "清涟初起：远山含黛、池水微澜，以克制留白铺陈清雅底色。", "entityIds": ["knowledge-water"]},
            {"timeMs": 1800, "caption": "重瓣舒华：荷叶田田托起工笔重瓣莲花，出淤泥而不染。", "entityIds": ["knowledge-lotus"]},
            {"timeMs": 3600, "caption": "月印清心：月晕澄明与朱文印记相映，守持清正本心。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "petalColor", "label": "主花瓣色", "description": "盛放莲花外层与花蕾主花瓣颜色", "type": "color", "default": "#E9A3B1"},
        {"name": "innerPetalColor", "label": "内瓣叠色", "description": "莲花中层叠色，用于调节浓淡层次", "type": "color", "default": "#D97388"},
        {"name": "leafColor", "label": "荷叶青色", "description": "大荷叶主色，可切换石青、墨绿或黛青", "type": "color", "default": "#47725B"},
        {"name": "lotusScale", "label": "莲华盛放度", "description": "整体缩放主莲花大小（0.78～1.10）", "type": "number", "default": 1.0, "min": 0.78, "max": 1.10, "step": 0.02},
        {"name": "moonOpacity", "label": "月辉明度", "description": "调整明月与月晕透明度（0.15～1.0）", "type": "number", "default": 0.95, "min": 0.15, "max": 1.0, "step": 0.05},
        {"name": "swayAngle", "label": "花枝倾角", "description": "调节主莲花倾角（-18°～18°）", "type": "integer", "default": 0, "min": -18, "max": 18, "step": 1},
    ]

    bindings = [
        *[{"parameter": "petalColor", "nodeId": f"petal-outer-{i}", "property": "style.fill"} for i in range(7)],
        {"parameter": "petalColor", "nodeId": "bud-petal-back", "property": "style.fill"},
        *[{"parameter": "innerPetalColor", "nodeId": f"petal-mid-{i}", "property": "style.fill"} for i in range(5)],
        {"parameter": "innerPetalColor", "nodeId": "bud-petal-left", "property": "style.fill"},
        {"parameter": "leafColor", "nodeId": "leaf-back", "property": "style.fill"},
        {"parameter": "leafColor", "nodeId": "leaf-right", "property": "style.fill"},
        {"parameter": "lotusScale", "nodeId": "lotus-user", "property": "transform.scaleX"},
        {"parameter": "lotusScale", "nodeId": "lotus-user", "property": "transform.scaleY"},
        {"parameter": "moonOpacity", "nodeId": "moon-user", "property": "style.opacity"},
        {"parameter": "swayAngle", "nodeId": "lotus-user", "property": "transform.rotation"},
    ]

    metadata = {
        "name": "清莲映月·水墨工笔",
        "description": "高精度新中式水墨莲花卷：包含远山叠墨与柔和云岚、皎月清晕、高密度波浪叶缘荷叶与脉络、重瓣尖瓣莲花、含苞花蕾、水波涟漪及朱文闲章。",
        "entityIds": ["knowledge-lotus", "knowledge-water", "knowledge-integrity"],
        "tagIds": ["tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["lotus", "water"],
        "timeTheme": "夏日",
    }

    return {
        "slug": "art-lotus",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式水墨工笔莲花模板（曲线多边形拟合、多层晕染与 4 秒清风叙事）",
    }


# ---------------------------------------------------------------------------
# 模板 2：《劲节清风·墨竹听泉》
# ---------------------------------------------------------------------------


def _add_bamboo_culm(
    nodes: list[dict[str, Any]],
    parent_id: str,
    prefix: str,
    joints: list[Point],
    base_half_w: float,
    taper: float,
    color_main: str,
    color_ring: str,
    color_highlight: str | None,
    opacity: float = 1.0,
) -> None:
    """生成带真实收腰分节、节间留白、深色竹节环与侧面高光的修竹主竿。"""
    n_seg = len(joints) - 1
    for i in range(n_seg):
        p0, p1 = joints[i], joints[i + 1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        seg_len = math.hypot(dx, dy) or 1.0
        ux, uy = dx / seg_len, dy / seg_len
        gap = 1.6
        a = (p0[0] + ux * gap, p0[1] + uy * gap)
        b = (p1[0] - ux * gap, p1[1] - uy * gap)
        hw_end = base_half_w * (1.0 - taper * (i / max(1, n_seg)))
        hw_mid = hw_end * 0.87
        nodes.append(
            make_polygon(
                f"{prefix}-seg-{i}", parent_id,
                make_bamboo_segment_pts(a, b, hw_end, hw_mid),
                fill=color_main, stroke=color_ring, stroke_width=0.8, opacity=opacity
            )
        )
        if color_highlight:
            nx, ny = -uy, ux
            hx0 = a[0] + nx * (hw_mid * 0.34)
            hy0 = a[1] + ny * (hw_mid * 0.34)
            hx1 = b[0] + nx * (hw_mid * 0.34)
            hy1 = b[1] + ny * (hw_mid * 0.34)
            nodes.append(
                make_polyline(
                    f"{prefix}-hi-{i}", parent_id, [(hx0, hy0), (hx1, hy1)],
                    stroke=color_highlight, stroke_width=max(1.1, hw_mid * 0.28), opacity=opacity * 0.42
                )
            )
        if i < n_seg - 1:
            nx, ny = -uy, ux
            rw = hw_end * 1.18
            nodes.append(
                make_polyline(
                    f"{prefix}-ring-{i}", parent_id,
                    [(p1[0] - nx * rw, p1[1] - ny * rw), (p1[0] + nx * rw, p1[1] + ny * rw)],
                    stroke=color_ring, stroke_width=2.3, opacity=min(1.0, opacity + 0.05)
                )
            )


def build_art_bamboo() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 远山烟岚层（外层 mist-user 承接 mistOpacity 参数，内层 mist-group 承接渐明动画）
    nodes.append(make_group("mist-user", "painting", x=0, y=0, opacity=0.86))
    nodes.append(make_group("mist-group", "mist-user", x=0, y=0, opacity=1.0))
    nodes.append(
        make_polygon(
            "mtn-distant", "mist-group",
            make_mountain_pts(28, 772, 492, [(170, 175, 118), (370, 134, 130), (600, 152, 135)], steps=30),
            fill="#BDC9C2", opacity=0.3
        )
    )
    nodes.append(
        make_polygon(
            "mtn-near", "mist-group",
            make_mountain_pts(28, 772, 526, [(115, 108, 95), (465, 92, 118), (695, 112, 95)], steps=28),
            fill="#95ABA0", opacity=0.24
        )
    )
    nodes.append(make_polygon("mist-cloud-1", "mist-group", make_mist_cloud_pts(240, 488, 200, 34, steps=20), fill="#F4F1E8", opacity=0.65))
    nodes.append(make_polygon("mist-cloud-2", "mist-group", make_mist_cloud_pts(560, 494, 195, 32, steps=20), fill="#F4F1E8", opacity=0.62))

    # 2. 远景淡墨竹影
    nodes.append(make_group("far-bamboo", "painting", x=0, y=0, opacity=0.36))
    _add_bamboo_culm(
        nodes, "far-bamboo", "culm-far",
        [(214, 540), (220, 450), (228, 360), (238, 270), (250, 182), (264, 102)],
        base_half_w=6.0, taper=0.28, color_main="#647B6F", color_ring="#475D52", color_highlight=None, opacity=0.72
    )
    far_leaves = [
        (234, 280, 58, 9, -152, 0.2),
        (234, 280, 64, 9.5, -174, 0.24),
        (234, 280, 52, 8.5, 156, 0.16),
        (254, 168, 56, 9, -24, 0.22),
        (254, 168, 62, 9, 4, 0.26),
        (254, 168, 48, 8, -48, 0.18),
    ]
    for idx, (lx, ly, ln, wd, ang, dr) in enumerate(far_leaves):
        nodes.append(make_polygon(f"far-leaf-{idx}", "far-bamboo", make_bamboo_leaf_pts(lx, ly, ln, wd, ang, droop=dr, steps=5), fill="#6C8477", opacity=0.75))

    # 3. 坡岸底土
    nodes.append(
        make_polygon(
            "ground-slope", "painting",
            [(46, 566), (146, 536), (310, 526), (480, 518), (656, 528), (754, 566)],
            fill="#DFD9C9", opacity=0.66
        )
    )

    # 4. 主体墨竹组（外层 bamboo-user 承接倾角/缩放参数，内层 bamboo-main 承接清风动画）
    nodes.append(make_group("bamboo-user", "painting", x=366, y=544, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("bamboo-main", "bamboo-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    main_joints = [(-26, 0), (-20, -86), (-12, -172), (-2, -258), (10, -342), (24, -422), (38, -492)]
    _add_bamboo_culm(
        nodes, "bamboo-main", "culm-1", main_joints,
        base_half_w=9.2, taper=0.30, color_main="#3E6748", color_ring="#23412B", color_highlight="#84B08D", opacity=0.98
    )

    sub_joints = [(44, -8), (52, -88), (62, -168), (74, -246), (88, -322), (104, -392)]
    _add_bamboo_culm(
        nodes, "bamboo-main", "culm-2", sub_joints,
        base_half_w=7.0, taper=0.28, color_main="#4D7857", color_ring="#2B4B33", color_highlight="#91BC9A", opacity=0.94
    )

    twigs = [
        ("twig-1", [(-12, -172), (-68, -206), (-124, -228)]),
        ("twig-2", [(-2, -258), (-60, -296), (-122, -324)]),
        ("twig-3", [(10, -342), (72, -378), (132, -398)]),
        ("twig-4", [(24, -422), (-36, -456), (-82, -474)]),
        ("twig-5", [(62, -168), (120, -198), (172, -214)]),
        ("twig-6", [(74, -246), (136, -278), (192, -294)]),
        ("twig-7", [(88, -322), (146, -352), (194, -366)]),
        ("twig-8", [(10, -342), (-42, -376), (-86, -392)]),
    ]
    for tid, pts in twigs:
        nodes.append(make_polyline(tid, "bamboo-main", pts, stroke="#2B4C34", stroke_width=1.7, opacity=0.9))

    # 更丰茂有节奏的“个字 / 介字”分组竹叶（44 片，疏密相间）
    leaf_clusters = [
        # 主竿左中簇
        (-96, -218, 78, 12.5, 162, 0.24, True),
        (-96, -218, 84, 13.0, 186, 0.22, True),
        (-96, -218, 68, 11.0, 140, 0.18, False),
        (-122, -228, 72, 11.5, 174, 0.24, True),
        (-122, -228, 60, 10.0, 200, 0.20, False),
        (-54, -198, 68, 11.5, 152, 0.20, False),
        # 主竿左上簇
        (-90, -310, 86, 13.5, 170, 0.22, True),
        (-90, -310, 76, 12.0, 196, 0.20, True),
        (-90, -310, 68, 11.5, 146, 0.20, False),
        (-120, -322, 74, 11.8, 182, 0.24, True),
        (-120, -322, 62, 10.2, 156, 0.18, False),
        (-46, -288, 72, 12.0, 160, 0.22, True),
        (-46, -288, 62, 10.5, 134, 0.16, False),
        # 主竿中上左侧小簇
        (-62, -384, 72, 11.5, 166, 0.22, True),
        (-62, -384, 66, 11.0, 192, 0.20, False),
        (-84, -392, 60, 10.0, 148, 0.18, True),
        # 主竿右上舒展簇
        (82, -382, 84, 13.0, -14, 0.26, True),
        (82, -382, 90, 13.5, 10, 0.28, True),
        (82, -382, 70, 11.5, -38, 0.20, False),
        (118, -394, 78, 12.0, 4, 0.25, True),
        (118, -394, 66, 10.5, 28, 0.22, False),
        (118, -394, 62, 10.2, -22, 0.18, False),
        # 主竿梢头轻叶
        (-44, -460, 74, 11.5, 168, 0.22, True),
        (-44, -460, 68, 11.0, 194, 0.18, False),
        (-44, -460, 58, 10.0, 142, 0.18, True),
        (-76, -472, 64, 10.5, 178, 0.22, False),
        (32, -456, 72, 11.5, -24, 0.22, True),
        (32, -456, 64, 10.5, 2, 0.24, False),
        (32, -456, 56, 9.8, -46, 0.16, True),
        # 副竿右侧中簇
        (124, -200, 80, 12.5, -8, 0.24, True),
        (124, -200, 74, 11.5, 18, 0.22, False),
        (162, -212, 70, 11.0, 6, 0.26, True),
        (162, -212, 60, 10.0, -20, 0.20, False),
        # 副竿右上丰茂簇
        (140, -280, 86, 13.0, -16, 0.24, True),
        (140, -280, 80, 12.5, 10, 0.26, True),
        (140, -280, 66, 11.0, 34, 0.20, False),
        (182, -292, 74, 11.5, -4, 0.24, False),
        (182, -292, 64, 10.5, 22, 0.22, True),
        # 副竿顶梢
        (148, -352, 74, 11.5, -26, 0.22, True),
        (148, -352, 70, 11.0, 0, 0.25, True),
        (148, -352, 60, 10.0, 24, 0.20, False),
        (186, -364, 62, 10.2, -10, 0.24, True),
    ]
    for idx, (lx, ly, ln, wd, ang, dr, is_dark) in enumerate(leaf_clusters):
        fill = "#2C5538" if is_dark else "#4E7E5B"
        stroke = "#1D3B26" if is_dark else "#345C40"
        op = 0.94 if is_dark else 0.84
        nodes.append(
            make_polygon(
                f"bleaf-{idx}", "bamboo-main",
                make_bamboo_leaf_pts(lx, ly, ln, wd, ang, droop=dr, steps=5),
                fill=fill, stroke=stroke, stroke_width=0.55, opacity=op
            )
        )

    # 5. 前景多层次太湖青石 + 竹根青苔（自然遮掩竹根切口）
    rock_outline = catmull_rom(
        [(430, 560), (442, 514), (468, 476), (508, 452), (558, 444), (608, 458), (648, 488), (684, 526), (696, 560)],
        seg_steps=2,
    )
    nodes.append(
        make_polygon(
            "rock-main", "painting", rock_outline,
            fill="#89948C", stroke="#4E5A53", stroke_width=1.3, opacity=0.97
        )
    )
    rock_light = catmull_rom(
        [(448, 518), (472, 480), (512, 458), (558, 450), (590, 472), (556, 502), (496, 516)],
        seg_steps=2,
    )
    nodes.append(make_polygon("rock-light", "painting", rock_light, fill="#B6BFB6", opacity=0.66))
    rock_dark = catmull_rom(
        [(556, 502), (590, 472), (640, 492), (676, 528), (688, 556), (566, 556)],
        seg_steps=2,
    )
    nodes.append(make_polygon("rock-dark-facet", "painting", rock_dark, fill="#606D66", opacity=0.62))
    # 左侧小伴石（托住竹根）
    nodes.append(
        make_polygon(
            "rock-left-small", "painting",
            catmull_rom([(280, 562), (298, 536), (336, 528), (376, 534), (410, 548), (424, 562)], seg_steps=2),
            fill="#9BA59D", stroke="#5A665E", stroke_width=1.0, opacity=0.92
        )
    )
    # 苔点与石脚草丛（传统国画点苔，收束根部）
    moss_specs = [
        (312, 542, 16, 6, -8, "#4B6E53"),
        (356, 546, 18, 7, 5, "#3E6147"),
        (434, 550, 20, 7.5, -6, "#3C5E45"),
        (468, 482, 11, 4.5, -24, "#4C6B54"),
        (518, 458, 12, 4.8, -8, "#45634D"),
        (612, 472, 11, 4.5, 22, "#455E4B"),
    ]
    for idx, (mx, my, rx, ry, mang, mcol) in enumerate(moss_specs):
        nodes.append(make_polygon(f"moss-{idx}", "painting", make_ellipse_pts(mx, my, rx, ry, mang, steps=12), fill=mcol, opacity=0.72))

    # 6. 右上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 714, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 202,
        "canvas": {"width": 800, "height": 600, "background": "#F4F1E8"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "bamboo-main", "property": "rotation", "from": -1.5, "to": 1.5, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "mist-group", "property": "opacity", "from": 0.68, "to": 1.0, "startMs": 0, "endMs": 3200, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "破岩立根：青石坡岸厚重沉稳，为修竹筑牢根基。", "entityIds": ["knowledge-bamboo"]},
            {"timeMs": 2000, "caption": "劲节凌风：竹竿分节挺拔、清风拂叶而不折。", "entityIds": ["knowledge-bamboo", "knowledge-integrity"]},
            {"timeMs": 3800, "caption": "守正清气：烟岚流转、朱印落款，彰显守正自持风骨。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "bambooColor", "label": "主竹竿色", "description": "修竹竹节主色调（翠绿、墨竹或石青）", "type": "color", "default": "#3E6748"},
        {"name": "leafColor", "label": "竹叶深色", "description": "前景主竹叶颜色", "type": "color", "default": "#2C5538"},
        {"name": "rockColor", "label": "青石主色", "description": "坡岸太湖石主体色调", "type": "color", "default": "#89948C"},
        {"name": "windAngle", "label": "清风倾角", "description": "控制竹林迎风微倾角度（度）", "type": "integer", "default": 0, "min": -5, "max": 5, "step": 1},
        {"name": "bambooScale", "label": "竹影比例", "description": "整体调整修竹缩放比例", "type": "number", "default": 1.0, "min": 0.84, "max": 1.03, "step": 0.02},
        {"name": "mistOpacity", "label": "远山烟岚", "description": "远景青山与烟岚透明度（0.1～1.0）", "type": "number", "default": 0.86, "min": 0.1, "max": 1.0, "step": 0.05},
    ]

    bindings = [
        *[{"parameter": "bambooColor", "nodeId": f"culm-1-seg-{i}", "property": "style.fill"} for i in range(6)],
        *[{"parameter": "bambooColor", "nodeId": f"culm-2-seg-{i}", "property": "style.fill"} for i in range(5)],
        *[{"parameter": "leafColor", "nodeId": f"bleaf-{i}", "property": "style.fill"} for i, spec in enumerate(leaf_clusters) if spec[6]],
        {"parameter": "rockColor", "nodeId": "rock-main", "property": "style.fill"},
        {"parameter": "windAngle", "nodeId": "bamboo-user", "property": "transform.rotation"},
        {"parameter": "bambooScale", "nodeId": "bamboo-user", "property": "transform.scaleX"},
        {"parameter": "bambooScale", "nodeId": "bamboo-user", "property": "transform.scaleY"},
        {"parameter": "mistOpacity", "nodeId": "mist-user", "property": "style.opacity"},
    ]

    metadata = {
        "name": "劲节清风·墨竹听泉",
        "description": "高精度新中式墨竹山石图：刻画收腰分节竹竿、竹节环与侧光高光、42 片“个”字分组柳叶竹叶、多层次太湖青石与点苔、远山淡竹及朱砂闲章。",
        "entityIds": ["knowledge-bamboo", "knowledge-integrity"],
        "tagIds": ["tag-steadfast", "tag-pure", "tag-integrity"],
        "symbols": ["bamboo"],
        "timeTheme": "四季",
    }

    return {
        "slug": "art-bamboo",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式墨竹山石模板（真实竹节轮廓、丰茂柳叶竹叶簇与太湖石点苔）",
    }


# ---------------------------------------------------------------------------
# 模板 3：《疏影寒梅·雪月清辉》
# ---------------------------------------------------------------------------


def _add_plum_blossom(
    nodes: list[dict[str, Any]],
    parent_id: str,
    prefix: str,
    cx: float,
    cy: float,
    scale: float,
    petal_color: str,
    stroke_color: str,
    rot_offset: float = 0.0,
) -> None:
    """绘制一朵饱满圆润的 5 瓣工笔梅花（12 点圆卵花瓣 + 花萼中心 + 5 枚金黄花蕊）。"""
    for i in range(5):
        ang = rot_offset + i * 72.0 - 90.0
        nodes.append(
            make_polygon(
                f"{prefix}-p{i}", parent_id,
                make_plum_petal_pts(cx, cy, 10.6 * scale, 9.2 * scale, 8.8 * scale, ang, steps=12),
                fill=petal_color, stroke=stroke_color, stroke_width=0.75, opacity=0.94
            )
        )
    # 浅红花心晕染 + 花萼点 + 金黄花蕊
    nodes.append(make_circle(f"{prefix}-blush", parent_id, cx, cy, 5.8 * scale, fill="#8D2B38", opacity=0.45))
    nodes.append(make_circle(f"{prefix}-core", parent_id, cx, cy, 3.4 * scale, fill="#6F1F2B", opacity=0.92))
    for j in range(5):
        a = rot_offset + j * 72.0 - 54.0
        rad = math.radians(a)
        nodes.append(
            make_circle(
                f"{prefix}-st{j}", parent_id,
                cx + 6.0 * scale * math.cos(rad), cy + 6.0 * scale * math.sin(rad),
                1.55 * scale, fill="#F7D574", opacity=0.96
            )
        )


def build_art_plum() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 雪月清辉（外层 moon-user 承接 moonOpacity 参数，内层 moon-group 承接渐明动画）
    nodes.append(make_group("moon-user", "painting", x=562, y=184, opacity=0.95))
    nodes.append(make_group("moon-group", "moon-user", x=0, y=0, opacity=1.0))
    nodes.append(make_circle("plum-moon-halo2", "moon-group", 0, 0, 114, fill="#EAE0CF", opacity=0.28))
    nodes.append(make_circle("plum-moon-halo1", "moon-group", 0, 0, 86, fill="#F2EAD9", opacity=0.44))
    nodes.append(make_circle("plum-moon", "moon-group", 0, 0, 60, fill="#FCF8EE", stroke="#DDD1BA", stroke_width=1.0, opacity=0.96))

    # 2. 寒山雪峰剪影（柔和连绵）
    nodes.append(
        make_polygon(
            "snow-mtn-far", "painting",
            make_mountain_pts(28, 772, 574, [(195, 134, 132), (490, 108, 145), (700, 142, 114)], steps=30),
            fill="#CBD0D3", opacity=0.34
        )
    )
    nodes.append(
        make_polygon(
            "snow-mtn-near", "painting",
            make_mountain_pts(28, 772, 574, [(120, 82, 96), (645, 92, 108)], steps=26),
            fill="#A8B0B5", opacity=0.25
        )
    )

    # 3. 苍劲虬曲古梅老干与横斜铁枝（外层 plum-user 承接倾角/缩放参数，内层 plum-tree 承接轻摇动画）
    nodes.append(make_group("plum-user", "painting", x=138, y=468, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("plum-tree", "plum-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    # 主干采用 S 型虬曲曲线，彻底摆脱生硬直杆（根部嵌入左下积雪崖石 y=476..504 之间）
    trunk_spine = catmull_rom(
        [(0, 30), (44, -24), (104, -76), (178, -110), (242, -158), (316, -186)],
        seg_steps=3,
    )
    n_t = len(trunk_spine)
    trunk_widths = [15.5 * ((1.0 - 0.72 * (i / max(1, n_t - 1))) ** 0.9) for i in range(n_t)]
    nodes.append(
        make_polygon(
            "plum-trunk", "plum-tree", make_ribbon(trunk_spine, trunk_widths),
            fill="#433834", stroke="#29221F", stroke_width=1.0, opacity=0.97
        )
    )
    trunk_hi_spine = [(px - 2.0, py - 3.0) for px, py in trunk_spine[1:-2]]
    nodes.append(make_polyline("plum-trunk-hi", "plum-tree", trunk_hi_spine, stroke="#7B6E68", stroke_width=1.8, opacity=0.45))

    # 横出穿月主枝（起点深深嵌入主干中心，无缝衔接）
    b1_spine = catmull_rom(
        [(234, -154), (318, -188), (398, -226), (484, -246), (566, -264)],
        seg_steps=3,
    )
    n_b1 = len(b1_spine)
    b1_widths = [6.4 * (1.0 - 0.82 * (i / max(1, n_b1 - 1))) for i in range(n_b1)]
    nodes.append(
        make_polygon(
            "plum-branch-east", "plum-tree", make_ribbon(b1_spine, b1_widths),
            fill="#483D38", stroke="#2B2421", stroke_width=0.85, opacity=0.96
        )
    )

    # 上挑傲骨主枝（从主干弯曲处自然分叉，带遒劲折势）
    b2_spine = catmull_rom(
        [(168, -108), (192, -172), (236, -242), (272, -304), (318, -356)],
        seg_steps=3,
    )
    n_b2 = len(b2_spine)
    b2_widths = [7.4 * (1.0 - 0.84 * (i / max(1, n_b2 - 1))) for i in range(n_b2)]
    nodes.append(
        make_polygon(
            "plum-branch-up", "plum-tree", make_ribbon(b2_spine, b2_widths),
            fill="#453A36", stroke="#29221F", stroke_width=0.85, opacity=0.96
        )
    )

    # 下垂回环枝（无缝从穿月枝中段垂出）
    b3_spine = catmull_rom(
        [(366, -212), (420, -186), (476, -152), (534, -124)],
        seg_steps=3,
    )
    n_b3 = len(b3_spine)
    b3_widths = [4.0 * (1.0 - 0.76 * (i / max(1, n_b3 - 1))) for i in range(n_b3)]
    nodes.append(
        make_polygon(
            "plum-branch-drop", "plum-tree", make_ribbon(b3_spine, b3_widths),
            fill="#4F433E", opacity=0.94
        )
    )

    # 纤细横斜梅梢（增强国画“瘦硬”线条感）
    twigs = [
        ("plum-twig-1", [(204, -192), (168, -238), (132, -278)]),
        ("plum-twig-2", [(262, -288), (316, -314), (364, -326)]),
        ("plum-twig-3", [(432, -234), (472, -282), (512, -312)]),
        ("plum-twig-4", [(116, -82), (168, -68), (222, -74)]),
        ("plum-twig-5", [(448, -168), (484, -184), (522, -188)]),
    ]
    for tid, pts in twigs:
        nodes.append(make_polyline(tid, "plum-tree", pts, stroke="#3C322E", stroke_width=1.7, opacity=0.9))

    # 4. 错落盛放的 9 朵丰润五瓣工笔梅花 + 9 枚含苞花蕾
    blossoms = [
        ("flw-1", 286, -174, 1.24, 14.0),
        ("flw-2", 424, -236, 1.32, 30.0),
        ("flw-3", 518, -254, 1.02, 8.0),
        ("flw-4", 238, -246, 1.18, 44.0),
        ("flw-5", 304, -338, 0.96, 18.0),
        ("flw-6", 468, -156, 1.12, 26.0),
        ("flw-7", 162, -246, 0.94, 52.0),
        ("flw-8", 190, -72, 1.04, 22.0),
        ("flw-9", 362, -214, 0.92, 64.0),
    ]
    for bid, bx, by, bsc, brot in blossoms:
        _add_plum_blossom(nodes, "plum-tree", bid, bx, by, bsc, "#C94A5A", "#8B2736", brot)

    buds = [
        (558, -262, 5.4), (502, -304, 4.8), (358, -324, 4.6),
        (526, -126, 5.2), (136, -274, 4.6), (218, -74, 4.5),
        (514, -188, 4.6), (206, -126, 5.0), (474, -244, 4.5),
    ]
    for idx, (ux, uy, ur) in enumerate(buds):
        nodes.append(make_circle(f"plum-bud-{idx}", "plum-tree", ux, uy, ur, fill="#C94A5A", stroke="#7D2432", stroke_width=0.9, opacity=0.95))

    # 5. 左下角前景积雪崖石（自然承接古梅老干，不再悬空！）
    nodes.append(
        make_polygon(
            "plum-crag-main", "painting",
            catmull_rom([(28, 574), (36, 514), (72, 482), (124, 476), (186, 496), (242, 536), (274, 574)], seg_steps=2),
            fill="#7D8285", stroke="#52575B", stroke_width=1.2, opacity=0.94
        )
    )
    nodes.append(
        make_polygon(
            "plum-crag-snow", "painting",
            catmull_rom([(44, 512), (76, 484), (126, 478), (176, 496), (146, 514), (92, 510)], seg_steps=2),
            fill="#F4F1EA", opacity=0.88
        )
    )

    # 6. 空中轻盈飘落的 4 片梅瓣
    nodes.append(make_group("falling-petals", "painting", x=0, y=0, opacity=0.88))
    falling = [
        (468, 396, 8.8, 6.2, 38),
        (542, 434, 8.0, 5.6, -24),
        (618, 368, 8.4, 5.8, 62),
        (386, 448, 7.6, 5.4, 14),
    ]
    for idx, (fx, fy, rx, ry, fang) in enumerate(falling):
        nodes.append(
            make_polygon(
                f"fall-petal-{idx}", "falling-petals",
                make_plum_petal_pts(fx, fy, 0, rx, ry, fang, steps=10),
                fill="#D46674", stroke="#9B3846", stroke_width=0.6, opacity=0.86
            )
        )

    # 7. 左上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 56, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 303,
        "canvas": {"width": 800, "height": 600, "background": "#F3F0E9"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "moon-group", "property": "opacity", "from": 0.55, "to": 1.0, "startMs": 0, "endMs": 2200, "easing": "linear"},
            {"nodeId": "plum-tree", "property": "rotation", "from": -1.0, "to": 1.0, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "falling-petals", "property": "y", "from": -12, "to": 28, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "falling-petals", "property": "x", "from": -8, "to": 14, "startMs": 0, "endMs": 4000, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "寒月照雪：远峰积素、冷月清辉，以留白营造清旷意境。", "entityIds": ["knowledge-plum"]},
            {"timeMs": 2000, "caption": "铁干疏影：老梅横斜穿月，红梅五瓣清雅绽放。", "entityIds": ["knowledge-plum", "knowledge-integrity"]},
            {"timeMs": 3800, "caption": "只留清气：梅瓣微落、暗香浮动，不慕浮华唯留清气。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "blossomColor", "label": "梅花主色", "description": "梅花花瓣颜色（朱砂红梅 / 素白雪梅 / 宫粉梅）", "type": "color", "default": "#C94A5A"},
        {"name": "branchColor", "label": "老干墨色", "description": "梅树主干与枝条墨色深浅", "type": "color", "default": "#433834"},
        {"name": "moonColor", "label": "明月色调", "description": "明月主色（暖金月 / 清冷素月）", "type": "color", "default": "#FCF8EE"},
        {"name": "blossomScale", "label": "花枝疏朗度", "description": "整体调整梅枝构图比例", "type": "number", "default": 1.0, "min": 0.85, "max": 1.03, "step": 0.02},
        {"name": "moonOpacity", "label": "月辉明度", "description": "调节明月与月晕透明度（0.15～1.0）", "type": "number", "default": 0.95, "min": 0.15, "max": 1.0, "step": 0.05},
        {"name": "branchTilt", "label": "梅枝倾角", "description": "微调梅树横斜倾角（度）", "type": "integer", "default": 0, "min": -3, "max": 3, "step": 1},
    ]

    bindings = [
        *[
            {"parameter": "blossomColor", "nodeId": f"{bid}-p{i}", "property": "style.fill"}
            for bid, *_ in blossoms
            for i in range(5)
        ],
        *[{"parameter": "blossomColor", "nodeId": f"plum-bud-{i}", "property": "style.fill"} for i in range(len(buds))],
        {"parameter": "branchColor", "nodeId": "plum-trunk", "property": "style.fill"},
        {"parameter": "branchColor", "nodeId": "plum-branch-east", "property": "style.fill"},
        {"parameter": "branchColor", "nodeId": "plum-branch-up", "property": "style.fill"},
        {"parameter": "moonColor", "nodeId": "plum-moon", "property": "style.fill"},
        {"parameter": "blossomScale", "nodeId": "plum-user", "property": "transform.scaleX"},
        {"parameter": "blossomScale", "nodeId": "plum-user", "property": "transform.scaleY"},
        {"parameter": "moonOpacity", "nodeId": "moon-user", "property": "style.opacity"},
        {"parameter": "branchTilt", "nodeId": "plum-user", "property": "transform.rotation"},
    ]

    metadata = {
        "name": "疏影寒梅·雪月清辉",
        "description": "高精度新中式雪月梅花图：虬曲变宽笔势描绘古梅老干铁枝并扎根积雪崖石，配九朵工笔五瓣红梅与金黄花蕊、空中落瓣、清辉冷月及朱文闲章。",
        "entityIds": ["knowledge-plum", "knowledge-integrity"],
        "tagIds": ["tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["narrative"],
        "timeTheme": "冬夜",
    }

    return {
        "slug": "art-plum",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式雪月寒梅模板（虬曲老干、积雪崖石、九朵五瓣工笔梅花与落瓣动画）",
    }


def verify_template(spec: dict[str, Any]) -> tuple[int, int]:
    """用后端真实校验与采样引擎严格检查每个模板（含边界参数）。"""
    TemplateMetadata.model_validate(spec["metadata"])
    ir = DrawingIR.model_validate(spec["irTemplate"])
    param_adapter = TypeAdapter(ParameterDefinition)
    constraint_adapter = TypeAdapter(Constraint)
    schema = [param_adapter.validate_python(p) for p in spec["parameterSchema"]]
    bindings = [Binding.model_validate(b) for b in spec["bindings"]]
    constraints = [constraint_adapter.validate_python(c) for c in spec["constraints"]]

    report = validate_ir(ir)
    if report.issues or report.outOfBoundsNodeCount:
        raise AssertionError(
            f"{spec['slug']} 基础 IR 校验失败：outOfBounds={report.outOfBoundsNodeCount}, issues={report.issues}"
        )

    defaults = {p.name: p.default for p in schema}
    min_params = {p.name: (p.min if getattr(p, "min", None) is not None else p.default) for p in schema}
    max_params = {p.name: (p.max if getattr(p, "max", None) is not None else p.default) for p in schema}
    for label, params in [("default", defaults), ("min", min_params), ("max", max_params)]:
        outcome = instantiate(ir, schema, bindings, constraints, params)
        if not outcome.ok:
            raise AssertionError(f"{spec['slug']} ({label}) 参数实例化失败：{outcome.issues}")
        inst_ir = outcome.ir
        inst_report = validate_ir(inst_ir)
        if inst_report.issues or inst_report.outOfBoundsNodeCount:
            raise AssertionError(
                f"{spec['slug']} ({label}) 实例化校验失败：outOfBounds={inst_report.outOfBoundsNodeCount}, issues={inst_report.issues}"
            )
        for t in (0, 2000, 4000):
            sample_scene(inst_ir, t)

    # 逐参数核验：修改任意单个参数后，在 t=0 与 t=2000 采样结果均须发生真实变化（防止被动画覆盖成“死参数”）
    def _scene_sig(sampled) -> list[tuple]:
        return [
            (
                n.id,
                round(n.opacity, 4),
                n.style.get("fill"),
                n.style.get("stroke"),
                round(float(n.style.get("strokeWidth") or 0), 3),
                [(round(x, 2), round(y, 2)) for x, y in n.points[:3]],
            )
            for n in sampled.nodes
        ]

    for param in schema:
        if param.type == "color":
            val_a, val_b = "#112233", "#AABBCC"
        elif getattr(param, "min", None) is not None and getattr(param, "max", None) is not None:
            val_a, val_b = param.min, param.max
        else:
            continue
        p_a = dict(defaults, **{param.name: val_a})
        p_b = dict(defaults, **{param.name: val_b})
        ir_a = instantiate(ir, schema, bindings, constraints, p_a).ir
        ir_b = instantiate(ir, schema, bindings, constraints, p_b).ir
        for t in (0, 2000):
            if _scene_sig(sample_scene(ir_a, t)) == _scene_sig(sample_scene(ir_b, t)):
                raise AssertionError(
                    f"{spec['slug']} 参数「{param.name}」在 t={t}ms 时对采样画面无效果（可能被同节点动画覆盖）"
                )

    total_vertices = sum(
        len(n["geometry"]["points"])
        for n in spec["irTemplate"]["nodes"]
        if n["type"] in ("polygon", "polyline")
    )
    node_count = len(spec["irTemplate"]["nodes"])
    if node_count > 190 or total_vertices > 1950:
        raise AssertionError(f"{spec['slug']} 预算超限：nodes={node_count}, vertices={total_vertices}")
    return node_count, total_vertices


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    builders = [build_art_lotus, build_art_bamboo, build_art_plum]
    for fn in builders:
        spec = fn()
        nodes_n, verts_n = verify_template(spec)
        out_path = OUT_DIR / f"{spec['slug']}.json"
        out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"[OK] {spec['metadata']['name']} ({out_path.relative_to(PROJECT_ROOT)}): "
            f"nodes={nodes_n}, vertices={verts_n}, bindings={len(spec['bindings'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
