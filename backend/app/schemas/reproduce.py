"""复现与像素比较的请求/响应模型。

对齐契约 ``ReproduceRequest`` / ``Comparison`` / ``FrameComparison``。

``Compatibility`` 的三项指标分开给，而不是合成一个"像不像"的分数——
用户需要知道「差在哪」：完全一致、差一点点、还是根本不可比。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ir import NodeId
from app.schemas.works import ClientEnvironment


class ReproduceRequest(BaseModel):
    """按历史快照重建渲染任务。

    只需要渲染器版本与浏览器环境：IR 和时刻都从历史里读，不让客户端传——
    传了就等于允许"复现成别的东西"，那就不叫复现了。
    """

    model_config = ConfigDict(extra="forbid")

    rendererVersion: str = Field(min_length=1, max_length=80)
    clientEnvironment: ClientEnvironment


class FrameComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeMs: int = Field(ge=0, le=15000)
    exactMatch: bool
    # 平均绝对误差，0~1（已按 255 归一化）
    mae: float = Field(ge=0, le=1)
    # 有任何通道差异的像素占比，0~1
    differentPixelRatio: float = Field(ge=0, le=1)


class Comparison(BaseModel):
    """原任务与复现任务的像素比较结果。

    ``compatible=false`` 时 ``exactMatch`` / ``mae`` / ``differentPixelRatio``
    都是 null——不可比的图上算出来的数字没有意义，给 0 会被误读成"完全一致"。
    """

    model_config = ConfigDict(extra="forbid")

    jobId: NodeId
    originalJobId: NodeId
    compatible: bool
    reason: str | None = Field(default=None, max_length=500)
    exactMatch: bool | None = None
    mae: float | None = Field(default=None, ge=0, le=1)
    differentPixelRatio: float | None = Field(default=None, ge=0, le=1)
    frames: list[FrameComparison] = Field(default_factory=list, max_length=3)
