"""作品、作品版本、渲染任务与产物的请求/响应模型。

字段与取值范围逐条取自 ``docs/openapi-core.json``，不额外放宽：

* ``Manifest.schemaVersion`` 在契约里是 ``const: 1.0``（number），不是字符串
* ``RenderCreate.backend`` / ``executionMode`` 都是 ``const``，只接受单值
* ``timesMs`` 契约给 RenderCreate 限 1~3 个、单个 0~15000；给 manifest 限 12 个

``Work`` / ``WorkVersion`` / ``RenderJob`` 的可空字段在契约里用 ``anyOf [T, null]``
表达，这里实现成 ``T | None``；构造时必须显式写 ``None``，用默认值兜会掩盖漏赋值。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.generation import Backend, Generation
from app.schemas.ir import NodeId, Viewport
from app.schemas.quality import Assessment
from app.schemas.validation import Issue

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

JobStatus = Literal["queued", "running", "succeeded", "failed", "timed_out", "cancelled"]
JobPhase = Literal["awaiting_frames", "rendering", "completed", "finished"]
ExecutionMode = Literal["browser_upload", "server"]
WorkVersionStatus = Literal["private", "pending_review", "published", "rejected"]
Scope = Literal["public", "mine", "all", "favorites"]

SHA256 = r"^[0-9a-f]{64}$"


# ---------------------------------------------------------------------------
# 渲染任务
# ---------------------------------------------------------------------------


class ClientEnvironment(BaseModel):
    """浏览器环境指纹。deviceScaleFactor 契约写死 const 1，不做缩放。"""

    model_config = ConfigDict(extra="forbid")

    userAgent: str = Field(min_length=1, max_length=512)
    deviceScaleFactor: float = Field(description="契约固定为 1")

    @field_validator("deviceScaleFactor")
    @classmethod
    def _only_unit_scale(cls, value: float) -> float:
        if value != 1:
            raise ValueError("deviceScaleFactor 必须是 1")
        return value


class JobError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=80)
    message: str = Field(max_length=1000)
    details: object | None = None


class Frame(BaseModel):
    """manifest 里的一帧：指向一个已上传产物。"""

    model_config = ConfigDict(extra="forbid")

    artifactId: NodeId
    sha256: str = Field(pattern=SHA256)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    timeMs: int = Field(ge=0, le=15000)


class Manifest(BaseModel):
    """渲染清单。整份存进 render_jobs.manifest_json，不拆表。"""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: float
    generationId: NodeId
    backend: Backend
    rendererVersion: str = Field(max_length=80)
    environmentHash: str = Field(max_length=64)
    irHash: str = Field(pattern=SHA256)
    dependencySnapshotHash: str = Field(max_length=64)
    viewport: Viewport
    timesMs: list[int] = Field(min_length=1, max_length=12)
    frames: list[Frame] = Field(min_length=1, max_length=12)
    archiveArtifactId: NodeId | None = None
    outputSource: ExecutionMode

    @field_validator("schemaVersion")
    @classmethod
    def _schema_version(cls, value: float) -> float:
        if value != 1.0:
            raise ValueError("Manifest.schemaVersion 必须是 1.0")
        return value

    @model_validator(mode="after")
    def _frames_match_times(self) -> Manifest:
        if len(self.frames) != len(self.timesMs):
            raise ValueError("frames 数量必须与 timesMs 一致")
        if [f.timeMs for f in self.frames] != list(self.timesMs):
            raise ValueError("frames 的 timeMs 必须与 timesMs 顺序一致")
        return self


class FrameQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeMs: int = Field(ge=0, le=15000)
    nonBackgroundPixelRatio: float = Field(ge=0, le=1)
    emptyOutput: bool


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pixelDifferenceThreshold: int = Field(ge=0)
    nonBackgroundPixelRatio: float = Field(ge=0, le=1)
    emptyOutput: bool
    frames: list[FrameQuality] = Field(default_factory=list, max_length=12)
    issues: list[Issue] = Field(default_factory=list, max_length=100)
    assessment: Assessment | None = None


class RenderCreate(BaseModel):
    """建渲染任务（仅浏览器上传模式）。

    三个浏览器后端都能表达；``turtle`` 需要 ``executionMode=server``，那套
    判别联合要到 R3 才注册，在此之前请求它会在 schema 层被明确拒绝，
    而不是被当成浏览器任务混进来。
    """

    model_config = ConfigDict(extra="forbid")

    generationId: NodeId
    backend: Literal["canvas2d", "css", "webgl"]
    timesMs: list[int] = Field(min_length=1, max_length=3)
    executionMode: Literal["browser_upload"]
    rendererVersion: str = Field(max_length=80)
    clientEnvironment: ClientEnvironment

    @field_validator("timesMs")
    @classmethod
    def _times_range(cls, value: list[int]) -> list[int]:
        for item in value:
            if not 0 <= item <= 15000:
                raise ValueError("timesMs 每项必须在 0 到 15000 之间")
        if len(set(value)) != len(value):
            raise ValueError("timesMs 不能有重复值")
        return value


class RenderJob(BaseModel):
    """渲染任务。契约 18 个字段全必填，可空字段显式写 None。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    ownerId: NodeId
    generationId: NodeId
    status: JobStatus
    backend: Backend
    timesMs: list[int] = Field(max_length=12)
    policyRevision: int = Field(ge=1)
    rendererVersion: str | None
    originalJobId: NodeId | None
    startedAt: datetime | None
    finishedAt: datetime | None
    manifest: Manifest | None
    qualityReport: QualityReport | None
    error: JobError | None
    executionMode: ExecutionMode
    phase: JobPhase
    expiresAt: datetime | None
    clientEnvironment: ClientEnvironment | None


