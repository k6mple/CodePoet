"""语义规则推理：文字/实体 → 规则命中 → 图形决策 → IR。

这个模块是 P1B-01 的核心。它存在的意义不是"能生成一张图"，而是
**同样的输入永远出同样的图，不同的输入出不同的图**。为此：

* 规则排序确定：``priority`` 降序，同优先级按 ``created_at`` 升序、最后按 id。
  缺了最后这一层，同优先级的两条规则在两次查询里可能换位置，
  同一句话刷两次就画出两张不同的图。
* 空规则不命中任何输入。让空规则匹配一切是配置事故的常见来源——
  一条刚建好还没填条件的规则会静默覆盖所有其他规则。
* 命中依据写进 provenance（``matched_by``），前端能显示"为什么画出这个"。

不导入 FastAPI、不接触 ORM：入参是行 + 事实集合，这样匹配逻辑能脱离
数据库单独测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.schemas.ir import DrawingIR
from app.schemas.rules import RuleEffects, RuleMatch

CANVAS_W = 800
CANVAS_H = 600

# 规则没给调色板时的兜底。四个颜色对应四种符号的惯用色，但不绑定——
# 规则里写了什么就用什么。
DEFAULT_PALETTE = ["#D85A30", "#1D9E75", "#378ADD", "#BA7517"]

_STROKE_DARK = "#4A1B0C"


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

    # 空规则不命中：否则一条刚建好还没填条件的规则会覆盖所有输入
    if not non_empty:
        return []

    if match.operator == "all":
        # 组内 OR、组间 AND：每个非空组都要有命中
        if not all(hits for _, _, hits in non_empty):
            return []
    else:
        # any：任一组命中即可
        if not any(hits for _, _, hits in non_empty):
            return []

    merged: list[str] = []
    for hits in (keyword_hits, entity_hits, concept_hits, tag_hits):
        for item in hits:
            if item not in merged:
                merged.append(item)
    return merged


def order_key(row: Any) -> tuple:
    """规则排序键：优先级高的在前，同优先级让先建的胜出。

    最后一层用 id 兜底，保证排序是全序——只靠 priority + created_at
    时，同一秒创建的两条规则顺序不确定，同一输入会出不同的图。
    """
    return (-int(row.priority), str(row.created_at), str(row.id))


def match_rules(rows: list[Any], facts: RuleFacts,
                effects_of) -> list[MatchedRule]:
    """筛出命中的启用规则，按确定顺序返回。

    ``effects_of(row)`` 把行的 JSON 解析成 ``RuleEffects``，交给调用方实现
    是为了让本函数不必知道模型长什么样。
    """
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
    """把多条规则的 effects 合成一份可执行的图形决策。"""

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
    """高优先级规则决定"怎么摆、什么色、动不动"，符号取所有命中的并集。

    为什么符号取并集而其余取最高优先级：输入"莲花和竹子"时两样都该出现，
    但构图只能有一种。palette 取第一条非空的——最高优先级规则没指定
    颜色时，让下一条有意见的上，比直接掉回默认色更符合直觉。
    """
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
        # composition / motion 在契约里是必填且有默认值，所以"是否被显式
        # 指定"看不出来；这里按最高优先级规则的取值走，语义最直白
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
    cx, cy = CANVAS_W / 2, CANVAS_H / 2
    if composition == "vertical":
        step = 380 / count
        return [(cx, 110 + step * (index + 0.5)) for index in range(count)]
    if composition == "horizontal":
        step = 640 / count
        return [(80 + step * (index + 0.5), cy) for index in range(count)]
    if composition == "radial":
        radius = 150
        return [
            (
                cx + radius * math.cos(2 * math.pi * index / count - math.pi / 2),
                cy + radius * math.sin(2 * math.pi * index / count - math.pi / 2),
            )
            for index in range(count)
        ]
    # centered：单个居中，多个纵向微错开
    if count == 1:
        return [(cx, cy)]
    step = 160
    start = cy - step * (count - 1) / 2
    return [(cx, start + step * index) for index in range(count)]


# ---------------------------------------------------------------------------
# 符号 → 节点
# ---------------------------------------------------------------------------


def _style(fill: str | None, stroke: str | None = _STROKE_DARK,
           width: float = 2.0, opacity: float = 1.0) -> dict:
    return {
        "fill": fill,
        "stroke": stroke,
        "strokeWidth": width,
        "opacity": opacity,
    }


def _lotus(prefix: str, ax: float, ay: float, color: str) -> list[dict]:
    """莲：六瓣环绕 + 花心。花瓣用圆表达，视觉上能分辨出与竹/水的不同。"""
    nodes = []
    for index in range(6):
        angle = 2 * math.pi * index / 6 - math.pi / 2
        nodes.append({
            "id": f"{prefix}-p{index}",
            "parentId": None,
            "type": "circle",
            "geometry": {
                "cx": round(ax + 58 * math.cos(angle), 2),
                "cy": round(ay + 58 * math.sin(angle), 2),
                "r": 46,
            },
            "style": _style(color),
        })
    nodes.append({
        "id": f"{prefix}-core",
        "parentId": None,
        "type": "circle",
        "geometry": {"cx": round(ax, 2), "cy": round(ay, 2), "r": 30},
        "style": _style("#FAC775", "#854F0B"),
    })
    return nodes


def _bamboo(prefix: str, ax: float, ay: float, color: str) -> list[dict]:
    """竹：三段竖节。用矩形表达"节节上升"。"""
    nodes = []
    height, width, gap = 62.0, 34.0, 8.0
    total = height * 3 + gap * 2
    top = ay - total / 2
    for index in range(3):
        y0 = top + index * (height + gap)
        x0 = ax - width / 2
        nodes.append({
            "id": f"{prefix}-seg{index}",
            "parentId": None,
            "type": "polygon",
            "geometry": {
                "points": [
                    [round(x0, 2), round(y0, 2)],
                    [round(x0 + width, 2), round(y0, 2)],
                    [round(x0 + width, 2), round(y0 + height, 2)],
                    [round(x0, 2), round(y0 + height, 2)],
                ]
            },
            "style": _style(color),
        })
    return nodes


def _water(prefix: str, ax: float, ay: float, color: str) -> list[dict]:
    """水：两条波浪。用折线表达流动感。"""
    nodes = []
    for row in range(2):
        base_y = ay - 30 + row * 60
        points = [
            [round(ax - 120 + 12 * step, 2), round(base_y + 14 * math.sin(step / 2), 2)]
            for step in range(21)
        ]
        nodes.append({
            "id": f"{prefix}-wave{row}",
            "parentId": None,
            "type": "polyline",
            "geometry": {"points": points},
            "style": _style(None, color, 4.0),
        })
    return nodes


def _narrative(prefix: str, ax: float, ay: float, color: str) -> list[dict]:
    """叙事：一个框 + 一点。框代表"这里要讲故事"。"""
    width, height = 220.0, 140.0
    return [
        {
            "id": f"{prefix}-frame",
            "parentId": None,
            "type": "polygon",
            "geometry": {
                "points": [
                    [round(ax - width / 2, 2), round(ay - height / 2, 2)],
                    [round(ax + width / 2, 2), round(ay - height / 2, 2)],
                    [round(ax + width / 2, 2), round(ay + height / 2, 2)],
                    [round(ax - width / 2, 2), round(ay + height / 2, 2)],
                ]
            },
            "style": _style("#F1EFE8", color, 3.0),
        },
        {
            "id": f"{prefix}-dot",
            "parentId": None,
            "type": "circle",
            "geometry": {"cx": round(ax, 2), "cy": round(ay, 2), "r": 26},
            "style": _style(color, None, 0.0),
        },
    ]


_SYMBOL_BUILDERS = {
    "lotus": _lotus,
    "bamboo": _bamboo,
    "water": _water,
    "narrative": _narrative,
}


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
                "from": -6.0,
                "to": 6.0,
                "startMs": 0,
                "endMs": duration,
                "easing": "linear",
            })
        else:  # reveal
            animations.append({
                "nodeId": node_id,
                "property": "opacity",
                "from": 0.0,
                "to": 1.0,
                "startMs": 0,
                "endMs": duration,
                "easing": "linear",
            })
    return animations, duration


def build_ir(synth: Synthesized, *, seed: int, entity_ids: list[str]) -> DrawingIR:
    """把合成结果翻译成完整 IR。"""
    points = anchors(len(synth.symbol_ids), synth.composition)
    nodes: list[dict] = []
    primary_ids: list[str] = []

    for index, (symbol, (ax, ay)) in enumerate(zip(synth.symbol_ids, points)):
        color = synth.palette[index % len(synth.palette)]
        builder = _SYMBOL_BUILDERS.get(symbol)
        if builder is None:
            continue
        produced = builder(f"s{index}", ax, ay, color)
        nodes.extend(produced)
        if produced:
            primary_ids.append(produced[0]["id"])

    # 一个符号都没解析出来时给一张占位图，而不是空 IR（空 IR 无法通过校验）
    if not nodes:
        nodes = _lotus("s0", CANVAS_W / 2, CANVAS_H / 2, synth.palette[0])
        primary_ids = [nodes[0]["id"]]

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
        "canvas": {"width": CANVAS_W, "height": CANVAS_H, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H},
        "durationMs": duration,
        "nodes": nodes,
        "animations": animations,
        "narrative": narrative,
    })
