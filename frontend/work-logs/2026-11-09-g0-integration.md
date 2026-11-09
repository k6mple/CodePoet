# 工作日志：G0 真实保存与隔离联调

日期：2026-11-09（GMT+8）
任务：G0 集成验收与前端缺陷修复
状态：部分完成（前端检查与问题交接完成；G0 被后端契约差异阻断）

## 开始记录

- 目标：在当前集成分支验证真实登录、模板调参、PNG 上传保存、刷新重开及两账号私有资源隔离，并检查错误恢复。
- 用户边界：不修改任何后端源码、后端脚本或接口契约；后端问题记录复现步骤和实际响应，交由后端负责人修复。
- 允许修改范围：`frontend/**`、前端验收文档及必要前端测试素材；已有后端程序只运行与只读排查。使用 `.tools/` 下独立运行目录和隔离数据库，不碰现有业务数据。不提交或推送。
- 初始 Git 状态：`feat/integration-backend` / `a2f450b`；已有上轮未提交的开工条件核对日志与日志索引改动，保留。
- 计划：检查运行环境/端口 → 记录隔离数据位置并迁移/seed → 启动专用前后端 → 浏览器真实成功和失败流程 → 前端修复/回归 → 形成脱敏证据与后端问题单。
- 已知限制：语义/子树/审核/会话/收藏等不属于 G0 成功判据，接口未齐备不做假成功。日志和证据不保存密码、Cookie、CSRF 或认证请求体。

## 执行记录

- 读取现有启动、迁移、种子、鉴权、上传服务与前端保存逻辑；后端文件仅只读定位。
- 使用现有 `scripts/migrate.py` 与 `scripts/seed.py`，通过环境变量 `CODEPOET_DATA_DIR=C:\Users\hp\Desktop\CodePoet\.tools\g0-20261109-01\data` 隔离数据，应用 001～005 迁移，创建 4 个演示账号和 1 个种子模板。seed 输出过滤密码行，未记录认证材料。
- 用 `Start-Process -WindowStyle Hidden` 启动专用服务：后端 uvicorn 18100；前端 Next dev 13100，`API_PROXY_TARGET=http://127.0.0.1:18100`。浏览器通过前端同源代理调用真实后端。
- 使用真实浏览器注册两名临时创作者，密码仅在检查进程内随机生成，不保存。新增 `frontend/tests/g0-browser-check.cjs`，将本轮核心检查整理为可重复执行的脚本，不新增项目依赖。
- 新增 `docs/前端联调问题清单.md`，记录 BE-G0-001；更新启动验收文档和日志索引。本轮未修改任何前端或后端业务源码，没有通过放宽任务状态校验来绕过阻断。
- 浏览器截图、请求状态/请求编号与脱敏结果保存在同名子目录。标准结果和辅助诊断结果分开保存，均明确 `g0Passed=false`。

## 验证与后端问题

### 已实际验证

- 真实注册/登录成功，默认模板生成 201，画布为 800×600；合法调参生成新记录，Canvas 像素实际改变。
- 单字段越界时保留旧 Canvas、提示未应用且禁用保存；服务端跨字段校验真实返回 422 / VALIDATION_ERROR。
- 通过真实 logout 使服务端会话失效后，页面保存收到 401 并打开登录；同账号重新登录后保留原参数和 generation，可继续操作。
- 对第一条真实 generation 响应延迟 900ms，后发 34 的响应先于 30 返回；页面仍显示半径 34 对应的 generation，旧响应未覆盖新画面。未伪造生成结果。
- 390px 页面无横向溢出；无未捕获 pageerror。预期的访客/过期会话 401、非法参数 422 等 HTTP 错误不作为页面崩溃。
- 对创建任务请求注入一次网络断开，页面保留 generation、显示失败、不出现保存成功；重试能调用真实创建接口。

### BE-G0-001：标准保存被后端初始任务状态阻断

