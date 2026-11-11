# 工作日志：再次同步 main 与可推进工作核对

日期：2026-11-11（Asia/Shanghai）
任务 ID：main-readiness-review-20261111
状态：已完成同步与静态核对；主线真实联调待执行

## 开始记录

- 目标：同步最新 main，阅读最新开发文档，对照实际源码检查前端和联调可推进工作。
- 允许修改范围：本次日志、日志索引、安全同步 Git；只读检查业务代码、后端及共享契约。不主动开发新功能，不推送。
- 初始 Git：main / 9cef990；相对当前 origin/main ahead 1、behind 25。已有未追踪 `frontend/work-logs/2026-11-10-main-readiness-review.md`，予以保留；没有已跟踪文件的未提交修改。
- 执行计划：读取规范和近期日志 → fetch 并核对分叉及差异 → 保留本地提交合并远端 main → 阅读最新看板、文档和源码 → 记录可开工项、依赖及验证边界。
- 已知阻塞：上轮 PR #10 隔离 G0 通过，模板版本 PATCH 缺失导致退回修订阻塞；尚未核对最新主线。历史通过不代表本轮主线通过。

## 实际改动与决定

- 已读取项目 skill、根/前端规范、日志说明、11/10 同步和验收日志，重读交付看板、B 任务书、双人总纲、当前验收/问题文档，并对照实际 API、服务、模型及前端入口。
- `git fetch origin` 成功；最新远端 main 为 `8bbc10c646f20a39e415049fd4f09215dca8aca5`，包含 PR #10/#11/#12。远端此次新增 25 个提交，变更 53 个文件，均为后端实现、迁移、依赖/测试及后端 seed 脚本，前端业务源码没有新增变化。
- 因本地有验收提交 `9cef990`，不能快进。执行 `git merge --no-edit origin/main`，无冲突，生成本地合并提交 `044045325fd1dbc82039bba754ba25048ed99d88`，双亲为 `9cef990`、`8bbc10c`。原提交及未追踪日志保留，没有 rebase/reset 或推送。
- 主线现有上传任务创建为 `running / awaiting_frames`，有效期使用 `UPLOAD_TTL_SECONDS`（300 秒）；上轮 BE-G0-001 修复已进入主线。此次没有启动服务，不能将源码修复或历史隔离验收记为本轮 main 的 G0 通过。
- 新增会话/快照/恢复、收藏、审核、导入导出/审计、规则/语义、子树、复现/像素比较、质量报告和 `scripts/seed_demo.py`。相关路由已注册，前端 Creator、SpacePage、AdminOps、MappingRules、WorkReproduction 已有对应请求入口；仍需真实请求/权限/错误恢复验收。
- 静态扫描路由方法/路径，与核心 OpenAPI 统一参数名后为 **66/67**；唯一缺失为 `PATCH /template-versions/{id}`。初次只扫描 api 目录得到 65/67，随后核对 health 在 main.py 内定义，补入扫描得到上述最终结果。仅为路径覆盖，不代表契约字段、权限或业务通过。
- **BE-TEMPLATE-EDIT-001 仍未解决**：前端 TemplateManager 编辑已有草稿/退回版本调用 PATCH，而最新后端仍无路由；退回→修订保存→再送审继续阻塞。未通过克隆绕过或修改后端。
- **子树能力声明仍阻塞页面**：`backend/app/api/engine.py` 中 `semanticGeneration=True`、`subgraphEditing=False`。虽然 POST 子树接口和服务存在，Creator 尊重能力开关，不展示可操作子树编辑；需后端确认并启用后再验收。完整 IR 编辑可独立推进。
- 现有前端交接文档的“当前 main=e309093、PR 尚未合入”等说明已经过时；本轮以本日志记录当前状态，历史记录未覆盖，交接文档更新列为后续收尾。

## 可推进工作与优先级

| 顺序 | 工作 | 已具备的条件 / 验收重点 |
| --- | --- | --- |
| 1 | 最新 main 标准 G0 回归 | 上传修复已合入；使用新隔离库、迁移 001～010，验证真实 PNG 上传/保存/刷新重开/第二账号 404；补上传中断重试、429、过期恢复等遗留项 |
| 2 | GA：个人空间与管理闭环 | 优先角色审批/知识与标签约束，再两次保存进度、恢复旧快照、作品新版本、收藏持久化、双维筛选、非法导入无部分写入、审计追溯；可独立执行，不必等模板 PATCH |
| 3 | GB：语义、动画与复现 | 已启用语义；用隔离库运行现有账号种子和 demo 导入，确认莲/竹命中不同规则和 IR，叙事定位、三帧真实上传、修改规则后旧来源复现与服务端像素比较、质量报告 |
| 4 | 前端交付收尾 | 针对联调发现的问题修页面状态，整理稳定的演示数据/四角色流程、3 件真实作品、拒审/归档样例、8～10 分钟操作脚本和新证据，更新启动/问题文档并执行移动端与生产构建回归 |

- 仍需后端推进：模板版本 PATCH；确认子树能力开关及对应主线验收。可独立联调项无需等待它们。
- 前端主要功能入口已有实现；不能据此宣称前端无任务或 GA/GB 已完成。当前重点是集成验收、缺陷修复、演示与交付收尾，不新增 P1-C/P2。
- 看板 GA 为 11/12、GB 为 11/16、11/18 功能冻结；P0/P1-A/P1-B 全过才可选一项 P1-C。本轮未勾选看板。

## 验证与结束记录

- `git fetch origin`、`git merge --no-edit origin/main`：退出 0，无冲突。
- `git merge-base --is-ancestor origin/main HEAD`：退出 0，确认已包含最新远端 main。
- `git diff --exit-code origin/main -- backend scripts shared frontend/src frontend/package.json frontend/package-lock.json`：退出 0，业务代码与最新 main 一致；没有自行修改后端/契约。
- 一次性 Node 只读扫描：67 个核心操作匹配 66 个，缺版本 PATCH；扫描未落地新脚本。
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\npm.ps1 run typecheck`：退出 0，Next typegen 和 tsc 通过。
- `git diff --check`：退出 0。
- 未执行：生产构建、后端测试、迁移/种子/demo 导入、服务启动、浏览器 G0/GA/GB、实际 API 请求。未新增测试或业务代码。
- 结束状态：main / `0440453`，相对 origin/main ahead 2（原验收提交 + 本次同步合并提交），没有落后；新增本日志及索引项，原 11/10 未追踪日志保留。本次日志/索引未提交；已产生上述 Git 同步合并提交，未推送。
