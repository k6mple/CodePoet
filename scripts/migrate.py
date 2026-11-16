"""按编号顺序执行 backend/migrations/*.sql。

用法（在仓库根目录执行）：
    uv run --project backend python scripts/migrate.py
    uv run --project backend python scripts/migrate.py --status

设计要点：
  * 已执行的迁移记录在 ``schema_migrations`` 表，重复运行是安全的（幂等）。
  * 每个迁移的建表语句和「记账」写在同一个事务里，失败整体回滚，
    不会出现「表建了一半但没记账」或「记了账但表没建」。
  * SQLite 的 ``executescript`` 会先提交当前事务，所以 BEGIN 必须写进脚本，
    不能在外面用 connection.execute("BEGIN")。脚本执行后检查外键再提交。
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

# 让脚本能 import backend/app 下的模块，而不要求额外安装
sys.path.insert(0, str(BACKEND_DIR))

from app.settings import Settings  # noqa: E402


def connect(settings: Settings) -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path)
    # 建表涉及外键依赖，必须打开外键约束，否则顺序错了也不报错
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_registry(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def discover() -> list[tuple[int, Path]]:
    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"^(\d+)_", path.name)
        if match is None:
            raise SystemExit(f"迁移文件名必须以数字编号开头：{path.name}")
        migrations.append((int(match.group(1)), path))
    versions = [version for version, _ in migrations]
    if len(set(versions)) != len(versions):
        raise SystemExit(f"迁移编号重复：{versions}")
    return migrations


def applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_one(connection: sqlite3.Connection, version: int, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    # 只拦「独立成行的 BEGIN; / COMMIT;」。触发器本体是
    # ``CREATE TRIGGER ... BEGIN ... END;``，那个 BEGIN 后面没有分号，
    # 不能被这条检查误伤。
    if re.search(
        r"^\s*(BEGIN|COMMIT)\s*;\s*$", sql, flags=re.IGNORECASE | re.MULTILINE
    ):
        raise SystemExit(f"{path.name} 里不要自带 BEGIN/COMMIT，事务由迁移器管理")

    applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    name = path.name.replace("'", "''")

    # Rebuilding a referenced SQLite table requires FK enforcement to be disabled
    # outside the transaction. Only explicitly marked migrations may do so; check
    # every reference before committing, and restore enforcement even on failure.
    rebuild = '-- migrate: foreign_keys_off' in sql
    foreign_keys = connection.execute('PRAGMA foreign_keys').fetchone()[0]
    if rebuild:
        if connection.in_transaction:
            raise RuntimeError('Table rebuild requires an idle connection')
        connection.execute('PRAGMA foreign_keys=OFF')
        sql = re.sub(r'^PRAGMA foreign_keys=(OFF|ON);\s*$', '', sql, flags=re.MULTILINE)

    # 建表 + 记账打包成一个事务
    script = (
        "BEGIN;\n"
        f"{sql}\n"
        "INSERT INTO schema_migrations (version, name, applied_at) "
        f"VALUES ({version}, '{name}', '{applied_at}');\n"
    )
    try:
        connection.executescript(script)
        violations = connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise sqlite3.IntegrityError(f'{path.name}: foreign key violations: {violations}')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if rebuild:
            connection.execute(f'PRAGMA foreign_keys={int(foreign_keys)}')


def main() -> int:
    parser = argparse.ArgumentParser(description="执行数据库迁移")
    parser.add_argument("--status", action="store_true", help="只显示状态，不执行")
    args = parser.parse_args()

    settings = Settings()
    connection = connect(settings)
    try:
        ensure_registry(connection)
        done = applied_versions(connection)
        migrations = discover()

        if args.status:
            print(f"数据库：{settings.database_path}")
            for version, path in migrations:
                state = "已应用" if version in done else "待应用"
                print(f"  {version:03d}  {path.name:<40} {state}")
            return 0

        pending = [(v, p) for v, p in migrations if v not in done]
        if not pending:
            print(f"没有待应用的迁移（共 {len(migrations)} 个，全部已应用）")
            return 0

        for version, path in pending:
            print(f"应用 {version:03d} {path.name} ... ", end="", flush=True)
            apply_one(connection, version, path)
            print("成功")

        print(f"\n完成，数据库：{settings.database_path}")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print("当前表：" + ", ".join(row[0] for row in tables))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
