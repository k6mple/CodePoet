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
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.errors import (
    execution_busy,
    invalid_state,
    not_found,
    revision_conflict,
    validation_error,
)
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
from app.services import quality as quality_service
from app.services import uploads
from app.services.generations import build_generation_schema
from app.settings import Settings

# 依赖快照占位：P1B-04 要做真实的依赖哈希，现在用固定值避免假装精确
_DEPENDENCY_SNAPSHOT = hashlib.sha256(b"codepoet-p0-04").hexdigest()

# 契约 7.1：上传任务创建后有效期 5 分钟
UPLOAD_TTL_SECONDS = 300
# 契约 7.1：每用户最多两个未完成的上传任务
MAX_OPEN_UPLOADS = 2
# 未完成 = 还能往上传帧的状态；timed_out 已经出局，不算占额度
_OPEN_JOB_STATUSES = ("queued", "running")
# 允许收帧的状态。**必须独立判断，不能依赖「到期检查有没有改过记录」**：
# 任务可能早就被 GET 标成 timed_out，此时到期检查返回 False，
# 「没改过」会被误读成「可以收帧」，重试一次就把过期任务救活成 succeeded。
# succeeded 仍允许收帧：同一 timeMs 重复上传按替换处理（幂等），
# 这样「响应丢了、前端重试最后一张」不会因为任务已成功而失败。
_FRAME_ACCEPTING_STATUSES = ("queued", "running", "succeeded")
# 过期任务对外暴露的错误码，契约 7.1 点名要求
UPLOAD_EXPIRED_CODE = "UPLOAD_EXPIRED"


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


def _require_generation_for_job(
    db: Session, user: User, row: RenderJob
) -> GenerationModel:
    """取任务绑定的快照，按任务类型决定要不要归属校验。

    普通上传任务要求快照属于本人；复现任务绑的是**原作者**的快照（契约允许
    复现他人的公开作品），此时凭「任务属于本人」放行——任务已经过归属校验，
    快照只是它的绘制输入，不是用户可以随意指派的资源。少了这一条，B 复现
    A 的公开作品时上传帧会 404，整条链路走不通。
    """
    if row.original_job_id:
        generation = db.get(GenerationModel, row.generation_id)
        if generation is None:
            raise not_found("生成快照不存在")
        return generation
    return _require_owned_generation(db, user, row.generation_id)


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


def can_read_generation(db: Session, user: User | None, generation) -> bool:
    """契约的 V 权限：本人、管理员，或「通过某个可读版本关联到这份快照」。

    最后一种正是复现他人公开作品需要的：B 复现 A 的公开作品时，浏览器要画的
    是 A 的快照，但 B 与这份快照没有归属关系。判据必须落在「你能不能读那个
    引用它的版本」，而不是「快照是不是你的」——否则契约里那句
    「V=…公开作品具体版本…授权关联快照」在代码里就没有对应实现。
    """
    if user is not None and (generation.owner_id == user.id or _is_admin(user)):
        return True
    versions = db.scalars(
        select(WorkVersion).where(WorkVersion.generation_id == generation.id)
    ).all()
    return any(can_read_version(db, user, version) for version in versions)


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


def _expire_stale_uploads(db: Session, owner_id: str) -> int:
    """把该用户已过期但仍挂着可上传状态的任务标成 timed_out。

    契约 7.1 写的是「过期时检查并标 timed_out，不需常驻 worker」，所以
    清理挂在读写路径上：建新任务前清一批，读单个任务时清一条。不这样做
    的话，一个两小时前就该过期的任务会永久占着两个额度中的一个，用户
    再也建不出新任务，而看状态字段又找不出原因。
    """
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(RenderJob).where(
            RenderJob.owner_id == owner_id,
            RenderJob.execution_mode == "browser_upload",
            RenderJob.status.in_(_OPEN_JOB_STATUSES),
        )
    ).all()
    return sum(1 for row in rows if _mark_expired_if_due(row, now))


