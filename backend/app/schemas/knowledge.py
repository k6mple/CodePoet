"""知识条目的请求 / 响应模型。

对标 ``docs/openapi-core.json`` 的 ``KnowledgeCreate`` / ``KnowledgeUpdate`` /
``KnowledgeEntity`` / ``KnowledgeEntityPage``，以及被它们引用的
``SourceReference`` 与 ``Relation``。

两个容易漏的点：

* ``sourceReferences`` 的 ``url`` 是 **必填但可为 null**——条目常常只有文献
  题名没有在线链接，用 ``url: str | None`` 而不是给它默认值；
* ``KnowledgeEntity`` 比 ``KnowledgeCreate`` 多两个只读字段
  ``effectiveTagIds`` / ``inheritedTagIds``，它们由标签树推导，
  客户端传进来的同名字段一律被 ``extra="forbid"`` 拒掉。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.taxonomy import ID_PATTERN, NodeId

# 知识条目四种类型，与 knowledge_entities.type 的 CHECK 约束一致
KnowledgeType = Literal["person", "story", "concept", "symbol"]

# 实体之间四种固定谓词
RelationPredicate = Literal[
    "embodies", "associated_with", "symbolizes", "derived_from"
]


class SourceReference(BaseModel):
    """一条来源引用。url 允许为 null（纸质文献、口述材料）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(
        default=None,
        max_length=2000,
        pattern=r"^https?://",
        description="必须是 http/https 绝对地址；没有在线版本时显式给 null",
    )
    note: str = Field(default="", max_length=500)


class Relation(BaseModel):
    """知识实体之间的有向关系。targetEntityId 允许指向尚未创建的对象吗？

    不允许——服务层会校验目标存在，否则详情页会渲染出断链。
    """

    model_config = ConfigDict(extra="forbid")

    predicate: RelationPredicate
    targetEntityId: NodeId


class KnowledgeCreate(BaseModel):
    """新建知识条目。

    契约 ``required`` 是 ``type`` / ``name`` / ``summary`` / ``content`` 四项，
    所以摘要和正文必须显式传——空字符串也算传了，但不能省字段。
    这比「默认空串」严格，好处是客户端不会以为自己建了一条有内容的条目。
    """

    model_config = ConfigDict(extra="forbid")

    type: KnowledgeType
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(max_length=500)
    content: str = Field(max_length=20_000)
    sourceReferences: list[SourceReference] = Field(default_factory=list, max_length=20)
    conceptIds: list[NodeId] = Field(default_factory=list, max_length=100)
    tagIds: list[NodeId] = Field(default_factory=list, max_length=100)
    relations: list[Relation] = Field(default_factory=list, max_length=100)
    timeTheme: str | None = Field(default=None, max_length=40)
    status: Literal["active", "inactive"] = "active"


class KnowledgeUpdate(BaseModel):
    """局部更新知识条目。除 expectedRevision 外全部可选。

    契约没有声明 ``minProperties``，所以这里不做「必须改点什么」的强制校验，
    与标签接口的行为有意区分——调用方按契约来就行。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    type: KnowledgeType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    aliases: list[str] | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=20_000)
    sourceReferences: list[SourceReference] | None = Field(default=None, max_length=20)
    conceptIds: list[NodeId] | None = Field(default=None, max_length=100)
    tagIds: list[NodeId] | None = Field(default=None, max_length=100)
    relations: list[Relation] | None = Field(default=None, max_length=100)
    timeTheme: str | None = Field(default=None, max_length=40)
    status: Literal["active", "inactive"] | None = None


class KnowledgeEntity(BaseModel):
    """知识条目响应。

    ``effectiveTagIds`` = 直接打的标签 ∪ 这些标签的祖先链；
    ``inheritedTagIds`` 只算祖先链那部分。两者都由服务层现算，
    不落库——否则调整标签层级就要批量重写所有条目。
    """

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    type: KnowledgeType
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=500)
    content: str = Field(default="", max_length=20_000)
    sourceReferences: list[SourceReference] = Field(default_factory=list, max_length=20)
    conceptIds: list[NodeId] = Field(default_factory=list, max_length=100)
    tagIds: list[NodeId] = Field(default_factory=list, max_length=100)
    relations: list[Relation] = Field(default_factory=list, max_length=100)
    timeTheme: str | None = Field(default=None, max_length=40)
    status: Literal["active", "inactive"] = "active"
    effectiveTagIds: list[NodeId] = Field(default_factory=list, max_length=100)
    inheritedTagIds: list[NodeId] = Field(default_factory=list, max_length=100)


class KnowledgeEntityPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeEntity] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


__all__ = [
    "ID_PATTERN",
    "KnowledgeCreate",
    "KnowledgeEntity",
    "KnowledgeEntityPage",
    "KnowledgeType",
    "KnowledgeUpdate",
    "Relation",
    "RelationPredicate",
    "SourceReference",
]
