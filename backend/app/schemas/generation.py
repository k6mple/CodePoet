"""生成请求与响应的 Pydantic 模型。

唯一依据是 ``docs/openapi-core.json``。四块内容：

1. 模板参数定义（integer / number / boolean / color / enum 五种）、
   声明式绑定 ``Binding``、参数间约束 ``Constraint``；
2. 三种生成请求（template / semantic / ir），按 ``mode`` 判别；
3. ``Generation`` 响应——不可变快照，含 irHash 与来源快照；
4. 引擎能力 ``Capabilities``。

P0-03 只实现 template 与 ir 两条路径。semantic 请求模型也一并定义好，
但服务层会明确拒绝，返回 422 UNSUPPORTED_CAPABILITY，不做假实现。
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.ir import Color, DrawingIR, NodeId
from app.schemas.validation import Issue, ValidationReport

# ---- 通用受限类型 --------------------------------------------------------

# 参数名必须是合法标识符：字母开头，字母数字下划线，总长不超过 40
ParameterName = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")]

# 契约里 params 的值只能是数字、字符串或布尔
ParameterValue = Union[bool, int, float, str]

# 后端枚举：契约允许四种，但当前基础版只实现 canvas2d
Backend = Literal["turtle", "canvas2d", "webgl", "css"]

NodeType = Literal["group", "circle", "polygon", "polyline"]

AnimationProperty = Literal["rotation", "x", "y", "opacity", "scaleX", "scaleY"]


def _ensure_unique(items: list[str], field: str) -> list[str]:
    """契约里的 uniqueItems，Pydantic 没有现成开关，自己检查。"""
    if len(set(items)) != len(items):
        raise ValueError(f"{field} 不能有重复项")
    return items


# ---- 5 种参数定义 --------------------------------------------------------


class ParameterBase(BaseModel):
    """所有参数类型的公共字段。"""

    model_config = ConfigDict(extra="forbid")

    name: ParameterName
    label: str = Field(min_length=1, max_length=80)
    # 契约要求 description 必填，但允许空字符串
    description: str = Field(max_length=300)


class IntegerParameter(ParameterBase):
    type: Literal["integer"]
    default: int = Field(ge=-1_000_000, le=1_000_000)
    min: int = Field(ge=-1_000_000, le=1_000_000)
    max: int = Field(ge=-1_000_000, le=1_000_000)
    step: int = Field(ge=1, le=1_000_000)

    @field_validator("max")
    @classmethod
    def _max_not_below_min(cls, value: int, info) -> int:
        minimum = info.data.get("min")
        if minimum is not None and value < minimum:
            raise ValueError("max 不能小于 min")
        return value

    @model_validator(mode="after")
    def _default_within_range(self) -> "IntegerParameter":
        """默认值必须落在 [min, max] 内。

        引擎的 ``_resolve_parameters`` 在「未提供该参数」时直接用 ``param.default``
        而不复查，注释写的是「默认值在模型层已校验过」——所以这条必须在模型层
        真的存在。少了它，一个 default 越界的模板建出来就带着「一按生成就报错」
        的缺陷，P1A-03 的「默认参数校验」也就无从谈起。
        """
        if not self.min <= self.default <= self.max:
            raise ValueError(
                f"default「{self.default}」必须在 {self.min} 到 {self.max} 之间"
            )
        return self


class NumberParameter(ParameterBase):
    type: Literal["number"]
    default: float = Field(ge=-1_000_000, le=1_000_000)
    min: float = Field(ge=-1_000_000, le=1_000_000)
    max: float = Field(ge=-1_000_000, le=1_000_000)
    step: float = Field(ge=1e-06, le=1_000_000)

    @field_validator("max")
    @classmethod
    def _max_not_below_min(cls, value: float, info) -> float:
        minimum = info.data.get("min")
        if minimum is not None and value < minimum:
            raise ValueError("max 不能小于 min")
        return value

    @model_validator(mode="after")
    def _default_within_range(self) -> "NumberParameter":
        """同上：默认值必须在 [min, max] 内，否则模板一建出来就是坏的。"""
        if not self.min <= self.default <= self.max:
            raise ValueError(
                f"default「{self.default}」必须在 {self.min} 到 {self.max} 之间"
            )
        return self


class BooleanParameter(ParameterBase):
    type: Literal["boolean"]
    default: bool


class ColorParameter(ParameterBase):
    type: Literal["color"]
    default: Color


class EnumParameter(ParameterBase):
    type: Literal["enum"]
    default: str = Field(max_length=80)
    options: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _check_options(self) -> "EnumParameter":
        # 用模型级校验而不是字段级：字段级校验器依赖声明顺序，
        # default 在 options 之前声明时拿不到 options，会静默跳过检查。
        _ensure_unique(self.options, "options")
        if self.default not in self.options:
            raise ValueError(f"default「{self.default}」不在 options 里")
        return self


ParameterDefinition = Annotated[
    Union[
        IntegerParameter,
        NumberParameter,
        EnumParameter,
        BooleanParameter,
        ColorParameter,
    ],
    Field(discriminator="type"),
]


# ---- 绑定与约束 ----------------------------------------------------------

# 参数只能写到这 12 个属性上，这是契约给定的白名单
BindableProperty = Literal[
    "geometry.cx",
    "geometry.cy",
    "geometry.r",
    "transform.x",
    "transform.y",
    "transform.rotation",
    "transform.scaleX",
    "transform.scaleY",
    "style.fill",
    "style.stroke",
    "style.strokeWidth",
    "style.opacity",
]


class Binding(BaseModel):
    """把某个参数的值写到某个节点的某个属性上。"""

    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(max_length=40)
    nodeId: NodeId
    property: BindableProperty


class LessThanConstraint(BaseModel):
    """left 参数的值必须小于 right 参数的值。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["lessThan"]
    left: str = Field(max_length=40)
    right: str = Field(max_length=40)


