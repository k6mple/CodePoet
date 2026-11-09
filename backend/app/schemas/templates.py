"""模板容器与模板版本的响应模型。

字段逐条对标 ``docs/openapi-core.json`` 的 ``TemplateMetadata`` /
``Template`` / ``TemplateVersion`` / ``TemplatePage``，不另造字段名。

参数定义、绑定、约束三块在 ``app/schemas/generation.py`` 里已经按契约定义过
（POST /generations 要用同一套），这里直接复用，避免同一个结构出现两份
定义、改一处漏一处。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.generation import Binding, Constraint, ParameterDefinition
from app.schemas.ir import DrawingIR

# 契约里的后端枚举是四个，但当前基础版只实现 canvas2d
Backend = Literal["turtle", "canvas2d", "webgl", "css"]

# 模板元数据里的意象标签，四种固定取值
Symbol = Literal["lotus", "bamboo", "water", "narrative"]

# 模板版本的审核状态机：draft -> submitted -> published / rejected，published -> archived
VersionStatus = Literal["draft", "submitted", "published", "rejected", "archived"]

# scope 是查询参数，不是响应字段；放这里让服务和路由共用同一个枚举
Scope = Literal["public", "mine", "all"]


class TemplateMetadata(BaseModel):
    """模板的公开元数据。六个字段全部必填，没有值就显式给空数组或 null。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    # 契约允许空字符串，所以不设 min_length
    description: str = Field(max_length=1000)
    entityIds: list[str] = Field(default_factory=list, max_length=100)
    tagIds: list[str] = Field(default_factory=list, max_length=100)
    symbols: list[Symbol] = Field(default_factory=list, max_length=4)
    timeTheme: str | None = Field(default=None, max_length=40)


class Template(BaseModel):
    """模板容器。

    ``metadata`` 是「读者看到的元数据」：有发布版就取发布版的快照，
    没有发布版才回落到容器自己的工作元数据。``workingMetadata`` 只在
    作者本人或管理员眼里有值，其他人一律 null —— 契约 4.3 要求
    「容器工作元数据仅作者/管理员可见」。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    ownerId: str
    metadata: TemplateMetadata
    workingMetadata: TemplateMetadata | None
    publishedVersionId: str | None
    latestVersionId: str | None
    supportedBackends: list[Backend]
    thumbnailArtifactId: str | None
    # 由角色和归属推导，不落库，避免「角色改了权限没跟着改」
    permissions: list[str]


class TemplatePage(BaseModel):
    """分页信封。四个字段与契约一致，不用 items/nextCursor 那一套。"""

    model_config = ConfigDict(extra="forbid")

    items: list[Template] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


class TemplateVersion(BaseModel):
    """一个不可覆盖的模板版本。

    ``irTemplate`` 里不放占位符：模板的 IR 骨架本身就是一份合法 IR，
    参数通过声明式 bindings 写进节点属性（见 app/engine/instantiate.py）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime
    templateId: str
    versionNumber: int = Field(ge=1)
    createdBy: str
    submittedBy: str | None
    status: VersionStatus
    metadata: TemplateMetadata
    irTemplate: DrawingIR
    # 当前只支持 IR 模板，片元源码（fragmentSource）恒为 null
    generatorRef: Literal["ir"] = "ir"
    parameterSchema: list[ParameterDefinition] = Field(default_factory=list, max_length=16)
    bindings: list[Binding] = Field(default_factory=list, max_length=100)
    constraints: list[Constraint] = Field(default_factory=list, max_length=50)
    supportedBackends: list[Literal["canvas2d"]] = Field(default_factory=lambda: ["canvas2d"])
    fragmentSource: None = None
    ontologyVersion: int = Field(ge=1)
    changeNote: str = Field(default="", max_length=500)
    submittedAt: datetime | None
    publishedAt: datetime | None
    # 审核记录结构属于审核任务（P1A-04）；P0 只做透传，没有记录就是 null
    lastReview: dict[str, Any] | None


class TemplateCreate(BaseModel):
    """新建模板容器。只带元数据，版本另开接口创建。

    容器和版本分开建是刻意的：容器的元数据是可以反复改的「工作区」，
    版本才是不可变快照。先有容器才能挂版本，顺序反过来会出一堆悬空引用。
    """

    model_config = ConfigDict(extra="forbid")

    metadata: TemplateMetadata


class TemplateUpdate(BaseModel):
    """更新容器的工作元数据。

    只改 ``working_metadata_json`` 和容器上冗余的 name/description，
    **不碰已发布版本的快照**——契约明确要求「不修改已发布版本元数据」。
    新版本创建时才把当前工作元数据复制过去。
    """

    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=1)
    metadata: TemplateMetadata


class VersionCreate(BaseModel):
    """创建新版本，或从既有版本克隆。

    契约里这个 schema 是 ``oneOf`` 两支，这里用「一个模型 + 校验器」表达：

    * 给了 ``fromVersionId`` → 克隆分支，其余字段可选，显式给了就覆盖；
    * 没给 → 一次性新建分支，必须自带 ``irTemplate`` / ``parameterSchema``
      / ``supportedBackends``。

    合成一个模型的好处是前端可以只写一个构造函数，也免得 Pydantic 在
    ``oneOf`` 上给出难以解读的联合错误。
    """

    model_config = ConfigDict(extra="forbid")

    fromVersionId: str | None = Field(default=None, min_length=1, max_length=80)
    metadata: TemplateMetadata | None = None
    irTemplate: DrawingIR | None = None
    generatorRef: Literal["ir"] | None = None
    parameterSchema: list[ParameterDefinition] | None = Field(default=None, max_length=16)
    bindings: list[Binding] | None = Field(default=None, max_length=100)
    constraints: list[Constraint] | None = Field(default=None, max_length=50)
    # 契约限定 minItems=maxItems=1 且元素只能是 canvas2d
    supportedBackends: list[Literal["canvas2d"]] | None = Field(
        default=None, min_length=1, max_length=1
    )
    # 当前实现只有 IR 模板，片元源码恒为 null
    fragmentSource: None = None
    ontologyVersion: int | None = Field(default=None, ge=1)
    changeNote: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _require_new_version_fields(self) -> "VersionCreate":
        if self.fromVersionId is not None:
            return self
        missing = [
            name
            for name in ("irTemplate", "parameterSchema", "supportedBackends")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError("新建版本必须提供：" + "、".join(missing))
        return self


class TemplateVersionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TemplateVersion] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)


__all__ = [
    "Backend",
    "Scope",
    "Symbol",
    "Template",
    "TemplateCreate",
    "TemplateMetadata",
    "TemplatePage",
    "TemplateUpdate",
    "TemplateVersion",
    "TemplateVersionPage",
    "VersionCreate",
    "VersionStatus",
]
