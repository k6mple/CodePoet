"""知识条目、本体概念与标签的 HTTP 接口。

契约里这三组共 13 个操作，全部属 P1A-02（``x-task-id`` 一致）。
控制器只做三件事：解析参数、调服务、把 204 收尾；判断全在
``app/services/taxonomy.py`` 与 ``app/services/knowledge.py``，
这样规则能脱离 HTTP 单独测。

两个统一约定：

* 读接口用 ``current_user``（允许匿名，返回 None），写接口用
  ``require_user`` + ``require_csrf``——写操作必须过 CSRF 这一关；
* 路径 ID 用 ``Annotated[str, Path(...)]`` 而不是模块级常量，
  否则 FastAPI 会把参数名串到别的路由上（之前踩过）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.models.users import User as UserModel
from app.schemas.knowledge import (
    KnowledgeCreate,
    KnowledgeEntity,
    KnowledgeEntityPage,
    KnowledgeType,
    KnowledgeUpdate,
)
from app.schemas.taxonomy import (
    Concept,
    ConceptCreate,
    ConceptPage,
    ConceptUpdate,
    ManageScope,
    Tag,
    TagCreate,
    TagPage,
    TagUpdate,
)
from app.services import knowledge as knowledge_service
from app.services import taxonomy as taxonomy_service

router = APIRouter(tags=["知识"])

# 契约里所有不透明 ID 共用同一形状
IdPath = Annotated[
    str,
    Path(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="不透明 ID，不要解析前缀推导权限",
    ),
]


# ---- 知识条目 -------------------------------------------------------------


@router.get(
    "/knowledge",
    response_model=KnowledgeEntityPage,
    summary="组合检索知识条目",
    description=(
        "scope=public 只出未停用条目（匿名可读）；scope=manage 需要知识维护权限，"
        "并带上已停用条目。tagIds 按「有效标签」匹配（含从父标签继承来的），"
        "同一维度内命中任一即可，不同维度之间取交集。"
    ),
)
def list_knowledge(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    scope: ManageScope = Query(default="public"),
    q: str | None = Query(default=None, description="名称/别名/摘要/正文子串匹配"),
    type_: KnowledgeType | None = Query(default=None, alias="type"),
    tag_ids: list[str] | None = Query(default=None, alias="tagIds"),
    entity_ids: list[str] | None = Query(default=None, alias="entityIds"),
    time_theme: str | None = Query(default=None, alias="timeTheme"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> KnowledgeEntityPage:
    return knowledge_service.list_entities(
        db,
        viewer,
        scope=scope,
        page=page,
        page_size=page_size,
        q=q,
        type_=type_,
        tag_ids=tag_ids,
        entity_ids=entity_ids,
        time_theme=time_theme,
    )


@router.post(
    "/knowledge",
    response_model=KnowledgeEntity,
    status_code=status.HTTP_201_CREATED,
    summary="新增知识实体",
)
def create_knowledge(
    payload: KnowledgeCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> KnowledgeEntity:
    return knowledge_service.create_entity(db, user, payload)


@router.get(
    "/knowledge/{entity_id}",
    response_model=KnowledgeEntity,
    summary="知识详情",
)
def read_knowledge(
    entity_id: IdPath,
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> KnowledgeEntity:
    return knowledge_service.get_entity(db, viewer, entity_id)


@router.patch(
    "/knowledge/{entity_id}",
    response_model=KnowledgeEntity,
    summary="编辑知识实体",
)
def update_knowledge(
    payload: KnowledgeUpdate,
    entity_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> KnowledgeEntity:
    return knowledge_service.update_entity(db, user, entity_id, payload)


@router.delete(
    "/knowledge/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="停用知识实体",
    description="软停用，不物理删除；旧作品快照保留。",
)
def delete_knowledge(
    entity_id: IdPath,
    expected_revision: int = Query(..., ge=1, alias="expectedRevision"),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Response:
    knowledge_service.deactivate_entity(db, user, entity_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 标签 -----------------------------------------------------------------


@router.get("/tags", response_model=TagPage, summary="查询标签列表")
def list_tags(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    scope: ManageScope = Query(default="public"),
    q: str | None = Query(default=None, description="名称/简介子串匹配"),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> TagPage:
    return taxonomy_service.list_tags(
        db, viewer, scope=scope, page=page, page_size=page_size, q=q
    )


@router.post(
    "/tags",
    response_model=Tag,
    status_code=status.HTTP_201_CREATED,
    summary="创建标签",
)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Tag:
    return taxonomy_service.create_tag(db, user, payload)


@router.patch("/tags/{tag_id}", response_model=Tag, summary="更新标签")
def update_tag(
    payload: TagUpdate,
    tag_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Tag:
    return taxonomy_service.update_tag(db, user, tag_id, payload)


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="停用标签",
    description="有 active 子节点时返回 DEPENDENCY_CONFLICT；否则软停用。",
)
def delete_tag(
    tag_id: IdPath,
    expected_revision: int = Query(..., ge=1, alias="expectedRevision"),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Response:
    taxonomy_service.deactivate_tag(db, user, tag_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 本体概念 -------------------------------------------------------------


@router.get(
    "/ontology/concepts",
    response_model=ConceptPage,
    summary="查询本体列表",
)
def list_concepts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    scope: ManageScope = Query(default="public"),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    viewer: UserModel | None = Depends(current_user),
) -> ConceptPage:
    return taxonomy_service.list_concepts(
        db, viewer, scope=scope, page=page, page_size=page_size, q=q
    )


@router.post(
    "/ontology/concepts",
    response_model=Concept,
    status_code=status.HTTP_201_CREATED,
    summary="创建本体",
)
def create_concept(
    payload: ConceptCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Concept:
    return taxonomy_service.create_concept(db, user, payload)


@router.patch(
    "/ontology/concepts/{concept_id}",
    response_model=Concept,
    summary="更新本体",
)
def update_concept(
    payload: ConceptUpdate,
    concept_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Concept:
    return taxonomy_service.update_concept(db, user, concept_id, payload)


@router.delete(
    "/ontology/concepts/{concept_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="停用本体",
    description="有 active 子节点时返回 DEPENDENCY_CONFLICT；否则软停用。",
)
def delete_concept(
    concept_id: IdPath,
    expected_revision: int = Query(..., ge=1, alias="expectedRevision"),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> Response:
    taxonomy_service.deactivate_concept(db, user, concept_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
