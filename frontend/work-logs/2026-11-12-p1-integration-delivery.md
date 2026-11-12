# 工作日志：P1 集成验收与交付收尾

日期：2026-11-12（Asia/Shanghai）
任务 ID：p1-integration-delivery-20261112
状态：本地集成、GA/GB 主路径验收与演示交付材料已完成；未执行远端发布或人工计时彩排

## 开始记录

- 目标：按用户要求顺序推进上轮下一步：核对 PR #16 剩余问题及集成条件，完成前端导入导出适配与 GA/GB 真实验收，整理演示交付材料。
- 范围：前端、前端验收脚本与脱敏证据、前端交接文档及本日志；可安全创建本地集成分支、运行既有后端迁移/种子于隔离数据目录。后端/共享契约遵守项目 skill 只读边界；未授权推送，不自动远端合并 PR。
- 初始 Git：main / `615d1dc`，ahead origin/main 4。已有 `frontend/AGENTS.md` 修改、四个未追踪日志（PR15审查、模板初始化、main同步、open PR审查），全部保留。
- 计划：最新远端/审查核对 → 独立复核 #16 两项问题及可用集成基线 → 前端类型选择和文案适配 → 隔离服务与真实浏览器 G0/模板/P1/子树/导入导出验收 → 演示材料、构建及最终状态核验。
- 已知阻塞：上轮 #16 非法 match 校验 500、多帧缺失标志不实；最新状态待核对。主线 GA/GB 尚未全过；历史通过不能代替本轮验收。
- Git：本轮尚未提交、推送或远端合并。

## 实际改动与验证

- 复核远端后，PR #16 已更新至 `003083d1c7af8a731629f9d8b0e3dc7a77b61c56`，新增可复跑 HTTP 冒烟脚本，两条审查仍未解决。创建本地分支 `feat/p1-integration-delivery-20261112`，合入其候选代码，生成无冲突本地合并提交 `2713b7cfc334062809220959be2b4ee843f69189`。本地 main 仍保留 615d1dc，不改写历史。
- merge 首次因沙箱不能写 ORIG_HEAD.lock 失败，按工具要求获批后重试成功。所有 GUI/服务启动通过工具审批运行，只控制本轮登记 PID/端口。
- 用户通过本轮问题明确答复“授权本次两项后端修复”，例外范围仅为后端导入导出服务及对应回归测试，不推送、不远端合并。先独立复现并运行新增正式回归：29 项中 3 个断言失败（两类非法 match 500、部分缺帧误报完整）；修复后专项 29 项通过，新增清单 ID/多帧文件完整性回归也包含在完整 293 项通过结果中。后端细节和源文件哈希见最终汇总，本日志聚焦前端集成验收。
- 前端 `AdminOps.tsx` 恢复规则/作品 IR 快照两类选择，默认六类全部选中；明确作品快照“仅留档、不可导回”，导入说明列出可导回的五类，不暗示恢复 PNG 或公开状态。
- 新增 `frontend/tests/delivery-browser-check.cjs`：真实页面子树/裁剪、服务端冲突拒绝、兄弟节点与旧快照不变、实际裁剪 PNG/哈希、空白质量提示、六类默认导出/下载/计数、非法规则 UI 拒绝、快照不可导回、规则导回后真实命中。没有 mock 成功响应。
- 新增 `frontend/tests/delivery-catalog-check.cjs`：从本轮证据定位三件真实作品，登录后逐帧请求并核对 SHA256、在浏览器打开三页并确认服务器 PNG 加载；不保存会话或凭据。
- `p1b-controls-check.cjs` 增加 `CODEPOET_P1B_OUTPUT_DIR`，避免覆盖历史 fixture 证据。实际复跑 17 项控件检查；该套不等于 API 验收。
- 新增 `docs/前端P1验收与演示交付.md`，整理验收矩阵、四角色 8～10 分钟讲解排程、三件作品目录、启动/回归与备份恢复说明。更新前端启动/问题清单的最新状态并保留历史失败记录，更新日志索引。未修改共享契约或根任务看板的阶段勾选。

