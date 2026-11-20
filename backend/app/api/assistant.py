"""X1 LLM 智能辅助提案接口。

三个端点：
- ``POST /assistant/proposals``            —— 202 受理，后台调用模型并隔离校验
- ``GET  /assistant/proposals/{id}``       —— 仅归属者可读，他人 404
- ``POST /assistant/proposals/{id}/apply`` —— 201 显式幂等应用为新 Generation
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.orm import Session

from app import errors
from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.assistant import AssistantProposal, AssistantProposalCreate
from app.schemas.generation import Generation as GenerationSchema
from app.services import assistant as assistant_service

router = APIRouter(tags=["智能辅助"])

ProposalIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="提案 ID，不透明串",
    ),
]


def _executor(request: Request) -> assistant_service.AssistantExecutor:
    executor = getattr(request.app.state, "assistant_executor", None)
    if executor is None:
        raise errors.assistant_unavailable("智能辅助执行器未就绪")
    return executor


@router.post(
    "/assistant/proposals",
    response_model=AssistantProposal,
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建 AI 辅助提案",
    description=(
        "接收自然语言需求，由服务端配置的模型生成 IR 片段或参数空间提案，"
        "经隔离校验与最多一次自动修复后进入 ready。严禁客户端传入模型 URL 或密钥。"
    ),
)
def create_proposal(
    payload: AssistantProposalCreate,
    request: Request,
    user: UserModel = Depends(require_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> AssistantProposal:
    proposal = assistant_service.create_proposal(
        db,
        user,
        payload,
        request.state.request_id,
        request.app.state.settings,
    )
    _executor(request).submit(proposal.id)
    return proposal


@router.get(
    "/assistant/proposals/{id}",
    response_model=AssistantProposal,
    summary="查询 AI 辅助提案",
    description="仅提案归属者可读，他人查询返回 404。未就绪前 proposal 字段恒为 null。",
)
def read_proposal(
    id: ProposalIdPath,
    user: UserModel = Depends(require_user),
    db: Session = Depends(get_db),
) -> AssistantProposal:
    return assistant_service.get_proposal(db, user, id)


@router.post(
    "/assistant/proposals/{id}/apply",
    response_model=GenerationSchema,
    status_code=status.HTTP_201_CREATED,
    summary="显式应用已就绪的 AI 辅助提案",
    description=(
        "将已通过隔离校验的提案显式应用为新的不可变 Generation（201）。"
        "重复应用受事务幂等保护，直接返回同一 Generation；不修改原模板版本。"
    ),
)
def apply_proposal(
    id: ProposalIdPath,
    request: Request,
    user: UserModel = Depends(require_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GenerationSchema:
    return assistant_service.apply_proposal(
        db,
        user,
        id,
        request.state.request_id,
    )
