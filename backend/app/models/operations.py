"""审计日志、导入校验凭证与数据版本。

审核记录（``reviews``）和收藏（``work_favorites``）已经各自成表，
这里只放 P1A-07 新增的三张。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """关键写操作的留痕。字段对齐契约 ``AuditLog`` 的 18 项。

    表上有 ``audit_logs_forbid_update`` / ``_delete`` 两个触发器：
    能改的审计等于没有审计。
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    # 匿名操作没有 actor
    actor_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 追溯的钥匙：一个请求改了多张表，靠它串起来
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    work_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # success / failure
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ImportValidation(Base):
    """预校验与执行之间的凭证。

    存整份包体：执行时不再信任客户端重发的版本，否则中间被换掉
    content_hash 就对不上了，而校验过的和实际导入的会是两份东西。
    """

    __tablename__ = "import_validations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_data_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # 校验是否通过：apply 会拒绝 valid=0 的凭证
    valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    package_json: Mapped[str] = mapped_column(Text, nullable=False)
    counts_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    # 单次消费
    consumed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class DataMeta(Base):
    """单行表，记录全局数据版本。每次导入成功 +1。"""

    __tablename__ = "data_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
