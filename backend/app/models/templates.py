"""模板容器与模板版本的 ORM 映射。

表结构由 ``migrations/001_init.sql`` 定义，这里只做映射。

P0-03 只需要「读模板版本」这一条路径：``template`` 模式的生成要拿
``ir_template_json`` / ``parameter_schema_json`` / ``bindings_json`` /
``constraints_json`` 四份数据去实例化。完整的模板 CRUD 与审核归属
P1A-03 / P1A-04，这里不实现。

注意 ``published_version_id`` 与 ``latest_version_id`` 在 SQL 层没有外键：
它们指向 ``template_versions``，而 ``template_versions.template_id``
又指回 ``templates``，两张表互相引用。加了 FK 就删不掉任何一边，
所以 001 里刻意留空，靠服务层维护一致性。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # draft / submitted / published / rejected / archived
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    published_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    working_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    supported_backends_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["canvas2d"]'
    )
    thumbnail_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class TemplateVersion(Base):
    """一个不可覆盖的模板版本。结构改动产生新版本，旧版本只读。"""

    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    # draft / submitted / published / rejected / archived
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 带占位符的 IR，实例化时把参数值按 bindings 写进去
    ir_template_json: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    bindings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    constraints_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    supported_backends_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["canvas2d"]'
    )
    fragment_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    ontology_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_by: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=True
    )
    submitted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
