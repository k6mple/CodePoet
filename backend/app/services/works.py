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

from sqlalchemy import func, select, text, true, update
from sqlalchemy.orm import Session

from app.errors import (
    execution_busy,
    forbidden,
    invalid_state,
    not_found,
    revision_conflict,
    validation_error,
)
from app.models.creations import Generation as GenerationModel
from app.models.users import User
from app.models.works import Artifact, RenderJob, Work, WorkVersion
from app.schemas.reviews import ArchiveRequest
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
    WorkUpdate,
)
from app.services import audit
from app.services import permissions
from app.engine import capabilities
from app.services import quality as quality_service
from app.services import uploads
from app.services.generations import build_generation_schema
from app.settings import Settings

def _dependency_snapshot_hash(generation: GenerationModel) -> str:
    """这次渲染依赖了什么——按真实依赖算，不是一个固定串。

    之前这里写死 ``sha256(b"codepoet-p0-04")``，等于声明「所有作品的依赖完全
    相同」。那让这个字段失去全部意义：外部事实变了、规则改版了、渲染器升级了，
    依赖哈希都不动，复现时也就无法判断两份作品是不是同一套依赖。

    组成（顺序由 sort_keys 固定）：

    * 渲染器版本（能力声明里的那个）
    * 引擎 / 本体 / 策略版本
    * IR 内容哈希
    * 命中的规则（id + 版本）与知识实体（id + 修订）
    * **外部图谱快照的身份与内容哈希**（K1）——这条最关键：
      同一实体在不同时间抓到的内容只要变了，依赖就该变。
    """
    provenance = json.loads(generation.provenance_json or "{}")
    external = provenance.get("externalGraph") or None
    payload = {
        "rendererVersion": capabilities.renderer_version(generation.backend) or "unknown",
        "engineVersion": provenance.get("engineVersion"),
        "ontologyVersion": provenance.get("ontologyVersion"),
        "policyRevision": provenance.get("policyRevision"),
        "irHash": generation.ir_hash,
        "rules": [
            [item.get("ruleId"), item.get("version")]
            for item in provenance.get("matchedRules") or []
        ],
        "entities": [
            [item.get("entityId"), item.get("revision")]
            for item in provenance.get("matchedEntities") or []
        ],
        "external": (
            {
                "sourceId": external.get("sourceId"),
                "externalEntityId": external.get("externalEntityId"),
                "contentHash": external.get("contentHash"),
            }
            if external
            else None
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
    # 待审版本对审核人开放：契约 V 明说「待审版本授权关联快照」。队列会给
    # 非提交者的专家返回 canReview=true，但审核台要先读到详情和图片才能裁决。
    # 只放行待审的那一个版本，不是作者的全部历史。
    if row.status == "pending_review" and user is not None:
        from app.services import permissions

        if permissions.has_permission(user, permissions.REVIEWS_DECIDE):
            return True
    # 版本 status 是**审核结论**，容器指针是**当前公开开关**，撤回标记是
    # **这件事发生过**的证据，三个都要看：
    # - 只看 status：下线后版本仍是 published，详情和产物照旧匿名可读；
    # - 只看指针：先发 v1 → 下线 → 再发 v2，指针重新非空，v1 又被带回来
    #   （评审 #13 P1 复现的正是这条），等于绕过「重新公开需新版本审核」；
    # - 撤回标记必须持久化在版本上，才能一直生效。
    # published 是终态（WORK_TRANSITIONS），不能为了下线把状态改回 private，
    # 否则等于把"当初审核通过了"这段历史抹掉，派生过它的作品来源也会漂移。
    return (
        row.status == "published"
        and work.published_version_id is not None
        and row.public_revoked_at is None
    )


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


def can_read_artifact(db: Session, user: User | None, row) -> bool:
    """产物能不能读：本人、管理员，或**引用这次渲染任务的那个版本**可读。

    PNG 属于某一次渲染任务，而**同一个快照可以渲染出多次**：作者可能把
    J1 的结果发布成公开作品、把 J2 的结果留在私有作品里。所以判据必须是
    「artifact → 它自己的 render_job → 引用了**这个 job** 的版本」，
    而不是「artifact → job → 快照」。
    """
    if user is not None and (row.owner_id == user.id or _is_admin(user)):
        return True
    if not row.render_job_id:
        # 还没挂到任何作品（比如刚上传完的帧）：只有本人和管理员能读
        return False
    versions = db.scalars(
        select(WorkVersion).where(WorkVersion.render_job_id == row.render_job_id)
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


def _public_thumbnail_id(db: Session, row: Work) -> str | None:
    """对外缩略图只能来自**公开版本**那次渲染的产物。

    ``create_version`` 无条件把 ``thumbnail_artifact_id`` 改成新版本第一帧；
    新版本一旦是私有待审，直接透出这个 id 等于让匿名列表拿到一个读不到的
    artifact（取图 404）。所以对外视图必须回到公开版本自己的渲染任务。
    """
    version_id = row.published_version_id
    if not version_id:
        return None
    render_job_id = db.scalar(
        select(WorkVersion.render_job_id).where(WorkVersion.id == version_id)
    )
    if not render_job_id:
        return None
    return db.scalar(
        select(Artifact.id)
        .where(Artifact.render_job_id == render_job_id)
        .order_by(Artifact.time_ms)
    )


def _public_thumbnails(db: Session, rows: list[Work]) -> dict[str, str | None]:
    """批量算对外缩略图，避免列表页 N+1（每个 job 的帧数受 maxFrames 约束）。"""
    needed = {r.published_version_id for r in rows if r.published_version_id}
    if not needed:
        return {}
    job_of = dict(
        db.execute(
            select(WorkVersion.id, WorkVersion.render_job_id).where(
                WorkVersion.id.in_(needed)
            )
        ).all()
    )
    job_ids = {job for job in job_of.values() if job}
    first: dict[str, str] = {}
    if job_ids:
        for job_id, artifact_id, _ in db.execute(
            select(Artifact.render_job_id, Artifact.id, Artifact.time_ms)
            .where(Artifact.render_job_id.in_(job_ids))
            .order_by(Artifact.render_job_id, Artifact.time_ms)
        ).all():
            first.setdefault(job_id, artifact_id)
    return {
        r.id: first.get(job_of.get(r.published_version_id))  # type: ignore[arg-type]
        for r in rows
    }


def _favorite_ids(db: Session, user: User | None, rows: list[Work]) -> set[str]:
    """批量查这页里哪些被当前用户收藏，避免逐条查造成 N+1。"""
    if user is None or not rows:
        return set()
    from app.models.sessions import WorkFavorite

    ids = [r.id for r in rows]
    return set(
        db.scalars(
            select(WorkFavorite.work_id).where(
                WorkFavorite.owner_id == user.id, WorkFavorite.work_id.in_(ids)
            )
        ).all()
    )


def _is_favorite(
    db: Session, user: User | None, work_id: str, favorite_ids: set[str] | None
) -> bool:
    """收藏标记**必须读服务端关系表**，不能回固定值。

    ``favorites`` 服务在函数内导入：它反过来依赖本模块的 ``can_read_work``，
    顶层导入会成环。
    """
    if user is None:
        return False
    if favorite_ids is not None:
        return work_id in favorite_ids
    from app.services import favorites

    return favorites.is_favorited(db, user, work_id)


def build_work(
    db: Session,
    row: Work,
    user: User | None,
    *,
    public_thumbnails: dict[str, str | None] | None = None,
    favorite_ids: set[str] | None = None,
) -> WorkSchema:
    is_owner = user is not None and row.owner_id == user.id
    is_admin = user is not None and _is_admin(user)
    # latest/display 在正常流程里由服务层维护，历史数据缺失时回落到最新版本
    latest = row.latest_version_id or row.display_version_id or ""
    if is_owner or is_admin:
        display = row.display_version_id or latest
        thumbnail = row.thumbnail_artifact_id
    else:
        # 契约 5.1：公共 metadata 来自发布版。display 是「按这个 id 就能打开」
        # 的承诺，指到私有待审新版会让公共列表点进去直接 404。
        display = row.published_version_id or ""
        if public_thumbnails is not None:
            thumbnail = public_thumbnails.get(row.id)
        else:
            thumbnail = _public_thumbnail_id(db, row)
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
        isFavorite=_is_favorite(db, user, row.id, favorite_ids),
        thumbnailArtifactId=thumbnail,
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
    db: Session,
    user: User,
    payload: RenderCreate,
    *,
    enabled_backends: frozenset[str] | None = None,
) -> RenderJobSchema:
    generation = _require_owned_generation(db, user, payload.generationId)
    if payload.backend != generation.backend:
        raise validation_error(f"快照的后端是 {generation.backend}，与请求的 {payload.backend} 不一致")

    # 快照能生成不代表这个实例现在还能渲染它：启用集合是可以被运维调整的，
    # 关掉 CSS 之后旧快照仍然读得到，但不该还能发起新的 CSS 渲染任务。
    capabilities.require_enabled(
        payload.backend,
        enabled_backends
        if enabled_backends is not None
        else capabilities.enabled_backends(),
    )

    # 版本校验走注册表的统一入口，别在这里内联比较：内联版本会在
    # 「渲染器尚未注册」时被 `if expected` 静默跳过，等于没校验（评审指出的）。
    capabilities.require_renderer_version(payload.backend, payload.rendererVersion)

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
        dependencySnapshotHash=_dependency_snapshot_hash(generation),
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
    from app.models.sessions import WorkFavorite

    stmt = select(Work)
    order = [Work.updated_at.desc()]
    if scope == "mine":
        if user is None:
            raise not_found("作品不存在")
        stmt = stmt.where(Work.owner_id == user.id)
    elif scope == "all":
        if user is None or not _is_admin(user):
            raise not_found("作品不存在")
    elif scope == "favorites":
        if user is None:
            raise not_found("作品不存在")
        # 收藏是用户自己的数据，但列表里只能出现**当前还读得到**的作品：
        # 被下线、或转私有的他人作品留在列表里只会是一张点进去 404 的卡片。
        # 关系本身不删（取消收藏才是删），用户仍可从「我的收藏」里移除。
        visible = Work.owner_id == user.id
        if _is_admin(user):
            visible = visible | true()
        else:
            visible = visible | Work.published_version_id.is_not(None)
        stmt = (
            stmt.join(WorkFavorite, WorkFavorite.work_id == Work.id)
            .where(WorkFavorite.owner_id == user.id, visible)
        )
        # 收藏列表按收藏时间倒序：刚收的排前面，比「作品更新时间」更贴合
        order = [WorkFavorite.created_at.desc()]
    else:  # public
        stmt = stmt.where(Work.published_version_id.is_not(None))

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Work.title.like(like))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()
    # 作者与管理员走各自分支，不会用到这份映射；只有对外视图才需要它
    thumbnails = _public_thumbnails(db, list(rows))
    favorites = _favorite_ids(db, user, list(rows))
    return WorkPage(
        items=[
            build_work(
                db, r, user, public_thumbnails=thumbnails, favorite_ids=favorites
            )
            for r in rows
        ],
        total=total,
        page=page,
        pageSize=page_size,
    )


def get_work(db: Session, user: User | None, work_id: str) -> WorkSchema:
    row = db.get(Work, work_id)
    if row is None or not can_read_work(db, user, row):
        raise not_found("作品不存在")
    return build_work(db, row, user)


def update_work(db: Session, user: User, work_id: str, payload: WorkUpdate) -> WorkSchema:
    """O。改工作元数据，**不动任何既有版本的标题/描述**。

    契约那句「不修改既有版本标题/描述；新版本读取工作元数据」是一条规则，
    不是两句话：版本的 title/description 在创建时就是快照，改它会让人对不上
    「当初审核通过的是哪一份内容」。所以这里写的是 ``working_*``，由
    ``create_version`` 在下一版创建时读取。
    """
    row = _require_owned_work(db, user, work_id)
    if payload.title is None and payload.description is None:
        raise validation_error("没有需要更新的字段：至少给 title 或 description")

    values: dict[str, object] = {}
    if payload.title is not None:
        values["working_title"] = payload.title
    if payload.description is not None:
        values["working_description"] = payload.description

    # 还没有公开版本时，作者的工作元数据就是唯一元数据：不同步容器字段的话，
    # 列表、搜索、详情全都还停在创建时那个名字，改完等于没改。
    # 一旦有了公开版本，公共元数据由发布版决定，这里就不再动容器字段——
    # 否则等于绕过审核直接改公开页。
    sync_container = row.published_version_id is None
    if sync_container:
        if payload.title is not None:
            values["title"] = payload.title
        if payload.description is not None:
            values["description"] = payload.description

    now = _now_iso()
    statement = update(Work).where(
        Work.id == row.id, Work.revision == payload.expectedRevision
    )
    if sync_container:
        # 「读到 publishedVersionId 为空」和「写下去」之间必须没有窗口。
        # 中间如果有一笔审核把作品公开了，这里写的就成了未经审核的公开标题。
        # 光靠 revision 挡不住：审核推进 revision 是这一版才补的，历史数据
        # 与别的写路径未必守；把当前公开状态也放进 WHERE 才不依赖别人的自觉。
        statement = statement.where(Work.published_version_id.is_(None))

    affected = db.execute(
        statement.values(revision=payload.expectedRevision + 1, updated_at=now, **values)
    ).rowcount
    if affected != 1:
        db.rollback()
        actual = db.scalar(select(Work.revision).where(Work.id == row.id)) or 0
        if actual != payload.expectedRevision:
            raise revision_conflict(payload.expectedRevision, actual)
        # revision 没变却写不进去，只可能是作品刚被公开——让客户端刷新重试，
        # 别把它当成版本冲突（数字一样，报冲突会让人以为是自己算错了版本号）
        raise invalid_state("作品刚刚被公开，请刷新后重试")

    db.commit()
    db.refresh(row)
    return build_work(db, row, user)


def unpublish_work(
    db: Session, user: User, work_id: str, payload: ArchiveRequest
) -> WorkSchema:
    """A。下线作品：清公开指针，关闭所有旧公开版本与产物的公共访问。

    **不改版本状态。** ``published`` 在 ``WORK_TRANSITIONS`` 里是终态，把它
    改回 ``private`` 等于抹掉「当初审核通过了」这段历史，派生过它的作品来源
    也会漂移。关闭公共访问靠容器指针：``can_read_version`` 要求版本是
    published **且** 容器仍有公开版本，所以清指针一处就同时关掉了详情、产物
    和公开列表。
    """
    if not permissions.has_permission(user, permissions.WORKS_UNPUBLISH_ANY):
        raise forbidden("只有管理员或专家可以下线作品")

    row = db.get(Work, work_id)
    if row is None:
        raise not_found("作品不存在")
    if row.published_version_id is None:
        raise invalid_state("作品当前没有公开版本，无需下线")

    before_published = row.published_version_id
    now = _now_iso()
    affected = db.execute(
        update(Work)
        .where(Work.id == row.id, Work.revision == payload.expectedRevision)
        .values(
            revision=payload.expectedRevision + 1,
            updated_at=now,
            published_version_id=None,
        )
    ).rowcount
    if affected != 1:
        db.rollback()
        actual = db.scalar(select(Work.revision).where(Work.id == row.id)) or 0
        raise revision_conflict(payload.expectedRevision, actual)

    # 给当时所有公开版本打上撤回标记。只清容器指针是不够的：下次发布新版本
    # 指针会重新非空，这些旧版本会跟着"复活"——而契约要求它们一直关闭，
    # 重新公开得走新版本的审核。状态仍是 published，这里只记"曾经被撤回"。
    db.execute(
        update(WorkVersion)
        .where(
            WorkVersion.work_id == row.id,
            WorkVersion.status == "published",
            WorkVersion.public_revoked_at.is_(None),
        )
        .values(public_revoked_at=now)
    )

    audit.record(
        db,
        actor=user,
        action="work.unpublish",
        target_type="work",
        target_id=row.id,
        input_summary={"reason": payload.reason},
        before={"publishedVersionId": before_published},
        after={"publishedVersionId": None},
        result="success",
    )
    db.commit()
    db.refresh(row)
    return build_work(db, row, user)


def list_versions(
    db: Session, user: User | None, work_id: str, page: int, page_size: int
) -> WorkVersionPage:
    work = db.get(Work, work_id)
    if work is None or not can_read_work(db, user, work):
        raise not_found("作品不存在")

    stmt = select(WorkVersion).where(WorkVersion.work_id == work_id)
    if user is None or (work.owner_id != user.id and not _is_admin(user)):
        # 对外只列「当前公开且没被撤回」的版本。判定要和 can_read_version 对齐：
        # 少一个条件，详情已经 404 的旧版本会从列表里把标题、描述、changeNote、
        # lastReview 和关联 ID 又吐出来——下线只关了一半的门。
        stmt = stmt.where(
            WorkVersion.status == "published",
            WorkVersion.public_revoked_at.is_(None),
        )

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

    generation = _require_owned_generation(db, user, payload.generationId)
    job = _require_owned_job(db, user, payload.renderJobId)
    if job.generation_id != payload.generationId:
        raise validation_error("renderJobId 不属于该生成快照")
    if job.phase != "completed":
        raise validation_error("渲染任务还没完成，不能创建版本")

    # 乐观锁必须是原子的。原来写的是「先比 expectedRevision，再无条件
    # work.revision += 1」，两个并发请求会同时通过比较，各自算出
    # version_number = max+1，然后第二条撞 UNIQUE(work_id, version_number)，
    # 冒出来的是 500 而不是 409。条件放进 UPDATE 的 WHERE 之后，数据库保证
    # 只有一个请求能把 revision 从 N 推到 N+1，另一个在这里就 409 退出，
    # 连版本都插不进去。
    now = _now_iso()
    affected = db.execute(
        update(Work)
        .where(Work.id == work.id, Work.revision == payload.expectedRevision)
        .values(revision=payload.expectedRevision + 1, updated_at=now)
    ).rowcount
    if affected != 1:
        db.rollback()
        actual = db.scalar(select(Work.revision).where(Work.id == work.id)) or 0
        raise revision_conflict(payload.expectedRevision, actual)

    max_no = db.scalar(
        select(func.max(WorkVersion.version_number)).where(WorkVersion.work_id == work_id)
    ) or 0
    version = WorkVersion(
        id=_new_id("wv"),
        work_id=work.id,
        version_number=max_no + 1,
        created_by=user.id,
        status="private",
        # 契约「新版本读取工作元数据」：作者在 PATCH /works/{id} 改的标题要靠
        # 这里带进新版本，否则改了等于没改。空串是合法的工作描述，所以用
        # is not None 判断而不是真值判断。
        # 回落到 work.title / work.description 只发生在作者从没改过的时候；
        # 改过就一直用 working_*（见下面「不清空」那段）。
        title=work.working_title if work.working_title is not None else work.title,
        description=(
            work.working_description
            if work.working_description is not None
            else work.description
        ),
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
    # 这里**不清空** working_*。它是作者的"当前工作元数据"，是一个持续的默认值，
    # 不是一次性缓冲：清空之后，下一次 create_version 会回落到 work.title /
    # work.description，而这两个字段在有公开版本时是**上一版公开的**元数据，
    # 于是「公开 v1(A) → 改标题 B → 建 v2(B) → 直接建 v3」的第二、第三版
    # 会静默退回 A（评审 #13 第四轮的 P2，就是这么来的）。
    # 保留它还有另一个好处：批准旧版本时不会误伤作者给下一版攒的编辑。
    work.latest_version_id = version.id
    work.display_version_id = version.id
    # revision 已经由上面那条 CAS 推进过了，这里不能再加一次
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
