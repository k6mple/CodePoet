"""X1 LLM 智能辅助提案的 ORM 映射。

表结构由 ``migrations/014_assistant_proposals.sql`` 定义。
提案行遵循状态机：queued -> running -> ready / failed；
显式 apply 后写入 ``applied_generation_id`` 保证幂等。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AssistantProposal(Base):
    """一次 LLM 辅助提案的可追溯记录。"""

    __tablename__ = "assistant_proposals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    base_generation_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=False
    )
    # ir_fragment / parameter_schema
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    root_node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # queued / running / ready / failed
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    proposal_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    applied_generation_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=True
    )

    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)
