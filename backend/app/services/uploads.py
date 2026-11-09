"""产物文件存储：PNG 帧与 ZIP 打包。

设计取舍：**二进制落磁盘，数据库只存元数据与相对路径。**

不把 PNG 塞进 SQLite 的理由：备份从「拷一个库文件」变成「拷库 + 拷目录」两步，
且 SQLite 读 BLOB 是整块进内存，没法流式返回。落盘就能用 ``FileResponse``
直接 sendfile。

安全性上做三件事：
1. 只接受 PNG 魔数，不信任客户端的 Content-Type 与后缀
2. 宽高从 IHDR 真实读出，而不是让客户端报——报错的尺寸会污染 manifest
3. 文件名由服务端生成（``art-<uuid>.png``），**绝不使用客户端传的文件名**，
   否则 ``../../`` 这类路径能穿出数据目录
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.errors import validation_error
from app.settings import Settings

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# 单帧上限。契约没给 FrameUpload 的字节上限，按导入包的口径取 5MiB：
# 800x600 的 PNG 正常在 30~200KB，5MiB 已经宽出两个数量级。
MAX_FRAME_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    """落盘结果，由调用方写进 artifacts 表。"""

    artifact_id: str
    kind: str
    content_type: str
    byte_size: int
    storage_path: str
    sha256: str
    width: int | None
    height: int | None
    time_ms: int | None


def _artifacts_root(settings: Settings) -> Path:
    root = Path(settings.data_dir) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def read_png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 的 IHDR 块读真实宽高。

    IHDR 固定在第一个块：8 字节签名 + 4 字节长度 + 4 字节类型，
    width 在偏移 16~19、height 在 20~23，都是大端 32 位。
    """
    if len(data) < 24:
        raise validation_error("PNG 文件不完整，至少需要 24 字节")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise validation_error("PNG 尺寸非法")
    if width > 8192 or height > 8192:
        raise validation_error(f"PNG 尺寸超出上限：{width}x{height}")
    return width, height


def store_frame(
    settings: Settings,
    owner_id: str,
    data: bytes,
    time_ms: int,
) -> StoredArtifact:
    """校验并保存一帧 PNG。"""
    if not data:
        raise validation_error("上传内容为空")
    if len(data) > MAX_FRAME_BYTES:
        raise validation_error(
            f"单帧超过 {MAX_FRAME_BYTES // (1024 * 1024)}MiB 上限，实际 {len(data)} 字节"
        )
    if not data.startswith(PNG_MAGIC):
        raise validation_error("只接受 PNG 格式，文件头不匹配")

    width, height = read_png_size(data)
    digest = hashlib.sha256(data).hexdigest()
    artifact_id = f"art-{uuid4().hex}"

    # 按 owner 分目录：单目录堆几十万文件会让目录索引变慢，
    # 也方便将来按用户清理
    bucket = owner_id[:12] if owner_id else "shared"
    relative = f"{bucket}/{artifact_id}.png"
    target = _artifacts_root(settings) / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    # 先写临时文件再 rename：中途崩溃不会留下半个 PNG 被当成有效产物
    tmp = target.with_suffix(".png.part")
    tmp.write_bytes(data)
    tmp.replace(target)

    return StoredArtifact(
        artifact_id=artifact_id,
        kind="png",
        content_type="image/png",
        byte_size=len(data),
        storage_path=relative,
        sha256=digest,
        width=width,
        height=height,
        time_ms=time_ms,
    )


def artifact_path(settings: Settings, storage_path: str) -> Path:
    """把库里的相对路径还原成绝对路径，并挡住目录穿越。

    即使有人手工把 ``..\\..\\etc\\passwd`` 写进库，这里也会拒绝，
    因为 ``resolve()`` 之后再比对父目录。
    """
    root = _artifacts_root(settings).resolve()
    candidate = (root / storage_path).resolve()
    if not candidate.is_relative_to(root):
        raise validation_error("产物路径非法")
    return candidate


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
