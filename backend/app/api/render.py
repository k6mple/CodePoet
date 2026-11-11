"""渲染任务与产物文件接口。

两个关键设计：

1. **帧上传是 multipart**。契约里 ``FrameUpload`` 的 ``file`` 是 ``binary``，
   所以用 ``UploadFile`` 而不是 base64 JSON——base64 会把体积放大 33%，
   而且流式更好处理。
2. **产物下载走 ``FileResponse``**，不整块读进内存。文件落盘的收益在这里兑现。

权属统一 404（契约 2.2）：别人的产物返回「不存在」，不返回 403。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_csrf, require_user
from app.errors import not_found, validation_error
from app.models.users import User as UserModel
from app.models.works import Artifact
from app.schemas.works import RenderCreate, RenderJob
from app.services import uploads
from app.services import works as works_service
from app.settings import Settings

router = APIRouter(tags=["渲染与产物"])

# 用 Annotated 而不是共享的 Path(...) 对象，理由见 api/works.py 的说明
IdPath = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]

SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/render-jobs",
    response_model=RenderJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建四后端渲染任务",
)
def create_render_job(
    payload: RenderCreate,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> RenderJob:
    return works_service.create_render_job(db, user, payload)


@router.get("/render-jobs/{job_id}", response_model=RenderJob, summary="任务状态与帧清单")
def read_render_job(
    job_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
) -> RenderJob:
    return works_service.get_render_job(db, user, job_id)


@router.post(
    "/render-jobs/{job_id}/frames",
    response_model=RenderJob,
    summary="上传浏览器实际绘制的PNG帧",
)
async def upload_frame(
    request: Request,
    job_id: IdPath,
    file: UploadFile = File(..., description="浏览器 toBlob 得到的 PNG"),
    time_ms: int = Form(..., alias="timeMs", ge=0, le=5000),
    ir_hash: str = Form(..., alias="irHash", pattern=SHA256_PATTERN),
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> RenderJob:
    # 先读内容再交给服务层：服务层不该依赖 UploadFile 这种 HTTP 类型
    data = await file.read()
    return works_service.upload_frame(
        db, _settings(request), user, job_id, data, time_ms, ir_hash
    )


@router.get("/artifacts/{artifact_id}", summary="鉴权读取 PNG/ZIP")
def read_artifact(
    request: Request,
    artifact_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel | None = Depends(current_user),
):
    """V 权限：本人、管理员，或这份产物所属快照当前可读（公开作品/待审目标）。

    只判 ``owner_id`` 会让已发布作品的图片取不到：作品详情匿名可读 200，
    但同一作品的 PNG 对别人是 401/404，公共列表就是一片破图。待审作品同理——
    审核人能看到队列行，却打不开要审的那张图。
    """
    row = db.get(Artifact, artifact_id)
    if row is None or not works_service.can_read_artifact(db, user, row):
        raise not_found("产物不存在")

    path = uploads.artifact_path(_settings(request), row.storage_path)
    if not path.exists():
        # 库里有记录但文件被删：这是数据不一致，不该假装 404 让调用方重试
        raise not_found("产物文件已丢失")

    return FileResponse(
        path,
        media_type=row.content_type,
        headers={
            "Content-Length": str(row.byte_size),
            # 私有产物：允许浏览器缓存但禁止中间代理缓存
            # 公开可见的产物不能被中间代理当成私有缓存，否则换了人还是旧图
            "Cache-Control": (
                "private, max-age=3600"
                if user is not None and row.owner_id == user.id
                else "public, max-age=3600"
            ),
            # 内容寻址：同一个 artifactId 的内容不会变，可以直接用哈希做强校验
            "ETag": f'"{row.sha256}"',
        },
    )


@router.post("/render-jobs/{job_id}/cancel", response_model=RenderJob, summary="取消渲染任务")
def cancel_render_job(
    job_id: IdPath,
    db: Session = Depends(get_db),
    user: UserModel = Depends(require_user),
    _csrf: object = Depends(require_csrf),
) -> RenderJob:
    """P0-04 只支持取消还没上传过帧的任务。

    已经 completed 的任务不能再取消——帧已经在磁盘上了，改状态会造成
    「状态是 cancelled 但产物可下载」的矛盾。
    """
    from app.models.works import RenderJob as RenderJobModel

    row = db.get(RenderJobModel, job_id)
    if row is None or row.owner_id != user.id:
        raise not_found("渲染任务不存在")
    if row.phase in ("completed", "finished"):
        raise validation_error("任务已完成，不能取消")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row.status = "cancelled"
    row.finished_at = now
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return works_service.build_render_job(row)
