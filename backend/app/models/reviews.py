"""审核记录。

一张表承载两种审核目标（模板版本、作品版本），用 ``target_type`` 区分。
目标 ID 不做外键——它多态指向 ``template_versions`` 或 ``work_versions``，
单一外键表达不了；代价是目标被删后这里会留孤儿记录，而这正是想要的：
审核历史不该因为目标消失就跟着蒸发。

记录本身只追加。``reviews_forbid_update`` / ``reviews_forbid_delete``
两个触发器在数据库层兜住这一点，``submitted_by <> reviewer_id`` 的 CHECK
则把「禁止自审」变成绕不过去的约束。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    # template_version / work_version
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    # 审核人当时看到的目标版本号，用于事后核对判断依据
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    # approve / reject
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
