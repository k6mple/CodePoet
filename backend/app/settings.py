"""Environment configuration with paths independent of the working directory."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEPOET_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = PROJECT_ROOT / "data"

    # 生产环境走 HTTPS 时必须设为 true（CODEPOET_COOKIE_SECURE=true），
    # 否则会话 Cookie 会在明文连接上传输；本地 http 开发保持 false。
    cookie_secure: bool = False

    @field_validator("data_dir", mode="after")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return (PROJECT_ROOT / value.expanduser()).resolve()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "codepoet.sqlite3"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"
