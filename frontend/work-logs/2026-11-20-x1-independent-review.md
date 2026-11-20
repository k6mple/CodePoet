# 交接：X1 独立审查问题清单

日期 / 任务 ID / 代码基线：2026-11-20（Asia/Shanghai） / X1 独立审查 / 分支 `feat/x1-llm-assistant-20261120`（相对 `main` 基线 `2313ce2`）
当前状态与影响范围：独立只读审查与实测复验完成。真实 DeepSeek 的 `ir_fragment`、`parameter_schema`、真实 `attempts=2` 自动修复及“生成 → 隔离校验 → 应用 → 保存 → 重开复现 `exactMatch=true`”均已通过；未发现 Blocker/High 级越权或密钥泄露。但存在 1 项核心子树语义中危、2 项验证工具链中危、1 项前端状态中危、1 项策略溯源中危，以及若干低危/文档项。**按本次零信任准入标准，结论为“需修复后再审”。**

本文档自包含，不链接或依赖 `frontend/local-work-logs/` 等本地忽略文件；原始截图与临时运行产物保留在审查者本机，不作为本文档结论的唯一依据。

---

## 1. 审查范围与结论

- **范围**：`git diff main..HEAD` 全部 26 个文件，重点为 `settings.py`、`schemas/generation.py`、`api/engine.py`、`api/assistant.py`、`services/assistant.py`、`services/sandbox.py`、`services/works.py`、`engine/assistant_worker.py`、`engine/llm_client.py`、`migrations/014_assistant_proposals.sql`、`tests/test_assistant.py`、`AssistantPanel.tsx`、`Creator.tsx`、`GenerationEvidence.tsx`、`types/core.ts`、`main.css`。
- **方法**：逐行代码审查 + 隔离副本库实测；真实 DeepSeek API、生产 `next start` UI、真实浏览器保存/重开/复现；未使用 mock 替代真实闭环。正式 `data/` 与 `backups/f1-backup-20261119/` 未写入。
- **总体结论**：
  - 运行时功能闭环：**通过**（真实 3+1、保存复现 `exactMatch=true`、无密钥零回归、s1b 10/10、k1 三阶段重试通过）。
  - 零信任准入：**需修复后再审**（见第 2、3 节）。

---

## 2. 中危问题（Medium）

### M1. `ir_fragment` 未强制“替换根延续原 parentId”，可把子树替换成脱离原父节点的根

- **位置**：
  - `backend/app/engine/assistant_worker.py:188-230`：合并候选 IR 时未校验替换根 parentId。
  - `backend/app/services/assistant.py:332`：prompt 明确写“替换根节点须延续原 parentId”。
  - `backend/app/services/assistant.py:867-898`：apply 重建 IR 时同样未校验。
  - 对照 `frontend/src/components/IRControls.tsx:66`：现有子图 UI 明确要求“必须保留根节点 ID 和原 parentId”。
- **现象/证据**：边界探针（mock 模型仅用于让片段确定，worker 与校验为真实实现）提交
  `{rootNodeId:"lotus_core", replacementNodes:[{id:"new-root", parentId:null, ...}]}`
  得到 `status=ready`、`attempts=1`、`apply=201`；应用后 `new-root.parentId=null`，而原 `lotus_core.parentId="scene_root"`。
- **影响**：模型可把“替换子树”变成“新增根/改挂到其他节点”；IR 结构仍合法，但不是契约要求的合法子树替换。无跨用户/权限影响，用户仍需显式 apply，但核心语义未被保证。
- **修复建议**：
  1. 在 `_validate_ir_fragment` 中找出 `replacementNodes` 的根节点（父节点不在替换集合内的节点），要求**恰好一个**，且其 `parentId === 原目标节点.parentId`；建议同时要求其 `id === rootNodeId`，与现有子图 UI 一致。
  2. 在 `apply_proposal` 重建 IR 前重复同一校验，避免只依赖 worker。
  3. 增加回归用例：`parentId=null` 必须失败；`parentId=其他既有节点` 必须失败。

### M2. `verify_sandbox_guards.py` 运行后残留行尾改动，`git status` 变脏

- **位置**：`scripts/verify_sandbox_guards.py:121,126,130`。
- **现象**：脚本用 `read_text()` 读入（通用换行把 CRLF 变成 `\n`），再用 `write_text(..., newline="\n")` 写回。运行后以下文件从 `w/crlf` 变为 `w/lf`，`git status` 出现 3 个 `M`：
  - `backend/app/engine/dsl.py`
  - `backend/app/engine/dsl_worker.py`
  - `backend/app/services/policy.py`
  `backend/app/services/sandbox.py` 原本已是 LF，未变化。
