"""创作相关接口：生成快照的创建与读取。

控制器只做三件事：解析请求、调用 services、拼响应。所有判断逻辑在
``app/services/generations.py``，这样业务规则能脱离 HTTP 单独测试。

权限记号来自接口文档 2.1：
* ``POST /generations`` —— L（登录），写操作还要过 CSRF；
* ``GET /generations/{id}`` —— V（本人 / 管理员），无权访问返回 404。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import AuthSession, User as UserModel
from app.schemas.generation import Generation as GenerationSchema
from app.schemas.generation import GenerationCreate
from app.schemas.subgraph import SubgraphEdit, SubgraphGeneration
from app.engine import capabilities
from app.services import generations
from app.services import subgraph as subgraph_service

router = APIRouter(tags=["创作"])


@router.post(
    "/generations",
    response_model=GenerationSchema,
    status_code=status.HTTP_201_CREATED,
    summary="模板/语义/手工 IR 生成",
    description=(
        "创建一份不可变快照。template 模式按模板版本的声明式绑定实例化 IR；"
        "ir 模式接收前端提交的完整 IR。semantic 模式尚未实现，会返回 "
        "422 UNSUPPORTED_CAPABILITY。"
    ),
)
def create_generation(
    payload: GenerationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: AuthSession = Depends(require_csrf),
) -> GenerationSchema:
    # requestId 由中间件统一生成并挂在 request.state 上，
    # 直接读它，不要在这里另造一个 —— 否则响应体的 requestId
    # 和响应头 X-Request-Id 会对不上，排查时无法关联。
    request_id = getattr(request.state, "request_id", "req-unknown")
    # 能力集合从实例配置读，服务层不自己猜环境
    return generations.create_generation(
        db,
        user,
        payload,
        request_id,
        enabled_backends=capabilities.enabled_backends(request.app.state.settings),
    )


@router.get(
    "/generations/{generation_id}",
    response_model=GenerationSchema,
    summary="生成快照详情",
    description="本人或管理员可读。无权访问与不存在返回同一个 404，不暴露存在性。",
)
def read_generation(
    generation_id: str,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> GenerationSchema:
    return generations.get_generation(db, user, generation_id)


GenerationIdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="生成快照 ID",
    ),
]


@router.post(
    "/generations/{generation_id}/subgraph",
    response_model=SubgraphGeneration,
    status_code=status.HTTP_201_CREATED,
    summary="局部替换/裁剪为新快照",
    description=(
        "权限 L 且基础 generation 属本人。替换 rootNodeId 及其**全部后代**，"
        "其余节点原样保留；replacementNodes 为空数组表示删掉整棵子树。"
        "新节点 ID 不得撞到子树外的现有节点，否则两个节点同 ID，"
        "IR 校验只会说「重复 id」，看不出是这次编辑引入的。"
    ),
)
def edit_subgraph(
    payload: SubgraphEdit,
    generation_id: GenerationIdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> SubgraphGeneration:
    return subgraph_service.edit_subgraph(db, user, generation_id, payload)