## 真实服务与验收结果

- 源码基线：本地集成 HEAD 2713b7c + 本轮未提交修复。开发服务前端 13160 / 后端 18160，实际源文件哈希记录于 [acceptance-summary.json](2026-11-12-p1-integration-delivery/acceptance-summary.json)。仅本机隔离环境。
- 基础 G0 库 `.tools/p1-delivery-20261112/data-g0/`，既有迁移 001～010 + 默认 seed；P1 库为同目录 `data-p1/`，既有迁移 + seed --skip-templates + seed_demo。完整素材有 8 概念/9 标签/12 知识/3 模板/8 规则；真实验收另产生测试记录，不把导入素材当真实作品。
- 沿用已安装 uv Python 3.12.14、项目 venv site-packages、本机已有 Pillow/Playwright/Chrome 和项目 Node 24.22.2；没有修改依赖或下载安装新工具。browser-automation/docs-generator 引用的 skills/tool-index.md 不存在，已核查实际模块/文件路径，不猜安装路径或增加工具。
- 运行辅助脚本位于忽略的 `.tools/p1-delivery-20261112/`：start-services、stop-services、run-check、run-python、backup-verify 和 check-evidence。种子 stdout 在内存捕获后丢弃凭据，仅报告非敏感摘要；测试凭据用环境注入并在结束后移除。

| 套件 | 开发模式 | next start 生产模式 | 结果 |
| --- | --- | --- | --- |
| 标准 G0 | 20 项，退出 0 | 20 项，退出 0 | g0Passed=true，非诊断模式 |
| 模板审核 | 12 项，退出 0 | 12 项，退出 0 | templateReviewPassed=true，退回修订与 stale revision 拒绝通过 |
| P1 已选主路径 | 13 项，退出 0 | 13 项，退出 0 | blockers/failures/pageErrors 为空 |
| 子树/质量/导入导出增补 | 9 项，退出 0 | 9 项，退出 0 | 全部通过 |
| fixture 层级树/时间轴 | 17 项，退出 0 | 未重复运行 | 仅本地控件/绘制验证 |
| 上传恢复 | 6 项，退出 0 | 未重复运行 | 真实 429、abort 帧请求后原任务重试、自然五分钟过期后重建保存 |

- 恢复测试任务创建 03:38:53Z、到期 03:43:53Z、实际检查 03:43:54.567Z；未调整系统时钟、服务端 TTL 或数据库时间，旧任务未假报保存成功，重建后 PNG 哈希一致。
- `p1-results.json` 继续保留该脚本自身 gaPassed/gbPassed=false（它只负责选定路径）；本轮综合模板/增补/控件等证据，最终汇总明确为 **本地集成版本的 GA/GB 主路径通过**。不将其当作远端 main 阶段勾选、公网部署或人工完整计时彩排。
- PR #16 入库 `scripts/smoke_integration.py` 独立执行真实 HTTP 冒烟 36/36 通过，含默认 seed→demo→demo 幂等。该脚本作品链用受控 fixture，不能代替上述浏览器真实保存。此次实际使用脚本默认专用 `.smoke/data`；后续本轮 helper 已改为预检查的新建 GUID 目录，避免重复运行清空已有冒烟目录。
- 脚本开发中修正两处断言假设：parameterValid 属于 generation.validationReport，非 qualityReport；检查导出框数量前应等页面加载。前两次失败未归为产品缺陷，最终增补脚本开发/生产模式均通过。仅删除本轮两张旧失败截图，最终证据引用文件全部保留，历史任务文件未覆盖。
- 已实际查看 390px 六类导出、子树裁剪截图；最终生产截图另经核验。最终 JSON 可解析，结果/截图引用存在，未包含种子密码；不保存 Cookie/CSRF/token、浏览器 storageState 或 trace。

## 构建、备份与恢复

