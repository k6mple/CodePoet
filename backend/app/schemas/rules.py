"""语义映射规则的请求/响应模型。

字段逐条对齐 ``docs/openapi-core.json`` 的 ``MappingRule`` / ``RuleCreate``
/ ``RuleUpdate`` / ``RuleMatch`` / ``RuleEffects``。

一处刻意收紧：``palette`` 在契约里只是 ``string[]``，这里要求每项是
``#RRGGBB``。理由是这些颜色会被直接写进 IR 的 ``style.fill``，而 IR 那边的
校验就是这个正则——现在放过，等生成时才 422，报错还指不到是规则配错了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ir import NodeId
from app.schemas.templates import Symbol

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

MatchOperator = Literal["any", "all"]
Composition = Literal["centered", "vertical", "horizontal", "radial"]
Motion = Literal["none", "sway", "reveal"]

HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class RuleMatch(BaseModel):
    """命中条件。四组条件之间的关系由 ``operator`` 决定：

    * ``any``：任一组命中即算（组内也是 OR）
    * ``all``：每个**非空**组都要命中（组内 OR、组间 AND）

    全空的规则不会命中任何输入——空规则匹配一切是配置事故的常见来源。
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, max_length=20)
    entityIds: list[str] = Field(default_factory=list, max_length=100)
    conceptIds: list[str] = Field(default_factory=list, max_length=100)
    tagIds: list[str] = Field(default_factory=list, max_length=100)
    operator: MatchOperator = "any"


class RuleEffects(BaseModel):
    """命中之后要做什么。这些值会被翻译成真实的 IR 节点。"""

    model_config = ConfigDict(extra="forbid")

    symbolIds: list[Symbol] = Field(default_factory=list, max_length=4)
    palette: list[str] = Field(default_factory=list, max_length=8)
    composition: Composition = "centered"
    motion: Motion = "none"
    caption: str = Field(default="", max_length=200)

    @field_validator("palette")
    @classmethod
    def _check_colors(cls, value: list[str]) -> list[str]:
        pattern = re.compile(HEX_COLOR)
        for item in value:
            if not pattern.match(item):
                raise ValueError(f"palette 里的「{item}」不是 #RRGGBB 颜色")
        return value


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=1000)
    match: RuleMatch
    effects: RuleEffects
    explanation: str = Field(default="", max_length=500)


class RuleUpdate(BaseModel):
    """PATCH 语义：省略的字段保持原值。"""

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    match: RuleMatch | None = None
    effects: RuleEffects | None = None
    explanation: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class MappingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    name: str = Field(min_length=1, max_length=80)
    enabled: bool
    priority: int = Field(ge=0, le=1000)
    match: RuleMatch
    effects: RuleEffects
    explanation: str = Field(default="", max_length=500)
    version: int = Field(ge=1)


class MappingRulePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MappingRule] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)
