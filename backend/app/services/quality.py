"""渲染质量检查：空白输出与覆盖率。

契约里 ``QualityReport`` 有 5 个字段，之前一直是 ``None``——结构写好了
但没人算。这个模块补上计算。

两个量：

* ``nonBackgroundPixelRatio``：与画布背景色有实质差异的像素占比。
  用 ``pixelDifferenceThreshold`` 做容差，避免抗锯齿边缘把整幅图算成"有内容"。
* ``emptyOutput``：覆盖率低于 ``EMPTY_RATIO`` 就视为空图。

为什么值得单独算：一张全白的 PNG 通过了所有结构校验、能存进作品、能公开，
但它上面什么都没画。这种"技术上成功、事实上失败"的产物只有看像素才发现。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.works import Artifact
from app.schemas.validation import Issue
from app.schemas.works import FrameQuality, QualityReport
from app.services import uploads
from app.settings import Settings
from app.engine.assessment import assess, unavailable
from app.schemas.ir import DrawingIR

__all__ = ["build_quality_report", "EMPTY_RATIO", "PIXEL_THRESHOLD"]

# 任一分量差超过这个值才算"非背景"。8/255 能滤掉抗锯齿与压缩噪声
PIXEL_THRESHOLD = 8
# 覆盖率低于 0.1% 视为空图
EMPTY_RATIO = 0.001


def _background_color(ir_json: str) -> tuple[int, int, int]:
    try:
        ir = json.loads(ir_json or "{}")
        canvas = ir.get("canvas") or {}
        raw = str(canvas.get("background") or "#FFFFFF").lstrip("#")
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except (ValueError, IndexError):
        # 背景色坏了不该让整个上传失败，退回白色
        return (255, 255, 255)


def _coverage(image: Image.Image, background: tuple[int, int, int]) -> float:
    """与背景色有实质差异的像素占比。

    先取 RGB 三通道的最大差异再做阈值，而不是先转灰度：纯绿背景上的
    绿字在灰度里几乎分不出来，但通道差能看出来。
    """
    bg = Image.new("RGBA", image.size, (*background, 255))
    diff = ImageChops.difference(image, bg)
    red, green, blue, _alpha = diff.split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    # mask 里 255 表示"与背景有差异"，也就是有内容；直接取它占的比例
    mask = peak.point(lambda value: 255 if value > PIXEL_THRESHOLD else 0)
    covered = ImageStat.Stat(mask).mean[0] / 255.0
    return round(covered, 6)


def build_quality_report(
    db: Session, settings: Settings, job_id: str, ir_json: str, times_ms: list[int],
    *, provenance_json: str | None = None, validation_json: str | None = None,
) -> QualityReport:
    """按已上传的帧算质量报告。缺帧的时刻跳过，不编造数据。"""
    background = _background_color(ir_json)
    rows = db.scalars(
        select(Artifact)
        .where(Artifact.render_job_id == job_id, Artifact.kind == "png")
        .order_by(Artifact.time_ms)
    ).all()
    by_time = {row.time_ms: row for row in rows}

    frames: list[FrameQuality] = []
    images: dict[int, Image.Image] = {}
    for time_ms in times_ms:
        artifact = by_time.get(time_ms)
        if artifact is None:
            continue
        path: Path = uploads.artifact_path(settings, artifact.storage_path)
        if not path.exists():
            continue
        try:
            with Image.open(path) as image:
                coverage = _coverage(image.convert("RGBA"), background)
                images[time_ms] = image.convert('RGBA')
        except OSError:
            continue
        frames.append(
            FrameQuality(
                timeMs=time_ms,
                nonBackgroundPixelRatio=coverage,
                emptyOutput=coverage < EMPTY_RATIO,
            )
        )

    if not frames:
        # 一帧都读不出来时不假装成功，也不抛错——调用方已经走到这里了
        return QualityReport(
            pixelDifferenceThreshold=PIXEL_THRESHOLD,
            nonBackgroundPixelRatio=0.0,
            emptyOutput=True,
            frames=[],
            issues=[
                Issue(
                    code="QUALITY_UNAVAILABLE",
                    message="帧文件无法读取，无法计算质量报告",
                    path=None,
                    nodeIds=[],
                )
            ],
            assessment=unavailable([]),
        )

    average = round(
        sum(frame.nonBackgroundPixelRatio for frame in frames) / len(frames), 6
    )
    issues: list[Issue] = []
    empty_frames = [frame.timeMs for frame in frames if frame.emptyOutput]
    if empty_frames:
        issues.append(
            Issue(
                code="EMPTY_OUTPUT",
                message=f"以下时刻的画面基本是空白：{empty_frames}",
                path=None,
                nodeIds=[],
            )
        )

    try:
        if set(images) != set(times_ms):
            assessment = unavailable([])
            issues.append(Issue(code='QUALITY_UNAVAILABLE', message='部分帧无法读取，版本化质量分析不可用', path=None, nodeIds=[]))
        else:
            assessment = assess(DrawingIR.model_validate_json(ir_json), images,
                                json.loads(provenance_json or '{}'), json.loads(validation_json or '{}'))
    except Exception:
        # An algorithm failure must not invalidate an already verified PNG upload.
        logging.getLogger(__name__).exception('Quality assessment unavailable for render job %s', job_id)
        assessment = unavailable(list(images))
    finally:
        for image in images.values():
            image.close()
    return QualityReport(
        pixelDifferenceThreshold=PIXEL_THRESHOLD,
        nonBackgroundPixelRatio=average,
        # 任一帧空就算整体空：动画中间有一段空白，观感上就是断的
        emptyOutput=bool(empty_frames),
        frames=frames,
        issues=issues,
        assessment=assessment,
    )
