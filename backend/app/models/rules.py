"""语义映射规则。

规则决定「输入什么文字/实体 → 画什么、用什么色、怎么摆」。
``priority`` 越大越优先，同优先级按创建时间早的胜出——这条保证
同样的输入永远产出同样的图，否则同一句话刷两次会得到不同的画面。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MappingRule(Base):
    __tablename__ = "mapping_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # SQLite 没有布尔类型，用 0/1
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_json: Mapped[str] = mapped_column(Text, nullable=False)
    effects_json: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 规则内容版本：推理快照里记「当时用的是第几版」
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 乐观锁计数器：每次 PATCH +1
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    owner_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
