# 交接：X1 智能辅助可视计算能力（DeepSeek API）

日期 / 任务 ID / 代码基线：2026-11-20（Asia/Shanghai） / X1 / 分支 `feat/x1-llm-assistant-20261120`（基于 `main` `2313ce2`，经独立审查修复后合入 `main`）
当前状态与影响范围：**真实集成通过（已合入 `main`）**（未配置 `CODEPOET_LLM_API_KEY` 时 `features.llmAssistant = false`，既有主线零回归）。

## 接手者需要知道的变化

- **能力开关与配置**：`backend/app/settings.py` 新增 `CODEPOET_LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`CODEPOET_LLM_API_KEY`（默认 `""`）、`CODEPOET_LLM_MODEL`（默认 `deepseek-chat`）、`CODEPOET_LLM_TIMEOUT_SECONDS`（默认 `30.0`）；仅当 `llm_api_key` 与 `llm_base_url` 非空时，`GET /api/v1/engine/capabilities` 返回 `features.llmAssistant = true`。
- **数据库迁移**：新增 `backend/migrations/014_assistant_proposals.sql`（新建 `assistant_proposals` 表，不修改既有表结构；已验证在 `backups/f1-backup-20261119/` 副本库平滑升级且历史作品零破坏）。
- **新增接口（严格遵循《提分阶段接口增量约定》第 7、9 节）**：
  - `POST /api/v1/assistant/proposals`（登录 + CSRF，202）：接收 `{ baseGenerationId, prompt, target, rootNodeId? }`（`extra="forbid"`，禁止客户端传模型 URL/密钥）。未配置密钥返回 `503 ASSISTANT_UNAVAILABLE`；非模板或已手改基底请求 `parameter_schema` 返回 `422 ASSISTANT_UNSUPPORTED_BASE`；`proposal` 在 `status="ready"` 前恒为 `null`。
  - `GET /api/v1/assistant/proposals/{id}`（登录，200）：仅归属者可读，他人返回 `404`。
  - `POST /api/v1/assistant/proposals/{id}/apply`（登录 + CSRF，201）：未就绪返回 `409 INVALID_STATE`；重检当前用户对 `baseGenerationId` 的读权限；使用 `locking.begin_immediate(db)` 事务幂等保护，重复调用返回同一 `Generation`；`parameter_schema` 在临时模板定义中实例化，不修改原 `TemplateVersion`。
- **隔离校验与自动修复**：后台执行器通过独立子进程 `app.engine.assistant_worker`（复用最小 env、看门狗与 Windows Job Object 边界）校验候选提案、全局 `validate_ir` 限额及 `sample_scene` 采样；首次检出结构/ID 冲突/越界问题后自动将精确 `issues` 反馈给模型修复（`attempts <= 2`），两次均失败置 `status="failed"`、`error.code="ASSISTANT_VALIDATION_FAILED"`、`proposal=null`。
- **溯源与依赖哈希**：`Provenance` 新增可选只读 `assistant: { proposalId, model, attempts, proposalHash, policyRevision }`（旧数据缺字段按 `null` 兼容），随 `ir` / `subgraph` / `fragment`（DSL 沙箱）派生保留，并纳入 `services/works.py::_dependency_snapshot_hash`。
- **前端创作台与详情页**：创作台新增 `AssistantPanel`（无密钥明确提示并禁用、目标模式切换、节点选择、廉洁美学快捷提示词、轮询终态、模型/修复次数/修复问题清单/提案 JSON 预览、显式应用）；`GenerationEvidence` 新增 `AI 智能辅助溯源` 区块。

## 证据与验证边界

- **自动化检查**：
  - 后端全量单测：`cd backend && .\.venv\Scripts\python.exe -m unittest discover -s tests -q` → **493 tests OK（263.189s）**（含新增 `backend/tests/test_assistant.py` 9 项，覆盖迁移 014、503、跨用户 404、422、自动修复 `attempts=2`、两次失败 409、重复 apply 幂等、权限收回拒 apply、派生继承与孤儿恢复）。
  - 沙箱冒烟与守卫：`scripts/smoke_sandbox.py` 通过、`scripts/verify_sandbox_guards.py` **11/11 通过**。
  - 前端检查：`.\scripts\npm.ps1 run typecheck` 与 `API_PROXY_TARGET=http://127.0.0.1:8000 .\scripts\npm.ps1 run build` 退出码均为 `0`。
  - 原有核心浏览器回归（隔离副本库 + `next start`）：`s1b-browser-check.mjs` **10/10 通过**（`pageErrors=0`）；`k1-real-api-check.mjs`（莲/竹/梅 + 离线复现 `exactMatch=true`）**全部通过**（`failures=0`, `pageErrors=0`）。
