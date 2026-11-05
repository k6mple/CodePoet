# 前端（开发者 B）

使用 Vue 3 + TypeScript + Vite + Vue Router。当前提供开发连接检查页，UI 组件库在业务页面开发时再引入。

Node 版本要求为 `>=24.22.2 <25`，类型定义对齐 24.x。Windows 在仓库根目录运行 `./scripts/setup-node.ps1` 安装项目运行时，随后用 `./scripts/npm.ps1 ci` 安装锁定依赖、`./scripts/npm.ps1 run dev` 启动、`./scripts/npm.ps1 run build` 执行类型检查与构建、`./scripts/npm.ps1 run preview` 预览构建结果。

如果全局 Node 已满足版本要求，也可以在本目录直接执行对应 npm 命令。`.npmrc` 开启 `engine-strict`，避免误用系统旧版 Node。

开发地址固定为 `http://127.0.0.1:5173`，端口被占用时直接报错。`/api` 由 Vite 代理至 `http://127.0.0.1:8000`，浏览器始终使用相对路径。需要改目标时复制 `.env.example` 为 `.env` 后调整 `API_PROXY_TARGET` 并重启 Vite。该变量仅供 Vite 服务端读取。

启动页使用真实 `GET /api/v1/health` 显示连接结果；关闭后端再检查应显示失败。开发代理为后续 Cookie/CSRF 联调提供同源入口，当前尚未实现认证。

`src/api/` 集中处理 Cookie、CSRF、分页、统一错误和 requestId；`src/router/` 负责路由及权限导航；`src/views/` 按任务逐步添加登录、模板库、创作台、知识库、个人空间、作品详情和管理页面。

`src/renderers/canvas/` 只放 Canvas 图元、父子变换、时间采样、裁剪与 PNG 导出。后端负责权威参数校验、语义规则、审核和图片持久化；前端不得以本地假数据代替真实保存或审核结果。

`components/` 放复用组件，`composables/` 放交互逻辑，`types/` 放类型，`utils/` 放工具；测试聚焦矩阵、采样、异步请求及导出流程。联调 fixture 和演示数据在 `../shared/demo/`，当前接口以 `../docs/openapi-core.json` 为准。

P0-01 的 Canvas fixture 绘制仍待实现，其他目录保留原开发分工。
