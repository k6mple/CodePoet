"""编辑会话、快照与收藏的请求/响应模型。

字段逐条对齐 ``docs/openapi-core.json`` 的 ``EditorState`` / ``CreationSession``
/ ``Snapshot`` / ``SnapshotPage`` / ``SnapshotSaved`` / ``FavoriteResult``。

两处容易写松的地方，这里都收紧了：

* ``semanticInput`` 契约是 ``additionalProperties: false`` 且 ``text`` /
  ``entityIds`` 都必填——不是「随便传个对象」。写松了前端会以为可以传
  任意键，等 P1-B 真校验时才炸。
* ``params`` 契约限制值只能是数字(-1e6~1e6)/字符串(≤200)/布尔，且最多 32 个键。
  Pydantic 的联合类型对 ``True`` 会先匹配 ``int``，所以这里手工判一遍类型，
  不依赖模型自动推断。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.generation import Backend
from app.schemas.ir import DrawingIR, NodeId

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

EditorMode = Literal["template", "semantic", "ir"]

_PARAM_MAX_KEYS = 32
_PARAM_NUMBER_LIMIT = 1_000_000
_PARAM_STRING_LIMIT = 200


# ---------------------------------------------------------------------------
# 编辑现场
# ---------------------------------------------------------------------------


class SemanticInput(BaseModel):
    """语义输入。契约把 ``additionalProperties`` 设为 false，只能有这两个键。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=2000)
    entityIds: list[str] = Field(default_factory=list, max_length=100)


class EditorState(BaseModel):
    """一次编辑的完整现场。整份存进 ``creation_sessions.state_json``。"""

    model_config = ConfigDict(extra="forbid")

    mode: EditorMode
    templateVersionId: NodeId | None
    params: dict[str, Any] = Field(default_factory=dict)
    semanticInput: SemanticInput
    draftIR: DrawingIR | None
    # 契约写的是 const ""，P1-B 之前不允许别的取值
    fragmentSource: Literal[""] = ""
    backend: Backend
    seed: int = Field(ge=0, le=2147483647)
    timelineTimeMs: int = Field(ge=0, le=5000)
    selectedNodeId: NodeId | None
    generationId: NodeId | None

    @field_validator("params")
    @classmethod
    def _params_shape(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > _PARAM_MAX_KEYS:
            raise ValueError(f"params 最多 {_PARAM_MAX_KEYS} 个键")
        for key, item in value.items():
            # bool 必须排在数字前面判：Python 里 True 也是 int
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError(f"params.{key} 不能是 NaN 或无穷")
                if not -_PARAM_NUMBER_LIMIT <= item <= _PARAM_NUMBER_LIMIT:
                    raise ValueError(
                        f"params.{key} 超出 [{-_PARAM_NUMBER_LIMIT}, {_PARAM_NUMBER_LIMIT}]"
                    )
                continue
            if isinstance(item, str):
                if len(item) > _PARAM_STRING_LIMIT:
                    raise ValueError(f"params.{key} 超过 {_PARAM_STRING_LIMIT} 字符")
                continue
            raise ValueError(f"params.{key} 只能是数字、字符串或布尔")
        return value


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    state: EditorState


class SessionUpdate(BaseModel):
    """PATCH 语义：省略的字段保持原值。"""

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    state: EditorState | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    snapshotId: NodeId


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class CreationSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    ownerId: NodeId
    name: str = Field(max_length=80)
    state: EditorState
    latestSnapshotId: NodeId | None


class CreationSessionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CreationSession] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    sessionId: NodeId
    sessionRevision: int = Field(ge=1)
    state: EditorState
    name: str = Field(max_length=80)
    note: str = Field(max_length=200)
    restoredFromSnapshotId: NodeId | None


class SnapshotPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Snapshot] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


class SnapshotSaved(BaseModel):
    """``POST /sessions/{id}/restore`` 的响应：恢复后的会话 + 恢复后快照。"""

    model_config = ConfigDict(extra="forbid")

    session: CreationSession
    snapshot: Snapshot


class FavoriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workId: NodeId
    isFavorite: bool
