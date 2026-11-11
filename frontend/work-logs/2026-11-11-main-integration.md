# 工作日志：main G0 与 P1 联调

日期：2026-11-11（Asia/Shanghai）
任务 ID：G0 / GA / GB 主线联调
状态：本轮可执行范围已完成；G0/上传恢复通过，GA/GB 被后端缺口阻塞

## 开始记录

- 目标：用户授权开工；先复验最新 main 的标准 G0，再推进可独立执行的个人空间/恢复/收藏、管理及语义/动画/复现真实联调，修复前端问题并整理证据。
- 允许范围：frontend 代码、验收脚本、脱敏证据、前端交接文档及日志；运行既有后端迁移/种子在本轮独立数据目录，后端及共享契约只读。不提交或推送。
- 初始 Git：main / 0440453，ahead origin/main 2；已有 README 索引修改及 11/10、11/11 核对日志未追踪，均保留。
- 计划：新日志 → 新隔离库及服务 → 标准 G0 → GA/GB 可执行路径真实验收 → 前端问题定位修复与回归 → 构建、证据/文档整理、停止服务。
- 已知阻塞：模板版本 PATCH 缺失，子树能力开关关闭；不绕过能力声明，不将部分路径通过宣称为 GA/GB 全过。

## 实际改动与决定

- 后端直接运行 main 0440453 的既有源码，PYTHONDONTWRITEBYTECODE=1。端口为前端 13140、后端 18140；数据为 `.tools/main-integration-20261111/data/`（G0/模板）、`data-p1/`（P1/恢复），未动日常开发数据。
- 应用全部既有迁移 001～010。项目 venv 启动器历史被 Windows Application Control 拦截，本轮沿用可执行的原始 uv Python 3.12.14，PYTHONPATH 加入现有 site-packages。最新功能新增 Pillow，原环境缺失；从当前 uv.lock 提取 Windows cp312 wheel 及 SHA256，经 uv --require-hashes 安装 Pillow 13.0.0 到本轮 `.tools/.../deps/`。没有修改后端依赖声明或系统策略。
- 浏览器采用本机已有 Playwright 与 Chrome。browser-automation 的 tool-index.md 仍缺失，实际校对了已有模块路径；没有另行安装浏览器工具。运行 helper、PID 和服务日志均在忽略的 .tools 目录；种子 stdout 只在内存接收，密码仅注入环境变量。
- 新增 `frontend/tests/p1-browser-check.cjs`，真实 UI 操作配合实际 API 断言：会话/历史/恢复、作品新版本/审核、角色审批/资料、知识/本体/标签、语义/动画/规则修改/复现、组合检索、导入导出/审计。已知后端缺口记 blockers 并继续独立路径，退出 2；意外失败单列并退出 1，gaPassed/gbPassed 保持 false。
- 新增 `frontend/tests/render-recovery-browser-check.cjs`，核对真实任务额度 429、取消后页面重试、断网帧上传的原任务重试、真实五分钟过期和页面新建任务恢复。未修改服务端时钟或数据库状态；网络失败只 abort 请求，不伪造成功响应。
- 修复 AdminOps 默认导出必败：原默认六类包含后端明确拒绝的 rules/workSnapshots；调整为实际支持的四类，并明确未启用的导出范围。没有使用空规则数组冒充备份成功。
- 更新前端启动/问题文档和日志索引。新增 BE-FAVORITES-READ-001、BE-WORK-PUBLIC-POINTER-001、BE-EXPORT-TYPES-001、BE-DEMO-SEED-001、BE-SUBGRAPH-CAPABILITY-001；保留模板 PATCH 阻塞，不改后端。
- 初始化另有真实问题：默认 seed.py 后执行 seed_demo.py 重复 tpl-lotus，前半知识/标签已 commit、规则为 0，重跑又 skipped。单独新库复现并保存 [初始化结果](2026-11-11-main-integration/p1/demo-seed-order-results.json)。前一复现脚本因 PowerShell 标准输入中文编码影响“跳过”断言未完成保存，改成 Unicode escape 并换新库复现成功，不涉及业务修复。
- P1 改用现有 seed.py --skip-templates 再 seed_demo.py，新库成功导入 9 标签/8 概念/12 知识/3 模板/8 规则，不使用 --force，不清除失败库。
- 验收脚本开发阶段修正了选择器、演示模板参数名、创建接口 201、非法 apply 409、creator 已有模板权限等断言假设，按真实页面/契约/代码校对；这些脚本适配不计为产品修复。最终结果文件以最后一次运行生成，不用初期断言错误当后端缺陷。