- 停止本轮开发服务后，以 `API_PROXY_TARGET=http://127.0.0.1:18160` 执行 `scripts/npm.ps1 run typecheck`、`scripts/npm.ps1 run build`，均退出 0；Next 16.4.5 构建成功、15 个静态页面。生产服务使用 next start，而非 dev。生产四套验证在本轮最终业务代码上执行。
- 通过生产浏览器验证的[三件演示作品](2026-11-12-p1-integration-delivery/demo-catalog.json)：莲参数作品（published）、竹子树裁剪作品（private）、四秒叙事（private，0/2000/4000ms 三帧）。目录另包含真实退回/归档模板历史路径及四角色账号名，不含密码。
- 停止生产服务后，以 SQLite backup API 备份整个 P1 数据库，复制完整 artifacts，恢复到新目录 `.tools/p1-delivery-20261112/restored-p1/`，不覆盖原库。完整性 ok、外键错误 0、34 个 PNG 的源/备份/恢复副本及库内 SHA256 全部一致。
- 从恢复副本运行迁移（无待应用），不重跑 seed；启动 next start + 后端，浏览器重新登录并打开三件作品，全部帧哈希及 PNG 加载通过。结果 [backup-restore-results.json](2026-11-12-p1-integration-delivery/backup-restore-results.json)、[restored-demo-catalog.json](2026-11-12-p1-integration-delivery/restored-demo-catalog.json)。备份含运行敏感数据，只保存在 .tools 忽略目录，不进入证据或 Git。
- 最后停止本轮 4 个恢复服务进程，13160/18160/18161 无监听；未停止日常 3000/8000 服务，测试库、原 P1 库、备份及恢复副本保留。

## 运行命令与校验

- `powershell -NoProfile -ExecutionPolicy Bypass -File .tools/p1-delivery-20261112/start-services.ps1 -Dataset g0|p1`，开发模式；`-Production` 使用 next start；恢复模式额外 `-Restore`。
- `.../run-check.ps1 -Suite g0|templates|p1|delivery|controls|recovery`；生产 g0/templates/p1/delivery/catalog 额外 `-Production`。每次套件都检查原生退出码，生产证据单独存入 production/。
- `.../run-python.ps1 -Mode imports`：修复前 29 项 / 3 失败，修复后 29 项通过。`-Mode all`：293 项通过，112.420 秒。测试有现存 Starlette/httpx 弃用、Windows 临时 SQLite 文件占用/ResourceWarning，未发生断言失败；本轮未扩展修改测试基础设施。
- `.../run-python.ps1 -Mode smoke`：36/36 通过；`-Mode backup`：新目录完整备份/恢复及哈希检查通过。
- `node frontend/tests/demo-data-check.mjs`：通过，8 概念/9 标签/12 知识/3 模板/8 规则/5 fixture。
- 新增脚本及 p1b-controls 的 `node --check`、`git diff --check`：退出 0。`git diff --exit-code HEAD -- scripts shared`：退出 0，本轮没有直接修改脚本/共享契约（现有 PR 集成内容单独由 Git 记录）。
- `node .tools/p1-delivery-20261112/check-evidence.cjs`：最终通过，8 套开发/生产主路径结果及控件/恢复/备份/作品目录一致，记录实际源文件 SHA256。首次增强日志断言按 UTF-8 读了 PowerShell UTF-16 日志，修正 BOM 解码后通过；未因此重跑已通过的业务验收。
- 最终逐一检查本轮相关五份 Markdown 的 65 个本地链接，目标全部存在；实际查看生产 390px 六类导出与空白质量提示截图。最后服务端口检查使用空结果断言，不把“没有匹配监听”当产品失败。

## 结束记录

- 本轮完成：两项获授权后端修复、前端六类导出适配、本地 PR 集成、G0 与 GA/GB 主路径真实浏览器验收、生产构建及生产模式回归、演示材料/三件作品目录、实际备份恢复闭环。
- 未执行：提交/推送业务改动、发布 GitHub 评论或解决线程、远端 PR 合并、公网/正式部署、人工计时全场彩排或录屏。8～10 分钟为已编排的讲解计划，不编造已现场演示；未新增 P1-C/P2 或合入 PR #13。
- 剩余交付动作：审核并提交本轮差异、按用户每次明确授权推送/走 PR；在最终部署环境进行 HTTPS/Cookie/代理配置和人工现场演示验收。远端 main 上的历史问题状态不因本地修复自动改变。
- Git：当前分支 feat/p1-integration-delivery-20261112 / 2713b7c，仅新增本地集成合并提交。业务/测试/文档/本日志及证据均未提交、未暂存、未推送；原 frontend/AGENTS.md 修改及四个原有未追踪日志保持原状。

