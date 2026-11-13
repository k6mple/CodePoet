"""知识图谱接入的请求与快照模型（K1）。

边界刻意收得很窄：

* 请求只认 **``sourceId``（部署配置里的白名单键）**，不接受用户传 URL——
  否则服务端就成了任意地址的代理，SSRF 与凭据泄露都跟着来；
* 快照 **写进 ``provenance.externalGraph``**，不新建表：它天然属于「这份 IR
  是怎么来的」，跟着 generation 一起不可变；
* ``contentHash`` 必须有、``dataVersion`` 可以没有（来源不给版本号时不要编）。

外部 id 只在这里出现，**不写进只接受站内 id 的字段**（如 ``narrative.entityIds``）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.generation import Backend
from app.schemas.ir import NodeId

__all__ = [
    "ExternalEntity",
    "ExternalGraph",
    "ExternalRelation",
    "GraphGenerationRequest",
    "MappingDecision",
]

# 快照上限（契约：最多 50 实体 / 100 关系）
MAX_ENTITIES = 50
MAX_RELATIONS = 100
MAX_DECISIONS = 100
MAX_ATTRIBUTES = 20


class GraphGenerationRequest(BaseModel):
    """``POST /knowledge-graph/generations`` 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    #: 部署配置里的来源键，如 ``wikidata``；不是 URL
    sourceId: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    #: 来源侧实体标识，如 Wikidata 的 ``Q16528``
    externalEntityId: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    backend: Backend = "canvas2d"
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class ExternalEntity(BaseModel):
    """来源侧的一个实体。``attributes`` 只放已映射、有界的事实。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=MAX_ATTRIBUTES)


class ExternalRelation(BaseModel):
    """来源侧的一条关系：``fromId --type--> toId``。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    fromId: str = Field(min_length=1, max_length=80)
    toId: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=120)


class MappingDecision(BaseModel):
    """一条外部事实影响了什么。没有这条记录，快照就只是「抓了一堆数据」。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["entity", "relation", "attribute"]
    #: 外部侧标识（Q-id 或关系 id）
    externalId: str = Field(min_length=1, max_length=80)
    #: 影响到站内的哪一类对象
    target: Literal["knowledge-entity", "rule", "caption", "symbols"]
    targetId: str | None = Field(default=None, max_length=80)
    #: 具体影响了哪个决策
    affects: Literal["rule-selection", "caption", "symbols", "palette"]
    reason: str = Field(min_length=1, max_length=200)


class ExternalGraph(BaseModel):
    """写进 ``provenance.externalGraph`` 的快照。旧作品复现只读它，不联网。"""

    model_config = ConfigDict(extra="forbid")

    sourceId: str = Field(min_length=1, max_length=40)
    externalEntityId: str = Field(min_length=1, max_length=80)
    fetchedAt: datetime
    #: 来源不给版本号时为 null——不编一个出来
    dataVersion: str | None = Field(default=None, max_length=80)
    #: 必须有：复现时要能判断「快照和当时是不是同一份」
    contentHash: str = Field(min_length=8, max_length=64)
    entities: list[ExternalEntity] = Field(default_factory=list, max_length=MAX_ENTITIES)
    relations: list[ExternalRelation] = Field(default_factory=list, max_length=MAX_RELATIONS)
    mappingDecisions: list[MappingDecision] = Field(
        default_factory=list, max_length=MAX_DECISIONS
    )
