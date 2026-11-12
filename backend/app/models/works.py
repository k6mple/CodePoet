"""作品、作品版本、渲染任务与产物文件的 ORM 映射。

表结构由 ``migrations/004_works.sql`` 定义，这里只做映射，不建表。

``works.latest_version_id`` / ``published_version_id`` / ``display_version_id``
三个指针在 SQL 层没有外键，原因同 ``templates``：``works`` 与 ``work_versions``
互相引用，加 FK 就删不掉任何一边，一致性由服务层在同一事务里维护。

``render_jobs.manifest_json`` / ``quality_report_json`` / ``error_json`` 都是整份
JSON 文本，不做拆列——清单只整体读写，从没有按单帧检索的场景。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Artifact(Base):
    """落磁盘的产物文件元数据。二进制本身不进数据库。"""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    # png / zip
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # 相对 data/artifacts/ 的路径
    storage_path: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    render_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    generation_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=False
    )
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    # queued / running / succeeded / failed / timed_out / cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # awaiting_frames / rendering / completed / finished
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    times_ms_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    renderer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    client_environment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Work(Base):
    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latest_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    working_title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    working_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class WorkVersion(Base):
    __tablename__ = "work_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    work_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("works.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    # private / pending_review / published / rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=False
    )
    render_job_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("render_jobs.id"), nullable=False
    )
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    submitted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 被管理员下线撤回公开的时间。published 是终态，不能改回 private，
    # 所以「这个版本还能不能对外读」要靠这个标记持久化下来。
    public_revoked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