## 继续：用户授权同步、提交与推送

- 状态：进行中。
- 授权：用户明确要求更新 Git 状态、commit 并 push，注意同步最新 main；允许删除确认无用的文件。目标为当前工作分支的普通推送，不强推或直接绕过 main 保护。
- 初始状态：当前仍为 feat/p1-integration-delivery-20261112 / 2713b7c，业务改动与证据未提交。已观察到远端 main 跟踪引用有后续提交，另有其他本地冲突处理 worktree；不删除或改写该 worktree。
- 计划：fetch 与差异核对 → 整理提交范围和必要证据、保留有价值历史日志 → 安全整合最新 main → 按整合结果复验 → 本地提交、普通推送并回读远端与工作区。
- 原有 frontend/AGENTS.md 注释规范及审查/初始化日志将逐项审阅；不将数据库、备份、凭据、.tools 或本机 skill 纳入提交。

### 同步与提交前最终结果

- `git fetch origin` 确认最新 main 为 `15d79ff`，已合入 PR #13/#16；与上轮本地基线有 9 个新提交。同步产生本地合并提交 `f8542ef`，无冲突。其他 worktree 保持原状。
- 原本地两份后端改动先复制并校验 SHA256，保存在本轮忽略目录，再移除与上游重复的修改并整合 main。主线已包含原两项修复，不重复提交整套实现；仅保留清单产物 ID/PNG 类型/文件核验增强及一项接口回归。该增量先在新主线执行 31 项专项时出现 1 个预期失败，增强后完整 311 项通过。
- 新迁移 011 在基础 G0 与 P1 隔离库均实际应用成功。最终完整后端测试 311 项（196.874 秒）、真实 HTTP 冒烟 40/40、类型检查与生产构建全部通过。浏览器生产模式 G0 20、模板 12、P1 13、增补 9 项通过，三件作品浏览器打开及 PNG 哈希通过。[同步后汇总](2026-11-12-p1-integration-delivery/main-sync/acceptance-summary.json)独立存档，不覆盖此前证据。
- 最终 P1 首轮资料页刷新断言抢在 user effect 填入输入框前读取空值；新增服务端 nickname/bio 持久化断言与真实页面值等待后，13 项复验通过。未改业务页面；失败截图作为本轮过时临时文件删除，不替换历史验收。
- 文档增加最新 main 状态：PR #13 后端能力已随主线继承，未开发对应新前端入口；历史“未合入 PR”文字保留时间背景。此前 293/36 结果仍属于同步前的历史测试。
- 提交范围：本轮有效业务增量、前端验收脚本、文档、日志索引与必要脱敏证据；同时保留并纳入现有 frontend/AGENTS.md 注释规范及四份有交接价值的审查/初始化/同步日志。不删除有用历史证据，不提交 .tools、数据库、备份、凭据或本机 skill。
- 已完成阶段：同步、验证和提交范围整理。尚未声称业务提交/推送成功，实际结果由后续 Git 命令与最终回复核实。
- 提交前最后一次 fetch：origin/main 仍为 `15d79ff71adedcb88773c5d42bd6d75c46564a1d`，祖先检查通过，当前分支已包含最新 main。暂存范围为 92 个文件、约 8.5 MB，其中 54 张必要截图、21 份 JSON；凭据扫描、JSON/PNG 格式、文档本地链接及 diff --check 均通过。保留引用中的历史证据；清理了本轮过时失败截图，未删除有用工作日志。历史 PR15 日志的本机 .tools 链接已改成明确的本地路径，避免推送后出现伪仓库下载入口。