- **证据**：脚本本身 11/11 通过；运行后 `git status` 确见上述 3 个修改；按运行前备份恢复后 SHA256 一致，工作区恢复干净。
- **影响**：验收工具本身会弄脏工作区，可能导致误提交或掩盖真实差异；与“运行后 `git status` 应无额外脏文件”的验收要求冲突。
- **修复建议**：用二进制读写（`read_bytes`/`write_bytes`）或 `newline=""` 保留原始行尾；脚本结束前校验 `git diff --quiet`，异常时恢复。

### M3. 子进程 `text=True` 未固定 UTF-8，导致题述 PowerShell 命令在本机失败

- **位置**：`backend/tests/test_assistant.py:281-288`；`scripts/smoke_integration.py:56-62`。
- **现象**：当前 PowerShell 环境设置了 `PYTHONIOENCODING=utf-8:surrogateescape`，子进程输出 UTF-8，而 `subprocess.run(text=True)` 的父进程按 GBK（gb2312）解码，抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xae`；随后 `proc.stdout` 为 `None`，在 `proc.stdout + proc.stderr` 处变为 `TypeError`。
- **实测结果**：
  - `tests.test_assistant -v`（PowerShell 原样）：**8 通过 + 1 error**（`test_migration_014_upgrades_legacy_backup_copy`）。
  - 清除 `PYTHONIOENCODING` 后同一套件：**9/9 OK**。
  - `smoke_integration.py --data-dir <temp> --port 18236`（PowerShell 原样）：**退出 1**；清除该变量后：**40/40 通过**。
  - 全量 `unittest discover -s tests -q`（未设置该变量）：**493 tests OK（210.636s）**。
- **影响**：不是运行时产品缺陷；但题述验收命令在常用 IDE/CI 环境不可复现，会直接推翻“9/9、40/40、493 tests OK”的交接结论。
- **修复建议**：所有 `subprocess.run(..., text=True)` 显式加 `encoding="utf-8", errors="replace"`，或统一为子进程设置 `PYTHONIOENCODING`；测试/脚本不应依赖控制台代码页。

### M4. AI 面板未纳入 `dirty` / 后端切换状态，可能丢草稿或应用后保存按钮卡死

- **位置**：
  - `frontend/src/components/Creator.tsx:1035-1042`：面板 `disabled={initializing || saving || pending}`；`onApplied` 只调用 `acceptGeneration`，未同步 `backend`。
  - `frontend/src/components/Creator.tsx:519-540`：`switchBackend` 设置新 `backend`、`dirty=true`，旧 `generation` 仍保留到新生成返回。
  - `frontend/src/components/Creator.tsx:977`：保存按钮要求 `generationBackend(generation) === backend`。
  - `frontend/src/components/AssistantPanel.tsx:139-151`：只在 `generation.id` 变化时清理提案。
  - `frontend/src/components/Creator.tsx:345-361`：`acceptGeneration` 会重置 `draft`/`dirty`。
- **影响一（草稿丢失）**：用户在 JSON IR 中修改节点（`dirty=true`）后仍可点击“应用提案”；apply 后 `acceptGeneration` 直接重置草稿，未提示、未保存，手动修改丢失。
- **影响二（跨后端状态错位）**：生成画布后切换渲染后端（如 canvas2d → css），在新 generation 返回前的窗口内，AI 面板仍可操作；apply 会基于旧 base 的 backend 生成新快照，但 `backend` 状态仍是 css，保存按钮因 `generationBackend(generation) !== backend` 保持禁用，用户必须手动切回。
- **证据级别**：代码路径确认；本轮真实 UI 验收未切换后端/草稿，未单独做浏览器复现。
- **修复建议**：
  1. 把 `dirty`、`versionLoading`、`generationBackend(generation) !== backend` 纳入面板禁用条件；
  2. `onApplied` 中同步 `setBackend(generationBackend(applied))`，或禁止跨后端应用；
  3. 后端切换时清理当前提案，或在 apply 前给出草稿丢弃确认。

### M5. 策略版本溯源与 apply 时限额重校验不一致（窄 TOCTOU / 审计失真）

- **位置**：
  - `backend/app/services/assistant.py:192`：受理时 `policy_service.snapshot()` 并写入 `row.policy_revision`。
  - `backend/app/services/assistant.py:653-654`：执行时重新取 `policy_service.snapshot()`。
  - `backend/app/services/assistant.py:750-757`：worker 使用执行时策略值。
  - `backend/app/services/assistant.py:762-769`：`ProposalValidation.policyRevision` 记录的是受理时 revision。
  - `backend/app/services/assistant.py:820-1007`：apply 不再重新执行隔离校验或按当前策略校验节点/顶点/参数上限。
- **影响**：若管理员在“受理→执行”之间修改策略，`validation.policyRevision` 与 `provenance.assistant.policyRevision` 记录的不是实际执行所用策略；apply 也不会因策略收紧而拒绝已 ready 的提案（`maxIRNodes`/`maxVertices`/`maxParameters` 可能已被调低）。这不是水平越权（需管理员改策略），但属于可追溯性和一致性缺口。
- **修复建议**：执行时使用受理时保存的策略快照，或在执行/apply 时更新并校验 revision；apply 前重新校验当前策略限额（至少节点数、顶点数、参数数），或要求 `policyRevision` 未变。

---

## 3. 低危 / 改进建议（Low）

| 编号 | 问题 | 位置 / 证据 | 建议 |
|---|---|---|---|
| L1 | 当前接口文档与 OpenAPI 快照未同步 X1 | `docs/接口文档.md` 无 assistant 条目；`docs/openapi.json`/`openapi-core.json` 无 `llmAssistant`/assistant 路由；`docs/提分阶段接口增量约定.md` §8 要求同步运行时 OpenAPI | 将 `/assistant/proposals` 三端点、`Provenance.assistant`、`features.llmAssistant` 写入运行时导出 OpenAPI 和接口文档 |
| L2 | LLM 超时上限 120s，契约写 30s | `backend/app/settings.py:49`：`Field(default=30.0, gt=0, le=120.0)` | 默认已 30s；若坚持契约，将 `le` 收紧到 30，或明确“可运维调高”的边界 |
| L3 | 用户 prompt 明文入库 | `backend/app/models/assistant.py:32`、`backend/app/services/assistant.py:210` | 若“私密提示”包含用户自然语言需求，按契约应只存 hash/脱敏摘要；响应已不回显 prompt，风险有限 |
| L4 | 仓库内没有 X1 浏览器验收脚本 | `frontend/tests/` 无 assistant/X1 脚本；本轮真实 UI 证据在本地忽略目录 | 将本轮核心断言整理为可提交的 `frontend/tests/x1-browser-check.mjs`，避免“本地截图不可复现” |
| L5 | 前端轮询无总超时 | `frontend/src/components/AssistantPanel.tsx:167-184` | 轮询 600ms 无上限；若提案长期 queued/running，页面会一直轮询。增加最大等待（如 120s）和“重新发起/查看错误”入口 |
| L6 | `parameter_schema` min/max 只测 all-min / all-max 两个角 | `backend/app/engine/assistant_worker.py:415-454` | 可按每个数值参数单独取 min/max（其余取 default），覆盖混合极值；当前已满足最低边界校验要求 |
| L7 | DB CHECK 未强制 ready 时 `attempts>=1` | `backend/migrations/014_assistant_proposals.sql:21,41-44` | 服务层当前都写 1/2；若追求表级不变量，可加 `status != 'ready' OR attempts >= 1` |
| L8 | UI 只允许“自己的 generation”作基底 | `frontend/src/components/AssistantPanel.tsx:89` | 后端 `can_read` 允许公开可读基底派生；若产品希望 B 复用 A 的公开快照，UI 需提供入口；若只允许本人，需在契约写明 UI 限制 |

---

## 4. 契约核对摘要

| 契约要求 | 实现/证据 | 结论 |
|---|---|---|
| 未配置 `CODEPOET_LLM_API_KEY` 时 `features.llmAssistant=false`、POST 503 | `api/engine.py:103-107`；实测 false + 503 `ASSISTANT_UNAVAILABLE` + UI 禁用 | 通过 |
| POST 登录 + CSRF，`extra="forbid"`，禁止客户端传 URL/Key | `api/assistant.py:44-70`；`schemas/assistant.py:26-50`；实测无 CSRF 403、额外 `apiKey` 422 | 通过 |
| 202 Proposal；ready 前 `proposal=null`；GET 仅归属 404 | `services/assistant.py:98-125,250-255`；单元测试 + 实测 | 通过 |
| 服务端模型调用、超时 30s、最多一次修复 | `engine/llm_client.py:54-115`；`services/assistant.py:696-812`；真实 3+1 实测 | 基本通过；超时上限可配到 120s（L2） |
| 隔离 worker：独立子进程、阻断 `CODEPOET_*`、CPU/内存/墙钟、进程回收 | `services/sandbox.py` `_worker_env`/`run_isolated_module`；`engine/assistant_worker.py` `_Watchdog`；单测/实跑 | 通过 |
| `ir_fragment` 子树规则（ID 唯一/不冲突、无悬空/环、深度、节点/顶点、自交/退化、越界、动画清理） | `assistant_worker.py:148-319`；`engine/validate.py`；真实样例 | 部分通过：parentId 连续性未强制（M1），其余规则有校验 |
| `parameter_schema` 仅未手改模板基底，422 其他；临时定义实例化；不改原 TemplateVersion | `services/assistant.py:128-137,185-190,912-960`；单测 + 实测 | 通过 |
| `attempts<=2`；首次失败反馈 issues；两次失败 failed/proposal=null/`ASSISTANT_VALIDATION_FAILED`；apply 409 | `services/assistant.py:696-812,820-847`；真实 attempts=2 + 单测 | 通过 |
| apply 201、重复 apply 同 generation、`BEGIN IMMEDIATE` 幂等、权限收回 404 | `services/assistant.py:820-1007`；单测顺序幂等 + 权限收回；未做并发压测 | 通过（并发未压测） |
| apply 临时定义实例化、保存提案 hash、不原地改模板 | `services/assistant.py:912-960`；单测 | 通过 |
| `provenance.assistant`；IR/子图/DSL 派生保留；依赖哈希纳入 | `schemas/generation.py:295-305`；`services/sandbox.py:879-881`；`services/works.py:114-121`；单测 | 通过 |
| 403/404/409/422/503 错误码与 envelope | `errors.py:209-222`；实测 503/403/422/409 | 通过 |
| 看板/README 宣称 493 tests、9/9、真实 3+1、s1b/k1 | 本轮实测：全量 493 OK；定向 9/9（清除 `PYTHONIOENCODING` 后）；真实 3+1；s1b 10/10；k1 三阶段重试通过 | 基本属实，但受 M3 环境影响，原样 PowerShell 命令不可复现 |
| 当前接口文档/OpenAPI 同步 | `docs/接口文档.md`、`docs/openapi*.json` 无 X1 | 不一致（L1） |

---

## 5. 实测证据摘要

| 命令/场景 | 退出码 | 关键结果 |
|---|---:|---|
| `tests.test_assistant -v`（PowerShell 原样） | 1 | 8 通过 + 1 error（migration 子进程 GBK 解码） |
| `tests.test_assistant -q`（清除 `PYTHONIOENCODING`） | 0 | 9/9 OK |
| `unittest discover -s tests -q`（无该变量） | 0 | 493 tests OK（210.636s） |
| `scripts/smoke_sandbox.py` | 0 | 真实进程冒烟通过；AST 违规 `DSL_AUDIT_REJECTED` 留痕 |
| `scripts/verify_sandbox_guards.py` | 0 | 11/11 反向验证通过；运行后 3 个文件行尾变脏，已恢复 |
| `smoke_integration.py --data-dir <temp> --port 18236`（PowerShell 原样） | 1 | 子进程 GBK 解码失败 |
| 同上（清除 `PYTHONIOENCODING`） | 0 | 40/40 通过 |
| `npm run typecheck` | 0 | 通过 |
| `API_PROXY_TARGET=http://127.0.0.1:8000 npm run build` | 0 | 通过 |
| 无密钥能力/API/UI | — | `llmAssistant=false`；POST 503；无 CSRF 403；UI 禁用并提示 |
| 真实 DeepSeek `ir_fragment` API | 0 | `prop-eefafb5...`，`deepseek-flash`，attempts=1，hash `a26c18d3...`，apply 201 |
| 真实 DeepSeek `parameter_schema` API | 0 | `prop-2a33dd...`，`deepseek-flash`，attempts=1，hash `798b2ccf...`，apply 201，未改原模板 |
| 真实 DeepSeek 自动修复 API | 0 | `prop-4c8a2f...`，首次 `IR_OUT_OF_BOUNDS`，attempts=2，hash `b2a0334f...`，apply 201 |
| 真实 DeepSeek 生产 UI 三样例 | 0 | `ir_fragment` / `parameter_schema` / 自动修复均 `exactMatch=true`，MAE 0%，`pageErrors=[]` |
| `s1b-browser-check.mjs` | 0 | 10/10，`pageErrors=[]` |
| `k1-real-api-check.mjs`（无管理后端） | 0 | 莲/竹/梅 3 实体，`exactMatch=true`，failures=0，pageErrors=[] |
| `k1-real-api-check.mjs`（管理后端三阶段，首次） | 1 | lotus 阶段通过；第二实体 UI 就绪 120s 超时 |
| `k1-real-api-check.mjs`（管理后端三阶段，重试） | 0 | 全三阶段通过，`exactMatch=true`，failures=0，pageErrors=[] |
| 正式备份/数据核对 | — | `backups/f1-backup-20261119/` 20 文件 SHA256 + LastWriteTimeUtc 未变；正式 `data/codepoet.sqlite3` 最后写入仍为 2026-11-19T12:25Z |

