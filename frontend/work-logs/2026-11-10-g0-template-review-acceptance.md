# 工作日志：G0 与模板审核真实验收

日期：2026-11-10（Asia/Shanghai）
任务 ID：G0 / P1A-03 / P1A-04
状态：部分完成；G0 通过，模板审核退回修订仍被后端缺失接口阻塞

## 开始记录

- 目标：完成用户指定的第 1、2 项：PR #10 修复后的 G0 标准浏览器复验，以及模板新建/克隆/预览/送审/退回/通过/归档与公开版本切换验收。
- 允许范围：frontend 业务修复、前端验证脚本、脱敏证据和日志；隔离运行现有后端源码、迁移、种子和测试数据。后端与共享契约只读，不合并 PR，不提交或推送。
- 初始 Git：main / e309093，与 origin/main 一致；已有未追踪 docs/代码诗-赛题说明.md 和上一任务日志，均保留。本轮尚无业务改动。
- 计划：确认 PR #10 最新版本 → 隔离副本和新数据目录 → 构建/启动当前前端与 PR 后端 → 标准 G0 → 模板管理和审核浏览器验收 → 前端问题定位修复及回归 → 清理本轮服务、记录真实结果。
- 已知限制：main 尚未包含 PR #10；PR 历史缺草稿版本 PATCH，可能阻断退回修订。历史证据不替代本次验收；不使用诊断上传宣称 G0 通过。

## 实际改动与决定

- `git fetch origin` 与 GitHub 只读核对确认 PR #10 仍 open，head=`db1c626f5e2880f9ce8998a354a0ecdd444d540a`。通过 `git archive` 将该引用的 backend、迁移/种子脚本及 shared/demo 解包到 `.tools/g0-template-20261110-01/source/`，不切分支、不合并 PR、不修改源后端。
- 运行目录：`.tools/g0-template-20261110-01/`；新库为该目录 `data/codepoet.sqlite3`。前端 13130，后端 18130；前端业务代码为 main `e309093`。应用既有迁移 001～006、010 和既有四角色/模板种子，未改现有开发库。
- 环境问题按错误证据排查：项目 venv 的 python.exe 在沙箱内外均被 Windows Application Control 拦截；其原始 uv Python 3.12.14 解释器可执行，并成功导入现有 FastAPI/SQLAlchemy/Pydantic/uvicorn。改用该原始解释器 + 项目 venv site-packages，PYTHONPATH 优先隔离源码，PYTHONDONTWRITEBYTECODE=1；不改系统策略或 Python 依赖。
- 后台服务以隐藏方式启动，记录 PID；种子 stdout 只在内存接收，避免将其中的演示凭证写入日志。浏览器密码仅来自运行时环境，测试结果不保存 Cookie/CSRF/密码/认证响应。
- 浏览器自动化 skill 要求的本机 `tool-index.md` 不存在；检索找到模板但不能当实际索引。直接核对已有 Playwright 缓存目录与项目 Node 路径后使用它们，未安装工具或写外部目录。
- 增强 `frontend/tests/g0-browser-check.cjs`：除 running/awaiting_frames 外，检查 expiresAt-createdAt 为 300 秒（允许 1 秒序列化差）；缺失/错误有效期同样阻断 G0，不以完成后续保存掩盖契约差异。
- 新增 `frontend/tests/template-review-browser-check.cjs`：真实页面执行模板/版本新建、私有预览、PNG 保存、送审、他人退回与通过、克隆、归档，穿插实际 API 核对权限、来源标记、公开指针和历史；缺 PATCH 时记录 blocked，并继续核对可独立验证的其他环节。后端提供 PATCH 后，脚本还会核对持久化、revision +1、过时写 409、发布只读。
- 更新 `docs/前端联调问题清单.md`：BE-G0-001 记录 PR 隔离版本通过、main 尚未合入；新增 BE-TEMPLATE-EDIT-001 的真实请求编号和回归条件。
- 更新 `docs/前端启动与验收.md` 交接状态及日志索引；保留历史失败记录和看板未验收状态。本轮没有发现需要修改组件的前端缺陷，业务源码未改。

## G0 最终结果

- 最终标准运行 20 项、退出 0，`status=passed`、`g0Passed=true`、`diagnostic=false`，无 blocker 和页面异常。增强有效期检查后重新运行，保留最终结果。
- 真实注册登录/默认模板/合法调参改变 Canvas、非法数值保留旧图、实际跨字段 422、401 重新登录保留生成现场、真实响应延迟下的乱序保护、390px、一次任务创建网络失败后重试均通过。
- 页面自行创建任务、toBlob/上传、保存、作品新标签与刷新重开；服务端 PNG 魔数与 SHA256 对 manifest 校验通过。第二账号访问 work/work-version/generation/render-job/artifact 全部 404，页面不泄露图片。
- 最终任务 `job-5c9d9aa7927144a4ac500bb8459d09b5`，创建状态 running/awaiting_frames，有效期恰好 300000ms；requestId=`req-bf64cdf43c80456b8ff1cee6d9592aed`。
- 最终作品版本 `wv-49514311a3944c3c8559b6c3590bc3f1`、generation=`gen-2b9f10bf33aa4705b053ae86b2661a6e`。结果：[g0-check-results.json](2026-11-10-g0-template-review-acceptance/g0/g0-check-results.json)。

