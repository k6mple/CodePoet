"""DSL 沙箱作业与执行策略的 ORM 映射。

表结构由 ``migrations/013_sandbox_runs_and_policies.sql`` 定义，这里只做映射。

两条与别处不同的约定：

1. ``SandboxRun`` 是**可变**行——它要走状态机（queued → running → 终态）。
   这和 ``Generation`` / ``AuditLog`` 的「插入即不可变」相反，原因很直接：
   一次执行的结果要写回同一行才能把「受理」与「结束」串成一条记录。
   终态之后不应再更新，这一层靠服务层的状态条件更新保证，没有加触发器。
2. ``Policy`` 是单行表（``id = 1``），revision 做乐观锁。不用软删除，
   也不留历史行：历史版本的策略已经在每次 run 的 ``policy_snapshot_json``
   里冻结，再存一份完整历史只是重复。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SandboxRun(Base):
    """一次 DSL 执行的可追溯记录。"""

    __tablename__ = "sandbox_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    base_generation_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=False
    )
    # queued / running / succeeded / failed / timed_out / cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: 提交时绑定的策略版本与生效值。之后 PATCH 改不到已经跑起来的 run。
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)

    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 原始源码。随归属授权读取，审计只存 hash。
    source_json: Mapped[str] = mapped_column(Text, nullable=False)

    generation_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("generations.id"), nullable=True
    )

    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_findings_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )

    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class Policy(Base):
    """全局执行策略的当前值。单行，``id = 1``。"""

    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    values_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=True
    )