def _deadline_of(row: RenderJob) -> datetime | None:
    """任务真正的到期时刻。

    旧版本创建的上传任务是 ``queued`` 且 ``expires_at = NULL``（那时只写了
    状态，没有到期约定）。它们一样占着额度，所以这里回退到
    ``created_at + TTL`` 补算——否则这些任务永远不会释放，升级后用户必须
    手工取消才能再建任务，而状态字段上看不出任何原因。
    """
    deadline = _parse_dt(row.expires_at)
    if deadline is not None:
        return deadline
    created = _parse_dt(row.created_at)
    if created is None:
        return None
    return created + timedelta(seconds=UPLOAD_TTL_SECONDS)


def _mark_expired_if_due(row: RenderJob, now: datetime) -> bool:
    """单个任务到期就地标记，返回是否真的改过。phase 保留原值。

    **只对未完成状态生效**。``succeeded`` / ``failed`` / ``cancelled`` 都是
    终态：它们已经结束了，跟"上传期限"没有关系。之前这里不看 status，而
    ``get_render_job`` 会对任意状态调用它，于是一个已经成功上传完的任务，
    只要 expires_at 过了就被读成 ``timed_out``——读一次改一次，越读越坏，
    保存重试也会据此判失败。成功就是成功，不该被时钟改写。
    """
    if row.status not in _OPEN_JOB_STATUSES:
        return False
    deadline = _deadline_of(row)
    if deadline is None or deadline > now:
        return False
    stamp = now.isoformat(timespec="seconds")
    row.status = "timed_out"
    if not row.expires_at:
        # 补上补算出来的值：前端要能看出它是按哪个时刻判过期的，
        # 而不是拿到一个 null 又看见 timed_out
        row.expires_at = deadline.isoformat(timespec="seconds")
    row.error_json = json.dumps(
        {
            "code": UPLOAD_EXPIRED_CODE,
            "message": "上传任务已过期，请重新创建渲染任务",
            "details": None,
        },
        ensure_ascii=False,
    )
    row.finished_at = row.finished_at or stamp
    row.updated_at = stamp
    return True


def _begin_immediate(db: Session) -> None:
    """在当前 Session 上开一个**独占写事务**。

    SQLite 默认是 DEFERRED 事务：先读后写时，写锁要等到 INSERT 那一刻才
    升级，中间那段窗口里别的连接也能读到同样的 COUNT，于是两个并发请求
    各自看到"还有名额"、然后各插一条——上限就这么被突破了，而 429 只挡得住
    串行的第二次请求。IMMEDIATE 在事务一开始就拿写锁，COUNT 与 INSERT
    之间插不进任何人。

    调用前必须没有未提交的改动：这里先 commit 一次，把前面只读的事务
    收干净，再显式开 IMMEDIATE。
    """
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))


def reserve_upload_slot(db: Session, owner_id: str) -> None:
    """在独占写事务里完成「清过期 → 数额度 → 超额就 429」。

    调用方拿到写锁后自己插入任务并提交，两步之间不会插进别人。抽成函数是
    因为普通上传与复现任务共用同一份额度——两份各自实现迟早会漂移，而这里
    漂移的后果是限流失效。

    抛出 429 前会回滚：IMMEDIATE 已经拿到写锁，不回滚的话锁要挂到 Session
    依赖清理才放开，并发请求只能干等到超时（429 变成 500）。
    """
    _begin_immediate(db)
    try:
        # 先清过期再数额度。顺序反了的话，过期任务会把自己算成占用的名额
        _expire_stale_uploads(db, owner_id)

        open_count = db.scalar(
            select(func.count())
            .select_from(RenderJob)
            .where(
                RenderJob.owner_id == owner_id,
                RenderJob.execution_mode == "browser_upload",
                RenderJob.status.in_(_OPEN_JOB_STATUSES),
            )
        )
        if (open_count or 0) >= MAX_OPEN_UPLOADS:
            raise execution_busy(
                f"未完成的上传任务已达 {MAX_OPEN_UPLOADS} 个上限，请先完成或等待旧任务过期",
                details={"maxOpenUploads": MAX_OPEN_UPLOADS, "openUploads": open_count},
            )
    except Exception:
        db.rollback()
        raise


