"""IR 子树替换的请求/响应模型。

``replacementNodes`` 允许为空数组——那是"删掉整棵子树"的合法表达，
不是错误。真正不合法的是新节点 ID 撞车或替换后 IR 不自洽，那些在服务层拒。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.generation import Generation
from app.schemas.ir import IRNode, NodeId, Viewport


class SubgraphEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 要被替换的子树根。删掉的是它和它的全部后代
    rootNodeId: NodeId
    replacementNodes: list[IRNode] = Field(default_factory=list, max_length=200)
    # 不传则沿用基底的 viewport
    viewport: Viewport | None = None


class SubgraphGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: Generation
    # 替换进来的节点 ID（前端据此高亮"新加了什么"）
    changedNodeIds: list[NodeId] = Field(default_factory=list, max_length=200)
    # 被移除的节点 ID（含全部后代，前端据此高亮"删掉了什么"）
    removedNodeIds: list[NodeId] = Field(default_factory=list, max_length=200)
