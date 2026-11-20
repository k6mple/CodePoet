"""X1 LLM 智能辅助提案的请求与响应模型。

严格遵循 ``docs/提分阶段接口增量约定.md`` 第 7、9 节契约：
- 请求体禁止客户端传入任何模型 URL 或密钥（extra="forbid"）；
- ir_fragment 提案固定 {rootNodeId, replacementNodes}；
- parameter_schema 提案固定 {parameterSchema, bindings, defaults}；
- proposal 在 status == "ready" 前恒为 null。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.generation import Binding, ParameterDefinition, ParameterValue
from app.schemas.ir import IRNode, NodeId
from app.schemas.validation import Issue

AssistantTarget = Literal["ir_fragment", "parameter_schema"]
ProposalStatus = Literal["queued", "running", "ready", "failed"]
TERMINAL_STATUSES = frozenset({"ready", "failed"})


class AssistantProposalCreate(BaseModel):
    """POST /assistant/proposals 请求体。"""

    model_config = ConfigDict(extra="forbid")

    baseGenerationId: NodeId
    prompt: str = Field(min_length=1, max_length=2000)
    target: AssistantTarget
    rootNodeId: NodeId | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("自然语言需求不能为空")
        return stripped

    @model_validator(mode="after")
    def _check_target_root(self) -> "AssistantProposalCreate":
        if self.target == "ir_fragment" and not self.rootNodeId:
            raise ValueError("target 为 ir_fragment 时必须指定 rootNodeId")
        if self.target == "parameter_schema" and self.rootNodeId is not None:
            raise ValueError("target 为 parameter_schema 时不允许传入 rootNodeId")
        return self


class IRFragmentProposal(BaseModel):
    """ir_fragment 提案结构：对指定 rootNodeId 的合法子树替换。"""

    model_config = ConfigDict(extra="forbid")

    rootNodeId: NodeId
    replacementNodes: list[IRNode] = Field(min_length=1, max_length=200)


class ParameterSchemaProposal(BaseModel):
    """parameter_schema 提案结构：临时参数空间定义、绑定与默认值。"""

    model_config = ConfigDict(extra="forbid")

    parameterSchema: list[ParameterDefinition] = Field(min_length=1, max_length=16)
    bindings: list[Binding] = Field(min_length=1, max_length=64)
    defaults: dict[str, ParameterValue] = Field(default_factory=dict, max_length=16)


class ProposalValidation(BaseModel):
    """提案的隔离校验报告。"""

    model_config = ConfigDict(extra="forbid")

    policyRevision: int = Field(ge=1)
    issues: list[Issue] = Field(default_factory=list, max_length=100)
    isolatedValidationPassed: bool
    #: 若经历第 2 次自动修复，记录第 1 次被检出并修复的问题清单，便于前端展示修复过程
    repairedIssues: list[Issue] = Field(default_factory=list, max_length=100)


class ProposalError(BaseModel):
    """提案失败原因（脱敏，不含密钥或内部路径）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)
    details: dict[str, Any] | None = None


class AssistantProposal(BaseModel):
    """提案对外响应模型。"""

    model_config = ConfigDict(extra="forbid")

    id: NodeId
    status: ProposalStatus
    baseGenerationId: NodeId
    target: AssistantTarget
    rootNodeId: NodeId | None = None
    model: str = Field(min_length=1, max_length=120)
    attempts: int = Field(ge=0, le=2)
    proposal: dict[str, Any] | None = None
    proposalHash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validation: ProposalValidation | None = None
    error: ProposalError | None = None
    appliedGenerationId: NodeId | None = None
    createdAt: datetime
    finishedAt: datetime | None = None
