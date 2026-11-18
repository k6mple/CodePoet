# 代码诗

廉洁主题生成式可视化创作平台，当前按 `core-demo` 基础演示版组织开发。

## 当前状态

已完成核心主线与提分模块并合入 `main`（详细验收证据与边界见 [交付计划与任务看板](docs/交付计划与任务看板.md)）：

- **P0 / P1 基础主线**：四角色（`creator` / `developer` / `expert` / `admin`）注册登录、Session + CSRF 与细粒度 RBAC；领域知识/本体/标签管理（含防环与互斥校验）；模板与作品版本化送审、退回修订、发布与下线；个人空间、创作会话快照回滚与收藏；多维组合检索；六类 JSON 批量导入导出与全链路审计日志；规则驱动语义生成、子树局部替换与视口裁剪、4 秒时序叙事时间轴、历史作品一键复现与服务端像素比对。
- **S0 / R1 / R2 多渲染后端**：统一前后端能力契约与共用采样/矩阵语义，支持 `canvas2d`（`codepoet-canvas-1.0.0`）、`css`（`codepoet-css-1.0.0`，真实 SVG/WAAPI 动画与导出）和 `webgl`（`codepoet-webgl-1.0.0`，真实 GPU 着色器/三角化/帧缓冲导出与 context loss 处理）三套浏览器渲染器的生成、三时刻采样上传、保存、重开与原后端复现（WebGL 复现因跨上下文 MSAA 存在 ≤0.005% 边缘像素差异，MAE 为 0%）。默认仅启用 `canvas2d`，演示/验收通过 `CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl` 显式开启。
- **Q1 作品质量量化治理**：版本化质量评估（`codepoet-quality-1.0.0`），涵盖参数合法性、200×150 逻辑掩码布局冲突检测、规则色板色彩语义合规与前景掩码代理信噪比（SNR），提供可定位到节点与时刻的优化建议及 12 标注样本（含 6 坏例）改进闭环。
- **S1 受限 DSL 沙箱与执行策略**：纯 JSON AST 白名单解释器（支持 `circle` / `polygon` / `polyline` / `set_param`，兼容 IR 基底与模板基底参数重实例化），独立子进程 + Windows Job Object / 看门狗资源保护（墙钟、CPU、内存、步数、绘图调用上限）与管理员策略配置（`/sandbox`、`/admin/policies`，支持乐观锁 409 冲突恢复）。
- **K1 外部知识图谱联动生成**：对接 Wikidata 真实实体与一跳关系（莲 `Q16528`、竹 `Q670887`、梅 `Q157763`），将外部属性与关系映射至站内知识与叙事题记，持久化自包含 `externalGraph` 来源快照，支持断网/关闭来源后的历史作品离线复现。默认关闭，通过 `CODEPOET_KNOWLEDGE_GRAPH_SOURCES` 与 `CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE` 启用。
- **范围边界**：`R3`（Turtle 服务端渲染）因本机缺少 Ghostscript 无法完成 EPS→PNG 栅格化而保持未启用；`X1`（LLM 辅助）与 `X2`（WebGL 高级后处理）按门槛规则裁剪不启用。

技术方案：Next.js 16 App Router + React 19 + TypeScript；FastAPI + Pydantic + 同步 SQLAlchemy + SQLite + Pillow。

## 本地启动

安装 uv。后端限定 Python 3.12，由 uv 管理独立环境。前端使用 Node.js 24 LTS，最低版本为 24.22.2，项目基准记录在 `.node-version`。

首次安装与初始化数据库，在仓库根目录运行：

```powershell
uv sync --project backend --locked
.\scripts\setup-node.ps1
.\scripts\npm.ps1 ci

# 建表迁移 + 写入四角色演示账号与演示素材（12 知识实体 / 8 映射规则 / 3 模板）
uv run --project backend python scripts/migrate.py
uv run --project backend python scripts/seed.py --reset-passwords
uv run --project backend python scripts/seed_demo.py
```

