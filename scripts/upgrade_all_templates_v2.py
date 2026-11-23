"""将全部 10 套新中式国画模板全面升级至高画质 IR 渲染标准：

1. 宣纸 / 宋绢 / 古宣底纹（canvas.paper = xuan | silk | aged）
2. 工笔分染与水墨渐隐多色标渐变（style.gradient，且对调色参数节点自动开启 tintFromFill）
3. GPU/Canvas 水墨渗化颗粒（ink_wash）与工笔丝光（gongbi）材质着色器
4. 圆润笔触（lineCap / lineJoin = round）消除折线尖刺
5. 经典廉洁古诗词竖排楷书题跋 + 真实汉字朱文篆刻闲章（type = text）

全部 10 套模板升级后均通过后端 DrawingIR、validate_ir、instantiate(default/min/max)、
sample_scene(0/2000/4000) 及逐参数活性断言，确保零越界、零自交、零死参数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_art_templates as base  # noqa: E402
import build_more_templates as more  # noqa: E402


# 每幅画的专属画质配置：纸绢、竖排诗词题跋（位置避开主体与印章）、朱文篆印文字
ART_PROFILES: dict[str, dict[str, Any]] = {
    "art-lotus": {
        "paper": "xuan",
        "sealText": "清莲",
        "poem": [
            {"x": 134, "y": 54, "text": "出淤泥而不染", "fontSize": 20, "letterSpacing": 5},
            {"x": 106, "y": 86, "text": "濯清涟而不妖", "fontSize": 18, "letterSpacing": 5},
        ],
        "changeNote": "高画质终版：宣纸底纹、工笔花瓣分染渐变、柔光月晕、水墨远山与《爱莲说》竖排题跋篆印",
    },
    "art-bamboo": {
        "paper": "xuan",
        "sealText": "守正",
        "poem": [
            {"x": 88, "y": 54, "text": "咬定青山不放松", "fontSize": 20, "letterSpacing": 4},
            {"x": 58, "y": 102, "text": "立根原在破岩中", "fontSize": 18, "letterSpacing": 4},
        ],
        "changeNote": "高画质终版：宣纸底纹、圆柱墨竹渐变、水墨太湖石、圆润竹枝与《竹石》竖排题跋篆印",
    },
    "art-plum": {
        "paper": "aged",
        "sealText": "清气",
        "poem": [
            {"x": 134, "y": 54, "text": "疏影横斜水清浅", "fontSize": 20, "letterSpacing": 4},
            {"x": 106, "y": 88, "text": "暗香浮动月黄昏", "fontSize": 18, "letterSpacing": 4},
        ],
        "changeNote": "高画质终版：古宣底纹、工笔五瓣红梅分染渐变、柔光雪月与《山园小梅》竖排题跋篆印",
    },
    "art-orchid": {
        "paper": "xuan",
        "sealText": "慎独",
        "poem": [
            {"x": 88, "y": 54, "text": "空谷生幽兰", "fontSize": 21, "letterSpacing": 5},
            {"x": 58, "y": 96, "text": "清芬守慎独", "fontSize": 18, "letterSpacing": 5},
        ],
        "changeNote": "高画质终版：宣纸底纹、工笔素心春兰渐变、水墨幽谷苔石与慎独竖排题跋篆印",
    },
    "art-pine": {
        "paper": "aged",
        "sealText": "浩然",
        "poem": [
            {"x": 134, "y": 52, "text": "大雪压青松", "fontSize": 20, "letterSpacing": 5},
            {"x": 106, "y": 84, "text": "青松挺且直", "fontSize": 18, "letterSpacing": 5},
        ],
        "changeNote": "高画质终版：古宣底纹、丹霞朝阳径向柔光、水墨崖松针冠渐变与《青松》竖排题跋篆印",
    },
    "art-water-boat": {
        "paper": "silk",
        "sealText": "若水",
        "poem": [
            {"x": 664, "y": 54, "text": "上善若水", "fontSize": 21, "letterSpacing": 6},
            {"x": 634, "y": 96, "text": "利万物而不争", "fontSize": 18, "letterSpacing": 4},
        ],
        "changeNote": "高画质终版：宋绢底纹、青绿山水渐隐远峰、湖光倒影与《道德经》竖排题跋篆印",
    },
    "upgrade-lotus": {
        "paper": "silk",
        "sealText": "清正",
        "poem": [
            {"x": 134, "y": 56, "text": "中通外直", "fontSize": 20, "letterSpacing": 6},
            {"x": 106, "y": 88, "text": "不蔓不枝", "fontSize": 18, "letterSpacing": 6},
        ],
        "changeNote": "高画质终版：宋绢团扇工笔重瓣清莲、渐变晕染与竖排题跋（保留全部原参数与约束）",
    },
    "upgrade-bamboo": {
        "paper": "xuan",
        "sealText": "守正",
        "poem": [
            {"x": 88, "y": 54, "text": "千磨万击还坚劲", "fontSize": 19, "letterSpacing": 4},
            {"x": 58, "y": 98, "text": "任尔东西南北风", "fontSize": 17, "letterSpacing": 4},
        ],
        "changeNote": "高画质终版：宣纸三竿分节墨竹、水墨竹叶渐变与竖排题跋（保留全部原 5 项参数）",
    },
    "upgrade-narrative": {
        "paper": "xuan",
        "sealText": "清心",
        "poem": [
            {"x": 134, "y": 54, "text": "清涟映朝阳", "fontSize": 20, "letterSpacing": 5},
            {"x": 106, "y": 86, "text": "守正自清心", "fontSize": 18, "letterSpacing": 5},
        ],
        "changeNote": "高画质终版：宣纸晨光清莲竹影 4 秒叙事卷、工笔渐变与竖排题跋（保留全部原 4 项参数）",
    },
    "upgrade-ink-plum": {
        "paper": "aged",
        "sealText": "墨梅",
        "poem": [
            {"x": 84, "y": 60, "text": "不要人夸好颜色", "fontSize": 19, "letterSpacing": 4},
            {"x": 54, "y": 106, "text": "只留清气满乾坤", "fontSize": 17, "letterSpacing": 4},
        ],
        "changeNote": "高画质终版：古宣王冕淡墨倒垂梅、水墨花晕渐变与《墨梅》竖排题跋（保留 blossomRadius）",
    },
}


def _tint_grad(color: str, *, gtype: str = "linear", angle: float = 90.0, op_end: float = 0.92) -> dict[str, Any]:
    return {
        "type": gtype,
        "angle": angle,
        "cx": 0.48,
        "cy": 0.45,
        "r": 0.62,
        "tintFromFill": True,
        "stops": [
            {"offset": 0.0, "color": color, "opacity": 1.0},
            {"offset": 0.52, "color": color, "opacity": 0.96},
            {"offset": 1.0, "color": color, "opacity": op_end},
        ],
    }


def _mountain_fade_grad(fill: str, bg: str) -> dict[str, Any]:
    return {
        "type": "linear",
        "angle": 90.0,
        "tintFromFill": False,
        "stops": [
            {"offset": 0.0, "color": fill, "opacity": 0.92},
            {"offset": 0.62, "color": fill, "opacity": 0.48},
            {"offset": 1.0, "color": bg, "opacity": 0.02},
        ],
    }


def _radial_glow_grad(fill: str, *, inner_op: float = 0.9, mid_op: float = 0.42) -> dict[str, Any]:
    return {
        "type": "radial",
        "cx": 0.5,
        "cy": 0.5,
        "r": 0.52,
        "tintFromFill": False,
        "stops": [
            {"offset": 0.0, "color": fill, "opacity": inner_op},
            {"offset": 0.55, "color": fill, "opacity": mid_op},
            {"offset": 1.0, "color": fill, "opacity": 0.0},
        ],
    }


def _replace_seal_with_carved_text(nodes: list[dict[str, Any]], seal_text: str) -> list[dict[str, Any]]:
    """把原几何菱形印章升级为传统朱文竖排篆刻汉字印章（保持原位置，零交叠）。"""
    seal_grp = next((n for n in nodes if n["id"] == "seal"), None)
    if seal_grp is None:
        return nodes
    sx = float(seal_grp["transform"]["x"])
    sy = float(seal_grp["transform"]["y"])
    # 防止靠近底边时 44px 印章超出 584 画框
    if sy > 522:
        sy = 512.0
    if sx > 734:
        sx = 724.0

    filtered = [n for n in nodes if n["id"] != "seal" and n.get("parentId") != "seal"]
    carved_seal = [
        base.make_group("seal", None, x=sx, y=sy, opacity=0.94),
        base.make_polygon(
            "seal-bg",
            "seal",
            [(0, 0), (30, 0), (30, 44), (0, 44)],
            fill="#B5382B",
            stroke="#822219",
            stroke_width=1.1,
            opacity=0.95,
        ),
        base.make_polygon(
            "seal-inner",
            "seal",
            [(3.0, 3.0), (27.0, 3.0), (27.0, 41.0), (3.0, 41.0)],
            fill=None,
            stroke="#F7F0E4",
            stroke_width=0.9,
            opacity=0.88,
        ),
        {
            "id": "seal-text",
            "parentId": "seal",
            "type": "text",
            "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
            "geometry": {
                "x": 15.0,
                "y": 6.5,
                "text": seal_text,
                "fontSize": 13.5,
                "writingMode": "vertical",
                "fontFamily": "seal",
                "letterSpacing": 3.0,
            },
            "style": {"fill": "#F8F2E6", "stroke": None, "strokeWidth": 0.0, "opacity": 0.96},
        },
    ]
    filtered.extend(carved_seal)
    return filtered


def upgrade_template_quality(spec: dict[str, Any]) -> dict[str, Any]:
    slug = spec["slug"]
    profile = ART_PROFILES[slug]
    ir = spec["irTemplate"]
    bg = ir["canvas"]["background"]
    ir["canvas"]["paper"] = profile["paper"]

    # 收集所有被 color 参数绑定的节点 ID，保证对其渐变开启 tintFromFill 以便调色实时生效
    color_params = {p["name"] for p in spec["parameterSchema"] if p["type"] == "color"}
    color_bound_nodes = {b["nodeId"] for b in spec["bindings"] if b["parameter"] in color_params and b["property"] == "style.fill"}

    for node in ir["nodes"]:
        ntype = node["type"]
        if ntype == "group":
            continue
        nid: str = node["id"]
        style = node["style"]
        fill = style.get("fill")

        # 统一柔润笔触，彻底消除尖角毛刺
        if not nid.startswith("scroll-frame"):
            style["lineCap"] = "round"
            style["lineJoin"] = "round"

        if ntype == "polyline":
            continue

        # 1. 柔和光晕（月晕、日晕、灯晕、花晕）-> 径向渐变柔化至完全透明
        if any(k in nid for k in ("halo", "lantern-glow")) and fill:
            style["gradient"] = _radial_glow_grad(fill, inner_op=0.88, mid_op=0.40)
            style["opacity"] = min(1.0, max(float(style.get("opacity", 0.4)), 0.55))
            continue

        # 2. 月轮 / 日轮主体 -> 温润径向立体光晕
        if any(k in nid for k in ("moon-disc", "plum-moon", "pine-sun", "b-sun", "im-moon")) or nid == "sun":
            if fill:
                style["gradient"] = _tint_grad(fill, gtype="radial", op_end=0.92)
                node["material"] = {"shaderRef": "gongbi"}
            continue

        # 3. 远山 / 雪峰 -> 顶部青黛到底部宣纸平滑消隐（彻底告别平直山脚切线！）
        if "mtn" in nid and fill:
            if nid in color_bound_nodes:
                style["gradient"] = _tint_grad(fill, angle=90.0, op_end=0.14)
            else:
                style["gradient"] = _mountain_fade_grad(fill, bg)
            node["material"] = {"shaderRef": "ink_wash"}
            continue

        # 4. 云岚 / 雾带 -> 径向软消散云气
        if any(k in nid for k in ("mist", "cloud")) and fill:
            style["gradient"] = _radial_glow_grad(fill, inner_op=0.72, mid_op=0.35)
            continue

        # 5. 池水 / 湖面 -> 上淡下润水色渐变
        if any(k in nid for k in ("pond-wash", "ss-lake", "nar-pond")) and fill:
            style["gradient"] = _tint_grad(fill, angle=90.0, op_end=0.88) if nid in color_bound_nodes else {
                "type": "linear",
                "angle": 90.0,
                "stops": [
                    {"offset": 0.0, "color": bg, "opacity": 0.15},
                    {"offset": 0.35, "color": fill, "opacity": 0.78},
                    {"offset": 1.0, "color": fill, "opacity": 0.95},
                ],
            }
            continue

        # 6. 工笔花瓣、花蕾、春兰、梅花 -> 启用 tintFromFill 工笔分染渐变 + gongbi 丝光材质
        if any(k in nid for k in ("petal", "bud-", "flw-", "imf-", "orch-", "lotus-pod", "core")) and fill:
            if "halo" in nid:
                style["gradient"] = _radial_glow_grad(fill, inner_op=0.65, mid_op=0.28)
            else:
                gtype = "radial" if ntype == "circle" else "linear"
                style["gradient"] = _tint_grad(fill, gtype=gtype, angle=135.0, op_end=0.94)
                node["material"] = {"shaderRef": "gongbi"}
            continue

        # 7. 荷叶、竹节、竹叶、松冠、兰叶、崖石、山石、老干、船身 -> 渐变 + 水墨颗粒材质
        if any(k in nid for k in ("leaf", "culm", "bleaf", "oleaf", "can-", "rock", "stone", "crag", "cliff", "trunk", "branch", "bough", "boat-hull", "boat-canopy", "stem")) and fill:
            angle = 0.0 if ("culm" in nid or "stem" in nid) else 125.0
            style["gradient"] = _tint_grad(fill, angle=angle, op_end=0.95)
            node["material"] = {"shaderRef": "ink_wash"}

    # 8. 替换朱文印章为真实汉字篆刻印
    ir["nodes"] = _replace_seal_with_carved_text(ir["nodes"], profile["sealText"])

    # 9. 添加经典古诗词竖排楷书题跋（放入独立 calligraphy 顶层组，留白落款零交叠）
    poem_nodes: list[dict[str, Any]] = [
        base.make_group("calligraphy", None, x=0, y=0, opacity=1.0),
    ]
    for idx, col in enumerate(profile["poem"]):
        poem_nodes.append({
            "id": f"poem-col-{idx}",
            "parentId": "calligraphy",
            "type": "text",
            "transform": {"x": 0, "y": 0, "rotation": 0, "scaleX": 1, "scaleY": 1},
            "geometry": {
                "x": float(col["x"]),
                "y": float(col["y"]),
                "text": col["text"],
                "fontSize": float(col["fontSize"]),
                "writingMode": "vertical",
                "fontFamily": "kai",
                "letterSpacing": float(col["letterSpacing"]),
            },
            "style": {
                "fill": "#3D332B",
                "stroke": None,
                "strokeWidth": 0.0,
                "opacity": 0.84 if idx == 0 else 0.74,
            },
        })
    ir["nodes"].extend(poem_nodes)
    spec["changeNote"] = profile["changeNote"]
    return spec


def main() -> int:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    builders = [
        base.build_art_lotus,
        base.build_art_bamboo,
        base.build_art_plum,
        more.build_art_orchid,
        more.build_art_pine,
        more.build_art_water_boat,
        more.build_upgrade_lotus,
        more.build_upgrade_bamboo,
        more.build_upgrade_narrative,
        more.build_upgrade_ink_plum,
    ]
    for fn in builders:
        spec = upgrade_template_quality(fn())
        nodes_n, verts_n = base.verify_template(spec)
        out_path = base.OUT_DIR / f"{spec['slug']}.json"
        out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"[OK] {spec['metadata']['name']} (paper={spec['irTemplate']['canvas']['paper']}, "
            f"nodes={nodes_n}, verts={verts_n}, bindings={len(spec['bindings'])})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
