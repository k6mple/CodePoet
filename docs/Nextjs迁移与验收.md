# Next.js 迁移与验收

日期：2026-11-06。根据用户对 Next.js 的熟悉度，将前端从 Vue/Vite 迁移为 Next.js App Router。接口契约与后端职责沿用现有文档。

## 当前版本与结构

- Next.js 16.4.5、React / React DOM 19.3.1、TypeScript 6.0.3。
- 项目 Node.js 24.22.2、npm 11.19.1，保留独立运行时及启动脚本。
- `src/app/layout.tsx`：中文根布局、全局样式与页面元数据。
- `src/app/page.tsx`：首页；`src/components/StartupStatus.tsx`：客户端健康检查与重试。
- `src/hooks/`：后续 React Hooks；`src/renderers/canvas/`：保留 Canvas 职责目录。
- `next.config.ts`：通过 rewrites 将 `/api/*` 转发到 FastAPI，开发和生产均生效。
- 已移除 Vue、Vue Router、Vite、vue-tsc 与对应入口、配置、旧构建产物；依赖锁文件已更新。

## 已运行验收

| 检查 | 结果 |
| --- | --- |
| `./scripts/npm.ps1 run typecheck` | Next 路由类型生成与 TypeScript 检查通过 |
| `./scripts/npm.ps1 run build` | Next.js 生产构建通过，首页、404 与图标生成成功 |
| `./scripts/npm.ps1 run start` | 生产服务在 3000 端口启动 |
| 生产服务 `/api/v1/health` | 200，返回 `status=ok`、`apiVersion=1.1.0` |
| 浏览器打开首页 | 客户端挂载后显示“后端与数据库已连接 · API 1.1.0” |
| 关闭后端后点击重试 | 显示 HTTP 500 错误和可重试提示，无假成功 |
| 恢复后端再次重试 | 恢复“后端与数据库已连接” |
| `./scripts/dev-frontend.ps1` | 开发服务在 3000 端口启动，浏览器页面正常，开发 API 转发返回 200 |

开发命令为 `./scripts/dev-frontend.ps1`，前端访问 `http://127.0.0.1:3000`。生产模式先构建，再运行 `./scripts/npm.ps1 run start`，不要与开发服务同时占用 3000 端口。

`API_PROXY_TARGET` 保持服务端环境变量，修改后开发服务需重启，生产服务需重新构建并重启。部署需要 Node.js 服务；后端仍由 FastAPI 承担认证、权限、数据库和图片持久化。

Canvas、IR 校验、登录、保存等业务未在此次迁移中实现，P0-01 仍待完成剩余验收。首次 Vue/Vite 配置的历史记录见《启动配置与验收》，当前启动步骤以根 README 为准。
