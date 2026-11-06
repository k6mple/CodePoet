"""校验报告模型，对应契约里的 ValidationReport / Issue。

这个结构会出现在 ``POST /generations``、``/admin/imports/validate`` 等
接口的响应里，前端据此提示用户哪里不合法，所以字段名必须与契约一致。
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ir import NodeId


class Issue(BaseModel):
    """一条具体的校验问题。code 供前端做分支处理，message 给人看。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=80)
    message: str = Field(max_length=1000)
    # 出问题的位置，形如 nodes[3].geometry.points
    path: str | None = Field(default=None, max_length=200)
    # 相关节点，便于前端高亮；没有关联节点时为空数组
    nodeIds: list[NodeId] = Field(default_factory=list, max_length=100)


class ValidationReport(BaseModel):
    """校验结果汇总。issues 为空列表表示通过。"""

    model_config = ConfigDict(extra="forbid")

    # 参数是否满足模板约束；IR 校验阶段没有参数，固定为 True
    parameterValid: bool
    nodeCount: int = Field(ge=0, le=3000)
    outOfBoundsNodeCount: int = Field(ge=0, le=3000)
    issues: list[Issue] = Field(default_factory=list, max_length=100)