- **真实 DeepSeek API + 生产 UI 端到端实测（隔离副本库，零 mock）**：
  - 无密钥场景（`CODEPOET_LLM_API_KEY=""`）：`features.llmAssistant = false`，UI 禁用提交按钮并显示不可用提示，API 返回 `503 ASSISTANT_UNAVAILABLE`。
  - 样例 1（`ir_fragment` 清莲片段改造）：`model=deepseek-flash`, `attempts=1`, `proposalHash=57ff166538dd8ead7f80176bbb11cd9f2960f72388b476e5c49ba2434cb31ef7`, `isolatedValidationPassed=true`, 保存并重开复现 `exactMatch=true`。
  - 样例 2（`ir_fragment` 翠竹片段改造）：`model=deepseek-flash`, `attempts=1`, `proposalHash=700881bdaa2d35f6e2c45cbbf503287f743d5328592ae0fdf99646f9045397a7`, `isolatedValidationPassed=true`, 保存并重开复现 `exactMatch=true`。
  - 样例 3（`parameter_schema` 模板参数空间扩展）：`model=deepseek-flash`, `attempts=1`, `proposalHash=365ff5d373d8b7017c6901952b274483f7742403dcfbc931e93bf92858969efd`, `isolatedValidationPassed=true`, 保存并重开复现 `exactMatch=true`。
  - 样例 4（真实自动修复验证）：首次输出命中 `ASSISTANT_ID_CONFLICT`（`branch-sub` 冲突）被隔离校验检出并自动反馈 DeepSeek，第 2 次修复通过（`model=deepseek-flash`, `attempts=2`, `repairedIssueCount=1`, `proposalHash=054bcacd863092427ac0709c2ec1b25c8fad217b475359cf64c34214490a0388`），应用、保存并复现 `exactMatch=true`。
- **支持范围与未验证事项**：原始截图与过程日志保留在本地忽略目录，未随仓库提交；目标比赛机现场网络连通性及演示环境正式 `.env` 密钥由演示者在现场核验。

## 待办与责任方

- 审阅分支 `feat/x1-llm-assistant-20261120` 后按需合入 `main` 并决定是否推送远端（由仓库维护者执行）；
- 若在正式演示库启用 X1，需在正式 `data/` 上执行 `scripts/migrate.py` 升级至 `014` 并在 `.env` 保留有效 `CODEPOET_LLM_API_KEY`。

## 独立审查问题闭环（对照 `2026-11-20-x1-independent-review.md`）

已逐项完成独立审查中全部 5 项中危问题（M1–M5）与 L1/L2/L4/L5/L6/L7 及并发幂等用例修复并全量复验：
- **M1**：在 `assistant_worker.py` 与 `services/assistant.py::apply_proposal` 双端强制 `ir_fragment` 替换根节点唯一且保留原 `rootNodeId` 与原 `parentId`，补 4 组失败回归测试。
- **M2**：`scripts/verify_sandbox_guards.py` 改用二进制按原始换行符读写还原，11/11 通过且运行后 `git diff` 零行尾残留。
- **M3**：`backend/tests/test_assistant.py` 与 `scripts/smoke_integration.py` 子进程显式固定 `encoding="utf-8", errors="replace"` 与 `PYTHONIOENCODING=utf-8`，在 `PYTHONIOENCODING=utf-8:surrogateescape` PowerShell 环境下实测 `test_assistant` 12/12、`smoke_integration` 40/40 通过。
- **M4 / L5**：`AssistantPanel.tsx` 与 `Creator.tsx` 纳入 `dirty`、`versionLoading`、`backendMismatch` 禁用防护与切后端清理，`onApplied` 同步 `backend`，轮询增加 120s 总超时兜底。
- **M5**：`execute_proposal` 同步写入实际执行所用 `policy_revision`，`apply_proposal` 重检当前策略 `maxIRNodes`/`maxVertices`/`maxParameters`（收紧超限返回 `409 INVALID_STATE`），新增策略收紧与 4 线程并发 `apply` 幂等单测。
- **L1 / L2 / L4 / L6 / L7**：补充 `docs/接口文档.md` 第 13 节 X1 契约；收紧 `llm_timeout_seconds <= 30.0`；提交可复跑脚本 `frontend/tests/x1-browser-check.mjs`；补全 `parameter_schema` 逐参数单边 min/max 边界校验；`014_assistant_proposals.sql` 增加 `ready -> attempts >= 1` 表级约束。
- **复验结果**：后端 **496 tests OK（200.223s）**、`smoke_integration` **40/40**、`verify_sandbox_guards` **11/11（零脏文件）**、前端 `typecheck`/`build` 退出码 `0`、`frontend/tests/x1-browser-check.mjs` 真实 DeepSeek 3+1 + 无密钥 503 全通过（`exactMatch=true`, `pageErrors=[]`）。
