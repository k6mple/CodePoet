# 前端（开发者 B）

使用 Next.js 16 App Router + React 19 + TypeScript。2026-11-06 根据开发者熟悉度从 Vue/Vite 迁移；当前提供开发连接检查页，UI 组件库在业务页面开发时再引入。

Node 版本要求为 `>=24.22.2 <25`，类型定义对齐 24.x。Windows 在仓库根目录运行 `./scripts/setup-node.ps1` 安装项目运行时，随后用 `./scripts/npm.ps1 ci` 安装锁定依赖、`./scripts/npm.ps1 run dev` 启动、`./scripts/npm.ps1 run build` 执行类型检查与构建、`./scripts/npm.ps1 run start` 启动构建后的生产服务。可单独运行 `./scripts/npm.ps1 run typecheck` 检查路由及 TypeScript 类型。

如果全局 Node 已满足版本要求，也可以在本目录直接执行对应 npm 命令。`.npmrc` 开启 `engine-strict`，避免误用系统旧版 Node。

开发和生产地址固定为 `http://127.0.0.1:3000`，端口被占用时直接报错。`/api/*` 由 Next.js rewrites 转发至 `http://127.0.0.1:8000/api/*`，浏览器始终使用相对路径。需要改目标时复制 `.env.example` 为 `.env` 后调整 `API_PROXY_TARGET`：开发模式重启，生产模式重新构建并重启。该变量仅供服务端读取，不加 `NEXT_PUBLIC_` 前缀。

启动页使用真实 `GET /api/v1/health` 显示连接结果；关闭后端再检查应显示失败。开发代理为后续 Cookie/CSRF 联调提供同源入口，当前尚未实现认证。

`src/api/` 集中处理 HTTP 调用；后续补齐 Cookie、CSRF、分页、统一错误和 requestId。`src/app/` 使用文件路由，按任务逐步添加登录、模板库、创作台、知识库、个人空间、作品详情和管理页面。根布局和首页为服务端组件，健康检查交互放在标记 `'use client'` 的 `StartupStatus.tsx` 中；后续 Canvas 及浏览器动画也使用客户端组件。

`src/renderers/canvas/` 只放 Canvas 图元、父子变换、时间采样、裁剪与 PNG 导出。后端负责权威参数校验、语义规则、审核和图片持久化；前端不得以本地假数据代替真实保存或审核结果。

`components/` 放复用组件，`hooks/` 放 React 交互逻辑，`types/` 放类型，`utils/` 放工具；测试聚焦矩阵、采样、异步请求及导出流程。联调 fixture 和演示数据在 `../shared/demo/`，当前接口以 `../docs/openapi-core.json` 为准。FastAPI 仍负责全部权威业务逻辑，Next.js 不另建一套认证或数据库服务。

P0-01 的 Canvas fixture 绘制仍待实现，其他目录保留原开发分工。
