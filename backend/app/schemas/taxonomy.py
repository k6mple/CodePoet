"""标签与本体概念的请求 / 响应模型。

字段逐条对标 ``docs/openapi-core.json`` 的 ``Tag`` / ``TagCreate`` /
``TagUpdate`` / ``TagPage`` 与 ``Concept`` 系列，不另造字段名，也不放宽约束。

两处容易写错、这里明确：

* ``scope`` 在标签/本体接口里只有 ``public`` / ``manage`` 两档，
  和模板接口的 ``public/mine/all`` 不是同一套枚举，不能复用；
* ``TagUpdate`` / ``ConceptUpdate`` 契约写的是 ``minProperties: 2``，
  即「``expectedRevision`` 之外必须再改点什么」。用 ``model_validator``
  表达，而不是靠调用方自觉。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 契约里所有不透明 ID 共用这一个形状；不要解析前缀推导权限
ID_PATTERN = r"^[A-Za-z0-9_-]+$"

NodeId = Annotated[str, Field(min_length=1, max_length=80, pattern=ID_PATTERN)]

# 标签 / 本体接口的查询范围，与模板接口的 Scope 不是一回事
ManageScope = Literal["public", "manage"]

NodeStatus = Literal["active", "inactive"]


class _NodeBase(BaseModel):
    """标签与本体共用的字段形状。两者结构一致，只有互斥关系是标签独有。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    parentId: NodeId | None = None
    status: NodeStatus = "active"


# ---- 标签 -----------------------------------------------------------------


class TagCreate(_NodeBase):
    """新建标签。只有 name 必填，其余走默认值。"""

    conflictsWith: list[NodeId] = Field(default_factory=list, max_length=100)


class TagUpdate(_NodeBase):
    """局部更新标签。

    ``expectedRevision`` 必填（乐观锁）；加上它之后还必须至少改一个业务字段，
    否则就是一次「什么都没干」的写入，客户端应该直接跳过。
    """

    expectedRevision: int = Field(ge=1)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    parentId: NodeId | None = None
    status: NodeStatus | None = None
    conflictsWith: list[NodeId] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _has_business_field(self) -> "TagUpdate":
        # parentId 允许显式设成 null（挪到根），所以判断的是「有没有出现在请求里」
        if self.model_fields_set <= {"expectedRevision"}:
            raise ValueError("除了 expectedRevision，至少要提供一个要修改的字段")
        return self


class Tag(_NodeBase):
    """标签响应。

    ``inheritedTagIds`` 是父链上所有祖先的 ID（不含自己），
    ``effectiveTagIds`` 只出现在知识条目上，标签自身没有这个字段。
    """

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    parentId: NodeId | None = None
    status: NodeStatus = "active"
    conflictsWith: list[NodeId] = Field(default_factory=list, max_length=100)
    inheritedTagIds: list[NodeId] = Field(default_factory=list, max_length=100)


class TagPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Tag] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


# ---- 本体概念 -------------------------------------------------------------


class ConceptCreate(_NodeBase):
    """新建本体概念。"""


class ConceptUpdate(_NodeBase):
    """局部更新本体概念，规则同 TagUpdate。"""

    expectedRevision: int = Field(ge=1)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    parentId: NodeId | None = None
    status: NodeStatus | None = None

    @model_validator(mode="after")
    def _has_business_field(self) -> "ConceptUpdate":
        if self.model_fields_set <= {"expectedRevision"}:
            raise ValueError("除了 expectedRevision，至少要提供一个要修改的字段")
        return self


class Concept(_NodeBase):
    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    parentId: NodeId | None = None
    status: NodeStatus = "active"


class ConceptPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Concept] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


__all__ = [
    "Concept",
    "ConceptCreate",
    "ConceptPage",
    "ConceptUpdate",
    "ID_PATTERN",
    "ManageScope",
    "NodeId",
    "NodeStatus",
    "Tag",
    "TagCreate",
    "TagPage",
    "TagUpdate",
]
