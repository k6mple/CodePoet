"""编辑会话、不可变快照与作品收藏。

会话与快照分表：会话只保存「当前编辑现场」一份，快照一条条往下攒。
混成一张表的话，每次保存都得把旧状态迁走，丢数据只是时间问题。

``latest_snapshot_id`` 故意不加外键——快照表本身外键回会话，双向外键
在 SQLite 的插入顺序上会互相卡住。一致性交给服务层，读取时按
「取不到就当没有」处理。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CreationSession(Base):
    __tablename__ = "creation_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # EditorState 整份存 JSON：字段会随 P1-B 演进，拆列得不偿失
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    latest_snapshot_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class SessionSnapshot(Base):
    """某一时刻的编辑现场。写入后由触发器禁止修改。"""

    __tablename__ = "session_snapshots"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("creation_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # 写入时该会话的 revision，用于把快照与某次保存对上号
    session_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 由恢复产生的快照记下来源，界面上能标「从这一版恢复」
    restored_from_snapshot_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class WorkFavorite(Base):
    """收藏关系。(owner_id, work_id) 复合主键让幂等天然成立。"""

    __tablename__ = "work_favorites"

    owner_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id"), primary_key=True
    )
    work_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
