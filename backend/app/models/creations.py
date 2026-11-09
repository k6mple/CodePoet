"""生成快照的 ORM 映射。

表结构由 ``migrations/003_generations.sql`` 定义，这里只做映射。

P0-03 只用到 ``generations`` 一张表。渲染任务（renderJobs）、作品版本
（workVersions）、创作会话（sessions）属 P0-04 及以后，先不建。

``generations`` 是不可变快照：库里有触发器禁止 UPDATE，
所以这个 ORM 类只应该被插入和查询，永远不修改。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Generation(Base):
    """一次生成产出的不可变 IR 快照。

    IR 整份存 ``ir_json`` 不拆表，理由见 003_generations.sql 的头部注释。
    """

    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), ForeignKey("users.id"), nullable=False)
    # template / semantic / ir / subgraph / fragment
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # turtle / canvas2d / webgl / css
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    template_version_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("template_versions.id"), nullable=True
    )
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    ir_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 小写 64 位十六进制 SHA-256，库里有 length 检查
    ir_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_json: Mapped[str] = mapped_column(Text, nullable=False)
    validation_report_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 0/1：基于未发布模板生成时为 1，派生快照会继承
    unpublished_source: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