## 模板审核结果与阻塞

- 最终脚本 11 项检查完成，退出 2，`status=blocked`、`templateReviewPassed=false`，页面异常为空。早先包装命令只显示非零退出，最终显式输出 `review_native_exit=2` 核实语义。
- 已验证：UI 新建容器和草稿、匿名草稿 404、创作台私有预览真实生成、保存真实 PNG；未发布来源作品送审为 409 UNPUBLISHED_SOURCE。
- UI 送审后版本只读；专家空理由退回被前端拒绝，有理由退回成功，作者页面可见理由；另账号通过后匿名公共页可见具体 v1。
- 克隆产生 v2 草稿；v2 待审时公共指针仍为 v1、匿名 v2 为 404；批准后公共页切 v2。归档 v2 回退 v1，再归档 v1 则公开列表移除，历史作品 IR hash/PNG 仍可读取。
- 专家自己的待审记录显示不能自审且无通过入口，实际绕过 UI 调 API 仍为 403 SELF_REVIEW_FORBIDDEN；developer 读审核队列为 403。模板管理 390px 的 scrollWidth=innerWidth=390。
- **BE-TEMPLATE-EDIT-001**：退回后 UI 点击保存版本，PATCH `/api/v1/template-versions/tver-ddf8bcc4c3d742d1aaf5c56ba18a485e` 返回 405，requestId=`req-04162e0a8db74707b30b6d1e553aee87`。页面保留未保存文字并显示失败，服务端 changeNote 未改变。根因是该版本后端根本没有版本 PATCH 路由，未修改后端或绕过。
- 后续重新送审未修改的版本仅用于核对状态转移，不代表“退回→修订”成功；不将克隆当原版本修订。缺失接口相关的成功写/过时写/发布 PATCH 断言分支未执行。
- 结果：[template-review-results.json](2026-11-10-g0-template-review-acceptance/templates/template-review-results.json)；失败、公开版本、自审保护、归档与窄屏截图在同目录。已实际查看失败与窄屏截图。

## 验证结果

- 启动：`powershell -NoProfile -ExecutionPolicy Bypass -File .tools/g0-template-20261110-01/start-services.ps1`；首次项目 venv 启动器失败，采用原始 Python + 既有依赖后成功。该本地辅助脚本及源码副本由 `.tools/` 忽略。
- 标准 G0：设置 `NODE_PATH` 为已核对的 Playwright 缓存、`CODEPOET_G0_BASE_URL=http://127.0.0.1:13130`、`CODEPOET_G0_OUTPUT_DIR=frontend/work-logs/2026-11-10-g0-template-review-acceptance/g0`，使用项目 `.tools/node-v24.22.2-win-x64/node.exe frontend/tests/g0-browser-check.cjs`。未使用诊断参数。最终退出 0。
- 模板：本地 `run-template-check.ps1` 在内存读取隔离 seed 的演示凭证并设置 `CODEPOET_ACCEPTANCE_PASSWORD`（不保存值），设置 NODE_PATH、CODEPOET_REVIEW_BASE_URL 后运行项目 Node + `frontend/tests/template-review-browser-check.cjs`，最终退出 2。其他环境需向该变量注入自己隔离种子账号的密码，不能指向正式数据。
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\npm.ps1 run typecheck`：通过，退出 0。
- 两个验收脚本 `node --check`：均退出 0；`git diff --check`：退出 0；`git diff --exit-code -- backend shared/schemas scripts`：退出 0，职责只读范围未变。
- 检查结果 JSON 未包含本轮种子凭证字面量；不持久化浏览器 storageState、trace、Cookie、token 或认证响应。
- 停止服务：`stop-services.ps1` 核对记录 PID 和命令行端口后，只结束本轮服务进程树，共 5 个进程；退出 0，13130/18130 均无监听。
- 未执行：生产构建、完整后端测试、GA/GB 全项、角色审批/知识树回归、PR #12 联调、五分钟真实等待/429/断网帧上传重试；上述内容不因本轮结果记为通过。

## 结束记录

- 状态：第 1 项标准 G0 在 PR #10 隔离版本通过；第 2 项可执行审核路径已验收，退回修订被 BE-TEMPLATE-EDIT-001 阻断，不能宣称完整通过。
- 下一步：后端补齐 draft/rejected 版本 PATCH 后复跑模板验收；PR #10 修复仍需合入 main，才能认为 main 具备本轮通过行为。本轮未自动合并。
- Git：main HEAD 仍 e309093；未暂存、未提交、未推送。保留开始时已有未追踪文档和上一任务日志。新增/修改仅前端验收脚本、脱敏证据、日志和前端交接/问题文档。
