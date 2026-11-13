"""知识图谱接入接口（K1）。

控制器只做三件事：解析请求、调服务、拼响应。抓取、映射、快照全在
``services/knowledge_graph.py``，这样业务规则能脱离 HTTP 单独测。

权限：``POST /knowledge-graph/generations`` —— L（登录），写操作过 CSRF。
状态码：成功 201 Created（产生一份新的不可变快照，不是 200）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.deps import get_db, require_csrf, require_user
from app.models.users import AuthSession, User as UserModel
from app.schemas.generation import Generation as GenerationSchema
from app.schemas.knowledge_graph import GraphGenerationRequest
from app.services import knowledge_graph

router = APIRouter(tags=["知识图谱"])


@router.post(
    "/knowledge-graph/generations",
    response_model=GenerationSchema,
    status_code=status.HTTP_201_CREATED,
    summary="从外部知识图谱生成快照",
    description=(
        "按部署配置的白名单来源取一个外部实体，把它的属性与一跳关系映射到站内"
        "知识与规则，生成一份带来源快照的不可变 Generation。"
        "不接受用户传 URL；来源不可用 503、超时 504、实体不存在 422、"
        "映射不到站内知识 422 NO_SEMANTIC_MATCH。"
    ),
)
def create_generation_from_graph(
    payload: GraphGenerationRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: AuthSession = Depends(require_csrf),
) -> GenerationSchema:
    request_id = getattr(request.state, "request_id", "req-unknown")
    return knowledge_graph.generate_from_graph(
        db, user, payload, request_id, request.app.state.settings
    )
