"""作品复现与像素比较。

复现的语义是「用**当年那份 IR** 再画一次，看画出来是不是一样」。所以：

* IR、backend、采样时刻全部从历史 generation 读，**不接受客户端传**
  ——传了就等于允许"复现成别的东西"，那就不叫复现。
* 复现任务绑定 ``originalJobId``，比较接口靠它找原图。
* 复现走 ``browser_upload``：契约明确基础版不重新推理，浏览器画完上传。

比较的质量：
* ``compatible`` 只看三项硬条件（渲染器版本、浏览器指纹、尺寸）。
  契约说得很清楚：**这不是硬件环境认证**，只是"能不能放在一起比"。
* 不可比时三个指标给 null 而不是 0——给 0 会被读成"完全一致"，
  恰好是最坏的一种误导。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageStat
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.models.creations import Generation as GenerationModel
from app.models.users import User
from app.models.works import Artifact, RenderJob, Work, WorkVersion
from app.schemas.reproduce import Comparison, FrameComparison, ReproduceRequest
from app.schemas.works import RenderJob as RenderJobSchema
from app.services import audit
from app.services import uploads
from app.services import works as works_service
from app.settings import Settings

__all__ = ["compare_jobs", "reproduce_version"]

# 与 BE-G0-001 保持一致：browser_upload 任务创建即 running，5 分钟有效
_UPLOAD_TTL_SECONDS = 300
_MAX_OPEN_UPLOADS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def reproduce_version(
    db: Session, settings: Settings, user: User, version_id: str, payload: ReproduceRequest
) -> RenderJobSchema:
    """按作品版本的历史快照建复现任务。"""
    version = db.get(WorkVersion, version_id)
    if version is None:
        raise errors.not_found("作品版本不存在")
    work = db.get(Work, version.work_id)
    # 契约 access=V：必须登录且能看这个版本
    if work is None or not works_service.can_read_work(db, user, work):
        raise errors.not_found("作品版本不存在")

    generation = db.get(GenerationModel, version.generation_id)
    if generation is None:
        raise errors.not_found("历史生成快照不存在")

    original = db.get(RenderJob, version.render_job_id)
    if original is None or original.phase != "completed":
        raise errors.validation_error("原始渲染任务未完成，无法复现")

    times: list[int] = json.loads(original.times_ms_json or "[]")
    if not times:
        raise errors.validation_error("原始任务没有采样时刻，无法复现")

    # 未完成任务上限与普通上传任务共用一套额度，避免绕过限流
    open_count = db.scalar(
        select(func.count())
        .select_from(RenderJob)
        .where(
            RenderJob.owner_id == user.id,
            RenderJob.execution_mode == "browser_upload",
            RenderJob.status.in_(("queued", "running")),
        )
    )
    if (open_count or 0) >= _MAX_OPEN_UPLOADS:
        raise errors.execution_busy(
            f"未完成的上传任务已达 {_MAX_OPEN_UPLOADS} 个上限，请先完成或等待过期"
        )

    now = _now()
    stamp = now.isoformat(timespec="seconds")
    expires = (now + timedelta(seconds=_UPLOAD_TTL_SECONDS)).isoformat(timespec="seconds")

    row = RenderJob(
        id=f"job-{uuid4().hex}",
        owner_id=user.id,
        generation_id=generation.id,
        backend=original.backend,
        execution_mode="browser_upload",
        status="running",
        phase="awaiting_frames",
        times_ms_json=json.dumps(times),
        policy_revision=original.policy_revision,
        renderer_version=payload.rendererVersion,
        client_environment_json=payload.clientEnvironment.model_dump_json(),
        # 关键：绑定原任务，比较接口靠它找原图
        original_job_id=original.id,
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
    audit.record(
        db,
        actor=user,
        action="work.reproduce",
        target_type="render_job",
        target_id=row.id,
        input_summary={
            "workVersionId": version_id,
            "generationId": generation.id,
            "originalJobId": original.id,
            "timesMs": times,
        },
        result="success",
    )
    db.commit()
    db.refresh(row)
    return works_service.build_render_job(row)


# ---------------------------------------------------------------------------
# 像素比较
# ---------------------------------------------------------------------------


def _load_frame(db: Session, settings: Settings, job_id: str, time_ms: int) -> Image.Image | None:
    artifact = db.scalars(
        select(Artifact).where(
            Artifact.render_job_id == job_id,
            Artifact.time_ms == time_ms,
            Artifact.kind == "png",
        )
    ).first()
    if artifact is None:
        return None
    # storage_path 是相对 artifacts 根目录的，必须用 uploads 的解析函数，
    # 自己拼 data_dir 会少一层 "artifacts/" 从而永远找不到文件
    path = uploads.artifact_path(settings, artifact.storage_path)
    if not path.exists():
        return None
    try:
        with Image.open(path) as image:
            return image.convert("RGBA")
    except OSError:
        return None


def _compare_images(old: Image.Image, new: Image.Image) -> tuple[bool, float, float]:
    """返回 (exactMatch, mae, differentPixelRatio)。

    用 PIL 的 C 实现而不是 Python 逐像素循环：800×600 是 48 万像素，
    Python 循环一帧要一两秒，PIL 的分通道求差是毫秒级。

    ``differentPixelRatio`` 需要"任一通道有差"的像素比例，所以先把
    RGB 三通道取较亮者合成一张掩码，再统计非零比例——直接对灰度图比较
    会漏掉"只有蓝通道差 1"这种细微差异。
    """
    diff = ImageChops.difference(old, new)
    channels = diff.split()
    # 只比较 RGB；alpha 全不透明时恒为 0，把它算进去会稀释差异
    red, green, blue = channels[0], channels[1], channels[2]
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = peak.point(lambda value: 255 if value > 0 else 0)

    different_ratio = ImageStat.Stat(mask).mean[0] / 255.0
    means = ImageStat.Stat(diff).mean
    mae = (means[0] + means[1] + means[2]) / 3.0 / 255.0
    return different_ratio == 0.0, round(mae, 6), round(different_ratio, 6)


def _fingerprint(db: Session, job: RenderJob) -> dict:
    env = json.loads(job.client_environment_json or "{}")
    return {
        "rendererVersion": job.renderer_version,
        "userAgent": env.get("userAgent"),
        "deviceScaleFactor": env.get("deviceScaleFactor"),
    }


def compare_jobs(
    db: Session, settings: Settings, user: User, job_id: str, original_job_id: str
) -> Comparison:
    job = db.get(RenderJob, job_id)
    original = db.get(RenderJob, original_job_id)
    if job is None or job.owner_id != user.id:
        raise errors.not_found("渲染任务不存在")
    if original is None or original.owner_id != user.id:
        raise errors.not_found("原渲染任务不存在")

    if job.phase != "completed" or original.phase != "completed":
        raise errors.invalid_state("两个任务都完成才能比较像素")

    reasons: list[str] = []
    new_fp = _fingerprint(db, job)
    old_fp = _fingerprint(db, original)
    if new_fp["rendererVersion"] != old_fp["rendererVersion"]:
        reasons.append(
            f"渲染器版本不同（{old_fp['rendererVersion']} vs {new_fp['rendererVersion']}）"
        )
    if new_fp["userAgent"] != old_fp["userAgent"]:
        reasons.append("浏览器 UA 不同")
    if new_fp["deviceScaleFactor"] != old_fp["deviceScaleFactor"]:
        reasons.append("设备像素比不同")

    times_old: list[int] = json.loads(original.times_ms_json or "[]")
    times_new: list[int] = json.loads(job.times_ms_json or "[]")
    if times_old != times_new:
        reasons.append(f"采样时刻不同（{times_old} vs {times_new}）")

    frames: list[FrameComparison] = []
    if not reasons:
        for time_ms in times_new:
            old_image = _load_frame(db, settings, original.id, time_ms)
            new_image = _load_frame(db, settings, job.id, time_ms)
            if old_image is None or new_image is None:
                reasons.append(f"{time_ms}ms 的帧不齐，无法比较")
                break
            if old_image.size != new_image.size:
                reasons.append(
                    f"{time_ms}ms 尺寸不同（{old_image.size} vs {new_image.size}）"
                )
                break
            exact, mae, ratio = _compare_images(old_image, new_image)
            frames.append(
                FrameComparison(
                    timeMs=time_ms, exactMatch=exact, mae=mae, differentPixelRatio=ratio
                )
            )

    if reasons:
        # 不可比时指标给 null：给 0 会被读成"完全一致"，恰好最误导
        return Comparison(
            jobId=job.id,
            originalJobId=original.id,
            compatible=False,
            reason="；".join(reasons)[:500],
            exactMatch=None,
            mae=None,
            differentPixelRatio=None,
            frames=[],
        )

    return Comparison(
        jobId=job.id,
        originalJobId=original.id,
        compatible=True,
        reason=None,
        exactMatch=all(frame.exactMatch for frame in frames),
        # 多帧时取最差值，避免"平均下来还行"掩盖某一帧的严重偏差
        mae=max(frame.mae for frame in frames) if frames else None,
        differentPixelRatio=(
            max(frame.differentPixelRatio for frame in frames) if frames else None
        ),
        frames=frames,
    )
