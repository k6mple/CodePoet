"""审核相关的请求与响应模型。

字段逐条对齐 ``docs/openapi-core.json``：

* ``Review`` 契约 9 个字段全必填，``latestReview`` 可空但必须显式写
* ``ReviewCreate.reason`` 契约只给了 ``maxLength=500``，没给 ``minLength``——
  批准时可以留空，但拒绝必须有理由，这条按业务规则在模型层收紧
* ``ReviewQueueItem.submittedAt`` 契约是普通 ``string``（不是 ``date-time``），
  所以这里保持字符串，避免序列化时被强制转格式
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ir import NodeId

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

ReviewTargetType = Literal["template_version", "work_version"]
ReviewDecision = Literal["approve", "reject"]
ReviewStatusFilter = Literal["pending", "processed", "all"]

# 送审时允许的起始状态。模板与作品各一套，不共用——
# 混在一起以后加新状态很容易漏改一边。
TEMPLATE_SUBMITTABLE = ("draft", "rejected")
WORK_SUBMITTABLE = ("private", "rejected")


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class RevisionRequest(BaseModel):
    """带乐观锁的单字段请求体。``/submit`` 用它。"""

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)


class ArchiveRequest(BaseModel):
    """归档模板版本。``reason`` 是必填的——归档会让公开页失去模板，

    这个决定得留下理由，否则事后没人知道为什么它消失了。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class ReviewCreate(BaseModel):
    """审核裁决。``expectedRevision`` 指**目标版本**的版本号，不是审核记录。"""

    model_config = ConfigDict(extra="forbid")

    targetType: ReviewTargetType
    targetId: NodeId
    expectedRevision: int = Field(ge=1)
    decision: ReviewDecision
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> ReviewCreate:
        # 契约 5.1：拒绝理由非空。批准的 reason 可以留空。
        if self.decision == "reject" and not self.reason.strip():
            raise ValueError("拒绝时必须填写理由")
        return self


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class Review(BaseModel):
    """一条审核记录。写入后不可修改，所以没有 revision 字段。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    targetType: ReviewTargetType
    targetId: NodeId
    targetRevision: int = Field(ge=1)
    submittedBy: NodeId
    reviewerId: NodeId
    decision: ReviewDecision
    reason: str = Field(default="", max_length=500)


class ReviewQueueItem(BaseModel):
    """审核队列的一行。

    ``canReview`` 由服务层算：有审核权限、且**不是提交人**。前端据此
    决定按钮能不能点，但后端在 ``decide`` 时还会再判一次——前端状态
    只是提示，不是防线。
    """

    model_config = ConfigDict(extra="forbid")

    targetType: ReviewTargetType
    targetId: NodeId
    targetRevision: int = Field(ge=1)
    title: str = Field(max_length=120)
    submittedBy: NodeId
    submittedAt: str
    status: str = Field(max_length=24)
    canReview: bool
    latestReview: Review | None


class ReviewQueueItemPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReviewQueueItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)