## 已观察结果

- main 标准 G0：20 项、退出 0、g0Passed=true、diagnostic=false；真实登录/生成/调参、401/422、乱序保护、390px、上传/保存/重开/PNG 哈希、第二账号私有资源 404 通过。
- main 模板：11 项执行、脚本原生退出 2、templateReviewPassed=false。版本 PATCH 405，requestId=req-a2c26730ea1b4b7d867b70427675c050；其他审核路径已执行，未把原版本重新送审当成修订成功。
- P1 最终扩展检查完成 **13 项**，failures/pageErrors 均为空，status=blocked，脚本原生退出 2：收藏 PUT 成功后读 false/空页、匿名 public displayVersionId 错指 v2（匿名 v2 仍 404）、rules/workSnapshots 导出 422。额外概念 UI 新建及服务端防环通过；四秒模板播放/暂停、[0,2000,4000] 三帧保存与刷新重开通过。结果 [p1-results.json](2026-11-11-main-integration/p1/p1-results.json)，不能视为 GA/GB 全通过。
- 已真实验证：两次保存进度后恢复会保留原两条并追加前/后快照至 4 条；旧状态回创作台、跨账号 404。审批 creator→developer 后刷新获得知识权限，昵称/简介重读一致。知识直接/继承标签、互斥拒绝、父树防环、正常修改持久化。
- 莲/竹文本产生不同 IR 和规则依据；无匹配真实 NO_SEMANTIC_MATCH 并保留旧 generation。语义叙事三帧真实 PNG 的魔数与 SHA256 通过；修改所命中规则的实际色板后，新语义 IR 改变，历史来源未变，旧作品复现由服务端返回像素一致。390px 无横向溢出。
- 合法导入写入 2 个标签；坏父引用被预校验拒绝，绕过 UI apply 返回 409 INVALID_STATE，实际数量未变。四类导出计数与数组一致，审计可找到知识修改/审核/导入/规则修改/复现。
- 上传恢复 **6 项通过，退出 0**：真实第三个任务 429 EXECUTION_BUSY，取消占额任务后页面保存成功；abort 帧请求后重试原任务成功。任务 job-d87130b86b0f47f2a9de833fa9c19300 创建于 05:19:27Z、到期 05:24:27Z，实际检查为 05:24:28.563Z，成为 timed_out / UPLOAD_EXPIRED；页面不报成功，新建任务后保存，实际 PNG 哈希一致。未缩短 TTL 或修改数据库。结果 [render-recovery-results.json](2026-11-11-main-integration/recovery/render-recovery-results.json)。
- 最终新增缺口请求编号：收藏读 req-833bc76ff0cb46049945204a05ecb500 / 列表 req-e08eee3c836f401880990cceabc570ac；公开指针 req-2f5c9cb82a2e4440b0853f05a8cb14fb；导出不支持 req-510bdbc9ce14441a801efe9b7712747f。完整资源 ID 及方法/状态见对应 JSON。

## 验证与结束记录

