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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.models.creations import Generation as GenerationModel
from app.models.users import User
from app.models.works import Artifact, RenderJob, Work, WorkVersion
from app.schemas.reproduce import Comparison, FrameComparison, ReproduceRequest
from app.schemas.works import RenderJob as RenderJobSchema
from app.engine import capabilities
from app.services import audit
from app.services import uploads
from app.services import works as works_service
from app.settings import Settings

__all__ = ["compare_jobs", "reproduce_version"]

# 与 BE-G0-001 保持一致：browser_upload 任务创建即 running，5 分钟有效。
# 额度上限不在这里重复定义——统一由 works.reserve_upload_slot 管，
# 两份各自实现迟早会漂移，而这里漂移的后果是限流失效。
_UPLOAD_TTL_SECONDS = 300


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
    # 契约 access=V：判**这个版本**能不能看，不是"这个作品有没有公开版"。
    # 用容器级判断的话，公开 v1 会连带授权私有 v2 的复现——拿到的还是
    # 私有快照的 generationId 与原任务信息。
    if not works_service.can_read_version(db, user, version):
        raise errors.not_found("作品版本不存在")

    generation = db.get(GenerationModel, version.generation_id)
    if generation is None:
        raise errors.not_found("历史生成快照不存在")

    original = db.get(RenderJob, version.render_job_id)
    if original is None or original.phase != "completed":
        raise errors.validation_error("原始渲染任务未完成，无法复现")

    # 复现不是「随便拿旧快照重跑一遍」，它要能**真的被执行**。三道校验缺一不可：
    #
    # 1) 原任务的后端现在还得启用，并且有可执行渲染器。停用一个后端之后，
    #    旧作品仍可读（历史记录不许篡改），但不该还能发起新的渲染任务——
    #    否则用户以为在复现 CSS，实际没有任何执行器。
    capabilities.require_enabled(
        original.backend, capabilities.enabled_backends(settings)
    )
    # 2) 原任务当时用的渲染器版本，现在必须仍可执行。否则「复现」出来的东西
    #    和原图不是同一套渲染器，像素比较没有意义。
    capabilities.require_renderer_version(
        original.backend, original.renderer_version or ""
    )
    # 3) 客户端此刻上报的版本也必须可执行。
    #
    # 旧实现只比较「两个版本字符串是否相等」：两个**相同但都不认识**的版本
    # （比如都写 "unknown"）会被判成兼容——那是拿相等当兼容，等于没校验。
    capabilities.require_renderer_version(original.backend, payload.rendererVersion)

    times: list[int] = json.loads(original.times_ms_json or "[]")
    if not times:
        raise errors.validation_error("原始任务没有采样时刻，无法复现")

    # 未完成任务上限与普通上传任务共用一套额度，避免绕过限流。
    # 走 works 的共用函数而不是在这里再数一遍：它会把「清过期 + 数额度」
    # 放进同一个 IMMEDIATE 写事务，否则并发两次复现同样能突破上限。
    works_service.reserve_upload_slot(db, user.id)

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
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
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

    **必须包含 alpha**。契约规定 RGBA 四通道均值与「任一通道变化」的像素比。
    之前只比 RGB，理由是「alpha 全不透明时恒为 0，算进去会稀释差异」——
    但透明与不透明在图上看就是两张图：(10,20,30,255) 和 (10,20,30,0) 被判成
    exactMatch=true / mae=0，是这类接口最坏的一种误导。稀释的担心也不成立：
    MAE 取的是四通道平均，稳定不透明的两张图 alpha 差恒为 0，不会把 RGB 的
    差异压小到看不见。

    ``differentPixelRatio`` 需要"任一通道有差"的像素比例，所以把各通道取
    较亮者合成一张掩码，再统计非零比例——直接对灰度图比较会漏掉"只有蓝通道
    差 1"这种细微差异。
    """
    if old.mode != new.mode:
        raise errors.invalid_state("两张图的色彩模式不同，无法比较")
    if old.mode != "RGBA":
        old = old.convert("RGBA")
        new = new.convert("RGBA")

    diff = ImageChops.difference(old, new)
    channels = diff.split()
    peak = channels[0]
    for channel in channels[1:]:
        peak = ImageChops.lighter(peak, channel)
    mask = peak.point(lambda value: 255 if value > 0 else 0)

    different_ratio = ImageStat.Stat(mask).mean[0] / 255.0
    means = ImageStat.Stat(diff).mean
    mae = sum(means[:4]) / 4.0 / 255.0
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
    if original is None:
        raise errors.not_found("原渲染任务不存在")

    # 只允许跟这个任务**当初绑定的那个原任务**比较（契约 8.2）。
    # 上一轮我把授权放宽到"快照可读"，结果只要某个公开版本引用了这份
    # generation，就能拿自己的复现任务去比该 generation 下任意别的任务——
    # 包括产物根本读不到的私有任务，而 MAE/differentPixelRatio 会把那张图
    # 的像素统计泄露出去。快照公开不等于它底下的所有渲染输出都公开。
    if job.original_job_id is not None:
        if job.original_job_id != original.id:
            raise errors.not_found("原渲染任务不存在")
    elif original.owner_id != user.id:
        # 没绑定原任务的普通渲染任务，只能跟自己的任务比
        raise errors.not_found("原渲染任务不存在")

    original_generation = db.get(GenerationModel, original.generation_id)
    if original_generation is None or not works_service.can_read_generation(
        db, user, original_generation
    ):
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