def create_render_job(
    db: Session, user: User, payload: RenderCreate
) -> RenderJobSchema:
    generation = _require_owned_generation(db, user, payload.generationId)
    if payload.backend != generation.backend:
        raise validation_error(f"快照的后端是 {generation.backend}，与请求的 {payload.backend} 不一致")

    # 采样时刻必须与 IR 对得上：静态图只取 [0]；动画的完整保存必须
    # 恰好是 [0, floor(duration/2), duration]（契约 7.1）。否则会存下一份
    # "动画"却只画了开头一帧，回放时看不出问题但内容其实缺了 2/3
    ir_doc = json.loads(generation.ir_json or "{}")
    duration = int(ir_doc.get("durationMs") or 0)
    if duration == 0:
        if payload.timesMs != [0]:
            raise validation_error(
                f"静态图只能采样时刻 [0]，收到 {payload.timesMs}"
            )
    else:
        over = [item for item in payload.timesMs if item > duration]
        if over:
            raise validation_error(
                f"采样时刻 {over} 超出动画时长 {duration}ms"
            )
        if len(payload.timesMs) == 3:
            expected = [0, duration // 2, duration]
            if payload.timesMs != expected:
                raise validation_error(
                    f"完整动画保存的采样时刻必须是 {expected}，收到 {payload.timesMs}"
                )

    # 额度检查与插入必须落在**同一个写事务**里。DEFERRED 事务下"先读后写"
    # 会留出一个窗口：两个并发请求各自读到「还有名额」，然后各插一条，
    # 上限被突破——429 只挡得住串行的第二次请求。评审复现过一次：两个
    # POST /render-jobs 都返回 202，库里变成 3 个未完成任务。
    reserve_upload_slot(db, user.id)
    try:
        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="seconds")
        expires = (now + timedelta(seconds=UPLOAD_TTL_SECONDS)).isoformat(timespec="seconds")

        row = RenderJob(
            id=_new_id("job"),
            owner_id=user.id,
            generation_id=payload.generationId,
            backend=payload.backend,
            execution_mode=payload.executionMode,
            # 契约 7.1 对 browser_upload 明确规定「状态 running、phase=awaiting_frames」。
            # 帧齐了才变 succeeded/phase=completed。
            status="running",
            phase="awaiting_frames",
            times_ms_json=json.dumps(payload.timesMs),
            policy_revision=1,
            renderer_version=payload.rendererVersion,
            client_environment_json=payload.clientEnvironment.model_dump_json(),
            original_job_id=None,
            started_at=None,
            finished_at=None,
            expires_at=expires,
            manifest_json=None,
            quality_report_json=None,
            error_json=None,
            revision=1,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    return build_render_job(row)


def get_render_job(db: Session, user: User, job_id: str) -> RenderJobSchema:
    row = _require_owned_job(db, user, job_id)
    # 读路径也判一次：前端会反复 GET 同一任务，等下一次创建时才清理太晚
    if _mark_expired_if_due(row, datetime.now(timezone.utc)):
        db.commit()
        db.refresh(row)
    return build_render_job(row)


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
    # 先判到期（这一步可能把 running 翻成 timed_out 并拒绝），
    # 再**独立**判一次状态：任务可能早就被 GET 或上一次上传标成 timed_out，
    # 那时到期检查返回 False，只靠它的返回值判断就等于给过期任务开了后门
    # （评审复现：过期任务第一次上传 409，第二次 200 → succeeded → 保存 201）。
    if _mark_expired_if_due(row, datetime.now(timezone.utc)):
        db.commit()
        db.refresh(row)
        raise invalid_state("上传任务已过期，请重新创建渲染任务")
    if row.status not in _FRAME_ACCEPTING_STATUSES:
        raise invalid_state(
            f"任务当前是 {row.status}，不再接收帧；请重新创建渲染任务"
        )
    generation = _require_generation_for_job(db, user, row)

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
        # 帧齐了才谈质量：一张全白的 PNG 能通过所有结构校验、能存成作品，
        # 但它上面什么都没画——只有看像素才发现
        quality = quality_service.build_quality_report(
            db, settings, row.id, generation.ir_json, times
        )
        row.quality_report_json = quality.model_dump_json()
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