真实 UI 三样例摘要：

| 样例 | proposalId | model | attempts | proposalHash | 保存/复现 |
|---|---|---|---:|---|---|
| `ir_fragment` | `prop-2ca45aed...` | `deepseek-flash` | 1 | `4bcfe885...` | `exactMatch=true`，MAE 0% |
| `parameter_schema` | `prop-b99e3c65...` | `deepseek-flash` | 1 | `c45bef1d...` | `exactMatch=true`，MAE 0% |
| 真实自动修复 | `prop-464fc693...` | `deepseek-flash` | 2 | `b2a0334f...` | `exactMatch=true`，MAE 0% |

真实负路径：首轮复杂莲瓣提示下，模型连续两次输出越界，系统正确返回 `status=failed`、attempts=2、`ASSISTANT_VALIDATION_FAILED`，未产生 generation；该样例 UI 未执行 apply，失败提案 apply 的 409 由单元测试/边界探针覆盖。

---

## 6. 待办与责任方

| 优先级 | 待办 | 责任方 | 验收条件 |
|---|---|---|---|
| P1 | M1：在 worker + apply 强制替换根 parentId/根 ID，补失败用例 | 后端 | `parentId=null` 与改挂其他节点均返回失败；正常替换保留原 parentId |
| P1 | M3：subprocess 固定 UTF-8，确保 PowerShell 原样命令可复现 | 后端/测试 | `tests.test_assistant` 9/9、`smoke_integration` 40/40，无需清除环境变量 |
| P1 | M2：guard 脚本保留原始行尾，运行后 `git status` 干净 | 后端/测试 | 运行前后目标文件 SHA256 一致 |
| P2 | M4：AI 面板纳入 `dirty`/后端切换状态，apply 同步 backend 或禁用 | 前端 | 草稿不被静默丢弃；跨后端应用后保存按钮可用 |
| P2 | M5：策略 revision 溯源与 apply 重校验 | 后端 | 验证报告 revision 与实际执行策略一致；策略收紧后 apply 被拒绝或重校验 |
| P3 | L1：同步运行时 OpenAPI、`docs/接口文档.md` | 后端/文档 | 三端点与 `Provenance.assistant` 可查 |
| P3 | L4：提交 X1 浏览器验收脚本 | 前端 | 从干净检出可复跑真实 3+1 + 保存复现 |
| P3 | L2/L3/L5-L8：按第 3 节逐项收口 | 按模块 | 对应字段/行为可验证 |

---

## 7. 验证边界与说明

- 本次审查未在正式 `data/` 上执行迁移或写操作；正式 `data/` 与 `backups/f1-backup-20261119/` 时间戳与 SHA256 未变。
- 未做并发 apply 的真实多请求压测；`BEGIN IMMEDIATE` 与条件 UPDATE 的幂等性由代码审查和顺序测试覆盖，仍建议补并发用例。
- `k1-real-api-check.mjs` 管理后端三阶段首次在第二实体 UI 就绪处超时，重试通过；未在代码中找到与 X1 相关的确定原因，记录为测试时序波动，建议在 CI/演示前多跑一次。
- 目标比赛机现场网络、正式 `.env` 密钥与演示环境未验证；本文档不宣称“目标环境通过”。
- 未提交、未推送；未修改业务代码。本文档为新增共享记录，供后端/前端协作者按第 6 节处理。
