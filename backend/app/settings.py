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

    # 启用的渲染后端，逗号分隔。默认只有 canvas2d；隔离联调环境可以显式打开
    # 候选后端（CODEPOET_ENABLED_BACKENDS=canvas2d,css），公共部署保持默认。
    # 未知名字由 engine.capabilities 过滤，不在这里报错——配置项写错不该让
    # 整个服务起不来，但也不能默默当成已启用。
    enabled_backends: str = "canvas2d"

    # K1 知识图谱来源白名单，逗号分隔的 sourceId（目前只有 wikidata）。
    # 默认空 = 未配置，`POST /knowledge-graph/generations` 一律 503。
    # 只认这里的键，**不接受用户传 URL**——否则服务端就成了任意地址代理。
    knowledge_graph_sources: str = ""

    # 抓取超时（秒）。实测单次 3~6s，留一倍余量。
    knowledge_graph_timeout_seconds: float = 10.0

    # 外部实体 → 站内知识条目的显式映射表（JSON）。空 = 只用标签精确匹配。
    # 显式映射是人工确认过的对应关系，比字符串相似度可靠：
    # 「竹亚科」对「竹」、「梅」对「墨梅」这种差异，猜会错、表才对得上。
    knowledge_graph_mapping_file: str = ""

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
