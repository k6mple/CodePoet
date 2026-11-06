"""IR（绘图中间表示）的 Pydantic 模型。

唯一依据是 ``docs/openapi-core.json`` 的 ``components.schemas``，字段名、
取值范围、枚举和判别器逐条对应。这里只负责「单字段 + 单层结构」的校验；
跨字段规则（引用完整性、环、层级深度、顶点总数、多边形几何）放在
``app/engine/validate.py``，因为那些规则 JSON Schema 表达不了。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- 契约中的受限基础类型 ------------------------------------------------

# 只接受 #RRGGBB，不接受 #RGB / rgba() / 颜色名
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]

# 节点 ID 是不透明标识，允许字母数字下划线连字符，不要从前缀推断权限
NodeId = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]

Coordinate = Annotated[float, Field(ge=-8192, le=8192)]

# 一个点就是恰好两个数字的数组
Point = tuple[Coordinate, Coordinate]


class Transform(BaseModel):
    """节点自身的坐标变换。所有字段都有默认值，等于「不做变换」。"""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(default=0, ge=-8192, le=8192)
    y: float = Field(default=0, ge=-8192, le=8192)
    rotation: float = Field(default=0, ge=-36000, le=36000)
    scaleX: float = Field(default=1, ge=0.1, le=10)
    scaleY: float = Field(default=1, ge=0.1, le=10)


class Viewport(BaseModel):
    """逻辑画布上的可见窗口，用于局部编辑和裁剪。"""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, le=2047)
    y: int = Field(ge=0, le=2047)
    width: int = Field(ge=1, le=2048)
    height: int = Field(ge=1, le=2048)


class Canvas(BaseModel):
    """逻辑画布尺寸固定 800×600，只允许换背景色。"""

    model_config = ConfigDict(extra="forbid")

    width: Literal[800]
    height: Literal[600]
    background: Color


class LeafStyle(BaseModel):
    """叶子节点样式。四个字段全部必填，没有填充/描边时显式传 null。"""

    model_config = ConfigDict(extra="forbid")

    fill: Color | None
    stroke: Color | None
    strokeWidth: float = Field(ge=0, le=20)
    opacity: float = Field(ge=0, le=1)


class GroupStyle(BaseModel):
    """分组只有整体透明度，没有填充和描边。"""

    model_config = ConfigDict(extra="forbid")

    opacity: float = Field(ge=0, le=1)


class Material(BaseModel):
    """材质只支持 flat；WebGL 等其他后端属于选做，不在此表示。"""

    model_config = ConfigDict(extra="forbid")

    shaderRef: Literal["flat"]


class CircleGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cx: float = Field(ge=-8192, le=8192)
    cy: float = Field(ge=-8192, le=8192)
    r: float = Field(ge=0.1, le=2048)


class PolygonGeometry(BaseModel):
    """简单非自交、非零面积由 validate 做跨字段校验，这里只约束点数与坐标。"""

    model_config = ConfigDict(extra="forbid")

    points: list[Point] = Field(min_length=3, max_length=512)


class PolylineGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[Point] = Field(min_length=2, max_length=512)


class _NodeBase(BaseModel):
    """四种节点共有的字段。parentId 必填但可为 null。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    parentId: NodeId | None
    # 契约里 transform 不是必填项；缺省时等价于单位变换
    transform: Transform = Field(default_factory=Transform)


class GroupNode(_NodeBase):
    type: Literal["group"]
    style: GroupStyle


class CircleNode(_NodeBase):
    type: Literal["circle"]
    geometry: CircleGeometry
    style: LeafStyle
    material: Material | None = None


class PolygonNode(_NodeBase):
    type: Literal["polygon"]
    geometry: PolygonGeometry
    style: LeafStyle
    material: Material | None = None


class PolylineNode(_NodeBase):
    type: Literal["polyline"]
    geometry: PolylineGeometry
    style: LeafStyle
    material: Material | None = None


# 按 type 字段自动分派到具体节点模型
IRNode = Annotated[
    GroupNode | CircleNode | PolygonNode | PolylineNode,
    Field(discriminator="type"),
]


class Animation(BaseModel):
    """线性动画轨道。其他缓动和缩放动画按计划留作扩展。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    nodeId: NodeId
    property: Literal["rotation", "x", "y", "opacity"]
    # from 是 Python 保留字，只能用别名承接 JSON 里的 "from"
    from_: float = Field(alias="from", ge=-36000, le=36000)
    to: float = Field(ge=-36000, le=36000)
    startMs: int = Field(ge=0, le=5000)
    endMs: int = Field(ge=1, le=5000)
    easing: Literal["linear"]


class Narrative(BaseModel):
    """叙事时间点上关联的知识实体。"""

    model_config = ConfigDict(extra="forbid")

    timeMs: int = Field(ge=0, le=15000)
    entityIds: list[NodeId] = Field(max_length=100)
    caption: str = Field(max_length=200)

    @field_validator("entityIds")
    @classmethod
    def _entity_ids_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("entityIds 不能重复")
        return value


class DrawingIR(BaseModel):
    """完整绘图 IR，也是保存、复现和像素比较的最小单元。"""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"]
    seed: int = Field(ge=0, le=2147483647)
    canvas: Canvas
    viewport: Viewport
    durationMs: int
    nodes: list[IRNode] = Field(min_length=1, max_length=200)
    animations: list[Animation] = Field(max_length=200)
    narrative: list[Narrative] = Field(max_length=20)

    @field_validator("durationMs")
    @classmethod
    def _duration_allowed(cls, value: int) -> int:
        # 契约：0 表示静态图，否则必须在 1000~5000ms
        if value != 0 and not 1000 <= value <= 5000:
            raise ValueError("durationMs 只能是 0（静态）或 1000~5000（动画）")
        return value
