"""DSL 沙箱与执行策略接口（S1）。

四个端点：

* ``POST /sandbox-runs``        —— 受理，202 + queued 的 run
* ``GET  /sandbox-runs/{id}``   —— 归属校验后读状态与结果
* ``GET  /admin/policies``      —— 读当前策略与硬上限、固定审计规则
* ``PATCH /admin/policies``     —— 改 DSL 限额（只读写字段 422，revision 冲突 409）

控制器只做三件事：解析参数、调服务、格式化响应。所有判断（谁能读、配额、
源码超限、策略只读）都在 ``services/sandbox.py`` 与 ``services/policy.py``——
这样它们能脱离 HTTP 单独测，也不会出现「某个入口漏了鉴权」。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.orm import Session

from app import errors
from app.deps import get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.sandbox import (
    Policies,
    PolicyUpdate,
    SandboxCreate,
    SandboxRun,
)
from app.services import policy as policy_service
from app.services import sandbox as sandbox_service

router = APIRouter(tags=["沙箱"])

RunIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="执行 ID，不透明串",
    ),
]


def _executor(request: Request) -> sandbox_service.SandboxExecutor:
    executor = getattr(request.app.state, "sandbox_executor", None)
    if executor is None:
        # lifespan 没建执行器。这不是用户的问题，但也不能假装成功受理——
        # 受理了却没人执行，run 会永远停在 queued。
        raise errors.sandbox_unavailable("执行器未就绪")
    return executor


@router.post(
    "/sandbox-runs",
    response_model=SandboxRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="受理一次 DSL 执行",
    description=(
        "从 base 生成快照出发，在确定性新 root group 下追加图元。"
        "源码 ≤16KiB，base 必须可读。成功受理返回 202 + queued，"
        "AST 违规按 DSL_AUDIT_REJECTED 落成可追溯的失败记录。"
    ),
)
def create_sandbox_run(
    payload: SandboxCreate,
    request: Request,
    user: UserModel = Depends(require_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SandboxRun:
    run = sandbox_service.create_run(
        db, user, payload, request.state.request_id
    )
    # 事务已提交（create_run 里 commit），执行器线程才可能读到这一行。
    # 顺序不能反：先 submit 再 commit，线程有机会读到不存在的行。
    _executor(request).submit(run.id)
    return run


@router.get(
    "/sandbox-runs/{id}",
    response_model=SandboxRun,
    summary="查询一次执行",
    description="只能读自己的；他人的 ID 一律 404，不暴露其存在性。",
)
def read_sandbox_run(
    id: RunIdPath,
    user: UserModel = Depends(require_user),
    db: Session = Depends(get_db),
) -> SandboxRun:
    return sandbox_service.get_run(db, user, id)


@router.get(
    "/admin/policies",
    response_model=Policies,
    summary="读取执行策略",
    description=(
        "返回当前生效值、不可突破的硬上限，以及固定的 AST 审计规则清单。"
        "审计规则不可关闭，所以这里只有读。"
    ),
)
def read_policies(
    user: UserModel = Depends(require_user),
    db: Session = Depends(get_db),
) -> Policies:
    return policy_service.read_policies(db, user)


@router.patch(
    "/admin/policies",
    response_model=Policies,
    summary="修改执行策略",
    description=(
        "本期只允许改 DSL 相关的数值限额与 enabledDslFunctions。"
        "改只读字段返回 422 POLICY_FIELD_READ_ONLY；"
        "expectedRevision 与当前不符返回 409 REVISION_CONFLICT。"
        "新值只影响之后受理的执行，已在跑的 run 用提交时的快照。"
    ),
)
def update_policies(
    payload: PolicyUpdate,
    request: Request,
    user: UserModel = Depends(require_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Policies:
    return policy_service.update_policies(
        db, user, payload, request.state.request_id
    )
