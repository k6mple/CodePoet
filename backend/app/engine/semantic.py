"""语义规则推理：文字/实体 → 规则命中 → 图形决策 → 高画质水墨/工笔 IR。

这个模块是 P1B-01 的核心。它存在的意义不是"能生成一张图"，而是
**同样的输入永远出同样的图，不同的输入出不同的图**。为此：

* 规则排序确定：``priority`` 降序，同优先级按 ``created_at`` 升序、最后按 id。
* 空规则不命中任何输入。
* 命中依据写进 provenance（``matched_by``），前端能显示"为什么画出这个"。
* 画质升级：输出带宣纸底纹、工笔/水墨渐变材质、柔润笔触、竖排书法题跋与朱文篆印的
  完整新中式画卷，且全部符号均按 ``anchors(count, composition)`` 真实落位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.schemas.ir import DrawingIR
from app.schemas.rules import RuleEffects, RuleMatch

CANVAS_W = 800
CANVAS_H = 600

DEFAULT_PALETTE = ["#D85A30", "#1D9E75", "#378ADD", "#BA7517"]
_STROKE_INK = "#3E342D"

_SYMBOL_POEMS: dict[str, tuple[str, str]] = {
    "lotus": ("出淤泥而不染", "清莲"),
    "bamboo": ("咬定青山不放松", "守正"),
    "water": ("上善若水而不争", "若水"),
    "narrative": ("只留清气满乾坤", "清正"),
}


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleFacts:
    """匹配时需要的事实。由服务层查好传进来，这里不认识 ORM。"""

    text: str = ""
    entity_ids: frozenset[str] = frozenset()
    tag_ids: frozenset[str] = frozenset()
    concept_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MatchedRule:
    rule_id: str
    rule_version: int
    name: str
    priority: int
    effects: RuleEffects
    matched_by: tuple[str, ...] = field(default_factory=tuple)


def _group_hits(
    keys: list[str],
    haystack: frozenset[str],
    prefix: str,
) -> list[str]:
    return [f"{prefix}:{key}" for key in keys if key in haystack]


def _keyword_hits(keywords: list[str], text: str) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for keyword in keywords:
        needle = keyword.strip().lower()
        if needle and needle in lowered:
            hits.append(f"keyword:{keyword}")
    return hits


def rule_hits(match: RuleMatch, facts: RuleFacts) -> list[str]:
    """返回命中依据。空列表表示这条规则不适用于当前输入。"""
    keyword_hits = _keyword_hits(match.keywords, facts.text)
    entity_hits = _group_hits(match.entityIds, facts.entity_ids, "entity")
    concept_hits = _group_hits(match.conceptIds, facts.concept_ids, "concept")
    tag_hits = _group_hits(match.tagIds, facts.tag_ids, "tag")

    groups = [
        ("keywords", match.keywords, keyword_hits),
        ("entityIds", match.entityIds, entity_hits),
        ("conceptIds", match.conceptIds, concept_hits),
        ("tagIds", match.tagIds, tag_hits),
    ]
    non_empty = [g for g in groups if g[1]]

    if not non_empty:
        return []

    if match.operator == "all":
        if not all(hits for _, _, hits in non_empty):
            return []
    else:
        if not any(hits for _, _, hits in non_empty):
            return []

    merged: list[str] = []
    for hits in (keyword_hits, entity_hits, concept_hits, tag_hits):
        for item in hits:
            if item not in merged:
                merged.append(item)
    return merged


def order_key(row: Any) -> tuple:
    """规则排序键：优先级高的在前，同优先级让先建的胜出。"""
    return (-int(row.priority), str(row.created_at), str(row.id))


def match_rules(rows: list[Any], facts: RuleFacts, effects_of) -> list[MatchedRule]:
    """筛出命中的启用规则，按确定顺序返回。"""
    matched: list[MatchedRule] = []
    for row in sorted(rows, key=order_key):
        match = RuleMatch.model_validate_json(row.match_json)
        hits = rule_hits(match, facts)
        if not hits:
            continue
        matched.append(
            MatchedRule(
                rule_id=row.id,
                rule_version=int(row.version),
                name=row.name,
                priority=int(row.priority),
                effects=effects_of(row),
                matched_by=tuple(hits),
            )
        )
    return matched


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Synthesized:
    """多条命中规则合并后的图形决策，供 build_ir 与 provenance 共用。"""

    symbol_ids: tuple[str, ...]
    palette: tuple[str, ...]
    composition: str
    motion: str
    caption: str
    matched: tuple[MatchedRule, ...]

    @property
    def empty(self) -> bool:
        return not self.symbol_ids


def synthesize(matched: list[MatchedRule]) -> Synthesized:
    """把多条命中规则合成为一份确定的图形决策。"""
    symbols: list[str] = []
    palette: list[str] = []
    composition = "centered"
    motion = "none"
    caption = ""
    composition_set = False
    motion_set = False
    caption_set = False

    for item in matched:
        effects = item.effects
        for symbol in effects.symbolIds:
            if symbol not in symbols:
                symbols.append(symbol)
        if not palette and effects.palette:
            palette = list(effects.palette)
        if not composition_set:
            composition = effects.composition
            composition_set = True
        if not motion_set:
            motion = effects.motion
            motion_set = True
        if not caption_set and effects.caption:
            caption = effects.caption
            caption_set = True

    return Synthesized(
        symbol_ids=tuple(symbols),
        palette=tuple(palette or DEFAULT_PALETTE),
        composition=composition,
        motion=motion,
        caption=caption,
        matched=tuple(matched),
    )


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------


def anchors(count: int, composition: str) -> list[tuple[float, float]]:
    """每个符号的中心点。构图方式直接改变坐标，这是"构图真的生效"的证据。"""
    if count <= 0:
        return []
    cx, cy = CANVAS_W / 2, CANVAS_H / 2 + 12
    if composition == "vertical":
        step = 320 / count
        return [(cx, 150 + step * (index + 0.5)) for index in range(count)]
    if composition == "horizontal":
        step = 540 / count
        return [(130 + step * (index + 0.5), cy) for index in range(count)]
    if composition == "radial":
        radius = 128
        return [
            (
                cx + radius * math.cos(2 * math.pi * index / count - math.pi / 2),
                cy + radius * math.sin(2 * math.pi * index / count - math.pi / 2),
            )
            for index in range(count)
        ]
    if count == 1:
        return [(cx, cy)]
    step = 156
    start = cy - step * (count - 1) / 2
    return [(cx, start + step * index) for index in range(count)]


# ---------------------------------------------------------------------------
# 艺术化几何辅助
# ---------------------------------------------------------------------------


def _r2(v: float) -> float:
    return round(float(v), 2)


def _clamp_xy(x: float, y: float) -> list[float]:
    return [_r2(max(24.0, min(776.0, x))), _r2(max(24.0, min(576.0, y)))]


def _cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int,
) -> list[tuple[float, float]]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        out.append((
            u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
        ))
    return out


def _rot_trans(pts: list[tuple[float, float]], cx: float, cy: float, ang_deg: float) -> list[list[float]]:
    rad = math.radians(ang_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    return [[_r2(cx + x * ca - y * sa), _r2(cy + x * sa + y * ca)] for x, y in pts]


def _petal_pts(cx: float, cy: float, length: float, width: float, ang_deg: float, steps: int = 6) -> list[list[float]]:
    hw = width / 2.0
    left = _cubic((0, 0), (length * 0.24, -hw * 0.92), (length * 0.72, -hw * 0.76), (length, 0), steps)
    right = _cubic((length, 0), (length * 0.72, hw * 0.76), (length * 0.24, hw * 0.92), (0, 0), steps)
    return _rot_trans(left[:-1] + right[:-1], cx, cy, ang_deg)


def _bamboo_leaf_pts(cx: float, cy: float, length: float, width: float, ang_deg: float, droop: float = 0.22) -> list[list[float]]:
    hw = width / 2.0
    ty = length * droop
    up = _cubic((0, 0), (length * 0.26, -hw * 1.1 + ty * 0.1), (length * 0.68, -hw * 0.3 + ty * 0.55), (length, ty), 5)
    lo = _cubic((length, ty), (length * 0.68, hw * 0.55 + ty * 0.65), (length * 0.26, hw * 1.05 + ty * 0.18), (0, 0), 5)
    return _rot_trans(up[:-1] + lo[:-1], cx, cy, ang_deg)


def _grad(color: str, *, gtype: str = "linear", angle: float = 90.0, tint: bool = True, op_end: float = 0.92) -> dict:
    return {
        "type": gtype,
        "angle": angle,
        "cx": 0.5,
        "cy": 0.5,
        "r": 0.62,
        "tintFromFill": tint,
        "stops": [
            {"offset": 0.0, "color": color, "opacity": 1.0},
            {"offset": 0.5, "color": color, "opacity": 0.96},
            {"offset": 1.0, "color": color, "opacity": op_end},
        ],
    }


def _style(
    fill: str | None,
    stroke: str | None = _STROKE_INK,
    width: float = 1.2,
    opacity: float = 1.0,
    *,
    gradient: dict | None = None,
    line_cap: str | None = "round",
    line_join: str | None = "round",
    feather: float | None = None,
) -> dict:
    out: dict[str, Any] = {
        "fill": fill,
        "stroke": stroke,
        "strokeWidth": width,
        "opacity": opacity,
        "lineCap": line_cap,
        "lineJoin": line_join,
    }
    if gradient is not None:
        out["gradient"] = gradient
    if feather is not None:
        out["feather"] = feather
    return out


# ---------------------------------------------------------------------------
# 符号 → 艺术节点组
# ---------------------------------------------------------------------------


def _lotus(prefix: str, ax: float, ay: float, color: str, parent_id: str | None = None) -> list[dict]:
    """工笔重瓣莲：荷叶 + 荷茎 + 外/内两层尖瓣工笔晕染花瓣 + 金黄莲蓬。"""
    gid = f"{prefix}-lotus"
    nodes: list[dict] = [
        {
            "id": gid,
            "parentId": parent_id,
            "type": "group",
            "transform": {"x": _r2(ax), "y": _r2(ay)},
            "style": {"opacity": 1.0},
        },
        # 荷茎（局部坐标相对于 (ax, ay)）
        {
            "id": f"{prefix}-stem",
            "parentId": gid,
            "type": "polyline",
            "geometry": {"points": [[0, 14], [-3, 62], [-8, 108]]},
            "style": _style(None, "#3E6248", 5.5, 0.94),
        },
    ]
    # 写意荷叶（左右舒展）
    leaf_pts = [
        [_r2(-74 + 78 * math.cos(2 * math.pi * i / 20)), _r2(44 + 26 * math.sin(2 * math.pi * i / 20))]
        for i in range(20)
    ]
    nodes.append({
        "id": f"{prefix}-pad",
        "parentId": gid,
        "type": "polygon",
        "geometry": {"points": leaf_pts},
        "style": _style("#47725B", "#2C4E3D", 1.1, 0.88, gradient=_grad("#47725B", angle=125)),
        "material": {"shaderRef": "ink_wash"},
    })
    # 外层 7 片工笔尖瓣（使用主色 color 并开启 tintFromFill 分染渐变）
    for idx, (ang, ln, wd) in enumerate([
        (-162, 76, 28), (-138, 86, 32), (-114, 94, 34), (-90, 98, 36),
        (-66, 94, 34), (-42, 86, 32), (-18, 76, 28),
    ]):
        nodes.append({
            "id": f"{prefix}-p{idx}",
            "parentId": gid,
            "type": "polygon",
            "geometry": {"points": _petal_pts(0, 6, ln, wd, ang)},
            "style": _style(color, "#7F3A4C", 0.95, 0.92, gradient=_grad(color, angle=ang + 180)),
            "material": {"shaderRef": "gongbi"},
        })
    # 内层 5 片柔光内瓣
    for idx, (ang, ln, wd) in enumerate([(-144, 56, 24), (-116, 66, 26), (-90, 70, 28), (-64, 66, 26), (-36, 56, 24)]):
        nodes.append({
            "id": f"{prefix}-in{idx}",
            "parentId": gid,
            "type": "polygon",
            "geometry": {"points": _petal_pts(0, 4, ln, wd, ang)},
            "style": _style(color, "#9B4B60", 0.8, 0.82, gradient=_grad(color, angle=ang)),
            "material": {"shaderRef": "gongbi"},
        })
    # 金黄莲蓬与花蕊
    nodes.append({
        "id": f"{prefix}-core",
        "parentId": gid,
        "type": "circle",
        "geometry": {"cx": 0, "cy": -10, "r": 14},
        "style": _style("#E8B03C", "#8F6112", 0.9, 0.96, gradient=_grad("#E8B03C", gtype="radial")),
        "material": {"shaderRef": "gongbi"},
    })
    return nodes


def _bamboo(prefix: str, ax: float, ay: float, color: str, parent_id: str | None = None) -> list[dict]:
    """墨竹：分节收腰修竹竿 + 竹节环 + 斜出细枝 + 10 片柳叶竹叶。"""
    gid = f"{prefix}-bamboo"
    nodes: list[dict] = [
        {
            "id": gid,
            "parentId": parent_id,
            "type": "group",
            "transform": {"x": _r2(ax), "y": _r2(ay)},
            "style": {"opacity": 1.0},
        }
    ]
    seg_h, gap, half_w = 54.0, 4.0, 8.2
    top = -((seg_h * 4 + gap * 3) / 2.0)
    for idx in range(4):
        y0 = top + idx * (seg_h + gap)
        y1 = y0 + seg_h
        w_end = half_w * (0.82 + 0.06 * idx)
        w_mid = w_end * 0.86
        pts = [
            [_r2(-w_end), _r2(y0)], [_r2(w_end), _r2(y0)],
            [_r2(w_mid), _r2((y0 + y1) / 2)],
            [_r2(w_end), _r2(y1)], [_r2(-w_end), _r2(y1)],
            [_r2(-w_mid), _r2((y0 + y1) / 2)],
        ]
        nodes.append({
            "id": f"{prefix}-seg{idx}",
            "parentId": gid,
            "type": "polygon",
            "geometry": {"points": pts},
            "style": _style(color, "#203D27", 0.85, 0.95, gradient=_grad(color, angle=0)),
            "material": {"shaderRef": "ink_wash"},
        })
        if idx < 3:
            ny = y1 + gap / 2.0
            nodes.append({
                "id": f"{prefix}-ring{idx}",
                "parentId": gid,
                "type": "polyline",
                "geometry": {"points": [[_r2(-w_end * 1.25), _r2(ny)], [_r2(w_end * 1.25), _r2(ny)]]},
                "style": _style(None, "#1D3824", 2.4, 0.94),
            })

    # 斜出细枝与“个/介”字柳叶竹叶
    twig_specs = [
        (f"{prefix}-tw0", [[0, _r2(top + 54)], [-56, _r2(top + 26)]]),
        (f"{prefix}-tw1", [[0, _r2(top + 112)], [62, _r2(top + 82)]]),
        (f"{prefix}-tw2", [[0, _r2(top + 168)], [-52, _r2(top + 140)]]),
    ]
    for tid, pts in twig_specs:
        nodes.append({
            "id": tid,
            "parentId": gid,
            "type": "polyline",
            "geometry": {"points": pts},
            "style": _style(None, "#284730", 1.7, 0.9),
        })

    leaves = [
        (-42, top + 32, 66, 11, 165), (-42, top + 32, 58, 10, 192), (-54, top + 26, 52, 9.5, 144),
        (46, top + 88, 68, 11, -14), (46, top + 88, 60, 10, 14), (60, top + 82, 52, 9.5, -36),
        (-38, top + 146, 64, 10.5, 168), (-38, top + 146, 56, 9.5, 196),
        (10, top + 16, 56, 9.5, -24), (10, top + 16, 48, 8.8, 6),
    ]
    for idx, (lx, ly, ln, wd, ang) in enumerate(leaves):
        nodes.append({
            "id": f"{prefix}-lf{idx}",
            "parentId": gid,
            "type": "polygon",
            "geometry": {"points": _bamboo_leaf_pts(lx, ly, ln, wd, ang)},
            "style": _style(color, "#1F3B26", 0.55, 0.9, gradient=_grad(color, angle=ang)),
            "material": {"shaderRef": "ink_wash"},
        })
    return nodes


def _water(prefix: str, ax: float, ay: float, color: str, parent_id: str | None = None) -> list[dict]:
    """清涟水韵：柔和湖水晕染底面 + 四层流动波纹。"""
    gid = f"{prefix}-water"
    nodes: list[dict] = [
        {
            "id": gid,
            "parentId": parent_id,
            "type": "group",
            "transform": {"x": _r2(ax), "y": _r2(ay)},
            "style": {"opacity": 1.0},
        },
        {
            "id": f"{prefix}-wash",
            "parentId": gid,
            "type": "polygon",
            "geometry": {"points": [[-145, -36], [145, -36], [160, 44], [-160, 44]]},
            "style": _style(color, None, 0.0, 0.22, gradient=_grad(color, angle=90, op_end=0.05), feather=4.0),
        },
    ]
    for row in range(4):
        base_y = -28 + row * 20
        span = 148 - row * 12
        pts = [
            [_r2(-span + (2 * span) * (step / 20)), _r2(base_y + 7.5 * math.sin(step / 2.2 + row * 0.9) * math.sin(math.pi * step / 20))]
            for step in range(21)
        ]
        nodes.append({
            "id": f"{prefix}-wave{row}",
            "parentId": gid,
            "type": "polyline",
            "geometry": {"points": pts},
            "style": _style(None, color, 2.6 - row * 0.3, 0.72 - row * 0.08),
        })
    return nodes


def _narrative(prefix: str, ax: float, ay: float, color: str, parent_id: str | None = None) -> list[dict]:
    """叙事画境：素绢团扇月轮 + 水墨远峰 + 梅枝剪影意象。"""
    gid = f"{prefix}-narrative"
    return [
        {
            "id": gid,
            "parentId": parent_id,
            "type": "group",
            "transform": {"x": _r2(ax), "y": _r2(ay)},
            "style": {"opacity": 1.0},
        },
        {
            "id": f"{prefix}-moon",
            "parentId": gid,
            "type": "circle",
            "geometry": {"cx": 0, "cy": 0, "r": 78},
            "style": _style(color, "#C9B99B", 1.1, 0.28, gradient={
                "type": "radial", "cx": 0.5, "cy": 0.5, "r": 0.56, "tintFromFill": True,
                "stops": [
                    {"offset": 0.0, "color": color, "opacity": 0.9},
                    {"offset": 0.65, "color": color, "opacity": 0.4},
                    {"offset": 1.0, "color": color, "opacity": 0.0},
                ],
            }),
        },
        {
            "id": f"{prefix}-ring",
            "parentId": gid,
            "type": "circle",
            "geometry": {"cx": 0, "cy": 0, "r": 58},
            "style": _style(None, color, 1.4, 0.65),
        },
        {
            "id": f"{prefix}-dot",
            "parentId": gid,
            "type": "circle",
            "geometry": {"cx": 0, "cy": 0, "r": 18},
            "style": _style(color, None, 0.0, 0.88, gradient=_grad(color, gtype="radial")),
            "material": {"shaderRef": "gongbi"},
        },
    ]


_SYMBOL_BUILDERS = {
    "lotus": _lotus,
    "bamboo": _bamboo,
    "water": _water,
    "narrative": _narrative,
}


# ---------------------------------------------------------------------------
# 宣纸画卷底景、书法题跋与朱文篆印
# ---------------------------------------------------------------------------


def _backdrop_nodes(primary_symbol: str) -> list[dict]:
    """统一新中式宣纸画框、淡墨远山与月晕底衬。"""
    mtn_pts = [[28, 398]]
    for i in range(25):
        x = 28 + (744 * i / 24)
        h = 104 * math.exp(-(((x - 210) / 130) ** 2)) + 86 * math.exp(-(((x - 580) / 145) ** 2))
        edge = math.sin(math.pi * (i / 24)) ** 0.55
        mtn_pts.append([_r2(x), _r2(398 - h * edge)])
    mtn_pts.append([772, 398])

    return [
        {
            "id": "bg-frame-outer",
            "parentId": "painting",
            "type": "polygon",
            "geometry": {"points": [[16, 16], [784, 16], [784, 584], [16, 584]]},
            "style": _style(None, "#C8BDA6", 1.2, 0.72),
        },
        {
            "id": "bg-frame-inner",
            "parentId": "painting",
            "type": "polygon",
            "geometry": {"points": [[24, 24], [776, 24], [776, 576], [24, 576]]},
            "style": _style(None, "#DBD2BF", 0.8, 0.55),
        },
        {
            "id": "bg-moon-glow",
            "parentId": "painting",
            "type": "circle",
            "geometry": {"cx": 616, "cy": 122, "r": 76},
            "style": _style("#F3E4C2", None, 0.0, 0.72, gradient={
                "type": "radial", "cx": 0.5, "cy": 0.5, "r": 0.52,
                "stops": [
                    {"offset": 0.0, "color": "#FBF5E6", "opacity": 0.95},
                    {"offset": 0.52, "color": "#F2E2BD", "opacity": 0.55},
                    {"offset": 1.0, "color": "#F2E2BD", "opacity": 0.0},
                ],
            }),
        },
        {
            "id": "bg-mountain",
            "parentId": "painting",
            "type": "polygon",
            "geometry": {"points": mtn_pts},
            "style": _style("#9EB0A7", None, 0.0, 0.34, gradient={
                "type": "linear", "angle": 90,
                "stops": [
                    {"offset": 0.0, "color": "#7E948C", "opacity": 0.78},
                    {"offset": 0.7, "color": "#B3C2BB", "opacity": 0.35},
                    {"offset": 1.0, "color": "#F6F1E7", "opacity": 0.02},
                ],
            }),
            "material": {"shaderRef": "ink_wash"},
        },
    ]


def _inscription_and_seal(synth: Synthesized, primary_symbol: str) -> list[dict]:
    """在左上留白处落竖排书法题跋与传统汉字朱文印章。"""
    default_poem, seal_chars = _SYMBOL_POEMS.get(primary_symbol, ("清正自持", "清正"))
    raw_caption = (synth.caption or default_poem).strip()
    # 截取凝练的竖排题记（最多 12 字，超出则优雅截断保证不越界）
    poem = raw_caption[:12] if len(raw_caption) >= 2 else default_poem

    nodes: list[dict] = [
        {
            "id": "calligraphy-group",
            "parentId": None,
            "type": "group",
            "transform": {"x": 0, "y": 0},
            "style": {"opacity": 1.0},
        },
        {
            "id": "poem-main",
            "parentId": "calligraphy-group",
            "type": "text",
            "geometry": {
                "x": 86,
                "y": 64,
                "text": poem,
                "fontSize": 20,
                "writingMode": "vertical",
                "fontFamily": "kai",
                "letterSpacing": 5,
            },
            "style": _style("#3F352E", None, 0.0, 0.84),
        },
        {
            "id": "seal-group",
            "parentId": None,
            "type": "group",
            "transform": {"x": 706, "y": 48},
            "style": {"opacity": 0.94},
        },
        {
            "id": "seal-box",
            "parentId": "seal-group",
            "type": "polygon",
            "geometry": {"points": [[0, 0], [32, 0], [32, 46], [0, 46]]},
            "style": _style("#B2382C", "#832319", 1.1, 0.95),
        },
        {
            "id": "seal-border",
            "parentId": "seal-group",
            "type": "polygon",
            "geometry": {"points": [[3, 3], [29, 3], [29, 43], [3, 43]]},
            "style": _style(None, "#F6EFE2", 0.9, 0.88),
        },
        {
            "id": "seal-text",
            "parentId": "seal-group",
            "type": "text",
            "geometry": {
                "x": 16,
                "y": 7,
                "text": seal_chars,
                "fontSize": 14,
                "writingMode": "vertical",
                "fontFamily": "seal",
                "letterSpacing": 3,
            },
            "style": _style("#F7F0E4", None, 0.0, 0.95),
        },
    ]
    return nodes


# ---------------------------------------------------------------------------
# IR 生成
# ---------------------------------------------------------------------------


def _motion_animations(synth: Synthesized, primary_node_ids: list[str]) -> tuple[list[dict], int]:
    """把 motion 翻译成动画轨道。返回 (animations, durationMs)。"""
    if synth.motion == "none" or not primary_node_ids:
        return [], 0

    duration = 2000
    animations: list[dict] = []
    for node_id in primary_node_ids:
        if synth.motion == "sway":
            animations.append({
                "nodeId": node_id,
                "property": "rotation",
                "from": -4.0,
                "to": 4.0,
                "startMs": 0,
                "endMs": duration,
                "easing": "linear",
            })
        else:  # reveal
            animations.append({
                "nodeId": node_id,
                "property": "opacity",
                "from": 0.15,
                "to": 1.0,
                "startMs": 0,
                "endMs": duration,
                "easing": "linear",
            })
    return animations, duration


def build_ir(synth: Synthesized, *, seed: int, entity_ids: list[str]) -> DrawingIR:
    """把合成结果翻译成完整高画质新中式 IR。"""
    points = anchors(len(synth.symbol_ids), synth.composition)
    primary_symbol = synth.symbol_ids[0] if synth.symbol_ids else "lotus"

    nodes: list[dict] = [
        {
            "id": "painting",
            "parentId": None,
            "type": "group",
            "transform": {"x": 0, "y": 0},
            "style": {"opacity": 1.0},
        }
    ]
    nodes.extend(_backdrop_nodes(primary_symbol))

    primary_ids: list[str] = []
    for index, (symbol, (ax, ay)) in enumerate(zip(synth.symbol_ids, points)):
        color = synth.palette[index % len(synth.palette)]
        builder = _SYMBOL_BUILDERS.get(symbol)
        if builder is None:
            continue
        produced = builder(f"s{index}", ax, ay, color, parent_id="painting")
        nodes.extend(produced)
        if produced:
            primary_ids.append(produced[0]["id"])

    if not primary_ids:
        produced = _lotus("s0", CANVAS_W / 2, CANVAS_H / 2 + 12, synth.palette[0], parent_id="painting")
        nodes.extend(produced)
        primary_ids = [produced[0]["id"]]

    nodes.extend(_inscription_and_seal(synth, primary_symbol))

    animations, duration = _motion_animations(synth, primary_ids)
    narrative = []
    if synth.caption and entity_ids:
        narrative.append({
            "timeMs": 0,
            "entityIds": entity_ids[:100],
            "caption": synth.caption,
        })

    return DrawingIR.model_validate({
        "schemaVersion": "1.0",
        "seed": seed,
        "canvas": {"width": CANVAS_W, "height": CANVAS_H, "background": "#F6F1E7", "paper": "xuan"},
        "viewport": {"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H},
        "durationMs": duration,
        "nodes": nodes,
        "animations": animations,
        "narrative": narrative,
    })
