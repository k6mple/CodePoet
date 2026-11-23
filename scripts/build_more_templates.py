"""生成 3 套全新主题国画模板 + 4 套经典原有模板的美化升级版。

全新模板（shared/demo/templates/art/）：
  - art-orchid.json       —— 《幽谷空兰·清芬守拙》（四君子之兰）
  - art-pine.json         —— 《青松立崖·千仞不移》（岁寒青松与云海朝阳）
  - art-water-boat.json   —— 《上善若水·烟波孤舟》（青绿山水与乌篷孤舟）

原有 4 套模板的美化升级版（保留原容器 ID、原参数名/范围/约束，全面升级造型与配色）：
  - upgrade-lotus.json     -> tpl-lotus（《莲》宋韵团扇清莲）
  - upgrade-bamboo.json    -> tpl-bamboo（《竹·守正》三竿清风墨竹）
  - upgrade-narrative.json -> tpl-narrative（《四秒·清莲叙事》晨光莲竹叙事卷）
  - upgrade-ink-plum.json  -> tpl-1c48c20b38e742e8b9b6762d0c049ac8（《墨梅·清风》王冕水墨倒垂梅）
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_art_templates import (  # noqa: E402
    OUT_DIR,
    Point,
    _add_bamboo_culm,
    _add_plum_blossom,
    catmull_rom,
    cubic_bezier,
    make_bamboo_leaf_pts,
    make_circle,
    make_ellipse_pts,
    make_frame_nodes,
    make_group,
    make_lotus_leaf_pts,
    make_lotus_petal_pts,
    make_mist_cloud_pts,
    make_mountain_pts,
    make_plum_petal_pts,
    make_polygon,
    make_polyline,
    make_ribbon,
    make_seal_nodes,
    make_water_wave_pts,
    rotate_translate,
    verify_template,
)


# ---------------------------------------------------------------------------
# 专用造型辅助：兰花、松针冠层、飞鹤、垂柳
# ---------------------------------------------------------------------------


def make_orchid_petal_pts(
    cx: float, cy: float, length: float, width: float, angle_deg: float, curve: float = 0.0, steps: int = 7
) -> list[Point]:
    """清雅狭长兰花瓣（两头尖细、中段柔婉）。"""
    hw = width / 2.0
    left = cubic_bezier((0, 0), (length * 0.3, -hw + curve * 0.3), (length * 0.72, -hw * 0.65 + curve * 0.75), (length, curve), steps)
    right = cubic_bezier((length, curve), (length * 0.72, hw * 0.65 + curve * 0.75), (length * 0.3, hw + curve * 0.3), (0, 0), steps)
    return rotate_translate(left[:-1] + right[:-1], cx, cy, angle_deg)


def _add_orchid_blossom(
    nodes: list[dict[str, Any]],
    parent_id: str,
    prefix: str,
    cx: float,
    cy: float,
    scale: float,
    petal_color: str,
    lip_color: str,
    tilt: float = 0.0,
) -> list[str]:
    """一朵 5 瓣春兰（3 片外萼舒展 + 2 片内捧瓣 + 1 片绛红卷唇瓣）。返回外瓣 ID 列表。"""
    petal_ids: list[str] = []
    # 3 片外萼（上、左下、右下）
    sepals = [
        (-90 + tilt, 28 * scale, 8.2 * scale, 2.5 * scale),
        (-152 + tilt, 26 * scale, 7.6 * scale, -3.0 * scale),
        (-28 + tilt, 26 * scale, 7.6 * scale, 3.0 * scale),
    ]
    for i, (ang, ln, wd, cv) in enumerate(sepals):
        pid = f"{prefix}-sep-{i}"
        petal_ids.append(pid)
        nodes.append(
            make_polygon(
                pid, parent_id, make_orchid_petal_pts(cx, cy, ln, wd, ang, curve=cv, steps=6),
                fill=petal_color, stroke="#6E8262", stroke_width=0.75, opacity=0.95
            )
        )
    # 2 片内捧瓣（略短、微合）
    for i, (ang, ln, wd, cv) in enumerate([(-118 + tilt, 20 * scale, 7.0 * scale, 1.5 * scale), (-62 + tilt, 20 * scale, 7.0 * scale, -1.5 * scale)]):
        pid = f"{prefix}-in-{i}"
        petal_ids.append(pid)
        nodes.append(
            make_polygon(
                pid, parent_id, make_orchid_petal_pts(cx, cy, ln, wd, ang, curve=cv, steps=5),
                fill=petal_color, stroke="#7B8E6E", stroke_width=0.65, opacity=0.92
            )
        )
    # 唇瓣（花心点睛）
    nodes.append(
        make_polygon(
            f"{prefix}-lip", parent_id,
            make_orchid_petal_pts(cx, cy, 17 * scale, 11.5 * scale, 90 + tilt, curve=1.2 * scale, steps=6),
            fill=lip_color, stroke="#7D2930", stroke_width=0.7, opacity=0.96
        )
    )
    nodes.append(make_circle(f"{prefix}-core", parent_id, cx, cy, 2.4 * scale, fill="#F3D88B", opacity=0.95))
    return petal_ids


def make_pine_canopy_pts(cx: float, cy: float, rx: float, ry: float, lobes: int = 9, steps: int = 34) -> list[Point]:
    """国画扁云伞状松针簇轮廓（上拱下平、边缘呈细腻松针锯齿波纹）。"""
    pts: list[Point] = []
    for i in range(steps):
        a = 2.0 * math.pi * i / steps
        # 上半部拱起呈松针波浪，下半部略收平形成经典迎客松平底冠层
        is_top = math.sin(a) < 0.15
        ripple = (0.11 * math.sin(lobes * a) + 0.05 * math.cos((lobes * 2) * a)) if is_top else 0.03 * math.sin(lobes * a)
        sy = ry * (1.0 if math.sin(a) < 0 else 0.48)
        pts.append((cx + rx * (1.0 + ripple) * math.cos(a), cy + sy * (1.0 + ripple) * math.sin(a)))
    return pts


def make_crane_pts(cx: float, cy: float, scale: float = 1.0, flip: bool = False) -> list[Point]:
    """极简水墨远空仙鹤剪影（12 点平滑单多边形）。"""
    s = -scale if flip else scale
    raw = [
        (-18, -2), (-8, -8), (-2, -16), (4, -10), (14, -12), (24, -6),
        (14, -4), (6, 0), (12, 8), (3, 4), (-6, 2), (-14, 2),
    ]
    return [(cx + x * s, cy + y * scale) for x, y in raw]


# ---------------------------------------------------------------------------
# 新增模板 4：《幽谷空兰·清芬守拙》
# ---------------------------------------------------------------------------


def build_art_orchid() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 幽谷烟岚与远崖（mist-user 承接 mistOpacity 参数，mist-group 承接渐明动画）
    nodes.append(make_group("mist-user", "painting", x=0, y=0, opacity=0.86))
    nodes.append(make_group("mist-group", "mist-user", x=0, y=0, opacity=1.0))
    nodes.append(
        make_polygon(
            "valley-mtn-far", "mist-group",
            make_mountain_pts(28, 772, 480, [(145, 210, 115), (655, 195, 125)], steps=30),
            fill="#C7CFC7", opacity=0.3
        )
    )
    nodes.append(
        make_polygon(
            "valley-mtn-mid", "mist-group",
            make_mountain_pts(28, 772, 520, [(95, 155, 92), (420, 62, 130), (710, 138, 95)], steps=28),
            fill="#9EB0A3", opacity=0.22
        )
    )
    nodes.append(make_polygon("valley-mist-1", "mist-group", make_mist_cloud_pts(310, 468, 220, 36, steps=22), fill="#F5F2EA", opacity=0.68))
    nodes.append(make_polygon("valley-mist-2", "mist-group", make_mist_cloud_pts(560, 442, 175, 30, steps=20), fill="#F5F2EA", opacity=0.62))

    # 2. 幽兰主体组（锚点位于石畔兰根 (352, 488)）
    nodes.append(make_group("orchid-user", "painting", x=352, y=488, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("orchid-sway", "orchid-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    # 9 片飘逸撇捺兰叶（长弧变宽笔触，国画“撇兰”章法：交凤眼、破凤眼）
    leaf_specs = [
        # (ctrl_pts, base_w, color, opacity, is_main)
        ([(0, 0), (-58, -110), (-154, -172), (-238, -142)], 5.6, "#355C45", 0.94, True),
        ([(-4, 0), (-36, -138), (-106, -242), (-186, -264)], 5.2, "#467056", 0.90, True),
        ([(4, 0), (-14, -158), (-42, -296), (-98, -342)], 4.8, "#2B4D39", 0.95, True),
        ([(6, 0), (28, -162), (82, -288), (154, -324)], 5.0, "#355C45", 0.94, True),
        ([(8, 0), (62, -134), (156, -214), (248, -206)], 5.6, "#2E533D", 0.94, True),
        ([(10, 2), (82, -92), (186, -132), (266, -92)], 5.2, "#4E785D", 0.88, False),
        ([(-8, 2), (-86, -74), (-178, -96), (-236, -54)], 4.8, "#527B61", 0.86, False),
        ([(2, 0), (36, -124), (114, -182), (182, -156)], 4.2, "#618B6F", 0.76, False),
        ([(-2, 0), (-42, -112), (-124, -158), (-188, -124)], 4.0, "#668F73", 0.74, False),
    ]
    main_leaf_ids: list[str] = []
    for idx, (ctrl, bw, col, op, is_main) in enumerate(leaf_specs):
        spine = cubic_bezier(ctrl[0], ctrl[1], ctrl[2], ctrl[3], 10)
        # 兰叶基部微细、中段略丰、叶尖收锋如针
        widths = [bw * (0.58 + 0.55 * math.sin(math.pi * (i / 10)) ** 0.7) * (1.0 - 0.88 * ((i / 10) ** 1.6)) for i in range(11)]
        lid = f"oleaf-{idx}"
        if is_main:
            main_leaf_ids.append(lid)
        nodes.append(
            make_polygon(
                lid, "orchid-sway", make_ribbon(spine, widths),
                fill=col, stroke="#1F3B2A", stroke_width=0.55, opacity=op
            )
        )

    # 2 枝纤细幽兰花葶（从叶丛间亭亭抽出）
    stalk1 = cubic_bezier((2, -8), (12, -112), (42, -212), (76, -296), 8)
    nodes.append(make_polygon("orchid-stalk-1", "orchid-sway", make_ribbon(stalk1, [2.4 - 0.9 * (i / 8) for i in range(9)]), fill="#557A58", opacity=0.94))
    stalk2 = cubic_bezier((-4, -6), (-24, -92), (-54, -172), (-88, -234), 7)
    nodes.append(make_polygon("orchid-stalk-2", "orchid-sway", make_ribbon(stalk2, [2.1 - 0.8 * (i / 7) for i in range(8)]), fill="#5D8260", opacity=0.92))

    # 5 朵素心春兰花（疏朗清芬）
    all_petal_ids: list[str] = []
    all_lip_ids: list[str] = []
    blossom_specs = [
        ("orch-1", 76, -298, 1.12, 16.0),
        ("orch-2", 52, -228, 1.02, 24.0),
        ("orch-3", 28, -162, 0.92, 14.0),
        ("orch-4", -88, -236, 1.06, -22.0),
        ("orch-5", -64, -172, 0.94, -14.0),
    ]
    for bid, bx, by, bsc, btilt in blossom_specs:
        pids = _add_orchid_blossom(nodes, "orchid-sway", bid, bx, by, bsc, "#DCE6CC", "#B84B52", tilt=btilt)
        all_petal_ids.extend(pids)
        all_lip_ids.append(f"{bid}-lip")

    # 3. 幽谷苔石坡岸（稳托兰根，底部平滑贴地）
    nodes.append(
        make_polygon(
            "orchid-ground", "painting",
            [(36, 568), (144, 536), (320, 524), (512, 534), (748, 568)],
            fill="#E3DDD0", opacity=0.68
        )
    )
    nodes.append(
        make_polygon(
            "orchid-rock-main", "painting",
            catmull_rom([(182, 562), (206, 514), (256, 480), (326, 470), (398, 478), (456, 504), (496, 558)], seg_steps=2),
            fill="#848D85", stroke="#4C564F", stroke_width=1.2, opacity=0.96
        )
    )
    nodes.append(
        make_polygon(
            "orchid-rock-hi", "painting",
            catmull_rom([(212, 516), (260, 486), (326, 476), (382, 488), (336, 514), (262, 522)], seg_steps=2),
            fill="#B1B9B0", opacity=0.64
        )
    )
    for idx, (mx, my, rx, ry, ang) in enumerate([(244, 496, 15, 5.5, -18), (336, 484, 18, 6.0, 4), (422, 506, 16, 5.5, 18)]):
        nodes.append(make_polygon(f"orchid-moss-{idx}", "painting", make_ellipse_pts(mx, my, rx, ry, ang, steps=12), fill="#4E7358", opacity=0.72))

    # 4. 右上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 714, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 404,
        "canvas": {"width": 800, "height": 600, "background": "#F5F2EA"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "orchid-sway", "property": "rotation", "from": -1.6, "to": 1.6, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "mist-group", "property": "opacity", "from": 0.65, "to": 1.0, "startMs": 0, "endMs": 3200, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "空谷生幽：烟岚浮动于幽谷青石之间，不求喧哗自得清境。", "entityIds": ["knowledge-integrity"]},
            {"timeMs": 2000, "caption": "修叶舒展：兰叶撇捺如剑、刚柔相济，素心春兰静放。", "entityIds": ["knowledge-integrity"]},
            {"timeMs": 3800, "caption": "清芬守拙：不为无人而不芳，恰喻慎独守正的清廉品格。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "orchidColor", "label": "兰花素瓣色", "description": "春兰花瓣颜色（玉青素兰 / 象牙白兰 / 淡粉兰）", "type": "color", "default": "#DCE6CC"},
        {"name": "lipColor", "label": "花心绛唇色", "description": "兰花中心唇瓣点睛色", "type": "color", "default": "#B84B52"},
        {"name": "leafColor", "label": "兰叶墨青色", "description": "前景主兰叶墨绿色调", "type": "color", "default": "#355C45"},
        {"name": "orchidScale", "label": "兰丛舒展度", "description": "整体缩放兰丛与花枝", "type": "number", "default": 1.0, "min": 0.82, "max": 1.06, "step": 0.02},
        {"name": "breezeAngle", "label": "幽谷风斜角", "description": "调节兰丛迎风倾角（度）", "type": "integer", "default": 0, "min": -6, "max": 6, "step": 1},
        {"name": "mistOpacity", "label": "山谷烟岚", "description": "远崖与山谷雾岚浓度（0.15～1.0）", "type": "number", "default": 0.86, "min": 0.15, "max": 1.0, "step": 0.05},
    ]

    bindings = [
        *[{"parameter": "orchidColor", "nodeId": pid, "property": "style.fill"} for pid in all_petal_ids],
        *[{"parameter": "lipColor", "nodeId": lid, "property": "style.fill"} for lid in all_lip_ids],
        *[{"parameter": "leafColor", "nodeId": lid, "property": "style.fill"} for lid in main_leaf_ids],
        {"parameter": "orchidScale", "nodeId": "orchid-user", "property": "transform.scaleX"},
        {"parameter": "orchidScale", "nodeId": "orchid-user", "property": "transform.scaleY"},
        {"parameter": "breezeAngle", "nodeId": "orchid-user", "property": "transform.rotation"},
        {"parameter": "mistOpacity", "nodeId": "mist-user", "property": "style.opacity"},
    ]

    metadata = {
        "name": "幽谷空兰·清芬守拙",
        "description": "高精度新中式幽兰图（四君子之兰）：飘逸变宽笔势描绘九片撇捺兰叶、双枝花葶与五朵五瓣素心春兰，辅以幽谷苔石、远崖烟岚与朱文闲章，喻示“不为无人而不芳”的慎独操守。",
        "entityIds": ["knowledge-integrity"],
        "tagIds": ["tag-self-discipline", "tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["narrative"],
        "timeTheme": "春日",
    }

    return {
        "slug": "art-orchid",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式幽谷空兰模板（变宽撇捺兰叶、五朵素心春兰与幽谷烟岚）",
    }


# ---------------------------------------------------------------------------
# 新增模板 5：《青松立崖·千仞不移》
# ---------------------------------------------------------------------------


def build_art_pine() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 丹霞朝阳与暖金霞晕
    nodes.append(make_group("sun-group", "painting", x=596, y=148, opacity=0.95))
    nodes.append(make_circle("pine-sun-halo2", "sun-group", 0, 0, 96, fill="#F1CFAF", opacity=0.26))
    nodes.append(make_circle("pine-sun-halo1", "sun-group", 0, 0, 66, fill="#EBBC94", opacity=0.40))
    nodes.append(make_circle("pine-sun", "sun-group", 0, 0, 42, fill="#DF764F", stroke="#BA5634", stroke_width=0.9, opacity=0.92))

    # 2. 层叠远峰与流动云海（cloud-user 承接 cloudOpacity 参数，cloud-drift 承接横移云海动画）
    nodes.append(
        make_polygon(
            "pine-mtn-far", "painting",
            make_mountain_pts(28, 772, 472, [(240, 170, 118), (475, 196, 130), (695, 152, 105)], steps=32),
            fill="#BAC6C3", opacity=0.36
        )
    )
    nodes.append(
        make_polygon(
            "pine-mtn-mid", "painting",
            make_mountain_pts(28, 772, 520, [(340, 138, 108), (585, 148, 118)], steps=28),
            fill="#8EA19E", opacity=0.30
        )
    )
    nodes.append(make_group("cloud-user", "painting", x=0, y=0, opacity=0.88))
    nodes.append(make_group("cloud-drift", "cloud-user", x=0, y=0, opacity=1.0))
    clouds = [
        ("cloud-1", 310, 446, 185, 32, 0.72),
        ("cloud-2", 545, 472, 200, 34, 0.74),
        ("cloud-3", 430, 514, 210, 32, 0.68),
        ("cloud-4", 635, 392, 128, 22, 0.56),
    ]
    for cid, cx, cy, rx, ry, cop in clouds:
        nodes.append(make_polygon(cid, "cloud-drift", make_mist_cloud_pts(cx, cy, rx, ry, steps=20), fill="#F5F1E7", opacity=cop))

    # 3. 远空双仙鹤（在朝阳金晕旁自由振翅，不碰松冠）
    nodes.append(make_group("cranes-group", "painting", x=0, y=0, opacity=0.9))
    nodes.append(make_polygon("crane-1", "cranes-group", make_crane_pts(550, 150, 0.92), fill="#33393B", opacity=0.86))
    nodes.append(make_polygon("crane-2", "cranes-group", make_crane_pts(584, 198, 0.72), fill="#4B5356", opacity=0.78))

    # 4. 迎客古松组（锚点位于崖顶松根 (176, 422)）
    nodes.append(make_group("pine-user", "painting", x=176, y=422, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("pine-sway", "pine-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    # 盘曲苍劲主干（从左下崖石斜盘而上、再右探迎客）
    trunk_spine = catmull_rom(
        [(0, 42), (-14, -22), (24, -84), (78, -134), (118, -206), (176, -242)],
        seg_steps=3,
    )
    nt = len(trunk_spine)
    trunk_w = [19.0 * ((1.0 - 0.68 * (i / max(1, nt - 1))) ** 0.85) for i in range(nt)]
    nodes.append(
        make_polygon(
            "pine-trunk", "pine-sway", make_ribbon(trunk_spine, trunk_w),
            fill="#564439", stroke="#30251F", stroke_width=1.1, opacity=0.98
        )
    )
    # 树皮受光肌理线
    nodes.append(
        make_polyline(
            "pine-bark-hi", "pine-sway",
            [(x - 3.5, y - 2.0) for x, y in trunk_spine[1:-2]],
            stroke="#8B7363", stroke_width=2.0, opacity=0.48
        )
    )

    # 迎客横探主枝（向右长舒）
    b1_spine = catmull_rom([(72, -132), (164, -154), (262, -148), (348, -158)], seg_steps=3)
    b1_w = [9.2 * (1.0 - 0.78 * (i / max(1, len(b1_spine) - 1))) for i in range(len(b1_spine))]
    nodes.append(
        make_polygon(
            "pine-bough-right", "pine-sway", make_ribbon(b1_spine, b1_w),
            fill="#59473B", stroke="#30251F", stroke_width=0.95, opacity=0.97
        )
    )
    # 左侧护崖枝
    b2_spine = catmull_rom([(26, -86), (-36, -118), (-96, -124)], seg_steps=3)
    b2_w = [7.4 * (1.0 - 0.72 * (i / max(1, len(b2_spine) - 1))) for i in range(len(b2_spine))]
    nodes.append(
        make_polygon(
            "pine-bough-left", "pine-sway", make_ribbon(b2_spine, b2_w),
            fill="#544237", stroke="#2E231D", stroke_width=0.9, opacity=0.96
        )
    )
    # 顶部高枝
    b3_spine = catmull_rom([(116, -204), (192, -236), (266, -242)], seg_steps=3)
    b3_w = [6.2 * (1.0 - 0.76 * (i / max(1, len(b3_spine) - 1))) for i in range(len(b3_spine))]
    nodes.append(
        make_polygon(
            "pine-bough-top", "pine-sway", make_ribbon(b3_spine, b3_w),
            fill="#564439", stroke="#30251F", stroke_width=0.85, opacity=0.96
        )
    )

    # 7 层伞状松针冠层（每层含暗绿底影 + 苍翠主松针 + 朝阳高光顶边，层次丰富！）
    canopies = [
        ("can-left", -82, -134, 72, 34, 9),
        ("can-mid-l", 34, -186, 86, 40, 10),
        ("can-top", 162, -264, 94, 42, 11),
        ("can-top-r", 268, -250, 74, 34, 9),
        ("can-arm-1", 184, -162, 84, 36, 10),
        ("can-arm-2", 294, -166, 90, 38, 10),
        ("can-arm-tip", 364, -158, 62, 28, 8),
    ]
    canopy_main_ids: list[str] = []
    for cid, cx, cy, rx, ry, lobes in canopies:
        nodes.append(
            make_polygon(
                f"{cid}-shadow", "pine-sway",
                make_pine_canopy_pts(cx + 3, cy + 7, rx * 0.96, ry * 0.88, lobes=lobes, steps=26),
                fill="#1D3829", opacity=0.34
            )
        )
        mid_id = f"{cid}-main"
        canopy_main_ids.append(mid_id)
        nodes.append(
            make_polygon(
                mid_id, "pine-sway",
                make_pine_canopy_pts(cx, cy, rx, ry, lobes=lobes, steps=30),
                fill="#2E533E", stroke="#1B3526", stroke_width=1.0, opacity=0.96
            )
        )
        nodes.append(
            make_polygon(
                f"{cid}-hi", "pine-sway",
                make_pine_canopy_pts(cx + 4, cy - 5, rx * 0.72, ry * 0.56, lobes=lobes - 1, steps=22),
                fill="#558066", opacity=0.45
            )
        )

    # 5. 前景千仞陡崖（稳稳托住松树根部）
    nodes.append(
        make_polygon(
            "pine-cliff-main", "painting",
            catmull_rom([(28, 574), (28, 434), (82, 412), (168, 420), (242, 444), (294, 504), (324, 574)], seg_steps=2),
            fill="#5C6567", stroke="#383F41", stroke_width=1.3, opacity=0.98
        )
    )
    nodes.append(
        make_polygon(
            "pine-cliff-light", "painting",
            catmull_rom([(36, 442), (86, 418), (166, 426), (222, 448), (178, 488), (94, 496)], seg_steps=2),
            fill="#889293", opacity=0.58
        )
    )
    nodes.append(
        make_polygon(
            "pine-cliff-dark", "painting",
            catmull_rom([(178, 488), (222, 448), (282, 506), (314, 570), (156, 570)], seg_steps=2),
            fill="#3D4548", opacity=0.62
        )
    )

    # 6. 左上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 56, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 505,
        "canvas": {"width": 800, "height": 600, "background": "#F3EFE6"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "cloud-drift", "property": "x", "from": -14, "to": 14, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "cranes-group", "property": "x", "from": -16, "to": 18, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "cranes-group", "property": "y", "from": 6, "to": -8, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "pine-sway", "property": "rotation", "from": -0.8, "to": 0.8, "startMs": 0, "endMs": 4000, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "千仞立崖：云海奔涌间青松扎根危岩，巍然不移。", "entityIds": ["knowledge-integrity"]},
            {"timeMs": 2000, "caption": "丹霞映松：苍劲松盖沐朝阳而不改其青，守正浩然。", "entityIds": ["knowledge-integrity"]},
            {"timeMs": 3800, "caption": "鹤鸣清远：云舒鹤翔，寄托清朗致远的浩然正气。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "needleColor", "label": "松针主色", "description": "苍松针冠主色调（苍松绿 / 墨松黛 / 石青松）", "type": "color", "default": "#2E533E"},
        {"name": "sunColor", "label": "朝阳色调", "description": "丹霞旭日主色", "type": "color", "default": "#DF764F"},
        {"name": "cliffColor", "label": "崖石黛色", "description": "前景峭壁山崖主体颜色", "type": "color", "default": "#5C6567"},
        {"name": "pineScale", "label": "青松雄姿", "description": "整体缩放崖松冠幅", "type": "number", "default": 1.0, "min": 0.84, "max": 1.04, "step": 0.02},
        {"name": "pineTilt", "label": "松枝迎风角", "description": "微调迎客松俯仰角度（度）", "type": "integer", "default": 0, "min": -4, "max": 4, "step": 1},
        {"name": "cloudOpacity", "label": "云海明度", "description": "调节山间流云透明度（0.15～1.0）", "type": "number", "default": 0.88, "min": 0.15, "max": 1.0, "step": 0.05},
    ]

    bindings = [
        *[{"parameter": "needleColor", "nodeId": cid, "property": "style.fill"} for cid in canopy_main_ids],
        {"parameter": "sunColor", "nodeId": "pine-sun", "property": "style.fill"},
        {"parameter": "cliffColor", "nodeId": "pine-cliff-main", "property": "style.fill"},
        {"parameter": "pineScale", "nodeId": "pine-user", "property": "transform.scaleX"},
        {"parameter": "pineScale", "nodeId": "pine-user", "property": "transform.scaleY"},
        {"parameter": "pineTilt", "nodeId": "pine-user", "property": "transform.rotation"},
        {"parameter": "cloudOpacity", "nodeId": "cloud-user", "property": "style.opacity"},
    ]

    metadata = {
        "name": "青松立崖·千仞不移",
        "description": "高精度新中式崖松朝阳卷：盘曲变宽老干扎根千仞陡崖，托起七层伞状苍松针冠，配丹霞朝阳、流动画云、远空双鹤与朱文闲章。",
        "entityIds": ["knowledge-integrity"],
        "tagIds": ["tag-steadfast", "tag-symbol", "tag-integrity"],
        "symbols": ["narrative"],
        "timeTheme": "晨曦",
    }

    return {
        "slug": "art-pine",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式青松立崖模板（七层松针冠、千仞崖石、丹霞朝阳与云海双鹤）",
    }


# ---------------------------------------------------------------------------
# 新增模板 6：《上善若水·烟波孤舟》
# ---------------------------------------------------------------------------


def build_art_water_boat() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 青绿山水三层远峰
    nodes.append(
        make_polygon(
            "ss-mtn-far", "painting",
            make_mountain_pts(28, 772, 344, [(190, 146, 130), (455, 178, 145), (675, 134, 115)], steps=34),
            fill="#A6BDB8", opacity=0.40
        )
    )
    nodes.append(
        make_polygon(
            "ss-mtn-mid", "painting",
            make_mountain_pts(28, 772, 376, [(130, 108, 104), (360, 126, 118), (605, 142, 112)], steps=34),
            fill="#6B8E89", opacity=0.56
        )
    )
    # 青绿山脊石绿高光叠层
    nodes.append(
        make_polygon(
            "ss-mtn-jade", "painting",
            make_mountain_pts(170, 720, 378, [(355, 86, 86), (600, 104, 88)], steps=26),
            fill="#83A997", opacity=0.44
        )
    )

    # 2. 山腰柔雾（mist-user 承接 mistOpacity 参数）
    nodes.append(make_group("mist-user", "painting", x=0, y=0, opacity=0.88))
    nodes.append(make_group("mist-group", "mist-user", x=0, y=0, opacity=1.0))
    nodes.append(make_polygon("ss-mist-1", "mist-group", make_mist_cloud_pts(240, 368, 210, 28, steps=20), fill="#F2F4F0", opacity=0.72))
    nodes.append(make_polygon("ss-mist-2", "mist-group", make_mist_cloud_pts(565, 374, 200, 26, steps=20), fill="#F2F4F0", opacity=0.68))

    # 3. 澄明湖面与青峰倒影
    nodes.append(
        make_polygon(
            "ss-lake", "painting",
            [(28, 384), (772, 384), (772, 574), (28, 574)],
            fill="#A9C0BA", opacity=0.28
        )
    )
    nodes.append(
        make_polygon(
            "ss-reflect-1", "painting",
            [(180, 396), (360, 444), (540, 434), (660, 396)],
            fill="#6B8E89", opacity=0.14
        )
    )
    nodes.append(make_group("water-ripples", "painting", x=0, y=0, opacity=0.85))
    for idx, (x0, x1, wy, amp, ph) in enumerate([
        (64, 340, 418, 3.2, 0.0),
        (420, 734, 436, 3.0, 1.0),
        (92, 520, 476, 3.8, 0.5),
        (260, 718, 514, 3.4, 1.8),
        (118, 636, 548, 2.8, 0.9),
    ]):
        nodes.append(
            make_polyline(
                f"ss-wave-{idx}", "water-ripples",
                make_water_wave_pts(x0, x1, wy, amp, 2.5, ph, steps=16),
                stroke="#63827C", stroke_width=1.3, opacity=0.45
            )
        )

    # 4. 乌篷孤舟与蓑翁（boat-user 承接 boatShiftX，boat-glide 承接 4s 轻舟前行）
    nodes.append(make_group("boat-user", "painting", x=370, y=458, opacity=1.0))
    nodes.append(make_group("boat-glide", "boat-user", x=0, y=0, opacity=1.0))

    # 船影倒影
    nodes.append(make_polygon("boat-reflect", "boat-glide", make_ellipse_pts(0, 16, 68, 7.5, 0, steps=18), fill="#3E4C49", opacity=0.18))
    # 船尾水纹
    nodes.append(make_polyline("boat-wake-1", "boat-glide", [(-74, 8), (-118, 10), (-154, 8)], stroke="#63827C", stroke_width=1.3, opacity=0.52))
    nodes.append(make_polyline("boat-wake-2", "boat-glide", [(-68, 15), (-106, 18), (-136, 17)], stroke="#7D9B95", stroke_width=1.0, opacity=0.42))
    # 乌篷船篷
    nodes.append(
        make_polygon(
            "boat-canopy", "boat-glide",
            catmull_rom([(-26, -2), (-20, -24), (2, -28), (24, -22), (30, -2)], seg_steps=2),
            fill="#695A4C", stroke="#383029", stroke_width=1.1, opacity=0.96
        )
    )
    # 船篷竹篾细纹
    nodes.append(make_polyline("boat-rib-1", "boat-glide", [(-10, -25), (-12, -2)], stroke="#42372F", stroke_width=1.0, opacity=0.7))
    nodes.append(make_polyline("boat-rib-2", "boat-glide", [(8, -26), (10, -2)], stroke="#42372F", stroke_width=1.0, opacity=0.7))
    # 船舱暖灯一点
    nodes.append(make_circle("boat-lantern-glow", "boat-glide", 26, -12, 8.5, fill="#F3C978", opacity=0.38))
    nodes.append(make_circle("boat-lantern", "boat-glide", 26, -12, 3.0, fill="#F7D57E", opacity=0.96))
    # 船尾撑篙渔翁剪影（斗笠 + 蓑衣 + 长篙）
    nodes.append(make_polygon("boatman-body", "boat-glide", [(-42, -2), (-38, -20), (-30, -20), (-26, -2)], fill="#3E3A36", opacity=0.92))
    nodes.append(make_polygon("boatman-hat", "boat-glide", [(-47, -21), (-34, -30), (-21, -21)], fill="#8C7452", stroke="#52412A", stroke_width=0.9, opacity=0.96))
    nodes.append(make_polyline("boatman-pole", "boat-glide", [(-32, -16), (-56, 12)], stroke="#5A4A3A", stroke_width=1.5, opacity=0.88))
    # 曲翘船身（主色调绑定 boatColor）
    nodes.append(
        make_polygon(
            "boat-hull", "boat-glide",
            catmull_rom([(-74, -8), (-52, 8), (0, 12), (56, 8), (82, -10), (54, -1), (0, 1), (-50, -2)], seg_steps=2),
            fill="#3E3A36", stroke="#23201E", stroke_width=1.1, opacity=0.98
        )
    )

    # 5. 左上角前景垂柳嫩枝（从左岸枝梢探出，willow-user 承接 willowAngle，留足旋转安全边距）
    nodes.append(make_polygon("willow-bank", "painting", [(24, 26), (76, 26), (68, 86), (24, 114)], fill="#5B5449", opacity=0.82))
    nodes.append(make_group("willow-user", "painting", x=56, y=82, rotation=0, opacity=1.0))
    nodes.append(make_group("willow-sway", "willow-user", x=0, y=0, rotation=0, opacity=1.0))
    w_branch = cubic_bezier((0, 0), (42, 6), (86, 18), (132, 12), 7)
    nodes.append(make_polygon("willow-bough", "willow-sway", make_ribbon(w_branch, [4.2 - 0.45 * i for i in range(8)]), fill="#4B443B", opacity=0.92))
    # 4 条轻盈垂柳丝 + 嫩柳叶
    for idx, (sx, sy, ln) in enumerate([(32, 6, 134), (66, 14, 158), (98, 16, 132), (126, 14, 108)]):
        strand = cubic_bezier((sx, sy), (sx + 6, sy + ln * 0.4), (sx - 4, sy + ln * 0.75), (sx + 8, sy + ln), 6)
        nodes.append(make_polyline(f"willow-strand-{idx}", "willow-sway", strand, stroke="#5B7E5C", stroke_width=1.2, opacity=0.78))
        for k, frac in enumerate([0.28, 0.52, 0.76, 0.94]):
            lx = sx + (6 if k % 2 == 0 else -2)
            ly = sy + ln * frac
            ang = 54 if k % 2 == 0 else 122
            nodes.append(
                make_polygon(
                    f"wleaf-{idx}-{k}", "willow-sway",
                    make_bamboo_leaf_pts(lx, ly, 24, 5.4, ang, droop=0.14, steps=4),
                    fill="#678D66", opacity=0.84
                )
            )

    # 6. 右上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 714, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 606,
        "canvas": {"width": 800, "height": 600, "background": "#F2F4F0"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "boat-glide", "property": "x", "from": -22, "to": 26, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "water-ripples", "property": "x", "from": 6, "to": -6, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "willow-sway", "property": "rotation", "from": -1.5, "to": 1.8, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "mist-group", "property": "opacity", "from": 0.65, "to": 1.0, "startMs": 0, "endMs": 3200, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "青绿远山：远峰层叠、烟波浩渺，水色澄明如镜。", "entityIds": ["knowledge-water"]},
            {"timeMs": 2000, "caption": "孤舟不争：乌篷扁舟轻渡清波，上善若水、利万物而不争。", "entityIds": ["knowledge-water", "knowledge-integrity"]},
            {"timeMs": 3800, "caption": "淡泊明志：垂柳微拂、渔灯一点，守持清淡高远之心。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "mountainColor", "label": "青绿远山色", "description": "中景山水主色调（石青 / 黛绿 / 淡墨）", "type": "color", "default": "#6B8E89"},
        {"name": "waterColor", "label": "湖水清涟色", "description": "湖面底色水韵色调", "type": "color", "default": "#A9C0BA"},
        {"name": "boatColor", "label": "乌篷船身色", "description": "扁舟船身墨色", "type": "color", "default": "#3E3A36"},
        {"name": "boatShiftX", "label": "扁舟位置", "description": "左右移动乌篷孤舟在湖面的位置（-90～90）", "type": "integer", "default": 0, "min": -90, "max": 90, "step": 5},
        {"name": "mistOpacity", "label": "烟波雾岚", "description": "山腰柔雾透明度（0.15～1.0）", "type": "number", "default": 0.88, "min": 0.15, "max": 1.0, "step": 0.05},
        {"name": "willowAngle", "label": "垂柳风斜角", "description": "调节前景垂柳迎风摆动角度（度）", "type": "integer", "default": 0, "min": -6, "max": 6, "step": 1},
    ]

    bindings = [
        {"parameter": "mountainColor", "nodeId": "ss-mtn-mid", "property": "style.fill"},
        {"parameter": "waterColor", "nodeId": "ss-lake", "property": "style.fill"},
        {"parameter": "boatColor", "nodeId": "boat-hull", "property": "style.fill"},
        {"parameter": "boatShiftX", "nodeId": "boat-user", "property": "transform.x"},
        {"parameter": "mistOpacity", "nodeId": "mist-user", "property": "style.opacity"},
        {"parameter": "willowAngle", "nodeId": "willow-user", "property": "transform.rotation"},
    ]
    # 注意：boat-user 基准 x 为 370，若直接把 boatShiftX 绑定到 boat-user.transform.x 会覆盖 370！
    # 因此改为给 boat-user 设 min=270..max=470，或让 boat-shift 嵌套在 boat-anchor(370, 458) 内！
    # 立即在上方节点里改用清晰锚点：boat-anchor(370, 458) -> boat-user(x=0) -> boat-glide(anim x)！
    # 检查深度：painting(1) -> boat-anchor(2) -> boat-user(3) -> leaf? 不行，若再加 boat-glide 会到 5 层！
    # 更优雅：直接把 boat-user 挂在 painting 下，parameter boatPosX 默认 370（min 260, max 480），直观且仅 3 层！
    parameter_schema[3] = {
        "name": "boatPosX",
        "label": "扁舟位置",
        "description": "左右移动乌篷孤舟在湖面的水平位置（260～480）",
        "type": "integer",
        "default": 370,
        "min": 260,
        "max": 480,
        "step": 10,
    }
    bindings[3] = {"parameter": "boatPosX", "nodeId": "boat-user", "property": "transform.x"}

    metadata = {
        "name": "上善若水·烟波孤舟",
        "description": "高精度新中式青绿山水画卷：三层石青远峰、烟波柔雾、澄明湖水与青峰倒影、乌篷孤舟与蓑翁渔灯、前景拂风垂柳及朱文闲章，诠释“上善若水，利万物而不争”。",
        "entityIds": ["knowledge-water", "knowledge-integrity"],
        "tagIds": ["tag-humility", "tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["water"],
        "timeTheme": "烟雨",
    }

    return {
        "slug": "art-water-boat",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "高精度新中式青绿山水孤舟模板（青绿远峰、乌篷孤舟、垂柳与烟波动态）",
    }


# ---------------------------------------------------------------------------
# 原有模板美化 1：tpl-lotus（《莲》—— 宋韵团扇清莲图）
# 完整保留原 5 个参数与 lessThan(petalRadius, frameRadius) 约束！
# ---------------------------------------------------------------------------


def build_upgrade_lotus() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 古典团扇素绢圆底 + 外框圆环（frame 的 geometry.r 严格绑定 frameRadius！）
    nodes.append(make_circle("fan-silk-bg", "painting", 400, 270, 178, fill="#FAF6EC", stroke="#DFD5BF", stroke_width=1.2, opacity=0.92))
    nodes.append(make_circle("fan-silk-ring", "painting", 400, 270, 168, fill=None, stroke="#E6DEC9", stroke_width=0.9, opacity=0.75))
    # 团扇内柔墨远山与水波（营造清雅禅意底衬）
    nodes.append(
        make_polygon(
            "fan-mtn", "painting",
            make_mountain_pts(245, 555, 342, [(320, 62, 68), (468, 54, 72)], steps=24),
            fill="#C7D1CA", opacity=0.34
        )
    )
    # 团扇可调月轮虚框（id="frame"，原参数 frameRadius 40..120 绑定至此！）
    nodes.append(make_circle("frame", "painting", 400, 270, 80, fill="#F5E9CE", stroke="#C6B593", stroke_width=1.4, opacity=0.34))

    # 2. 水波与弧形荷茎、舒展荷叶
    for idx, (x0, x1, wy) in enumerate([(220, 580, 474), (256, 544, 502), (298, 502, 526)]):
        nodes.append(make_polyline(f"fan-wave-{idx}", "painting", make_water_wave_pts(x0, x1, wy, 3.0, 2.2, idx * 0.7, steps=14), stroke="#769083", stroke_width=1.3, opacity=0.45))

    # 荷叶茎藏于叶下
    nodes.append(make_polygon("stem-leaf-l", "painting", make_ribbon(cubic_bezier((296, 368), (308, 422), (322, 462), (336, 496), 6), [2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8]), fill="#385B45", opacity=0.88))
    nodes.append(
        make_polygon(
            "leaf-l", "painting",
            make_lotus_leaf_pts(296, 358, 98, 42, -14, notch_deg=-55, wave_amp=0.055, waves=7, steps=38),
            fill="#4A745E", stroke="#2E4F3D", stroke_width=1.2, opacity=0.92
        )
    )
    nodes.append(
        make_polygon(
            "leaf-r", "painting",
            make_lotus_leaf_pts(504, 364, 88, 36, 12, notch_deg=-125, wave_amp=0.055, waves=7, steps=34),
            fill="#3F6A55", stroke="#284637", stroke_width=1.1, opacity=0.9
        )
    )
    for idx, ang in enumerate([-152, -98, -22, 38, 112]):
        rad = math.radians(ang - 14)
        nodes.append(make_polyline(f"leaf-l-v{idx}", "painting", [(296, 358), (296 + 74 * math.cos(rad), 358 + 28 * math.sin(rad))], stroke="#8BB59C", stroke_width=0.85, opacity=0.46))

    # 主荷茎（优雅弯曲至下方水面）
    stem_spine = cubic_bezier((400, 290), (402, 366), (394, 434), (384, 502), 8)
    nodes.append(make_polygon("stem", "painting", make_ribbon(stem_spine, [3.6, 3.8, 4.0, 4.2, 4.5, 4.8, 5.0, 5.2, 5.4]), fill="#41664A", stroke="#2A4632", stroke_width=0.9, opacity=0.96))
    nodes.append(make_polygon("stem-ripple", "painting", make_ellipse_pts(384, 502, 24, 5.5, 0, steps=16), fill=None, stroke="#678275", stroke_width=1.0, opacity=0.46))

    # 3. 工笔重瓣莲花：外层 6 片尖瓣（颜色/透明度联动 petalColor & petalOpacity）+ 6 瓣柔圆重瓣（petal-1..petal-6 绑定 petalRadius！）
    for i in range(6):
        ang = i * 60.0 - 90.0
        nodes.append(
            make_polygon(
                f"outer-petal-{i}", "painting",
                make_lotus_petal_pts(400, 270, 96, 42, ang, tip_sharpness=0.20, steps=8),
                fill="#F2C7C7", stroke="#B87373", stroke_width=1.15, opacity=0.92
            )
        )
    # 6 片可调半径内重瓣（id="petal-1".."petal-6"，完美承接原 petalRadius 12..48 绑定！）
    for i in range(6):
        ang = math.radians(i * 60.0)
        cx = 400 + 44.0 * math.cos(ang)
        cy = 270 + 44.0 * math.sin(ang)
        nodes.append(
            make_circle(
                f"petal-{i + 1}", "painting", cx, cy, 28,
                fill="#F2C7C7", stroke="#B87373", stroke_width=1.2, opacity=0.92
            )
        )
    # 中心莲蓬与花蕊（id="core"，承接原 coreColor！）
    nodes.append(make_circle("core", "painting", 400, 270, 20, fill="#E8A020", stroke="#9A6A10", stroke_width=1.1, opacity=0.98))
    nodes.append(make_circle("core-inner", "painting", 400, 270, 10, fill="#F7D57E", opacity=0.72))
    for i in range(8):
        a = math.radians(i * 45.0)
        nodes.append(make_circle(f"core-seed-{i}", "painting", 400 + 25 * math.cos(a), 270 + 25 * math.sin(a), 2.3, fill="#F5C954", stroke="#9A6A10", stroke_width=0.5, opacity=0.95))

    # 4. 左上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 56, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 42,
        "canvas": {"width": 800, "height": 600, "background": "#FBF9F4"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": nodes,
        "animations": [],
        "narrative": [],
    }

    parameter_schema = [
        {"name": "petalRadius", "label": "花瓣半径", "description": "内层重瓣饱满度半径，调大花朵更丰润（需小于外框半径）", "type": "integer", "default": 28, "min": 12, "max": 48, "step": 1},
        {"name": "frameRadius", "label": "外框半径", "description": "团扇中心月晕虚框半径，需大于花瓣半径", "type": "integer", "default": 80, "min": 40, "max": 120, "step": 2},
        {"name": "petalColor", "label": "花瓣颜色", "description": "内外重瓣莲花统一填充色", "type": "color", "default": "#F2C7C7"},
        {"name": "coreColor", "label": "花心颜色", "description": "中心花蕊的填充色", "type": "color", "default": "#E8A020"},
        {"name": "petalOpacity", "label": "花瓣透明度", "description": "0.3 轻透至 1.0 浓艳", "type": "number", "default": 0.92, "min": 0.3, "max": 1.0, "step": 0.01},
    ]

    bindings = [
        {"parameter": "frameRadius", "nodeId": "frame", "property": "geometry.r"},
        *[{"parameter": "petalRadius", "nodeId": f"petal-{i}", "property": "geometry.r"} for i in range(1, 7)],
        *[{"parameter": "petalColor", "nodeId": f"petal-{i}", "property": "style.fill"} for i in range(1, 7)],
        *[{"parameter": "petalColor", "nodeId": f"outer-petal-{i}", "property": "style.fill"} for i in range(6)],
        *[{"parameter": "petalOpacity", "nodeId": f"petal-{i}", "property": "style.opacity"} for i in range(1, 7)],
        *[{"parameter": "petalOpacity", "nodeId": f"outer-petal-{i}", "property": "style.opacity"} for i in range(6)],
        {"parameter": "coreColor", "nodeId": "core", "property": "style.fill"},
    ]

    metadata = {
        "name": "莲",
        "description": "宋韵团扇清莲图：以古典素绢团扇、远山淡墨、波浪荷叶、内外重瓣莲花与月晕虚框呈现“出淤泥而不染”的清正意象，完整支持花瓣/外框约束调参。",
        "entityIds": ["knowledge-lotus", "knowledge-integrity"],
        "tagIds": ["tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["lotus"],
        "timeTheme": "夏日",
    }

    return {
        "slug": "upgrade-lotus",
        "targetTemplateId": "tpl-lotus",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [{"type": "lessThan", "left": "petalRadius", "right": "frameRadius"}],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "视觉精修版：升级为宋韵团扇重瓣清莲图，保留全部原参数与 lessThan 约束",
    }


# ---------------------------------------------------------------------------
# 原有模板美化 2：tpl-bamboo（《竹·守正》—— 三竿清风立轴墨竹）
# 完整保留原 5 个参数：stemScale / leafColor / leafOpacity / stemWidth / sway
# ---------------------------------------------------------------------------


def build_upgrade_bamboo() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 远山晨雾底衬
    nodes.append(make_circle("b-sun-halo", "painting", 210, 152, 74, fill="#F3E6C8", opacity=0.32))
    nodes.append(make_circle("b-sun", "painting", 210, 152, 40, fill="#FBF5E4", stroke="#E3D5B6", stroke_width=0.9, opacity=0.92))
    nodes.append(
        make_polygon(
            "b-mtn-far", "painting",
            make_mountain_pts(28, 772, 504, [(185, 132, 120), (545, 146, 130)], steps=28),
            fill="#C2CDC7", opacity=0.30
        )
    )
    nodes.append(make_polygon("b-mist", "painting", make_mist_cloud_pts(395, 496, 260, 34, steps=22), fill="#F4F1E9", opacity=0.66))

    # 2. 主体三竿修竹组（bamboo 锚点 (400, 526)，承接 stemScale 与 sway）
    nodes.append(make_group("bamboo", "painting", x=400, y=526, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    # 三竿微弧修竹主竿（使用折线，使原参数 stemWidth 4..12 实时生效！并叠加竹节环与亮部线）
    stems = [
        ("stem-1", [(-92, 0), (-86, -86), (-79, -172), (-72, -256), (-64, -334)], "#366341"),
        ("stem-2", [(0, 0), (-4, -92), (-2, -184), (3, -276), (8, -366), (14, -432)], "#2C5536"),
        ("stem-3", [(92, 0), (88, -82), (82, -162), (76, -240), (70, -310)], "#426E4D"),
    ]
    for sid, pts, col in stems:
        nodes.append(make_polyline(sid, "bamboo", pts, stroke=col, stroke_width=8.0, opacity=0.96))
        # 细高光折线增强圆竹立体感
        nodes.append(make_polyline(f"{sid}-hi", "bamboo", [(x + 1.8, y) for x, y in pts], stroke="#89B391", stroke_width=1.5, opacity=0.45))
        # 每节精致横节环
        for j, (jx, jy) in enumerate(pts[1:-1]):
            nodes.append(make_polyline(f"{sid}-node-{j}", "bamboo", [(jx - 8.5, jy), (jx + 8.5, jy)], stroke="#1E3C25", stroke_width=2.4, opacity=0.92))

    # 斜出细枝
    for idx, pts in enumerate([
        [(-79, -172), (-132, -206), (-178, -222)],
        [(-72, -256), (-124, -290), (-166, -306)],
        [(-2, -184), (48, -218), (94, -234)],
        [(3, -276), (-50, -312), (-96, -328)],
        [(8, -366), (62, -396), (108, -410)],
        [(82, -162), (132, -194), (176, -208)],
        [(76, -240), (126, -272), (168, -286)],
    ]):
        nodes.append(make_polyline(f"b-twig-{idx}", "bamboo", pts, stroke="#2A4C33", stroke_width=1.6, opacity=0.88))

    # 26 片柳叶形“个/介”竹叶（全部绑定 leafColor + leafOpacity）
    leaves = [
        (-132, -206, 72, 11.5, 166, 0.22), (-132, -206, 66, 11.0, 192, 0.20), (-168, -220, 60, 10.0, 148, 0.18),
        (-124, -290, 74, 11.8, 170, 0.22), (-124, -290, 64, 10.5, 198, 0.18), (-158, -304, 62, 10.2, 152, 0.20),
        (-54, -312, 76, 12.0, 162, 0.24), (-54, -312, 68, 11.0, 188, 0.20), (-88, -326, 62, 10.2, 140, 0.18),
        (54, -220, 70, 11.2, -16, 0.22), (54, -220, 64, 10.5, 12, 0.24), (86, -232, 58, 9.8, -38, 0.18),
        (64, -396, 78, 12.0, -14, 0.24), (64, -396, 70, 11.0, 14, 0.26), (98, -408, 62, 10.2, -4, 0.22),
        (12, -408, 66, 10.8, 164, 0.20), (12, -408, 60, 10.0, 190, 0.18),
        (132, -194, 72, 11.5, -12, 0.24), (132, -194, 66, 10.8, 16, 0.22), (168, -206, 58, 9.8, 2, 0.24),
        (126, -272, 74, 11.8, -18, 0.24), (126, -272, 68, 11.0, 10, 0.24), (162, -284, 60, 10.0, 26, 0.20),
        (-68, -314, 62, 10.2, 174, 0.20), (-68, -314, 56, 9.5, 146, 0.18), (72, -296, 60, 10.0, -24, 0.22),
    ]
    for idx, (lx, ly, ln, wd, ang, dr) in enumerate(leaves):
        nodes.append(
            make_polygon(
                f"leaf-{idx + 1}", "bamboo",
                make_bamboo_leaf_pts(lx, ly, ln, wd, ang, droop=dr, steps=5),
                fill="#4F7A3A", stroke="#294A23", stroke_width=0.55, opacity=0.95
            )
        )

    # 3. 坡岸青石与苔草（遮掩根部，立体感强）
    nodes.append(make_polygon("ground-base", "painting", [(76, 564), (210, 532), (400, 522), (590, 532), (724, 564)], fill="#DFD9C9", opacity=0.68))
    nodes.append(
        make_polygon(
            "stone", "painting",
            catmull_rom([(484, 558), (504, 506), (552, 476), (608, 482), (656, 514), (676, 558)], seg_steps=2),
            fill="#8D968E", stroke="#525C55", stroke_width=1.2, opacity=0.96
        )
    )
    nodes.append(
        make_polygon(
            "stone-hi", "painting",
            catmull_rom([(508, 510), (552, 482), (598, 490), (576, 516), (524, 524)], seg_steps=2),
            fill="#B7C0B6", opacity=0.65
        )
    )
    for idx, (mx, my, rx, ry) in enumerate([(308, 532, 18, 6.5), (400, 530, 22, 7.0), (492, 536, 18, 6.5)]):
        nodes.append(make_polygon(f"b-moss-{idx}", "painting", make_ellipse_pts(mx, my, rx, ry, 0, steps=12), fill="#4A6E52", opacity=0.72))

    # 4. 右上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 714, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 23,
        "canvas": {"width": 800, "height": 600, "background": "#F4F1E9"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": nodes,
        "animations": [],
        "narrative": [],
    }

    parameter_schema = [
        {"name": "stemScale", "label": "修竹尺度", "description": "整体缩放三竿修竹。", "type": "number", "default": 1.0, "min": 0.8, "max": 1.08, "step": 0.04},
        {"name": "leafColor", "label": "竹叶颜色", "description": "全部柳叶竹叶的填充颜色。", "type": "color", "default": "#4F7A3A"},
        {"name": "leafOpacity", "label": "竹叶透明度", "description": "竹叶浓淡透明度。", "type": "number", "default": 0.95, "min": 0.5, "max": 1.0, "step": 0.05},
        {"name": "stemWidth", "label": "竹竿线宽", "description": "三竿修竹的竿身粗细。", "type": "number", "default": 8.0, "min": 4.0, "max": 12.0, "step": 0.5},
        {"name": "sway", "label": "竹丛倾斜", "description": "整体迎风旋转角度。", "type": "number", "default": 0.0, "min": -5.0, "max": 5.0, "step": 1.0},
    ]

    bindings = [
        {"parameter": "stemScale", "nodeId": "bamboo", "property": "transform.scaleX"},
        {"parameter": "stemScale", "nodeId": "bamboo", "property": "transform.scaleY"},
        {"parameter": "sway", "nodeId": "bamboo", "property": "transform.rotation"},
        {"parameter": "stemWidth", "nodeId": "stem-1", "property": "style.strokeWidth"},
        {"parameter": "stemWidth", "nodeId": "stem-2", "property": "style.strokeWidth"},
        {"parameter": "stemWidth", "nodeId": "stem-3", "property": "style.strokeWidth"},
        *[{"parameter": "leafColor", "nodeId": f"leaf-{i}", "property": "style.fill"} for i in range(1, len(leaves) + 1)],
        *[{"parameter": "leafOpacity", "nodeId": f"leaf-{i}", "property": "style.opacity"} for i in range(1, len(leaves) + 1)],
    ]

    metadata = {
        "name": "竹·守正",
        "description": "以三竿分节修竹、二十六片“个”字柳叶竹叶、太湖青石与晨曦远山构图，表达“咬定青山不放松”的坚韧与守正。",
        "entityIds": ["knowledge-bamboo", "knowledge-integrity"],
        "tagIds": ["tag-steadfast", "tag-pure", "tag-integrity"],
        "symbols": ["bamboo"],
        "timeTheme": "四季",
    }

    return {
        "slug": "upgrade-bamboo",
        "targetTemplateId": "tpl-bamboo",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "视觉精修版：升级为三竿分节墨竹立轴与 26 片柳叶竹叶，完整保留原 5 项参数",
    }


# ---------------------------------------------------------------------------
# 原有模板美化 3：tpl-narrative（《四秒·清莲叙事》—— 晨光莲竹叙事卷）
# 完整保留原 4 个参数：lotusScale / lotusColor / waterDepth / sunSize
# ---------------------------------------------------------------------------


def build_upgrade_narrative() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 晨光朝阳（sun 的 geometry.r 严格绑定 sunSize 24..48！）
    nodes.append(make_group("sun-group", "painting", x=612, y=132, opacity=1.0))
    nodes.append(make_circle("sun-halo", "sun-group", 0, 0, 72, fill="#F5DFB5", opacity=0.32))
    nodes.append(make_circle("sun", "sun-group", 0, 0, 34, fill="#EF9F27", stroke="#B87A1F", stroke_width=1.0, opacity=0.92))

    # 2. 远山与晨雾
    nodes.append(
        make_polygon(
            "nar-mtn", "painting",
            make_mountain_pts(28, 772, 408, [(170, 112, 118), (440, 88, 128), (680, 106, 108)], steps=28),
            fill="#C1CCC5", opacity=0.34
        )
    )
    nodes.append(make_polygon("nar-mist", "painting", make_mist_cloud_pts(400, 404, 310, 28, steps=22), fill="#F7F6F0", opacity=0.68))

    # 3. 清涟水韵（water-user 承接 waterDepth，water 承接流动动画，互不覆盖！）
    nodes.append(make_polygon("nar-pond", "painting", [(28, 424), (772, 424), (772, 574), (28, 574)], fill="#DCE5DF", opacity=0.34))
    nodes.append(make_group("water-user", "painting", x=0, y=0, opacity=0.8))
    nodes.append(make_group("water", "water-user", x=0, y=0, opacity=1.0))
    for idx, (x0, x1, wy, amp) in enumerate([(76, 480, 456, 3.6), (260, 724, 492, 4.0), (112, 668, 532, 3.4), (176, 592, 558, 2.8)]):
        nodes.append(make_polyline(f"water-{idx + 1}", "water", make_water_wave_pts(x0, x1, wy, amp, 2.4, idx * 0.8, steps=16), stroke="#5E796D", stroke_width=1.6, opacity=0.72))

    # 4. 右岸清竹映衬（nar-bamboo 承接 4s 轻摇）
    nodes.append(make_group("nar-bamboo", "painting", x=626, y=532, rotation=0, opacity=0.92))
    _add_bamboo_culm(
        nodes, "nar-bamboo", "nb", [(0, 0), (-6, -86), (-12, -172), (-18, -254)],
        base_half_w=5.8, taper=0.28, color_main="#477354", color_ring="#274730", color_highlight="#89B493", opacity=0.92
    )
    for idx, (lx, ly, ln, wd, ang) in enumerate([
        (-10, -172, 62, 10, 168), (-10, -172, 56, 9.5, 194), (-16, -242, 64, 10, 162),
        (-16, -242, 58, 9.5, 188), (-14, -222, 54, 9.2, -14),
    ]):
        nodes.append(make_polygon(f"nb-leaf-{idx}", "nar-bamboo", make_bamboo_leaf_pts(lx, ly, ln, wd, ang, droop=0.22, steps=5), fill="#3B6849", opacity=0.88))

    # 5. 荷叶与荷茎
    nodes.append(make_polygon("stem-leaf", "painting", make_ribbon(cubic_bezier((238, 412), (246, 456), (254, 492), (262, 520), 6), [2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0]), fill="#385B45", opacity=0.88))
    nodes.append(make_polygon("nar-leaf-l", "painting", make_lotus_leaf_pts(238, 396, 126, 48, -9, notch_deg=-52, wave_amp=0.055, waves=7, steps=40), fill="#49745E", stroke="#2C4E3D", stroke_width=1.2, opacity=0.94))
    nodes.append(make_polygon("nar-leaf-r", "painting", make_lotus_leaf_pts(486, 426, 108, 40, 8, notch_deg=-130, wave_amp=0.055, waves=7, steps=36), fill="#3E6853", stroke="#264536", stroke_width=1.1, opacity=0.90))
    stem_sp = cubic_bezier((368, 536), (364, 466), (372, 396), (378, 326), 8)
    nodes.append(make_polygon("stem", "painting", make_ribbon(stem_sp, [5.0 - 1.4 * (i / 8) for i in range(9)]), fill="#3D6146", stroke="#27412F", stroke_width=0.9, opacity=0.96))

    # 6. 工笔重瓣清莲（lotus-user 承接 lotusScale，lotus 承接 4s 轻摇动画）
    nodes.append(make_group("lotus-user", "painting", x=378, y=318, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))
    nodes.append(make_group("lotus", "lotus-user", x=0, y=0, rotation=0, scale_x=1.0, scale_y=1.0, opacity=1.0))

    outer = [(-165, 114, 44, 7.0), (-140, 124, 48, 4.0), (-114, 132, 50, 2.0), (-90, 138, 52, 0.0), (-66, 132, 50, -2.0), (-40, 124, 48, -4.0), (-15, 114, 44, -7.0)]
    for i, (ang, ln, wd, crl) in enumerate(outer):
        nodes.append(make_polygon(f"petal-{i}", "lotus", make_lotus_petal_pts(0, 6, ln, wd, ang, tip_sharpness=0.18, curl=crl, steps=7), fill="#D4537E", stroke="#993556", stroke_width=1.1, opacity=0.92))
    inner = [(-144, 88, 38, 4.0), (-114, 98, 42, 2.0), (-90, 102, 44, 0.0), (-66, 98, 42, -2.0), (-36, 88, 38, -4.0)]
    for i, (ang, ln, wd, crl) in enumerate(inner):
        nodes.append(make_polygon(f"petal-in-{i}", "lotus", make_lotus_petal_pts(0, 4, ln, wd, ang, tip_sharpness=0.22, curl=crl, steps=6), fill="#F2B8C9", stroke="#B65B76", stroke_width=0.9, opacity=0.9))
    nodes.append(make_polygon("nar-pod", "lotus", [(-18, -4), (-12, -20), (0, -24), (12, -20), (18, -4), (10, 6), (-10, 6)], fill="#8EA65D", stroke="#5D7434", stroke_width=1.0, opacity=0.96))
    for i, (sx, sy) in enumerate([(-22, -16), (-12, -28), (0, -32), (12, -28), (22, -16)]):
        nodes.append(make_circle(f"nar-st-{i}", "lotus", sx, sy, 2.5, fill="#F5C74F", stroke="#A67115", stroke_width=0.5, opacity=0.96))

    # 7. 左上角独立朱文闲章
    nodes.extend(make_seal_nodes("seal", 56, 52, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 31,
        "canvas": {"width": 800, "height": 600, "background": "#F7F6F0"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "water", "property": "opacity", "from": 0.55, "to": 1.0, "startMs": 0, "endMs": 1400, "easing": "linear"},
            {"nodeId": "water", "property": "x", "from": -6, "to": 6, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "sun-group", "property": "y", "from": 152, "to": 124, "startMs": 0, "endMs": 2600, "easing": "linear"},
            {"nodeId": "lotus", "property": "rotation", "from": -3.2, "to": 3.2, "startMs": 0, "endMs": 4000, "easing": "linear"},
            {"nodeId": "nar-bamboo", "property": "rotation", "from": -2.0, "to": 2.0, "startMs": 0, "endMs": 4000, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "清涟初醒：晨雾渐散、池水微澜，以水之清澈铺陈叙事起点。", "entityIds": ["knowledge-water"]},
            {"timeMs": 1400, "caption": "莲开不染：重瓣工笔清莲在田田荷叶间舒展绽放。", "entityIds": ["knowledge-lotus"]},
            {"timeMs": 2800, "caption": "修竹映水：岸畔清竹随晨风轻摇，与清莲相映成趣。", "entityIds": ["knowledge-bamboo"]},
            {"timeMs": 3900, "caption": "守正清心：朝阳高照、水莲竹共生，寄托廉洁守正之志。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "lotusScale", "label": "莲花尺度", "description": "整体缩放重瓣莲冠；1.0 为标准大小。", "type": "number", "default": 1.0, "min": 0.8, "max": 1.15, "step": 0.05},
        {"name": "lotusColor", "label": "莲花颜色", "description": "七片外层莲花瓣的填充颜色。", "type": "color", "default": "#D4537E"},
        {"name": "waterDepth", "label": "水线浓度", "description": "清涟水线的透明度；组动画会在此基础上叠加渐显。", "type": "number", "default": 0.8, "min": 0.35, "max": 1.0, "step": 0.05},
        {"name": "sunSize", "label": "日轮半径", "description": "右上角朝阳日轮的半径，单位为逻辑像素。", "type": "integer", "default": 34, "min": 24, "max": 48, "step": 1},
    ]

    bindings = [
        {"parameter": "lotusScale", "nodeId": "lotus-user", "property": "transform.scaleX"},
        {"parameter": "lotusScale", "nodeId": "lotus-user", "property": "transform.scaleY"},
        *[{"parameter": "lotusColor", "nodeId": f"petal-{i}", "property": "style.fill"} for i in range(7)],
        {"parameter": "waterDepth", "nodeId": "water-user", "property": "style.opacity"},
        {"parameter": "sunSize", "nodeId": "sun", "property": "geometry.r"},
    ]

    metadata = {
        "name": "四秒·清莲叙事",
        "description": "以 4 秒时间轴演绎晨光初升、清涟水波、工笔重瓣莲开、岸畔竹影与守正题记，完整演示动画采样与叙事标记。",
        "entityIds": ["knowledge-water", "knowledge-lotus", "knowledge-bamboo", "knowledge-integrity"],
        "tagIds": ["tag-integrity", "tag-symbol", "tag-pure"],
        "symbols": ["lotus", "bamboo", "water", "narrative"],
        "timeTheme": "四季",
    }

    return {
        "slug": "upgrade-narrative",
        "targetTemplateId": "tpl-narrative",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "视觉精修版：升级为晨光清莲竹影 4 秒叙事画卷，完整保留原 4 项参数并修复动画叠加",
    }


# ---------------------------------------------------------------------------
# 原有模板美化 4：tpl-1c48c20b38e742e8b9b6762d0c049ac8（《墨梅·清风》—— 王冕水墨倒垂梅）
# 保留原参数 blossomRadius，并补充淡墨花色与清风倾角！
# ---------------------------------------------------------------------------


def build_upgrade_ink_plum() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    nodes.append(make_group("painting", None, x=0, y=0, opacity=1.0))
    nodes.extend(make_frame_nodes("painting"))

    # 1. 古雅冷宣底色上的淡墨月轮（位于左上方，与右垂墨梅形成对角呼应）
    nodes.append(make_circle("im-moon-halo", "painting", 216, 178, 84, fill="#E7E2D6", opacity=0.38))
    nodes.append(make_circle("im-moon", "painting", 216, 178, 52, fill="#FAF8F2", stroke="#D6CFC0", stroke_width=0.9, opacity=0.95))

    # 2. 远景极淡墨影山脊
    nodes.append(
        make_polygon(
            "im-mtn", "painting",
            make_mountain_pts(28, 772, 574, [(210, 86, 140), (590, 74, 130)], steps=24),
            fill="#D4D3CE", opacity=0.32
        )
    )

    # 3. 倒垂古墨梅枝组（锚点位于右上边缘内侧 (664, 166)，从右上向左下斜垂，致敬王冕《墨梅图》构图！）
    nodes.append(make_group("im-user", "painting", x=664, y=166, rotation=0, opacity=1.0))
    nodes.append(make_group("im-sway", "im-user", x=0, y=0, rotation=0, opacity=1.0))

    main_sp = catmull_rom([(0, 0), (-74, 38), (-158, 72), (-252, 128), (-346, 192), (-436, 262)], seg_steps=3)
    main_w = [12.5 * (1.0 - 0.78 * (i / max(1, len(main_sp) - 1))) for i in range(len(main_sp))]
    nodes.append(make_polygon("im-trunk", "im-sway", make_ribbon(main_sp, main_w), fill="#333332", stroke="#1C1C1B", stroke_width=0.9, opacity=0.96))

    # 上扬细劲梅枝
    up_sp = catmull_rom([(-166, 78), (-248, 34), (-336, 16), (-418, 28)], seg_steps=3)
    up_w = [5.4 * (1.0 - 0.78 * (i / max(1, len(up_sp) - 1))) for i in range(len(up_sp))]
    nodes.append(make_polygon("im-branch-up", "im-sway", make_ribbon(up_sp, up_w), fill="#3E3E3C", opacity=0.94))

    # 下垂细梢
    dn_sp = catmull_rom([(-264, 138), (-238, 212), (-212, 278), (-188, 336)], seg_steps=3)
    dn_w = [4.6 * (1.0 - 0.76 * (i / max(1, len(dn_sp) - 1))) for i in range(len(dn_sp))]
    nodes.append(make_polygon("im-branch-down", "im-sway", make_ribbon(dn_sp, dn_w), fill="#444442", opacity=0.92))

    # 瘦硬墨梅小枝
    for idx, pts in enumerate([
        [(-340, 188), (-384, 152), (-424, 126)],
        [(-282, 22), (-314, -14), (-352, -32)],
        [(-226, 242), (-268, 268), (-308, 284)],
    ]):
        nodes.append(make_polyline(f"im-twig-{idx}", "im-sway", pts, stroke="#383836", stroke_width=1.6, opacity=0.9))

    # 7 朵王冕风格水墨梅花（清雅银白/淡墨瓣 + 浓墨点萼 + 柔和墨晕圆圈绑定 blossomRadius 14..36！）
    ink_blossoms = [
        ("imf-1", -196, 94, 1.18, 12.0),
        ("imf-2", -312, 172, 1.24, 34.0),
        ("imf-3", -428, 256, 1.02, 18.0),
        ("imf-4", -294, 24, 1.12, 42.0),
        ("imf-5", -396, 26, 0.94, 8.0),
        ("imf-6", -222, 246, 1.06, 28.0),
        ("imf-7", -392, 146, 0.92, 54.0),
    ]
    petal_ids: list[str] = []
    halo_ids: list[str] = []
    for bid, bx, by, bsc, brot in ink_blossoms:
        hid = f"{bid}-halo"
        halo_ids.append(hid)
        # 淡墨花晕圈：半径直接绑定原参数 blossomRadius (14..36)，无外框硬边呈现水墨晕染感！
        nodes.append(make_circle(hid, "im-sway", bx, by, 24, fill="#CFCBC2", stroke=None, stroke_width=0.0, opacity=0.28))
        for i in range(5):
            ang = brot + i * 72.0 - 90.0
            pid = f"{bid}-p{i}"
            petal_ids.append(pid)
            nodes.append(
                make_polygon(
                    pid, "im-sway",
                    make_plum_petal_pts(bx, by, 9.8 * bsc, 8.6 * bsc, 8.2 * bsc, ang, steps=11),
                    fill="#E9E7E1", stroke="#6E6D68", stroke_width=0.85, opacity=0.94
                )
            )
        nodes.append(make_circle(f"{bid}-core", "im-sway", bx, by, 3.2 * bsc, fill="#2F2F2D", opacity=0.92))
        for j in range(5):
            a = math.radians(brot + j * 72.0 - 54.0)
            nodes.append(make_circle(f"{bid}-st{j}", "im-sway", bx + 5.8 * bsc * math.cos(a), by + 5.8 * bsc * math.sin(a), 1.3 * bsc, fill="#4C4B47", opacity=0.88))

    # 浓墨花苞（紧贴枝梢，绝不悬空）
    for idx, (ux, uy) in enumerate([(-432, 260), (-418, 126), (-350, -30), (-304, 282), (-192, 326)]):
        nodes.append(make_circle(f"im-bud-{idx}", "im-sway", ux, uy, 4.4, fill="#55544F", stroke="#2C2C2A", stroke_width=0.8, opacity=0.92))

    # 右上淡墨云雾柔化老干入画端
    nodes.append(make_polygon("im-edge-mist", "painting", make_mist_cloud_pts(688, 158, 72, 42, steps=18), fill="#F4F2EC", opacity=0.82))

    # 4. 左下角独立朱文闲章（与右上老干平衡）
    nodes.extend(make_seal_nodes("seal", 56, 514, size=28.0))

    ir_template = {
        "schemaVersion": "1.0",
        "seed": 707,
        "canvas": {"width": 800, "height": 600, "background": "#F4F2EC"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 4000,
        "nodes": nodes,
        "animations": [
            {"nodeId": "im-sway", "property": "rotation", "from": -1.0, "to": 1.2, "startMs": 0, "endMs": 4000, "easing": "linear"},
        ],
        "narrative": [
            {"timeMs": 0, "caption": "洗砚淡墨：冷宣素月之下，老梅自右上倒垂横斜。", "entityIds": ["knowledge-plum"]},
            {"timeMs": 2000, "caption": "不要人夸：五瓣墨梅淡雅脱俗，以少胜多、以淡见真。", "entityIds": ["knowledge-plum", "knowledge-integrity"]},
            {"timeMs": 3800, "caption": "清气满乾坤：微风轻拂墨梅清影，唯留清气长存。", "entityIds": ["knowledge-integrity"]},
        ],
    }

    parameter_schema = [
        {"name": "blossomRadius", "label": "梅花墨晕半径", "description": "主枝梅花水墨晕染外圈半径（14～36）", "type": "integer", "default": 24, "min": 14, "max": 36, "step": 1},
        {"name": "inkTone", "label": "墨梅花瓣色", "description": "梅花花瓣水墨深浅（素白淡墨 / 霜灰 / 轻绯）", "type": "color", "default": "#E9E7E1"},
        {"name": "windTilt", "label": "清风倾角", "description": "微调倒垂梅枝摆动倾角（度）", "type": "integer", "default": 0, "min": -3, "max": 3, "step": 1},
    ]

    bindings = [
        *[{"parameter": "blossomRadius", "nodeId": hid, "property": "geometry.r"} for hid in halo_ids],
        *[{"parameter": "inkTone", "nodeId": pid, "property": "style.fill"} for pid in petal_ids],
        {"parameter": "windTilt", "nodeId": "im-user", "property": "transform.rotation"},
    ]

    metadata = {
        "name": "墨梅·清风",
        "description": "王冕《墨梅》意境水墨画卷：老干自右上倒垂横斜，七朵素墨五瓣梅花配水墨晕染、淡月寒山与左下朱文闲章，“不要人夸好颜色，只留清气满乾坤”。",
        "entityIds": ["knowledge-plum", "knowledge-integrity"],
        "tagIds": ["tag-pure", "tag-symbol", "tag-integrity"],
        "symbols": ["narrative"],
        "timeTheme": "冬雪",
    }

    return {
        "slug": "upgrade-ink-plum",
        "targetTemplateId": "tpl-1c48c20b38e742e8b9b6762d0c049ac8",
        "metadata": metadata,
        "irTemplate": ir_template,
        "parameterSchema": parameter_schema,
        "bindings": bindings,
        "constraints": [],
        "supportedBackends": ["canvas2d", "css", "webgl"],
        "changeNote": "视觉精修版：升级为王冕淡墨倒垂梅花卷，保留原 blossomRadius 并新增墨色与倾角参数",
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    builders = [
        build_art_orchid,
        build_art_pine,
        build_art_water_boat,
        build_upgrade_lotus,
        build_upgrade_bamboo,
        build_upgrade_narrative,
        build_upgrade_ink_plum,
    ]
    for fn in builders:
        spec = fn()
        nodes_n, verts_n = verify_template(spec)
        out_path = OUT_DIR / f"{spec['slug']}.json"
        out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        target_note = f" -> {spec['targetTemplateId']}" if spec.get("targetTemplateId") else " (new)"
        print(
            f"[OK] {spec['metadata']['name']}{target_note} ({out_path.relative_to(PROJECT_ROOT)}): "
            f"nodes={nodes_n}, vertices={verts_n}, bindings={len(spec['bindings'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