- 运行命令：`powershell -NoProfile -ExecutionPolicy Bypass -File .tools/main-integration-20261111/start-services.ps1`（G0/模板），停止后以 `-DataName data-p1 -Demo` 启动 P1；`run-check.ps1 -Suite g0|templates|p1|recovery` 分别执行实际脚本，注入环境/Playwright 路径，退出时移除密码环境变量。helpers 为本地忽略文件，通用运行要求已写入前端启动文档。
- typecheck 退出 0；新增脚本 node --check 通过，最终脚本均实际执行；`git diff --check` 退出 0；`git diff --exit-code HEAD -- backend scripts shared` 退出 0。
- `stop-services.ps1` 核对 PID/端口后结束本轮 5 个服务进程，13140/18140 均无监听。随后 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\npm.ps1 run build` 退出 0，Next 16.4.5 生产构建成功、15 个静态页面生成。
- JSON 全部重新解析并核对最终通过/阻塞状态，检查未含种子密码字面量；不保存 Cookie/CSRF/token、storageState 或 trace。实际查看最终复现与 390px 截图。删除仅本轮早期脚本适配失败产生的过时截图，保留最终结果引用的 14 张截图和 5 份 JSON（共 19 文件、约 2.5 MB），不覆盖历史任务证据。
- 未执行：完整后端测试、生产服务浏览器回归、全角色/所有边界输入穷举、公开他人作品的复现专项、真实子树页面验收与完整部署/彩排。浏览器结果基于开发服务，生产构建通过不等于部署验证。
- 尚未完成：后端缺口修复后的复验、GA/GB 全项、稳定演示库最终角色/拒审/归档/3 件作品编排与完整彩排、生产部署验收。未勾选看板。
- 本轮未提交、未推送，HEAD 仍 0440453 / ahead origin/main 2；原有未提交修改/日志保留。实际改动为 AdminOps、两个新增前端验收脚本、两份交接/问题文档、本日志/索引及证据，后端/共享文件未改。

## 2026-11-11 继续：用户授权提交并推送

- 状态：提交检查与整理已完成；后续 Git 结果以最终回复和远端历史为准。
- 授权：用户明确要求“提交并推送”，将本轮反馈及证据同步给后端；目标为当前 main 的 origin/main，普通推送。
- 初始 Git：main / 0440453，ahead origin/main 2；已有 4 个已跟踪文件修改、两个新增验收脚本、本轮日志/证据，以及本对话两次同步检查日志未追踪。均为本对话相关成果。
- 提交范围：AdminOps 修复、验收脚本、19 个脱敏证据文件、两份前端交接/问题文档、日志和索引；一并纳入索引已引用的 11/10、11/11 同步核对日志，避免发布断链。不包含 .tools、数据库、环境文件、本机 skill 或后端源码变更。
- 计划：fetch 核对远端 → 审阅差异/脱敏/链接 → 重新执行必要检查 → 本地提交 → 普通推送 → 核实远端提交与工作区。
- 已知未完成项：后端问题保持未解决，GA/GB 不因提交/推送改为通过。
- 提交前核验：fetch 成功，origin/main 仍 8bbc10c，无新增远端提交；typecheck、生产 build（15 个静态页面）、两个脚本 node --check 均退出 0。5 份 JSON 可解析，最终 P1 无意外失败，证据未含种子密码；6 份相关 Markdown 的本地链接均存在；diff --check 与后端/共享目录无修改检查通过。
- 提交准备已完成：仅纳入上述前端成果和已引用日志。实际创建提交及远端推送将在本记录写入后执行；提交号和远端核对结果见本次最终回复及 Git 历史。本节不预先声称推送成功。
- 实际主体提交：df69465（fix(frontend): 修正默认导出并交接主线联调问题与证据），28 文件；提交后 show --check 通过，工作区干净。
- `git push origin main` 被 GitHub GH006 拒绝：main 为受保护分支，必须经 PR。远端 main 未因此更新；没有强推或绕过保护。已切换新分支 `fix/frontend-main-integration-feedback` 保留相同成果，后续普通推送该分支并以 main 为 base 创建审查 PR；本补记随交接分支提交。远端最终结果由随后命令及最终回复确认。