- 实际 `POST /render-jobs` 返回 202，但对象为 `queued / awaiting_frames`，`expiresAt=null`；文档 7.1 节要求 `running / awaiting_frames` 和五分钟有效期。GET 原任务仍为 queued。
- 前端依照契约保留任务 ID 并提示“任务不可上传：queued”，没有发 frames 或假称保存成功。
- 最初实际请求编号 `req-b89b5d37cca54e0bb0b567ab4994d5de`，任务 `job-2081fe1a465f4727874b29690f2b65a1`；随后脚本多次复现，最近结果详见子目录标准/诊断 JSON。
- 只读定位后端 `create_render_job` 写入 queued、expires_at=None；没有修改后端、接口文档或前端任务状态校验。
- 本问题已交接到 [前端联调问题清单](../../docs/前端联调问题清单.md)。到期清理行为本轮没有等待五分钟实测。

### 独立诊断后的后续结果（不计为 G0 全程通过）

- 使用当前真实 Canvas `toBlob`，经真实鉴权 frames API 上传 PNG，不修改数据库或服务端状态。
- 手工浏览器诊断首次 PNG 为 28776 字节，正确 PNG 魔数；上传 200，任务变为 succeeded/completed。之后页面重试保存返回真实私有 work/version。
- 新标签与刷新后可读取真实服务器 PNG 和 IR；自动检查另验证下载 PNG 的 SHA256 与 manifest 一致。
- 第二账号读取 work、work-version、generation、render-job、artifact 均实际返回 404；页面没有显示私有 PNG。
- 自动脚本诊断模式完成 21 项检查；标准模式完成上传前的 9 项检查并停止。两者均为 `status=blocked`、`g0Passed=false`，最终运行退出码均为 2。
- 未验证：无需辅助上传的全页面保存、帧上传中断后的前端原任务重试、真实任务到期/限额、动画三帧保存及 G0 后的 P1 功能。没有勾选任务看板。

### 实际命令与检查结果

隔离环境准备：本次运行的 Python 为 `backend/.venv/Scripts/python.exe`，对现有迁移和 seed 传入上述绝对数据目录，后端源码没有改动。测试服务启动方式为现有 uvicorn 模块及 Next CLI。

```powershell
$env:NODE_PATH = 'C:\Users\hp\AppData\Local\npm-cache\_npx\e41f203b7505f1fb\node_modules'
$env:CODEPOET_G0_BASE_URL = 'http://127.0.0.1:13100'
& .\.tools\node-v24.22.2-win-x64\node.exe frontend/tests/g0-browser-check.cjs --diagnostic-upload
& .\.tools\node-v24.22.2-win-x64\node.exe frontend/tests/g0-browser-check.cjs
```

- 两个模式分别写入 `g0-diagnostic-results.json` / `g0-check-results.json`；在 PowerShell 包装调用末尾显式 `exit $LASTEXITCODE` 后，正确向外传递阻塞退出码 2。首轮未显式传递时包装层报告 1，但 JSON 已正确标记 blocked；以最终标准/诊断结果为准。
- `node --check frontend/tests/g0-browser-check.cjs`：通过（使用项目 Node）。
- `.\scripts\npm.ps1 run typecheck`：通过。
- `.\scripts\npm.ps1 run build`：通过，Next.js 16.4.5，15 个静态页面生成完成。
- `git diff --exit-code -- backend scripts shared/schemas`：通过，后端及其脚本/契约目录无改动。
- `git diff --check`：通过。
- 已关闭浏览器测试上下文；停止核对过的专用服务根进程 12428 / 42924 及其 Python / Next 子进程，未停止其他服务。隔离库和运行日志保留在被 Git 忽略的 `.tools/`，便于后端排查。

## 结束记录

- 状态：本轮前端侧检查、可重复脚本、证据和后端问题交接已完成；G0 尚未通过。
- 下一步：后端负责人修复 BE-G0-001 后，按启动验收文档恢复隔离环境，先运行不带诊断参数的标准检查；通过后补帧上传失败重试与时效用例，再更新验收结论。
- Git 提交/推送情况：未提交、未推送。