class FrameUploadResult(BaseModel):
    """POST /render-jobs/{id}/frames 的响应就是更新后的 RenderJob。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 作品与版本
# ---------------------------------------------------------------------------


class WorkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    generationId: NodeId
    renderJobId: NodeId


class WorkVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    generationId: NodeId
    renderJobId: NodeId
    changeNote: str = Field(default="", max_length=500)


class WorkUpdate(BaseModel):
    """改作品的工作元数据（标题/描述）。

    契约明确写了「不修改既有版本标题/描述；新版本读取工作元数据」，所以
    这里改的不是任何一个已存在版本的快照——版本的 title/description 一旦
    创建就是历史，改它对不上当初审核/公开的那份内容。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)


class Work(BaseModel):
    """作品容器。15 个字段全必填，可空字段显式 None。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    ownerId: NodeId
    title: str = Field(max_length=80)
    description: str = Field(max_length=1000)
    workingTitle: str | None
    workingDescription: str | None
    publishedVersionId: NodeId | None
    latestVersionId: NodeId
    displayVersionId: NodeId
    isFavorite: bool
    thumbnailArtifactId: NodeId | None
    permissions: list[str]


class WorkPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Work]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


class WorkVersion(BaseModel):
    """作品版本。17 个字段全必填。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    workId: NodeId
    versionNumber: int = Field(ge=1)
    createdBy: NodeId
    submittedBy: NodeId | None
    status: WorkVersionStatus
    title: str = Field(max_length=80)
    description: str = Field(max_length=1000)
    generationId: NodeId
    renderJobId: NodeId
    changeNote: str = Field(max_length=500)
    submittedAt: datetime | None
    publishedAt: datetime | None
    lastReview: object | None


class WorkVersionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkVersion]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


class WorkVersionDetail(BaseModel):
    """重开作品时要拿全：版本 + 生成快照 + 渲染清单。"""

    model_config = ConfigDict(extra="forbid")

    workVersion: WorkVersion
    generation: Generation
    renderJob: RenderJob


class WorkCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work: Work
    version: WorkVersion
