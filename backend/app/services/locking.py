"""SQLite 独占写事务。

这个模块存在的唯一理由是：**「先数一数再插入」在 DEFERRED 事务下不是原子的**。

SQLite 默认的事务模式里，读不加锁、写才升级锁。于是「COUNT 看还有没有名额」
和随后的 INSERT 之间有一段窗口，另一个连接也能读，也能看到同样的 COUNT，
然后两边都插进去——上限就这么被突破了，而串行重试的第二次请求会被正常
拦下，看起来一切正常。这类 bug 只在并发时出现，单线程测试永远发现不了。

``BEGIN IMMEDIATE`` 在事务一开始就拿写锁，COUNT 与 INSERT 之间插不进任何人。

用法（调用前不能有未提交的改动，函数内部会先 commit 一次把只读事务收干净）：

```python
begin_immediate(db)
try:
    count = ...
    if count >= limit:
        raise execution_busy(...)   # 抛之前必须 rollback，见下面
    db.add(row)
    db.commit()
except Exception:
    db.rollback()
    raise
```

**抛出前一定要 rollback**：IMMEDIATE 已经拿到写锁，不回滚的话锁会挂到
Session 依赖清理时才释放，并发请求只能干等到超时——429 会变成 500。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["begin_immediate"]


def begin_immediate(db: Session) -> None:
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