class MutuallyExclusiveConstraint(BaseModel):
    """同一组参数里最多只能有一个被显式指定。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mutuallyExclusive"]
    parameters: list[str] = Field(min_length=2, max_length=8)

    @field_validator("parameters")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        _ensure_unique(value, "parameters")
        return value


Constraint = Annotated[
    Union[LessThanConstraint, MutuallyExclusiveConstraint],
    Field(discriminator="type"),
]


# ---- 三种生成请求 --------------------------------------------------------


class TemplateGeneration(BaseModel):
    """用模板版本 + 参数实例化出一份 IR。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["template"]
    templateVersionId: NodeId
    params: dict[str, ParameterValue] = Field(default_factory=dict, max_length=16)
    # 输入侧接受契约里的四个后端；能不能跑由能力层判定（S0-A1）。
    # 模板分支还要额外落在该版本的 supportedBackends 里，那是服务层的事。
    backend: Backend
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class SemanticInput(BaseModel):
    """语义生成的输入。两个字段都可选，但至少要有一个非空。"""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=2000)
    entityIds: list[NodeId] = Field(default_factory=list, max_length=100)

    @field_validator("entityIds")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        _ensure_unique(value, "entityIds")
        return value


class SemanticGeneration(BaseModel):
    """从文字或知识条目推理出 IR。P0-03 不实现，仅定义模型。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["semantic"]
    input: SemanticInput
    params: dict[str, ParameterValue] = Field(default_factory=dict, max_length=16)
    backend: Backend
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class IRGeneration(BaseModel):
    """前端直接提交一份完整 IR，用于手工编辑后的快照。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ir"]
    # 手工修改必须挂在某个已有快照上，不能凭空造
    baseGenerationId: NodeId
    ir: DrawingIR
    backend: Backend


GenerationCreate = Annotated[
    Union[TemplateGeneration, SemanticGeneration, IRGeneration],
    Field(discriminator="mode"),
]


# ---- 响应：来源快照 ------------------------------------------------------


