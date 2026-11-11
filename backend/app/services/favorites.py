"""作品收藏。

两个动作都做成幂等的，理由不是「图省事」，而是前端会重试：

* ``PUT`` 收藏：网络抖动时浏览器可能重发，第二次返回 500 会让用户
  以为操作失败，而其实第一次已经成功。
* ``DELETE`` 取消：契约明说「没有收藏也返回 204」，且「作品已下线仍可
  删除本人收藏关系」——收藏关系属于用户，不该被作品状态绑架。

复合主键 ``(owner_id, work_id)`` 让幂等在数据库层面也成立，
不依赖「先查后写」这种在并发下会穿透的写法。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import errors
from app.models.sessions import WorkFavorite
from app.models.users import User
from app.models.works import Work as WorkModel
from app.schemas.sessions import FavoriteResult
from app.services import works as works_service

__all__ = ["add_favorite", "is_favorited", "remove_favorite"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_visible_work(db: Session, user: User, work_id: str) -> WorkModel:
    """只能收藏看得见的作品。

    看不见的一律 404：如果对私有作品返回「无权收藏」，等于告诉对方
    「这个 ID 确实是一个作品」。
    """
    work = db.get(WorkModel, work_id)
    if work is None or not works_service.can_read_work(db, user, work):
        raise errors.not_found("作品不存在")
    return work


def add_favorite(db: Session, user: User, work_id: str) -> FavoriteResult:
    work = _load_visible_work(db, user, work_id)
    existing = db.get(WorkFavorite, (user.id, work.id))
    if existing is None:
        db.add(WorkFavorite(owner_id=user.id, work_id=work.id, created_at=_now_iso()))
        db.commit()
    return FavoriteResult(workId=work.id, isFavorite=True)


def remove_favorite(db: Session, user: User, work_id: str) -> None:
    """取消收藏。作品不存在、已下线、本来就没收藏，都算成功。

    这里刻意不校验作品可见性：用户可能先收藏、后来作品被下线，这时
    他还是应该能把它从自己的收藏列表里去掉，否则那条记录永远清不掉。
    """
    existing = db.get(WorkFavorite, (user.id, work_id))
    if existing is not None:
        db.delete(existing)
        db.commit()


def is_favorited(db: Session, user: User, work_id: str) -> bool:
    return db.get(WorkFavorite, (user.id, work_id)) is not None
