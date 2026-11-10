"""导入导出与审计的请求/响应模型。

字段逐条对齐 ``docs/openapi-core.json``：

* ``BackupPackage.schemaVersion`` 是 ``const: 1.0``（number），不是字符串
* ``ImportManifest.includesArtifacts`` 是 ``const: false`` —— core-demo
  不打包二进制产物，URL 里那个字段不能写 true
* ``data`` 的六个键各自有元素类型，但这里统一收成 ``list[dict]``：
  真正逐条校验放在服务层做，因为错误要精确到「第几个 tag 的 parentId
  指向不存在的 ID」，用 Pydantic 的嵌套报错很难定位到人和可读的中文
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ir import NodeId
from app.schemas.validation import Issue

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

ExportType = Literal[
    "concepts", "tags", "entities", "rules", "templates", "workSnapshots"
]
AuditResult = Literal["success", "failure"]
ActorRole = Literal["creator", "developer", "expert", "admin"]

DATA_KEYS: tuple[str, ...] = (
    "concepts",
    "tags",
    "entities",
    "rules",
    "templates",
    "workSnapshots",
)

# 契约 5.3 / 接口文档：包体与导出结果都不得超过 5MiB
MAX_PACKAGE_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """core-demo 按类型整库导出，**不支持 ids 选择**。

    ``ids`` 在契约里是 ``object`` 且 ``additionalProperties: false``——
    等于只能是空对象。这里显式拒绝非空，避免前端以为能按 ID 导。
    """

    model_config = ConfigDict(extra="forbid")

    types: list[ExportType] = Field(min_length=1, max_length=6)
    ids: dict[str, Any] = Field(default_factory=dict)

    @field_validator("types")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("types 不能重复")
        return value

    @field_validator("ids")
    @classmethod
    def _ids_must_be_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("core-demo 不支持按 ids 选择导出，请传空对象或不传")
        return value


class ManifestCounts(BaseModel):
    """六个类型的计数，契约要求全部必填（没有的填 0）。"""

    model_config = ConfigDict(extra="forbid")

    concepts: int = Field(default=0, ge=0)
    tags: int = Field(default=0, ge=0)
    entities: int = Field(default=0, ge=0)
    rules: int = Field(default=0, ge=0)
    templates: int = Field(default=0, ge=0)
    workSnapshots: int = Field(default=0, ge=0)


class ImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counts: ManifestCounts
    includesArtifacts: Literal[False] = False


class BackupData(BaseModel):
    """包体内容。缺省的键按空数组处理，方便只导某一个类型。"""

    model_config = ConfigDict(extra="forbid")

    concepts: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    templates: list[dict[str, Any]] = Field(default_factory=list)
    workSnapshots: list[dict[str, Any]] = Field(default_factory=list)


class BackupPackage(BaseModel):
    """``POST /admin/exports`` 的响应，也是导入接口的请求体形状。"""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1.0] = 1.0
    exportedAt: datetime
    manifest: ImportManifest
    data: BackupData


class CoreImportPackage(BackupPackage):
    """导入请求体。结构与导出包完全一致——导出的东西应该能原样导回。"""


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------


class ImportValidation(BaseModel):
    """预校验结果。

    ``valid=false`` 时仍然返回 200 和完整结构：前端需要 ``errors`` 逐条
    展示「第几个 tag 的 parentId 悬空」，只给一个布尔值等于让人猜。
    """

    model_config = ConfigDict(extra="forbid")

    validationId: NodeId
    valid: bool
    contentHash: str = Field(max_length=64)
    expectedDataRevision: int = Field(ge=0)
    expiresAt: datetime
    counts: ManifestCounts
    errors: list[Issue] = Field(default_factory=list, max_length=100)


class ImportApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validationId: NodeId
    expectedDataRevision: int = Field(ge=0)


class ImportApplied(BaseModel):
    model_config = ConfigDict(extra="forbid")

    importId: NodeId
    dataRevision: int = Field(ge=1)
    counts: ManifestCounts
    warnings: list[Issue] = Field(default_factory=list, max_length=100)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


class AuditLog(BaseModel):
    """契约 18 个字段全必填，可空字段显式写 None。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    actorId: NodeId | None
    actorRole: ActorRole | None
    action: str = Field(max_length=80)
    targetType: str = Field(max_length=80)
    targetId: NodeId | None
    versionId: NodeId | None
    requestId: NodeId
    parentRequestId: NodeId | None
    generationId: NodeId | None
    jobId: NodeId | None
    workId: NodeId | None
    inputSummary: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    result: AuditResult
    errorCode: str | None = Field(default=None, max_length=80)


class AuditLogPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditLog] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)