class Decision(BaseModel):
    """某个决策的取值与依据，让复现时能解释「为什么是这样」。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=500)
    ruleIds: list[NodeId] = Field(default_factory=list, max_length=100)
    reason: str = Field(max_length=1000)


class TemplateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templateVersionId: NodeId
    reason: str = Field(max_length=1000)


class Provenance(BaseModel):
    """来源快照：这份 IR 是怎么来的、依据了哪些实体和规则。

    注意：``matchedEntities`` / ``matchedRules`` 在 P0-03 阶段恒为空数组。
    它们的严格模型是 EntitySnapshot / RuleSnapshot，依赖 KnowledgeEntity 与
    MappingRule，属于 P1B 语义生成阶段。这里先放宽为 ``list[Any]`` 并在
    服务层保证只写空数组，等 P1B 再收紧——不在 P0 阶段假装已实现。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["template", "semantic", "manual_edit", "subgraph_edit", "fragment"]
    baseGenerationId: NodeId | None = None
    matchedEntities: list[Any] = Field(default_factory=list, max_length=100)
    matchedRules: list[Any] = Field(default_factory=list, max_length=100)
    selectedSymbols: list[str] = Field(default_factory=list, max_length=10)
    paletteDecision: Decision | None = None
    compositionDecision: Decision | None = None
    timelineDecision: Decision | None = None
    templateSelection: TemplateSelection | None = None
    conflicts: list[Issue] = Field(default_factory=list, max_length=100)
    warnings: list[Issue] = Field(default_factory=list, max_length=100)
    engineVersion: str = Field(max_length=80)
    ontologyVersion: int = Field(ge=1)
    policyRevision: int = Field(ge=1)
    # 外部知识图谱快照（K1）。这里用 dict 而不是 ExternalGraph 模型：
    # schemas.knowledge_graph 为了复用 Backend 会 import 本模块，直接引用类型
    # 会成环。JSON 形状由 services.knowledge_graph 保证符合
    # schemas.knowledge_graph.ExternalGraph。
    # 旧记录的字段缺失按 null 读取，不改旧快照内容。
    externalGraph: dict[str, Any] | None = None
    fragmentSource: str | None = Field(default=None, max_length=16384)
    fragmentSourceHash: str | None = Field(default=None, max_length=64)


# ---- 响应：生成快照 ------------------------------------------------------


class Generation(BaseModel):
    """一次生成的不可变快照。落库后不再修改，只能基于它派生新快照。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    createdAt: datetime
    ownerId: NodeId
    mode: Literal["template", "semantic", "ir", "subgraph", "fragment"]
    backend: Backend
    ir: DrawingIR
    params: dict[str, ParameterValue] = Field(default_factory=dict, max_length=32)
    seed: int = Field(ge=0, le=2_147_483_647)
    templateVersionId: NodeId | None = None
    # 未发布来源会沿 IR/子图派生继承，只有基于发布版本重新生成才能清除
    unpublishedSource: bool
    # 小写十六进制 SHA-256，共 64 位
    irHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Provenance
    validationReport: ValidationReport
    warnings: list[Issue] = Field(default_factory=list, max_length=100)
    requestId: NodeId


# ---- 引擎能力 ------------------------------------------------------------


class CapabilityFeatureFlags(BaseModel):
    """哪些高级能力已经启用。未实现的一律 false，不说成「暂时可用」。"""

    model_config = ConfigDict(extra="forbid")

    semanticGeneration: bool
    subgraphEditing: bool
    #: S1 通过隔离执行、三档预算与静态审计验证后打开。为 true 的前提是
    #: 本实例真能拉起隔离 worker（``services.sandbox.probe()`` 实测）。
    dslSandbox: bool
    #: S1 的 /admin/policies 真实生效：改限额立刻影响后续 run，
    #: 只读字段与硬上限都有真实防护。
    advancedPolicies: bool
    headlessRendering: Literal[False]
    selectiveExport: Literal[False]
    workSnapshotImport: Literal[False]
    #: 本实例是否配了知识图谱来源。false 时 `POST /knowledge-graph/generations`
    #: 一律 503，前端据此关闭入口——而不是让用户点了才看到报错。
    knowledgeGraph: bool = False


class Capability(BaseModel):
    """单个后端的可用性与限制。"""

    model_config = ConfigDict(extra="forbid")

    backend: Backend
    available: bool
    unavailableReason: str | None = Field(default=None, max_length=300)
    rendererVersion: str = Field(max_length=80)
    nodeTypes: list[NodeType] = Field(max_length=4)
    shaderRefs: list[Literal["flat", "pulse"]] = Field(max_length=2)
    animationProperties: list[AnimationProperty] = Field(max_length=6)
    supportsContinuousPlayback: bool
    maxNodes: int = Field(ge=1, le=3000)
    maxFrames: int = Field(ge=1, le=12)
    executionModes: list[Literal["browser_upload", "server"]] = Field(max_length=2)


class Capabilities(BaseModel):
    """GET /engine/capabilities 的响应，前端据此知道自己能画什么。"""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"]
    backends: list[Capability] = Field(min_length=4, max_length=4)
    maxDurationMs: Literal[5000]
    maxCanvasSize: Literal[800]
    profile: Literal["core-demo"]
    maxVertices: Literal[2000]
    maxDepth: Literal[4]
    maxParameters: Literal[16]
    features: CapabilityFeatureFlags
