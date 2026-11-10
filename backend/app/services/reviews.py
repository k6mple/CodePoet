"""模板与作品审核：状态机、禁止自审、事务内改状态与公开指针。

这个模块的价值几乎全在「拒绝」上——它做的不是让审核通过，而是保证
不该通过的东西过不去。四条规则各自挡一类事故：

1. **状态机**（``TEMPLATE_TRANSITIONS`` / ``WORK_TRANSITIONS``）
   不在转移表里的跳转一律 409。最典型的是 ``published -> rejected``：
   两个值都合法，数据库 CHECK 拦不住，只有转移表能拦。允许它的话，
   一个已经被人拿去生成过作品的版本可以被"驳回"回滚，那些作品的来源
   会突然指向一个被否掉的版本。

2. **禁止自审**
   提交人不能是审核人。数据库里也有 ``CHECK (submitted_by <> reviewer_id)``，
   但那是最后一道；这里先判一次是为了给出可读的错误，而不是让
   IntegrityError 冒成 500。

3. **拒绝必须给理由**
   批准可以不说，驳回必须说。没有理由的驳回等于让作者去猜。

4. **事务内同时写记录与状态**
   审核记录、版本状态、容器公开指针三者要么一起生效，要么一起不生效。
   分开提交会留下"记录写了但状态没变"这种事后对不上账的状态。

分层约定：本模块不导入 FastAPI，不接触 Request/Response。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import errors
from app.engine.instantiate import TemplateDefinitionError, check_template_integrity
from app.models.creations import Generation as GenerationModel
from app.models.reviews import Review as ReviewModel
from app.models.templates import Template as TemplateModel
from app.models.templates import TemplateVersion as TemplateVersionModel
from app.models.users import User
from app.models.works import RenderJob as RenderJobModel
from app.models.works import Work as WorkModel
from app.models.works import WorkVersion as WorkVersionModel
from app.schemas.generation import Binding, Constraint, ParameterDefinition
from app.schemas.ir import DrawingIR
from app.schemas.reviews import (
    Review,
    ReviewCreate,
    ReviewQueueItem,
    ReviewQueueItemPage,
)
from app.schemas.templates import TemplateVersion as TemplateVersionSchema
from app.schemas.works import WorkVersion as WorkVersionSchema

from app.services import permissions
from app.services import templates as templates_service
from app.services import works as works_service

__all__ = [
    "TEMPLATE_TRANSITIONS",
    "WORK_TRANSITIONS",
    "archive_template_version",
    "decide_review",
    "list_reviews",
    "submit_template_version",
    "submit_work_version",
]

_PARAM_ADAPTER = TypeAdapter(ParameterDefinition)

# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

# 模板：draft -> submitted -> published / rejected；published -> archived
# 契约 5.1 没明说 rejected 能否再送审，但看板的验收项是「退回修订」，
# 要求能。否则被驳回的版本成了死路，作者只能新建版本，没必要。
TEMPLATE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("submitted",),
    "rejected": ("submitted",),
    "submitted": ("published", "rejected"),
    "published": ("archived",),
    "archived": (),
}

# 作品：private -> pending_review -> published / rejected
# 已发布的作品版本是终点：要改内容就出下一版，不让在原地改。
WORK_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "private": ("pending_review",),
    "rejected": ("pending_review",),
    "pending_review": ("published", "rejected"),
    "published": (),
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _is_admin(user: User) -> bool:
    return user.role == "admin"


def _require_owner_or_admin(user: User, owner_id: str, label: str) -> None:
    """D-own/A。无所有权按 404 处理，不确认资源是否存在。"""
    if user.id == owner_id or _is_admin(user):
        return
    raise errors.not_found(f"{label}不存在")


def _assert_transition(
    transitions: dict[str, tuple[str, ...]],
    current: str,
    target: str,
    label: str,
) -> None:
    """不在转移表里的跳转一律拒绝。"""
    if target in transitions.get(current, ()):
        return
    raise errors.invalid_state(
        f"{label}当前状态是 {current}，不能变成 {target}"
        f"（允许的下一步：{list(transitions.get(current, ()))}）"
    )


def _check_revision(row, expected: int) -> None:
    # 模板版本与作品版本都有 revision 列，这里不区分类型
    if row.revision != expected:
        raise errors.revision_conflict(expected, row.revision)


def _load_template_version(db: Session, version_id: str) -> TemplateVersionModel:
    row = db.get(TemplateVersionModel, version_id)
    if row is None:
        raise errors.not_found("模板版本不存在")
    return row


def _load_work_version(db: Session, version_id: str) -> WorkVersionModel:
    row = db.get(WorkVersionModel, version_id)
    if row is None:
        raise errors.not_found("作品版本不存在")
    return row


def _review_payload(
    review_id: str,
    created: str,
    target_type: str,
    target_id: str,
    target_revision: int,
    submitted_by: str,
    reviewer_id: str,
    decision: str,
    reason: str,
) -> dict:
    """``last_review_json`` 的形状必须和契约 ``Review`` 一致。

    版本接口把这一列原样塞进 ``lastReview``，形状不对就是前端解析失败，
    所以集中一处构造，不在调用点各拼一次。
    """
    return {
        "id": review_id,
        "createdAt": created,
        "targetType": target_type,
        "targetId": target_id,
        "targetRevision": target_revision,
        "submittedBy": submitted_by,
        "reviewerId": reviewer_id,
        "decision": decision,
        "reason": reason,
    }


def _revalidate_template(row: TemplateVersionModel) -> None:
    """送审时把版本定义重新过一遍引擎校验。

    契约 5.1 要求「送审重验 IR/参数/引用」。这不是形式主义：定义不自洽
    的模板一旦进了队列，审核人得花时间看完才发现是坏的，白费一次人工；
    而且驳回理由会变成"模板本身有问题"，和"内容不合适"混在一起，
    作者也不知道该改哪。
    """
    try:
        ir_template = DrawingIR.model_validate_json(row.ir_template_json)
        schema = [
            _PARAM_ADAPTER.validate_python(item)
            for item in json.loads(row.parameter_schema_json or "[]")
        ]
        bindings = [
            Binding.model_validate(item) for item in json.loads(row.bindings_json or "[]")
        ]
        constraints = [
            Constraint.model_validate(item)
            for item in json.loads(row.constraints_json or "[]")
        ]
        check_template_integrity(ir_template, schema, bindings, constraints)
    except TemplateDefinitionError as exc:
        raise errors.validation_error(f"模板定义不自洽，不能送审：{exc}") from exc
    except ValueError as exc:
        # IR/参数/绑定任一无法解析，同样属于定义坏了
        raise errors.validation_error(f"模板定义无法解析，不能送审：{exc}") from exc


# ---------------------------------------------------------------------------
# 送审
# ---------------------------------------------------------------------------


def submit_template_version(
    db: Session, user: User, version_id: str, expected_revision: int
) -> TemplateVersionSchema:
    """D-own/A。draft/rejected -> submitted。"""
    row = _load_template_version(db, version_id)
    template = db.get(TemplateModel, row.template_id)
    if template is None:
        raise errors.not_found("模板不存在")
    _require_owner_or_admin(user, template.owner_id, "模板版本")
    _check_revision(row, expected_revision)
    _assert_transition(TEMPLATE_TRANSITIONS, row.status, "submitted", "模板版本")
    _revalidate_template(row)

    now = _now_iso()
    row.status = "submitted"
    row.submitted_by = user.id
    row.submitted_at = now
    row.revision += 1
    row.updated_at = now
    # 容器状态跟随送审，前端列表才能显示"审核中"
    template.status = "submitted"
    template.updated_at = now
    db.commit()
    db.refresh(row)
    return templates_service.build_version(row)


def submit_work_version(
    db: Session, user: User, version_id: str, expected_revision: int
) -> WorkVersionSchema:
    """O。private/rejected -> pending_review。"""
    row = _load_work_version(db, version_id)
    work = db.get(WorkModel, row.work_id)
    if work is None or work.owner_id != user.id:
        raise errors.not_found("作品版本不存在")
    _check_revision(row, expected_revision)
    _assert_transition(WORK_TRANSITIONS, row.status, "pending_review", "作品版本")

    generation = db.get(GenerationModel, row.generation_id)
    if generation is None:
        raise errors.not_found("生成快照不存在")
    # 契约 5.1：派生作品不得公开送审
    if generation.unpublished_source:
        raise errors.unpublished_source(
            "该版本由未发布的模板派生，不能公开送审。"
            "请等模板通过审核后重新生成再保存。",
            details={"generationId": generation.id},
        )
    job = db.get(RenderJobModel, row.render_job_id)
    if job is None or job.phase != "completed":
        raise errors.validation_error("渲染任务未完成，不能送审")

    now = _now_iso()
    row.status = "pending_review"
    row.submitted_by = user.id
    row.submitted_at = now
    row.revision += 1
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return works_service.build_version(row)


# ---------------------------------------------------------------------------
# 裁决
# ---------------------------------------------------------------------------


def decide_review(db: Session, reviewer: User, payload: ReviewCreate) -> Review:
    """E/A。批准置 published 并切公开指针，驳回置 rejected。"""
    if not permissions.has_permission(reviewer, permissions.REVIEWS_DECIDE):
        raise errors.forbidden("只有专家或管理员可以审核")

    if payload.targetType == "template_version":
        row: TemplateVersionModel | WorkVersionModel = _load_template_version(
            db, payload.targetId
        )
        transitions = TEMPLATE_TRANSITIONS
        label = "模板版本"
    else:
        row = _load_work_version(db, payload.targetId)
        transitions = WORK_TRANSITIONS
        label = "作品版本"

    # 自审先判。顺序有讲究：自审是身份问题，与目标当前状态无关；
    # 先报"状态不对"会让审核人以为换个时机就能审自己的东西。
    if row.submitted_by == reviewer.id:
        raise errors.self_review_forbidden()

    _check_revision(row, payload.expectedRevision)

    target = "published" if payload.decision == "approve" else "rejected"
    _assert_transition(transitions, row.status, target, label)

    now = _now_iso()
    review_id = _new_id("rev")
    submitted_by = row.submitted_by or ""
    snapshot = _review_payload(
        review_id=review_id,
        created=now,
        target_type=payload.targetType,
        target_id=row.id,
        target_revision=row.revision,
        submitted_by=submitted_by,
        reviewer_id=reviewer.id,
        decision=payload.decision,
        reason=payload.reason,
    )
    db.add(
        ReviewModel(
            id=review_id,
            target_type=payload.targetType,
            target_id=row.id,
            target_revision=row.revision,
            submitted_by=submitted_by,
            reviewer_id=reviewer.id,
            decision=payload.decision,
            reason=payload.reason,
            created_at=now,
        )
    )

    # 先原子占住 (id, revision) 再改字段。`_check_revision` 只是提前给个
    # 可读错误，真正防并发的是这条带条件的 UPDATE：两个人同时用同一个
    # expectedRevision 提交 approve / reject，只有一个能让 rowcount=1，
    # 另一个 409。否则两条审核记录都指向同一个 targetRevision，而版本状态
    # 只留下后写的那条——记录和公开指针互相矛盾。
    model = TemplateVersionModel if payload.targetType == "template_version" else WorkVersionModel
    affected = db.execute(
        update(model)
        .where(model.id == row.id, model.revision == payload.expectedRevision)
        .values(revision=payload.expectedRevision + 1)
    ).rowcount
    if affected != 1:
        db.rollback()
        raise errors.revision_conflict(payload.expectedRevision, payload.expectedRevision + 1)

    # revision 已经由上面那条 UPDATE 推进，这里只改其余字段，
    # 别再把 row.revision 加一遍（会跳号）
    row.status = target
    row.updated_at = now
    row.last_review_json = json.dumps(snapshot, ensure_ascii=False)
    if target == "published":
        row.published_at = now

    if payload.targetType == "template_version":
        template = db.get(TemplateModel, row.template_id)
        if template is not None:
            # 容器状态跟着版本走：模板没有独立于版本的生命周期
            template.status = target
            template.updated_at = now
            if target == "published":
                template.published_version_id = row.id
                # 不动 latest_version_id：它指「最新创建的版本」，与"哪个版本
                # 对外公开"是两件事。批准一个旧版本时若把它拉回旧版本，
                # 作者更新的草稿就从"最新"里消失了。
    else:
        work = db.get(WorkModel, row.work_id)
        if work is not None:
            work.updated_at = now
            if target == "published":
                # 只有通过时才切公开指针，所以待审期间公开页仍指向旧版本
                work.published_version_id = row.id
                work.display_version_id = row.id
                # 同上：latest_version_id 保持指向最新创建的版本

    db.commit()
    return Review.model_validate(snapshot)


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------


def archive_template_version(
    db: Session, user: User, version_id: str, expected_revision: int, reason: str
) -> TemplateVersionSchema:
    """D-own/A。published -> archived，并清掉指向它的公开指针。

    契约 5.1：归档当前模板清除它的公开指针，历史作品仍可复现。
    后者不用额外做什么——作品版本早就把 generationId 存下来了，
    模板归档不会动它。
    """
    row = _load_template_version(db, version_id)
    template = db.get(TemplateModel, row.template_id)
    if template is None:
        raise errors.not_found("模板不存在")
    _require_owner_or_admin(user, template.owner_id, "模板版本")
    _check_revision(row, expected_revision)
    _assert_transition(TEMPLATE_TRANSITIONS, row.status, "archived", "模板版本")

    now = _now_iso()
    # 归档同样走 CAS：与"批准"并发时，两边都可能通过 _check_revision，
    # 然后一个写 status、另一个改公开指针，最后指针指向一个已归档的版本。
    affected = db.execute(
        update(TemplateVersionModel)
        .where(
            TemplateVersionModel.id == row.id,
            TemplateVersionModel.revision == expected_revision,
        )
        .values(revision=expected_revision + 1)
    ).rowcount
    if affected != 1:
        db.rollback()
        raise errors.revision_conflict(expected_revision, expected_revision + 1)

    row.status = "archived"
    row.updated_at = now
    # 归档理由追加到 change_note，但**必须在提交前就保证不超上限**。
    # changeNote 的响应模型上限是 500 字：原文合法 500 字、归档理由也合法，
    # 拼起来 514 字就会 commit 成功而响应模型校验失败，之后每次读这个版本
    # 都 500。这里优先保住作者原文，理由超出部分截断并标记，绝不让持久化
    # 的数据变成"读不出来"的状态。
    note = (row.change_note or "").strip()
    marker = "\n[归档] "
    budget = 500 - len(note) - len(marker)
    if budget >= 8:
        trimmed = reason if len(reason) <= budget else reason[: budget - 1] + "…"
        row.change_note = f"{note}{marker}{trimmed}" if note else f"[归档] {trimmed}"
    # budget 不够就保持原文不动：作者的版本说明比归档理由更重要

    if template.published_version_id == row.id:
        # 先落盘，避免下面的候选查询把自己的旧状态算进去
        db.flush()
        fallback = db.scalars(
            select(TemplateVersionModel)
            .where(
                TemplateVersionModel.template_id == template.id,
                TemplateVersionModel.status == "published",
            )
            .order_by(TemplateVersionModel.version_number.desc())
        ).first()
        if fallback is not None:
            # 还有别的已发布版本，模板不算归档，指针转过去
            template.published_version_id = fallback.id
            template.status = "published"
        else:
            template.published_version_id = None
            template.status = "archived"
    template.updated_at = now
    db.commit()
    db.refresh(row)
    return templates_service.build_version(row)


# ---------------------------------------------------------------------------
# 队列
# ---------------------------------------------------------------------------


def _template_title(row: TemplateVersionModel) -> str:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except ValueError:
        return "(标题损坏)"
    name = metadata.get("name")
    return str(name)[:120] if name else "(未命名模板)"


def _latest_reviews(db: Session, target_type: str) -> dict[str, ReviewModel]:
    """每个目标最近一条审核记录。

    在 Python 里做去重而不是写窗口函数：当前是演示规模，SQLite 的窗口
    函数支持还依赖编译选项，不值得为这点数据量赌。记录过千后改成
    ``ROW_NUMBER() OVER (PARTITION BY target_id ...)``。
    """
    rows = db.scalars(
        select(ReviewModel)
        .where(ReviewModel.target_type == target_type)
        .order_by(ReviewModel.created_at.desc())
    ).all()
    latest: dict[str, ReviewModel] = {}
    for row in rows:
        latest.setdefault(row.target_id, row)
    return latest


def _build_review(row: ReviewModel) -> Review:
    return Review(
        id=row.id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        targetType=row.target_type,
        targetId=row.target_id,
        targetRevision=row.target_revision,
        submittedBy=row.submitted_by,
        reviewerId=row.reviewer_id,
        decision=row.decision,
        reason=row.reason,
    )


def list_reviews(
    db: Session,
    viewer: User,
    target_type: str | None,
    status: str,
    page: int,
    page_size: int,
) -> ReviewQueueItemPage:
    """E/A。``status`` 取 pending / processed / all。

    ``canReview`` 只对当前用户算：有审核权限且不是提交人。前端拿它禁用
    按钮，真正的拦截在 ``decide_review``——前端状态只是提示。
    """
    if not permissions.has_permission(viewer, permissions.REVIEWS_DECIDE):
        raise errors.forbidden("只有专家或管理员可以查看审核队列")

    items: list[ReviewQueueItem] = []
    wants_pending = status in ("pending", "all")
    wants_processed = status in ("processed", "all")

    if target_type in (None, "template_version") and wants_pending:
        rows = db.scalars(
            select(TemplateVersionModel)
            .where(TemplateVersionModel.status == "submitted")
            .order_by(TemplateVersionModel.submitted_at.desc())
        ).all()
        for row in rows:
            if not row.submitted_by:
                continue
            items.append(
                ReviewQueueItem(
                    targetType="template_version",
                    targetId=row.id,
                    targetRevision=row.revision,
                    title=_template_title(row),
                    submittedBy=row.submitted_by,
                    submittedAt=row.submitted_at or row.updated_at,
                    status=row.status,
                    canReview=row.submitted_by != viewer.id,
                    latestReview=None,
                )
            )

    if target_type in (None, "work_version") and wants_pending:
        rows = db.scalars(
            select(WorkVersionModel)
            .where(WorkVersionModel.status == "pending_review")
            .order_by(WorkVersionModel.submitted_at.desc())
        ).all()
        for row in rows:
            if not row.submitted_by:
                continue
            items.append(
                ReviewQueueItem(
                    targetType="work_version",
                    targetId=row.id,
                    targetRevision=row.revision,
                    title=row.title,
                    submittedBy=row.submitted_by,
                    submittedAt=row.submitted_at or row.updated_at,
                    status=row.status,
                    canReview=row.submitted_by != viewer.id,
                    latestReview=None,
                )
            )

    if wants_processed:
        for kind in ("template_version", "work_version"):
            if target_type is not None and target_type != kind:
                continue
            for target_id, review_row in _latest_reviews(db, kind).items():
                if kind == "template_version":
                    target_t = db.get(TemplateVersionModel, target_id)
                    if target_t is None:
                        continue
                    title = _template_title(target_t)
                    submitted_by = target_t.submitted_by or review_row.submitted_by
                    submitted_at = target_t.submitted_at or review_row.created_at
                    current = target_t.status
                    revision = target_t.revision
                else:
                    target_w = db.get(WorkVersionModel, target_id)
                    if target_w is None:
                        continue
                    title = target_w.title
                    submitted_by = target_w.submitted_by or review_row.submitted_by
                    submitted_at = target_w.submitted_at or review_row.created_at
                    current = target_w.status
                    revision = target_w.revision
                # status=all 时，仍在待审的目标已经在上面列过，不重复出现
                if status == "all" and current in ("submitted", "pending_review"):
                    continue
                items.append(
                    ReviewQueueItem(
                        targetType=kind,
                        targetId=target_id,
                        targetRevision=revision,
                        title=title,
                        submittedBy=submitted_by,
                        submittedAt=submitted_at,
                        status=current,
                        canReview=False,
                        latestReview=_build_review(review_row),
                    )
                )

    # 新提交的排前面；时间为空的历史数据沉底
    items.sort(key=lambda item: item.submittedAt or "", reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    return ReviewQueueItemPage(
        items=items[start : start + page_size],
        total=total,
        page=page,
        pageSize=page_size,
    )
