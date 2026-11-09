"""作品、作品版本与渲染任务的业务编排。

分层约定：本模块**不导入 FastAPI**，也不接触 Request/Response，
只接收 Pydantic 模型与 Session，这样规则能脱离 HTTP 单独测。

三条硬规则，都在这里落地而不是散在控制器：

1. **权属失败一律 404**（契约 2.2）。返回 403 等于承认资源存在，
   私有作品的标题会因此泄露。``_require_owned_*`` 系列统一走 ``not_found()``。
2. **引用必须同属一个用户**。创建作品要同时校验 ``generationId`` 与
   ``renderJobId`` 都归当前用户、且 job 的 generation 就是传进来的那个，
   否则能把别人的渲染结果挂进自己的作品。
3. **状态流转靠 phase 推导，不信客户端**。帧没传齐就不能是 completed。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import not_found, revision_conflict, validation_error
from app.models.creations import Generation as GenerationModel
from app.models.users import User
from app.models.works import Artifact, RenderJob, Work, WorkVersion
from app.schemas.works import (
    ClientEnvironment,
    Frame,
    Manifest,
    RenderCreate,
    RenderJob as RenderJobSchema,
    Work as WorkSchema,
    WorkCreate,
    WorkCreated,
    WorkPage,
    WorkVersion as WorkVersionSchema,
    WorkVersionCreate,
    WorkVersionDetail,
    WorkVersionPage,
)
from app.services import uploads
from app.services.generations import build_generation_schema
from app.settings import Settings

# 依赖快照占位：P1B-04 要做真实的依赖哈希，现在用固定值避免假装精确
_DEPENDENCY_SNAPSHOT = hashlib.sha256(b"codepoet-p0-04").hexdigest()


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


def _loads(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


# ---------------------------------------------------------------------------
# 可见性
# ---------------------------------------------------------------------------


def _is_admin(user: User) -> bool:
    return user.role == "admin"


def _require_owned_generation(db: Session, user: User, generation_id: str) -> GenerationModel:
    row = db.get(GenerationModel, generation_id)
    # 不存在与不属于本人返回同一个 404，不泄露别人的快照是否存在
    if row is None or row.owner_id != user.id:
        raise not_found("生成快照不存在")
    return row


def _require_owned_job(db: Session, user: User, job_id: str) -> RenderJob:
    row = db.get(RenderJob, job_id)
    if row is None or row.owner_id != user.id:
        raise not_found("渲染任务不存在")
    return row


def _require_owned_work(db: Session, user: User, work_id: str) -> Work:
    row = db.get(Work, work_id)
    if row is None or row.owner_id != user.id:
        raise not_found("作品不存在")
    return row


def can_read_work(db: Session, user: User | None, row: Work) -> bool:
    """本人、管理员、或有已发布版本的作品对外可见。"""
    if user is not None and (row.owner_id == user.id or _is_admin(user)):
        return True
    return bool(row.published_version_id)


def can_read_version(db: Session, user: User | None, row: WorkVersion) -> bool:
    work = db.get(Work, row.work_id)
    if work is None:
        return False
    if user is not None and (work.owner_id == user.id or _is_admin(user)):
        return True
    return row.status == "published"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _permissions_for(is_owner: bool, is_admin: bool) -> list[str]:
    perms = ["work.read"]
    if is_owner:
        perms += ["work.update", "work.delete", "work.version.create"]
    if is_admin:
        perms += ["work.moderate"]
    return perms


def build_work(db: Session, row: Work, user: User | None) -> WorkSchema:
    is_owner = user is not None and row.owner_id == user.id
    is_admin = user is not None and _is_admin(user)
    # latest/display 在正常流程里由服务层维护，历史数据缺失时回落到最新版本
    latest = row.latest_version_id or row.display_version_id or ""
    display = row.display_version_id or latest
    return WorkSchema(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        updatedAt=_parse_dt(row.updated_at) or datetime.now(timezone.utc),
        ownerId=row.owner_id,
        title=row.title,
        description=row.description,
        # 草稿态元数据只对作者与管理员暴露
        workingTitle=row.working_title if (is_owner or is_admin) else None,
        workingDescription=row.working_description if (is_owner or is_admin) else None,
        publishedVersionId=row.published_version_id,
        latestVersionId=latest,
        displayVersionId=display,
        isFavorite=False,
        thumbnailArtifactId=row.thumbnail_artifact_id,
        permissions=_permissions_for(is_owner, is_admin),
    )


def build_version(row: WorkVersion) -> WorkVersionSchema:
    return WorkVersionSchema(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        updatedAt=_parse_dt(row.updated_at) or datetime.now(timezone.utc),
        workId=row.work_id,
        versionNumber=row.version_number,
        createdBy=row.created_by,
        submittedBy=row.submitted_by,
        status=row.status,
        title=row.title,
        description=row.description,
        generationId=row.generation_id,
        renderJobId=row.render_job_id,
        changeNote=row.change_note,
        submittedAt=_parse_dt(row.submitted_at),
        publishedAt=_parse_dt(row.published_at),
        lastReview=_loads(row.last_review_json, None),
    )


def build_render_job(row: RenderJob) -> RenderJobSchema:
    times = _loads(row.times_ms_json, [])
    manifest_raw = _loads(row.manifest_json, None)
    return RenderJobSchema(
        id=row.id,
        createdAt=_parse_dt(row.created_at) or datetime.now(timezone.utc),
        ownerId=row.owner_id,
        generationId=row.generation_id,
        status=row.status,
        backend=row.backend,
        timesMs=times,
        policyRevision=row.policy_revision,
        rendererVersion=row.renderer_version,
        originalJobId=row.original_job_id,
        startedAt=_parse_dt(row.started_at),
        finishedAt=_parse_dt(row.finished_at),
        manifest=Manifest.model_validate(manifest_raw) if manifest_raw else None,
        qualityReport=_loads(row.quality_report_json, None),
        error=_loads(row.error_json, None),
        executionMode=row.execution_mode,
        phase=row.phase,
        expiresAt=_parse_dt(row.expires_at),
        clientEnvironment=(
            ClientEnvironment.model_validate(_loads(row.client_environment_json, None))
            if row.client_environment_json
            else None
        ),
    )


# ---------------------------------------------------------------------------
# 渲染任务
# ---------------------------------------------------------------------------


def create_render_job(
    db: Session, user: User, payload: RenderCreate
) -> RenderJobSchema:
    generation = _require_owned_generation(db, user, payload.generationId)
    if payload.backend != generation.backend:
        raise validation_error(f"快照的后端是 {generation.backend}，与请求的 {payload.backend} 不一致")

    now = _now_iso()
    row = RenderJob(
        id=_new_id("job"),
        owner_id=user.id,
        generation_id=payload.generationId,
        backend=payload.backend,
        execution_mode=payload.executionMode,
        # 客户端还没上传任何一帧，所以先 queued + awaiting_frames
        status="queued",
        phase="awaiting_frames",
        times_ms_json=json.dumps(payload.timesMs),
        policy_revision=1,
        renderer_version=payload.rendererVersion,
        client_environment_json=payload.clientEnvironment.model_dump_json(),
        original_job_id=None,
        started_at=None,
        finished_at=None,
        expires_at=None,
        manifest_json=None,
        quality_report_json=None,
        error_json=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return build_render_job(row)


def get_render_job(db: Session, user: User, job_id: str) -> RenderJobSchema:
    return build_render_job(_require_owned_job(db, user, job_id))


def _rebuild_manifest(
    db: Session, row: RenderJob, generation: GenerationModel
) -> Manifest:
    """用当前已上传的帧重建清单。帧按 timesMs 顺序排列，缺帧就报错。"""
    times: list[int] = _loads(row.times_ms_json, [])
    frames_rows = db.scalars(
        select(Artifact)
        .where(Artifact.render_job_id == row.id, Artifact.kind == "png")
        .order_by(Artifact.time_ms)
    ).all()
    by_time = {f.time_ms: f for f in frames_rows}
    missing = [t for t in times if t not in by_time]
    if missing:
        raise validation_error(f"还有 {len(missing)} 个时刻的帧未上传：{missing[:5]}")

    ir = _loads(generation.ir_json, {})
    viewport = ir.get("viewport") or {"x": 0, "y": 0, "width": 800, "height": 600}
    return Manifest(
        schemaVersion=1.0,
        generationId=row.generation_id,
        backend=row.backend,
        rendererVersion=row.renderer_version or "unknown",
        environmentHash=hashlib.sha256(
            (row.client_environment_json or "").encode()
        ).hexdigest(),
        irHash=generation.ir_hash,
        dependencySnapshotHash=_DEPENDENCY_SNAPSHOT,
        viewport=viewport,
        timesMs=times,
        frames=[
            Frame(
                artifactId=by_time[t].id,
                sha256=by_time[t].sha256,
                width=by_time[t].width or 800,
                height=by_time[t].height or 600,
                timeMs=t,
            )
            for t in times
        ],
        archiveArtifactId=None,
        outputSource=row.execution_mode,
    )


def upload_frame(
    db: Session,
    settings: Settings,
    user: User,
    job_id: str,
    data: bytes,
    time_ms: int,
    ir_hash: str,
) -> RenderJobSchema:
    """上传一帧。同一 timeMs 重复上传按替换处理（幂等），不累积脏数据。"""
    row = _require_owned_job(db, user, job_id)
    generation = _require_owned_generation(db, user, row.generation_id)

    times: list[int] = _loads(row.times_ms_json, [])
    if time_ms not in times:
        raise validation_error(f"timeMs {time_ms} 不在任务声明的采样时刻 {times} 内")
    # 防错配：帧必须属于这份 IR，否则会出现「图是旧版的、IR 是新的」
    if ir_hash != generation.ir_hash:
        raise validation_error("irHash 与任务的生成快照不一致")

    stored = uploads.store_frame(settings, user.id, data, time_ms)

    # 同 timeMs 已有帧：删旧记录（文件留在磁盘，不做删除以免误删被别处引用的产物）
    existing = db.scalars(
        select(Artifact).where(
            Artifact.render_job_id == row.id, Artifact.time_ms == time_ms
        )
    ).all()
    for old in existing:
        db.delete(old)

    db.add(
        Artifact(
            id=stored.artifact_id,
            owner_id=user.id,
            kind=stored.kind,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            storage_path=stored.storage_path,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
            time_ms=stored.time_ms,
            render_job_id=row.id,
            created_at=_now_iso(),
        )
    )
    db.flush()

    # 只有帧齐了才生成 manifest 并推进状态，否则保持 awaiting_frames
    uploaded_times = {
        a.time_ms
        for a in db.scalars(
            select(Artifact).where(Artifact.render_job_id == row.id)
        ).all()
    }
    now = _now_iso()
    if all(t in uploaded_times for t in times):
        manifest = _rebuild_manifest(db, row, generation)
        row.manifest_json = manifest.model_dump_json()
        row.status = "succeeded"
        row.phase = "completed"
        row.started_at = row.started_at or now
        row.finished_at = now
    else:
        row.status = "running"
        row.phase = "rendering"

    row.updated_at = now
    db.commit()
    db.refresh(row)
    return build_render_job(row)


# ---------------------------------------------------------------------------
# 作品
# ---------------------------------------------------------------------------


def create_work(db: Session, user: User, payload: WorkCreate) -> WorkCreated:
    generation = _require_owned_generation(db, user, payload.generationId)
    job = _require_owned_job(db, user, payload.renderJobId)
    if job.generation_id != payload.generationId:
        raise validation_error("renderJobId 不属于该生成快照")
    if job.phase != "completed":
        raise validation_error("渲染任务还没完成，不能保存为作品")

    now = _now_iso()
    work = Work(
        id=_new_id("work"),
        owner_id=user.id,
        title=payload.title,
        description=payload.description,
        latest_version_id=None,
        published_version_id=None,
        display_version_id=None,
        working_title=None,
        working_description=None,
        thumbnail_artifact_id=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(work)
    db.flush()

    version = WorkVersion(
        id=_new_id("wv"),
        work_id=work.id,
        version_number=1,
        created_by=user.id,
        status="private",
        title=payload.title,
        description=payload.description,
        generation_id=generation.id,
        render_job_id=job.id,
        change_note="",
        submitted_by=None,
        submitted_at=None,
        published_at=None,
        last_review_json=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    db.flush()

    # 指针在同一事务里补上，避免出现「有版本但作品没有 latest」的中间态
    work.latest_version_id = version.id
    work.display_version_id = version.id
    # 缩略图取第一个采样时刻的帧
    first = db.scalars(
        select(Artifact)
        .where(Artifact.render_job_id == job.id)
        .order_by(Artifact.time_ms)
    ).first()
    work.thumbnail_artifact_id = first.id if first else None

    db.commit()
    db.refresh(work)
    db.refresh(version)
    return WorkCreated(work=build_work(db, work, user), version=build_version(version))


def list_works(
    db: Session,
    user: User | None,
    scope: str,
    q: str | None,
    page: int,
    page_size: int,
) -> WorkPage:
    stmt = select(Work)
    if scope == "mine":
        if user is None:
            raise not_found("作品不存在")
        stmt = stmt.where(Work.owner_id == user.id)
    elif scope == "all":
        if user is None or not _is_admin(user):
            raise not_found("作品不存在")
    elif scope == "favorites":
        # 收藏属 P1A-05，表还没建。返回空页而不是假装支持
        return WorkPage(items=[], total=0, page=page, pageSize=page_size)
    else:  # public
        stmt = stmt.where(Work.published_version_id.is_not(None))

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Work.title.like(like))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Work.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return WorkPage(
        items=[build_work(db, r, user) for r in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def get_work(db: Session, user: User | None, work_id: str) -> WorkSchema:
    row = db.get(Work, work_id)
    if row is None or not can_read_work(db, user, row):
        raise not_found("作品不存在")
    return build_work(db, row, user)


def list_versions(
    db: Session, user: User | None, work_id: str, page: int, page_size: int
) -> WorkVersionPage:
    work = db.get(Work, work_id)
    if work is None or not can_read_work(db, user, work):
        raise not_found("作品不存在")

    stmt = select(WorkVersion).where(WorkVersion.work_id == work_id)
    if user is None or (work.owner_id != user.id and not _is_admin(user)):
        stmt = stmt.where(WorkVersion.status == "published")

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(WorkVersion.version_number.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return WorkVersionPage(
        items=[build_version(r) for r in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def create_version(
    db: Session, user: User, work_id: str, payload: WorkVersionCreate
) -> WorkCreated:
    work = _require_owned_work(db, user, work_id)
    # 乐观锁：客户端拿的是旧 revision 就拒绝，避免覆盖别人的修改
    if payload.expectedRevision != work.revision:
        raise revision_conflict(payload.expectedRevision, work.revision)

    generation = _require_owned_generation(db, user, payload.generationId)
    job = _require_owned_job(db, user, payload.renderJobId)
    if job.generation_id != payload.generationId:
        raise validation_error("renderJobId 不属于该生成快照")
    if job.phase != "completed":
        raise validation_error("渲染任务还没完成，不能创建版本")

    max_no = db.scalar(
        select(func.max(WorkVersion.version_number)).where(WorkVersion.work_id == work_id)
    ) or 0
    now = _now_iso()
    version = WorkVersion(
        id=_new_id("wv"),
        work_id=work.id,
        version_number=max_no + 1,
        created_by=user.id,
        status="private",
        title=work.title,
        description=work.description,
        generation_id=generation.id,
        render_job_id=job.id,
        change_note=payload.changeNote,
        submitted_by=None,
        submitted_at=None,
        published_at=None,
        last_review_json=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    db.flush()
    work.latest_version_id = version.id
    work.display_version_id = version.id
    work.revision += 1
    work.updated_at = now
    db.commit()
    db.refresh(work)
    db.refresh(version)
    return WorkCreated(work=build_work(db, work, user), version=build_version(version))


def get_version_detail(db: Session, user: User | None, version_id: str) -> WorkVersionDetail:
    row = db.get(WorkVersion, version_id)
    if row is None or not can_read_version(db, user, row):
        raise not_found("作品版本不存在")
    generation = db.get(GenerationModel, row.generation_id)
    job = db.get(RenderJob, row.render_job_id)
    if generation is None or job is None:
        # 引用完整性理论上由外键兜住，这里是防御性检查
        raise not_found("作品版本引用的快照或渲染任务不存在")
    return WorkVersionDetail(
        workVersion=build_version(row),
        generation=build_generation_schema(generation),
        renderJob=build_render_job(job),
    )
