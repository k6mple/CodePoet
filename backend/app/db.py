"""Synchronous SQLite connection; business tables will use numbered migrations."""

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.settings import Settings


class Base(DeclarativeBase):
    pass


def create_database(settings: Settings) -> Engine:
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite", database=str(settings.database_path)),
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
