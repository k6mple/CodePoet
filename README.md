# 代码诗

廉洁主题生成式可视化创作平台，当前按 `core-demo` 基础演示版组织开发。

## 当前状态

已补充 FastAPI / SQLite 启动入口、Next.js / React 启动页、环境配置、API 转发和健康检查。其他业务模块仍为占位。P0-01 的 IR 校验、共享 fixture 和 Canvas 绘制尚未实现，因此不将 P0-01 标为完成。

技术方案：Next.js 16 App Router + React 19 + TypeScript；FastAPI + Pydantic + 同步 SQLAlchemy + SQLite。2026-11-06 按开发者熟悉度迁移到 Next.js，UI 组件库和 Pillow 在对应功能任务中添加。

## 本地启动

安装 uv。后端限定 Python 3.12，由 uv 管理独立环境。前端使用 Node.js 24 LTS，最低版本为 24.22.2，项目基准记录在 `.node-version`。

首次安装，在仓库根目录运行：

```powershell
uv sync --project backend --locked
.\scripts\setup-node.ps1
.\scripts\npm.ps1 ci
```

默认配置可直接使用。需要自定义时，将根目录 `.env.example` 复制为 `.env`，将 `frontend/.env.example` 复制为 `frontend/.env`，再修改路径或代理目标；已有 `.env` 时不要覆盖。

分别在两个 PowerShell 终端运行：

```powershell
# 终端一：后端
.\scripts\dev-backend.ps1
```

```powershell
# 终端二：前端
.\scripts\dev-frontend.ps1
```

访问 <http://127.0.0.1:3000>，启动页会实际请求后端；成功时显示“后端与数据库已连接”。健康接口为 <http://127.0.0.1:8000/api/v1/health>，已实现接口的交互文档为 <http://127.0.0.1:8000/api/docs>。按 Ctrl+C 停止对应服务。

若终端找不到 uv，可用 Windows 默认安装位置 `& "$env:USERPROFILE/.local/bin/uv.exe"` 代替 `uv`；后端启动脚本已包含此回退。不要使用当前机器 PATH 中缺少 pip 的 MSYS2 Python 建立本项目环境。

Windows 的 `setup-node.ps1` 从 Node 官方下载便携版，校验 SHA256 和 OpenJS 签名后安装到被 Git 忽略的 `.tools/`。`npm.ps1` 和前端启动脚本优先使用该运行时，只临时修改当前脚本的 PATH。系统 Node 仍可保留原版本。

脚本无法执行或使用其他系统时，先自行安装 Node.js 24.22.2 或更新的 24.x 补丁；再分别进入 `backend` 执行 `uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`，进入 `frontend` 执行 `npm ci` / `npm run dev`。前端通过 `engine-strict` 拒绝不匹配的 Node 版本。

检查与构建：

```powershell
# 在 backend 目录
uv run --locked python -m unittest discover -s tests -v
# 在仓库根目录，使用项目 Node
.\scripts\npm.ps1 run build
```

构建产物位于 `frontend/.next/`。构建后在仓库根目录运行 `./scripts/npm.ps1 run start` 启动生产模式，地址同为 `http://127.0.0.1:3000`，启动前需停止同端口的开发服务。Next.js 服务通过 rewrites 将 `/api/*` 转发到 FastAPI；修改 `API_PROXY_TARGET` 后需重新构建并重启。此项目使用 Node 服务部署，不使用静态导出。

依赖分别由 `backend/uv.lock` 和 `frontend/package-lock.json` 锁定，应一并提交。环境配置、虚拟环境、依赖目录及运行数据已被 Git 忽略。

前端依赖固定具体版本，`@types/node` 对齐 24.x。后端依赖限制在当前已验证的系列：FastAPI 0.162、Pydantic 2.14、pydantic-settings 2.16、SQLAlchemy 2.1、Uvicorn 0.54、HTTPX 0.28。常规安装始终使用锁文件；跨系列升级应单独验证后更新范围与锁文件。

本机访问官方 PyPI 时 TLS 握手失败，后端配置使用清华 PyPI HTTPS 镜像并保留证书校验。更换下载源时需同步更新 `backend/pyproject.toml` 和锁文件。

## 目录结构

```text
CodePoet/
├── backend/                    # A：后端业务与规则
│   ├── app/
│   │   ├── main.py             # FastAPI 应用入口与健康接口
│   │   ├── settings.py         # 环境配置与数据路径
│   │   ├── db.py               # 同步 SQLAlchemy / SQLite
│   │   ├── api/                # auth、knowledge、templates、creation、works、admin
│   │   ├── models/             # users、knowledge、templates、creations、operations
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── services/           # 权限、审核、上传、快照、导入导出、审计
│   │   └── engine/             # validate、instantiate、infer、subgraph、sample
│   ├── migrations/            # 少量数据库迁移
│   └── tests/                 # 后端关键行为测试
├── frontend/                   # B：Next.js 页面与 Canvas
│   ├── public/                # 无需构建处理的公开静态资源
│   ├── src/
│   │   ├── api/               # Cookie / CSRF / 错误处理与业务 API
│   │   ├── assets/            # 构建处理的样式及图片
│   │   ├── components/        # 通用表单、列表、JSON 编辑等组件
│   │   ├── hooks/             # 可复用 React Hooks
│   │   ├── app/               # App Router 页面、布局与路由
│   │   ├── renderers/canvas/  # Canvas 图元、变换、采样与 PNG 导出
│   │   ├── types/             # 前端类型
│   │   └── utils/             # 通用工具
│   └── tests/                 # 采样、矩阵及关键交互检查
├── shared/
│   ├── schemas/               # A：共享数据约束
│   └── demo/                  # B：演示数据与联调 fixture
│       ├── fixtures/
│       ├── knowledge/
│       ├── templates/
│       └── rules/
├── scripts/                   # 建库、幂等种子、检查及备份脚本
├── docs/                      # 已有需求、任务书、接口契约
└── data/                      # 本地运行数据，已忽略，不提交 Git
    └── artifacts/             # PNG、帧与 manifest，禁止直接静态公开
```

空目录通过 `.gitkeep` 保留；`data/artifacts` 是本地运行目录，不纳入版本控制，由后端启动时自动创建。SQLite 数据库默认位于 `data/codepoet.sqlite3`，目前没有业务表；后续通过编号迁移建表。

## 开发依据与边界

按以下顺序阅读，旧完整需求中的选做能力不应覆盖当前范围：

1. [交付计划与任务看板](docs/交付计划与任务看板.md)
2. [双人开发总纲与接口约定](docs/双人开发总纲与接口约定.md)
3. [开发者 A 任务书](docs/开发者A-平台后端与Python引擎.md) / [开发者 B 任务书](docs/开发者B-创作前端与浏览器渲染.md)
4. [当前接口文档](docs/接口文档.md) / [核心 OpenAPI](docs/openapi-core.json)

A 主责 `backend/`、`shared/schemas/` 及后端脚本，B 主责 `frontend/`、`shared/demo/` 及前端脚本。根规范和接口由 A 主改。`docs/openapi-core.json` 是当前接口契约来源，共享目录不另行复制一份契约。

主线只实现 Canvas 与 `browser_upload`，不预建 Turtle、CSS、WebGL、DSL、沙盒或无头浏览器服务。下一步按 P0-01 补充 IR 校验、可绘制的共享 fixture 和 Canvas 渲染。
