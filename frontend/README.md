# 前端（开发者 B / P0）

2026-11-07：已新增 P0 创作台、Cookie/CSRF 登录与会话恢复、Canvas fixture、参数控件、PNG 上传保存流程和作品详情路由。**完整 P0 联调尚未通过**：当前后端未注册模板/生成/上传/作品接口，本次后端运行时也启动失败。详见 [实际验收记录](../shared/demo/P0_ACCEPTANCE.md)。

当前页面：`/` 创作台、`/fixture` 本地 Canvas 验证、`/works` 本人作品列表、`/works/versions/[id]` 服务端 PNG/IR 详情。预览与导出共用 `renderScene`，保存时固定 generation/hash，锁定参数，用独立 Canvas toBlob，全部帧成功后再创建作品。失败保留本页参数与任务 ID，可重试原任务或新建任务。未保存现场只在内存中，刷新会丢失；只有真实保存成功的作品可通过 URL 刷新重开。

Canvas 浏览器检查：仓库根目录运行 `.\.tools\node-v24.22.2-win-x64\node.exe frontend/tests/canvas-browser-checks.mjs data/canvas-check.js`，将生成文件的完整函数作为浏览器 DevTools evaluate_script 的 function 参数执行。测试使用实际 renderScene/exportFrame 和真实浏览器 Canvas，不模拟 HTTP 接口。

使用 Next.js 16 App Router + React 19 + TypeScript。2026-11-06 根据开发者熟悉度从 Vue/Vite 迁移；本次复用已有组件和原生控件，未添加组件库依赖。

Node 版本要求为 `>=24.22.2 <25`，类型定义对齐 24.x。Windows 在仓库根目录运行 `./scripts/setup-node.ps1` 安装项目运行时，随后用 `./scripts/npm.ps1 ci` 安装锁定依赖、`./scripts/npm.ps1 run dev` 启动、`./scripts/npm.ps1 run build` 执行类型检查与构建、`./scripts/npm.ps1 run start` 启动构建后的生产服务。可单独运行 `./scripts/npm.ps1 run typecheck` 检查路由及 TypeScript 类型。

如果全局 Node 已满足版本要求，也可以在本目录直接执行对应 npm 命令。`.npmrc` 开启 `engine-strict`，避免误用系统旧版 Node。

开发和生产地址固定为 `http://127.0.0.1:3000`，端口被占用时直接报错。`/api/*` 由 Next.js rewrites 转发至 `http://127.0.0.1:8000/api/*`，浏览器始终使用相对路径。需要改目标时复制 `.env.example` 为 `.env` 后调整 `API_PROXY_TARGET`：开发模式重启，生产模式重新构建并重启。该变量仅供服务端读取，不加 `NEXT_PUBLIC_` 前缀。

启动页使用真实 `GET /api/v1/health` 显示连接结果；关闭后端再检查应显示失败。API 通过同源代理处理 Cookie/CSRF，认证 UI 的真实联调仍待后端启动后验证。

`src/api/` 集中处理 Cookie、CSRF、超时、统一错误、requestId 和受保护 PNG。`src/app/` 使用文件路由，根布局和首页是服务端组件，登录/健康检查/Canvas/参数是客户端组件。知识库、完整个人空间和管理页面属于后续任务。

`src/renderers/canvas/` 只放 Canvas 图元、父子变换、时间采样、裁剪与 PNG 导出。后端负责权威参数校验、语义规则、审核和图片持久化；前端不得以本地假数据代替真实保存或审核结果。

`components/` 放复用组件，`hooks/` 放 React 交互逻辑，`types/` 放类型，`utils/` 放工具；测试聚焦矩阵、采样、异步请求及导出流程。联调 fixture 和演示数据在 `../shared/demo/`，当前接口以 `../docs/openapi-core.json` 为准。FastAPI 仍负责全部权威业务逻辑，Next.js 不另建一套认证或数据库服务。

Canvas fixture 已完成浏览器实际验证；完整 P0 的 API 成功链路仍被后端阻塞。保存现场和未完成任务只在当前页内存中保留；服务端作品保存请求超时时，请先查看“我的作品”，避免重复创建（当前契约无创建幂等键）。
