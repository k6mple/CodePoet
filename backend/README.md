# 后端（开发者 A）

使用 FastAPI、Pydantic、同步 SQLAlchemy 和 SQLite。当前已实现应用启动、环境配置、数据库连接和 `GET /api/v1/health`；其他业务模块为占位。

依赖由 uv 管理，Python 固定为 3.12。首次运行 `uv sync --locked`；启动运行 `uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`。检查运行 `uv run --locked python -m unittest discover -s tests -v`。以上命令均在本目录执行。

读取仓库根目录 `.env` 中的 `CODEPOET_DATA_DIR`，相对路径始终相对于仓库根目录。启动创建数据目录并验证真实 SQLite 连接，关闭释放连接池。`/api/docs` 仅展示已实现接口。

- `api/`：HTTP 输入输出，按任务书的六个业务域组织。
- `models/`：用户、知识、模板、创作和运维数据模型；复杂内容使用 JSON 字段。
- `services/`：权限、状态流转、上传持久化、快照、导入导出和审计。
- `engine/`：IR 校验、参数实例化、规则推理、子树处理、确定性采样等纯计算。
- `schemas/` 位于 `app/` 下：Pydantic 请求/响应模型。
- `migrations/`：后续采用简单编号迁移，当前尚无业务表。
- `tests/`：重点覆盖权限、版本、IR、上传及复现行为。

数据库和产物默认使用仓库根目录的 `data/`。图片后续通过受鉴权 API 提供，不能直接将 `data/` 挂为静态目录。幂等种子入口按任务书放在 `scripts/seed.py`，待实现。

继续按 [P0-01](../docs/交付计划与任务看板.md) 补齐 IR 样例与校验。健康检查通过不代表创作、鉴权或保存已实现。