默认配置可直接使用（默认仅开启 `canvas2d`）。需要开启 `css` / `webgl` 或 `K1` 外部知识图谱时，将根目录 `.env.example` 复制为 `.env`，按注释启用 `CODEPOET_ENABLED_BACKENDS`、`CODEPOET_KNOWLEDGE_GRAPH_SOURCES` 与 `CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE`；已有 `.env` 时不要覆盖。

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
│   │   ├── main.py             # FastAPI 应用入口、生命周期与健康接口
│   │   ├── settings.py         # 环境配置与数据路径
│   │   ├── db.py               # 同步 SQLAlchemy / SQLite
│   │   ├── api/                # auth、knowledge、templates、creation、works、admin、sandbox、knowledge_graph
│   │   ├── models/             # users、knowledge、templates、creations、operations、sandbox
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── services/           # 权限、审核、上传、快照、导入导出、审计、quality、sandbox、policy、knowledge_graph
│   │   └── engine/             # capabilities、validate、instantiate、semantic、sample、dsl、dsl_worker
│   ├── migrations/            # 001～013 数据库编号迁移
│   └── tests/                 # 后端全量行为与安全边界测试（484 项）
├── frontend/                   # B：Next.js 页面与多渲染器
│   ├── public/                # 无需构建处理的公开静态资源
│   ├── src/
│   │   ├── api/               # Cookie / CSRF / 错误处理、渲染上传与沙箱 API
│   │   ├── assets/            # 构建处理的样式及图片
│   │   ├── components/        # 创作台、IR/时间轴、质量报告、沙箱与策略、后台管理等组件
│   │   ├── hooks/             # 可复用 React Hooks
│   │   ├── app/               # App Router 页面、布局与路由（含 /sandbox、/admin/policies 等）
│   │   ├── renderers/         # 统一 Renderer 协议与 canvas / css / webgl 三套实现
│   │   ├── types/             # 前端类型定义
│   │   └── utils/             # 通用工具
│   └── tests/                 # 渲染器、G0/P1、CSS/WebGL、Q1、K1、S1-B 真实浏览器验收脚本
├── shared/
│   ├── schemas/               # A：共享数据约束说明
│   └── demo/                  # B：演示数据、标注质量样本、外部图谱映射与联调 fixture
│       ├── fixtures/
│       ├── knowledge/
│       ├── quality/
│       ├── templates/
│       └── rules/
├── scripts/                   # 迁移（migrate.py）、种子（seed.py / seed_demo.py）、探针与冒烟脚本
├── docs/                      # 需求、任务看板、执行细则与接口契约
└── data/                      # 本地运行数据，已忽略，不提交 Git
    └── artifacts/             # PNG、帧与 manifest，禁止直接静态公开
```

空目录通过 `.gitkeep` 保留；`data/artifacts` 是本地运行目录，不纳入版本控制，由后端启动时自动创建。SQLite 数据库默认位于 `data/codepoet.sqlite3`，通过 `scripts/migrate.py` 按编号迁移建表。

## 开发依据与边界

按以下顺序阅读：

1. [交付计划与任务看板](docs/交付计划与任务看板.md)
2. [双人开发总纲与接口约定](docs/双人开发总纲与接口约定.md)
3. [提分阶段执行细则](docs/提分阶段执行细则.md)
4. [开发者 A 任务书](docs/开发者A-平台后端与Python引擎.md) / [开发者 B 任务书](docs/开发者B-创作前端与浏览器渲染.md)
5. [当前接口文档](docs/接口文档.md) / [提分阶段接口增量约定](docs/提分阶段接口增量约定.md) / [完整 OpenAPI](docs/openapi.json)

A 主责 `backend/`、`shared/schemas/` 及后端脚本，B 主责 `frontend/`、`shared/demo/` 及前端脚本。生产环境默认仅开启 `canvas2d`；演示或验收 `css`、`webgl`、`K1` 外部知识图谱时，按 `.env.example` 与看板第 5 节显式配置环境变量。
