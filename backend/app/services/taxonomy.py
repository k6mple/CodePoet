"""标签与本体概念的业务规则。

两棵树形状完全一致（单父树 + 软停用 + 乐观锁 + 分页筛选），差别只有标签
多一份「互斥关系」，所以共用一套实现、靠参数区分。放一个模块而不是抄两遍，
是为了让「防环」「停用前查子节点」这类规则只有一处代码——改一次两棵树都跟上。

几处刻意的选择：

* **环检测查全表，不写递归 SQL**。两棵树都是百级，一次把 ``id -> parent_id``
  读进内存走链，比每条语句跑一遍递归 CTE 快，也更好测。
* **互斥关系只存单向，判定和展示时双向展开**。如果入库时把两个方向都写上，
  就意味着改 A 要顺带改 B 的行、还要动 B 的 revision，B 的持有者会莫名
  撞上 409。改成「存单向、读时展开」后，写操作只碰自己那一行。
* **重名报 422 而不是 409**。契约 2.2 的 409 code 列表里没有「重名」，
  它本质是输入校验；返回 ``fieldErrors`` 指到 ``name`` 比让前端猜有用。
* **停用是幂等的**。已经停用再 DELETE 一次直接 204，不报错。
  客户端重试、网络抖动重发都不该炸。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.models.knowledge import Concept as ConceptModel
from app.models.knowledge import Tag as TagModel
from app.models.users import User
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
from app.services import permissions

# 父链最多走这么多层。百级树不可能更深，超了说明数据有环，直接停手。
MAX_DEPTH = 32
# 契约里 conflictsWith / inheritedTagIds 都是 maxItems 100
MAX_LINKS = 100


# ---- 基础工具 -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime:
    """库里的时间串解析不出来时退到 1970，宁可排序靠后也不让接口 500。"""
    try:
        return datetime.fromisoformat(value or "")
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _json_list(raw: str | None, *, limit: int = MAX_LINKS) -> list[str]:
    """把 JSON 文本列还原成字符串列表，脏数据过滤掉而不是抛异常。"""
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in data:
        if not isinstance(item, str) or not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _dump_list(values: list[str]) -> str:
    # 排序后再落库：同样的集合永远得到同样的字节，便于比对与去重
    return json.dumps(sorted(set(values)), ensure_ascii=False)


# ---- 树结构 ---------------------------------------------------------------


def parent_map(db: Session, model: type[TagModel] | type[ConceptModel]) -> dict[str, str | None]:
    rows = db.execute(select(model.id, model.parent_id)).all()
    return {row[0]: row[1] for row in rows}


def ancestors(parents: dict[str, str | None], node_id: str) -> list[str]:
    """从近到远列出祖先 ID（不含自己）。

    遇到自环或超过 MAX_DEPTH 就停，不靠「数据一定干净」这种假设。
    """
    chain: list[str] = []
    seen = {node_id}
    cursor = parents.get(node_id)
    while cursor and cursor not in seen and len(chain) < MAX_DEPTH:
        chain.append(cursor)
        seen.add(cursor)
        cursor = parents.get(cursor)
    return chain


def _would_cycle(
    parents: dict[str, str | None], node_id: str, new_parent: str | None
) -> bool:
    """把 node 挂到 new_parent 下会不会成环。

    只要 new_parent 的祖先链里出现 node 自己，挂上去就闭合了。
    """
    if new_parent is None:
        return False
    if new_parent == node_id:
        return True
    return node_id in ancestors(parents, new_parent)


# ---- 权限 -----------------------------------------------------------------


def _require_writer(viewer: User | None, permission: str, label: str) -> User:
    if viewer is None:
        raise errors.auth_required()
    if not permissions.has_permission(viewer, permission):
        raise errors.forbidden(f"需要「{label}」权限")
    return viewer


# ---- 互斥标签 -------------------------------------------------------------


def conflict_map(db: Session) -> dict[str, set[str]]:
    """把单向声明的互斥关系展开成双向。

    A 声明与 B 互斥，等价于 B 与 A 互斥——否则换个方向查就漏判。
    """
    result: dict[str, set[str]] = {}
    for row in db.scalars(select(TagModel)):
        for other in _json_list(row.conflicts_with_json):
            result.setdefault(row.id, set()).add(other)
            result.setdefault(other, set()).add(row.id)
    return result


def check_tag_selection(db: Session, tag_ids: list[str]) -> None:
    """校验一组标签里没有互斥组合。知识条目写入前调用。

    继承来的标签也可能互斥，所以入参应该是「直接标签 ∪ 祖先链」。
    """
    if len(tag_ids) < 2:
        return
    conflicts = conflict_map(db)
    chosen = set(tag_ids)
    hits: list[dict[str, str]] = []
    for tag_id in chosen:
        for other in conflicts.get(tag_id, ()):
            if other in chosen and tag_id < other:
                hits.append({"tagId": tag_id, "conflictsWith": other})
    if hits:
        raise errors.tag_conflict("所选标签存在互斥组合", details={"conflicts": hits})


# ---- 标签：序列化 ---------------------------------------------------------


def _build_tag(row: TagModel, parents: dict[str, str | None], conflicts: dict[str, set[str]]) -> Tag:
    return Tag(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_iso(row.created_at),
        updatedAt=_parse_iso(row.updated_at),
        name=row.name,
        description=row.description,
        parentId=row.parent_id,
        status=row.status,  # type: ignore[arg-type]
        # 展示时给全的双向关系，库里只存单向
        conflictsWith=sorted(conflicts.get(row.id, set()))[:MAX_LINKS],
        inheritedTagIds=ancestors(parents, row.id),
    )


def _build_concept(row: ConceptModel) -> Concept:
    return Concept(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_iso(row.created_at),
        updatedAt=_parse_iso(row.updated_at),
        name=row.name,
        description=row.description,
        parentId=row.parent_id,
        status=row.status,  # type: ignore[arg-type]
    )


# ---- 通用查询 -------------------------------------------------------------


def _page_query(
    db: Session,
    model: type[TagModel] | type[ConceptModel],
    viewer: User | None,
    *,
    permission: str,
    label: str,
    scope: ManageScope,
    page: int,
    page_size: int,
    q: str | None,
) -> tuple[list, int]:
    """列表查询的公共部分：可见性 → 模糊匹配 → 计数 → 分页。

    排序按契约 2：createdAt 降序，同刻按 id 降序保证稳定。
    """
    statement = select(model)
    if scope == "manage":
        # manage 要能看到停用项，否则运营没法把停用的再启用
        _require_writer(viewer, permission, f"{label}维护")
    else:
        statement = statement.where(model.status == "active")

    if q:
        # autoescape 让用户输入的 % 和 _ 当普通字符，不当通配符
        statement = statement.where(
            or_(
                model.name.contains(q, autoescape=True),
                model.description.contains(q, autoescape=True),
            )
        )

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        db.scalars(
            statement.order_by(model.created_at.desc(), model.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def _check_parent_exists(
    db: Session,
    model: type[TagModel] | type[ConceptModel],
    parent_id: str | None,
    label: str,
) -> None:
    if parent_id is None:
        return
    parent = db.get(model, parent_id)
    if parent is None:
        raise errors.validation_error(
            f"父{label}不存在",
            [{"path": "parentId", "message": "指向的对象不存在"}],
        )
    if parent.status != "active":
        raise errors.validation_error(
            f"父{label}已停用",
            [{"path": "parentId", "message": "不能挂到已停用的节点下"}],
        )


def _raise_if_duplicate_name(db: Session, model, exc: IntegrityError, label: str) -> None:
    """把唯一索引的 IntegrityError 翻成 422，并把错误定位到 name 字段。

    走到这里时外键已经在写入前逐条校验过了，剩下的 IntegrityError 只可能来自
    ``uq_*_active_name`` 这个部分唯一索引。仍然显式判一次 UNIQUE，
    避免把将来的其他约束误报成「重名」。
    """
    db.rollback()
    message = str(getattr(exc, "orig", exc)).upper()
    if "UNIQUE" in message:
        raise errors.validation_error(
            f"{label}名已被占用",
            [{"path": "name", "message": "同名的未停用对象已存在"}],
        )
    raise errors.validation_error(f"{label}写入失败")


# ---- 标签：用例 -----------------------------------------------------------


def list_tags(
    db: Session,
    viewer: User | None,
    *,
    scope: ManageScope = "public",
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
) -> TagPage:
    rows, total = _page_query(
        db,
        TagModel,
        viewer,
        permission=permissions.TAGS_WRITE,
        label="标签",
        scope=scope,
        page=page,
        page_size=page_size,
        q=q,
    )
    parents = parent_map(db, TagModel)
    conflicts = conflict_map(db)
    return TagPage(
        items=[_build_tag(row, parents, conflicts) for row in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def create_tag(db: Session, actor: User, payload: TagCreate) -> Tag:
    _require_writer(actor, permissions.TAGS_WRITE, "标签维护")
    _check_parent_exists(db, TagModel, payload.parentId, "标签")
    for other_id in payload.conflictsWith:
        if db.get(TagModel, other_id) is None:
            raise errors.validation_error(
                "互斥标签不存在",
                [{"path": "conflictsWith", "message": f"{other_id} 不存在"}],
            )

    now = _iso(_now())
    row = TagModel(
        id=f"tag-{uuid4().hex}",
        name=payload.name,
        description=payload.description,
        parent_id=payload.parentId,
        status=payload.status,
        conflicts_with_json=_dump_list(payload.conflictsWith),
        owner_id=actor.id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_if_duplicate_name(db, TagModel, exc, "标签")

    parents = parent_map(db, TagModel)
    return _build_tag(row, parents, conflict_map(db))


def update_tag(db: Session, actor: User, tag_id: str, payload: TagUpdate) -> Tag:
    _require_writer(actor, permissions.TAGS_WRITE, "标签维护")
    row = db.get(TagModel, tag_id)
    if row is None:
        raise errors.not_found("标签不存在")
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    changed = payload.model_fields_set
    if "name" in changed and payload.name is not None:
        row.name = payload.name
    if "description" in changed and payload.description is not None:
        row.description = payload.description
    if "status" in changed and payload.status is not None:
        row.status = payload.status
    if "conflictsWith" in changed and payload.conflictsWith is not None:
        for other_id in payload.conflictsWith:
            if db.get(TagModel, other_id) is None:
                raise errors.validation_error(
                    "互斥标签不存在",
                    [{"path": "conflictsWith", "message": f"{other_id} 不存在"}],
                )
        row.conflicts_with_json = _dump_list(payload.conflictsWith)

    if "parentId" in changed:
        # 显式传 null = 挪到根；字段缺席 = 不动
        parents = parent_map(db, TagModel)
        if _would_cycle(parents, tag_id, payload.parentId):
            raise errors.validation_error(
                "父标签不能是自身或自身的下级，否则会成环",
                [{"path": "parentId", "message": "成环"}],
            )
        _check_parent_exists(db, TagModel, payload.parentId, "标签")
        row.parent_id = payload.parentId

    row.revision += 1
    row.updated_at = _iso(_now())
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_if_duplicate_name(db, TagModel, exc, "标签")

    parents = parent_map(db, TagModel)
    return _build_tag(row, parents, conflict_map(db))


def deactivate_tag(db: Session, actor: User, tag_id: str, expected_revision: int) -> None:
    _require_writer(actor, permissions.TAGS_WRITE, "标签维护")
    row = db.get(TagModel, tag_id)
    if row is None:
        raise errors.not_found("标签不存在")
    # 幂等优先于乐观锁：已经停用就是目标状态，此时版本号过时也不该报 409。
    # 否则客户端重发一次 DELETE 就得到一个「冲突」，语义上说不通。
    if row.status == "inactive":
        return
    if row.revision != expected_revision:
        raise errors.revision_conflict(expected_revision, row.revision)

    active_children = db.scalar(
        select(func.count())
        .select_from(TagModel)
        .where(TagModel.parent_id == tag_id, TagModel.status == "active")
    ) or 0
    if active_children:
        raise errors.dependency_conflict(
            "还有未停用的子标签，请先停用或移走它们",
            details={"activeChildren": active_children},
        )

    row.status = "inactive"
    row.revision += 1
    row.updated_at = _iso(_now())
    db.commit()


# ---- 本体概念：用例 -------------------------------------------------------


def list_concepts(
    db: Session,
    viewer: User | None,
    *,
    scope: ManageScope = "public",
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
) -> ConceptPage:
    rows, total = _page_query(
        db,
        ConceptModel,
        viewer,
        permission=permissions.ONTOLOGY_WRITE,
        label="本体",
        scope=scope,
        page=page,
        page_size=page_size,
        q=q,
    )
    return ConceptPage(
        items=[_build_concept(row) for row in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def create_concept(db: Session, actor: User, payload: ConceptCreate) -> Concept:
    _require_writer(actor, permissions.ONTOLOGY_WRITE, "本体维护")
    _check_parent_exists(db, ConceptModel, payload.parentId, "本体")

    now = _iso(_now())
    row = ConceptModel(
        id=f"concept-{uuid4().hex}",
        name=payload.name,
        description=payload.description,
        parent_id=payload.parentId,
        status=payload.status,
        owner_id=actor.id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_if_duplicate_name(db, ConceptModel, exc, "本体")

    return _build_concept(row)


def update_concept(
    db: Session, actor: User, concept_id: str, payload: ConceptUpdate
) -> Concept:
    _require_writer(actor, permissions.ONTOLOGY_WRITE, "本体维护")
    row = db.get(ConceptModel, concept_id)
    if row is None:
        raise errors.not_found("本体不存在")
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    changed = payload.model_fields_set
    if "name" in changed and payload.name is not None:
        row.name = payload.name
    if "description" in changed and payload.description is not None:
        row.description = payload.description
    if "status" in changed and payload.status is not None:
        row.status = payload.status

    if "parentId" in changed:
        parents = parent_map(db, ConceptModel)
        if _would_cycle(parents, concept_id, payload.parentId):
            # 本体环有专属错误码，前端据此画出循环的节点
            raise errors.ontology_cycle(
                "父概念不能是自身或自身的下级",
                details={"conceptId": concept_id, "requestedParentId": payload.parentId},
            )
        _check_parent_exists(db, ConceptModel, payload.parentId, "本体")
        row.parent_id = payload.parentId

    row.revision += 1
    row.updated_at = _iso(_now())
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_if_duplicate_name(db, ConceptModel, exc, "本体")

    return _build_concept(row)


def deactivate_concept(
    db: Session, actor: User, concept_id: str, expected_revision: int
) -> None:
    _require_writer(actor, permissions.ONTOLOGY_WRITE, "本体维护")
    row = db.get(ConceptModel, concept_id)
    if row is None:
        raise errors.not_found("本体不存在")
    # 同上：幂等优先，停用后再删一次直接成功
    if row.status == "inactive":
        return
    if row.revision != expected_revision:
        raise errors.revision_conflict(expected_revision, row.revision)

    active_children = db.scalar(
        select(func.count())
        .select_from(ConceptModel)
        .where(ConceptModel.parent_id == concept_id, ConceptModel.status == "active")
    ) or 0
    if active_children:
        raise errors.dependency_conflict(
            "还有未停用的子概念，请先停用或移走它们",
            details={"activeChildren": active_children},
        )

    row.status = "inactive"
    row.revision += 1
    row.updated_at = _iso(_now())
    db.commit()
