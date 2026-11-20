# 代码诗（CodePoet）

廉洁主题生成式可视化创作平台（2026-11 收口定版；运行档位沿用 `core-demo` 能力边界）。

系统围绕「知识语义解析 → 视觉元素推理 → 统一绘图中间表示（IR） → 多后端异构渲染调度 → 作品质量治理与可复现档案」构建全链路数字创作工作台，支持**模板参数化创作**、**廉洁语义 / 外部图谱驱动生成**与**受限 DSL 低代码沙箱创作**三种范式。

## 项目最终交付状态

全部已立项主线与选定提分模块已完成开发、集成并合入 `main`（详细验收基线、测试数据与边界说明见 [交付计划与任务看板](docs/交付计划与任务看板.md)）：

- **P0 / P1 基础主线**：四角色（`creator` / `developer` / `expert` / `admin`）注册登录、Session + CSRF 与细粒度 RBAC；廉洁文化实体/本体/标签管理（含防环与互斥校验）；模板与作品版本化送审、退回修订、发布与下线；个人空间、创作会话快照回滚与收藏；多维组合检索；六类 JSON 批量导入导出与全链路审计日志；规则驱动语义生成、局部子树替换与视口裁剪、4 秒时序叙事时间轴、历史作品一键复现与服务端像素比对。
- **新中式画案工坊界面（UI 重设计）**：主创作导航与治理工作台双区收纳、一体化工坊工具栏、画案与典藏保存坞紧贴动线、模板参数「随机灵感」/「诗意主题快选」/「常用图谱实体快选」/「DSL 预设片段」零门槛引导、模板库实时画卷渲染，以及渐进式美学评估与溯源面板。
- **S0 / R1 / R2 异构多后端渲染**：统一前后端能力契约与共用采样/矩阵语义，支持 `canvas2d`（`codepoet-canvas-1.0.0`）、`css`（`codepoet-css-1.0.0`，真实 SVG/WAAPI 动画与导出）和 `webgl`（`codepoet-webgl-1.0.0`，真实 GPU 着色器/三角化/帧缓冲导出与 context loss 处理）三套浏览器渲染器的生成、三时刻采样上传、保存、重开与原后端复现。默认配置仅启用 `canvas2d`，演示或验收全能力时通过 `CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl` 显式开启。
- **Q1 作品质量量化治理**：版本化质量评估（`codepoet-quality-1.0.0`），覆盖参数合法性（`parameterValidity`）、200×150 逻辑掩码布局冲突检测（`layoutConflict`）、规则色板色彩语义合规（`colorSemantic`）与前景掩码代理信噪比（`signalNoise`），支持在时间轴上定位问题节点与时刻并带入编辑器改进对照（12 标注样本含 6 坏例闭环；指标 `score` 保持未校准 `null`）。
- **S1 受限 DSL 沙箱与执行策略**：纯 JSON AST 白名单解释器（支持 `circle` / `polygon` / `polyline` / `set_param`，兼容 IR 基底与模板基底参数重实例化），独立子进程 + Windows Job Object / 看门狗资源保护（墙钟、CPU、内存、步数、绘图调用上限）与管理员策略配置（`/sandbox`、`/admin/policies`，支持硬上限校验与 409 乐观锁恢复）。
- **K1 外部知识图谱联动生成**：对接 Wikidata 真实实体与一跳关系（莲 `Q16528`、竹 `Q670887`、梅 `Q157763`），将外部属性与关系映射至站内知识与叙事题记，持久化自包含 `externalGraph` 来源快照，支持断网或关闭外部来源后的历史作品离线复现。默认关闭，通过 `CODEPOET_KNOWLEDGE_GRAPH_SOURCES` 与 `CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE` 启用。
- **X1 智能辅助可视计算能力**：对接 OpenAI 兼容模型接口（默认 DeepSeek `deepseek-chat`），在创作台提供自然语言驱动的 **IR 片段改造（`ir_fragment`）** 与 **模板参数空间生成（`parameter_schema`）** 提案；模型只输出结构化 IR / 参数空间 JSON（不输出原始绘图代码），经独立子进程隔离校验（含 `validate_ir` 全图限额与采样）并在检出冲突/越界时自动反馈模型修复（最多 1 次，`attempts <= 2`），校验通过后由用户显式幂等应用并记录 `provenance.assistant` 溯源与依赖快照哈希。默认关闭，配置 `CODEPOET_LLM_API_KEY` 后自动启用；未配置时 `features.llmAssistant = false`、UI 禁用入口且接口返回 `503 ASSISTANT_UNAVAILABLE`。
- **已知范围边界（诚实声明）**：
  - `R3`（Turtle 服务端渲染）因目标环境缺少 Ghostscript 无法完成 EPS→PNG 栅格化，保持延期未启用；
  - `X2`（WebGL 高级后处理）按项目门槛规则裁剪未启用；
  - `WebGL` 复现因跨上下文 MSAA 抗锯齿存在 ≤0.005% 边缘像素差异（MAE 为 `0%`）；
  - `Q1` 质量分数为项目内部版本化代理指标，未做官方校准。

技术栈：Next.js 16 App Router + React 19 + TypeScript；FastAPI + Pydantic + 同步 SQLAlchemy + SQLite + Pillow。

## 本地启动与生产部署

安装 uv。后端限定 Python 3.12，由 uv 管理独立环境。前端使用 Node.js 24 LTS，最低版本为 24.22.2，项目基准记录在 `.node-version`。

### 1. 首次安装与数据库初始化

在仓库根目录执行：

```powershell
uv sync --project backend --locked
.\scripts\setup-node.ps1
.\scripts\npm.ps1 ci

# 建表迁移（001～014）+ 写入四角色演示账号与演示素材（知识/规则/模板）
uv run --project backend python scripts/migrate.py
uv run --project backend python scripts/seed.py --reset-passwords
uv run --project backend python scripts/seed_demo.py
```

默认配置可直接使用（默认仅开启 `canvas2d`）。需要开启 `css` / `webgl`、`K1` 外部知识图谱与 `X1` 智能辅助时，将根目录 [.env.example](.env.example) 复制为 `.env` 并取消对应项注释（`CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl`、`CODEPOET_KNOWLEDGE_GRAPH_SOURCES=wikidata`、`CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE=shared/demo/knowledge-graph-mappings.json`，以及 `CODEPOET_LLM_BASE_URL`、`CODEPOET_LLM_API_KEY`、`CODEPOET_LLM_MODEL`）；已有 `.env` 时不要覆盖，且严禁将真实密钥提交到 Git。

默认演示账号为 `creator_demo`、`developer_demo`、`expert_demo`、`admin_demo`（初始密码定义见 [scripts/seed.py](scripts/seed.py)）。

### 2. 开发模式与生产模式启动

**开发模式**（两个 PowerShell 终端，仓库根目录）：

```powershell
# 终端一：后端（默认监听 127.0.0.1:8000）
.\scripts\dev-backend.ps1
```

```powershell
# 终端二：前端（默认监听 127.0.0.1:3000）
.\scripts\dev-frontend.ps1
```

**生产模式**（构建后通过 `next start` 提供服务，推荐用于完整演示与录屏）：

```powershell
# 1. 构建前端生产产物（如修改后端地址，需重新设置 API_PROXY_TARGET 后重建）
$env:API_PROXY_TARGET = "http://127.0.0.1:8000"
.\scripts\npm.ps1 run build

# 2. 终端一：启动后端（加载根目录 .env）
uv run --project backend uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. 终端二：启动前端生产服务
.\scripts\npm.ps1 run start
```

访问 <http://127.0.0.1:3000>，启动页会实际请求后端健康接口（<http://127.0.0.1:8000/api/v1/health>）；后端交互式 API 文档为 <http://127.0.0.1:8000/api/docs>。按 Ctrl+C 停止对应服务。

> 若终端找不到 uv，可用 Windows 默认安装位置 `& "$env:USERPROFILE/.local/bin/uv.exe"` 代替 `uv`；后端启动脚本已包含此回退。不要使用当前机器 PATH 中缺少 pip 的 MSYS2 Python 建立本项目环境。
> Windows 的 `setup-node.ps1` 从 Node 官方下载便携版，校验 SHA256 和 OpenJS 签名后安装到被 Git 忽略的 `.tools/`。`npm.ps1` 和前端启动脚本优先使用该运行时，只临时修改当前脚本的 PATH。
> 若在非 Windows 环境部署，请自行安装 Node.js `>=24.22.2 <25`，再分别进入 `backend` 运行 `uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000`，进入 `frontend` 运行 `npm ci` / `npm run build` / `npm run start`。前端通过 `engine-strict` 拒绝不匹配的 Node 版本。

## 检查、验证与备份恢复

### 常用自动化检查命令

```powershell
# 1. 后端全量单元测试（在 backend 目录，当前 496 项）
uv run --locked python -m unittest discover -s tests -q

# 2. 沙箱进程冒烟与反向守卫验证（仓库根目录）
uv run --project backend python scripts/smoke_sandbox.py
uv run --project backend python scripts/verify_sandbox_guards.py

# 3. 真实服务 HTTP 联调冒烟（仓库根目录，使用独立临时目录）
uv run --project backend python scripts/smoke_integration.py --data-dir .smoke/int-check --port 18230

# 4. 前端类型检查与生产构建（仓库根目录）
.\scripts\npm.ps1 run typecheck
.\scripts\npm.ps1 run build
```

真实浏览器生产 UI 验收脚本位于 `frontend/tests/`（涵盖 G0、模板审核、P1、交付增补、恢复、Canvas/CSS/WebGL、Q1、S1-B 与 K1），运行方式与隔离目录规范详见 [前端P1验收与演示交付](docs/前端P1验收与演示交付.md)。

### 备份与异目录恢复原则

JSON 导出仅涵盖结构化业务数据，**不包含渲染 PNG 产物**。完整数据备份与恢复须遵循：
1. 停止前后端服务，避免备份期间写入；
2. 使用 SQLite Online Backup API 备份 `codepoet.sqlite3` 并完整复制 `artifacts/` 目录；
3. 对副本执行 `PRAGMA integrity_check`、`PRAGMA foreign_key_check` 并逐条核对产物 PNG 文件 SHA256；
4. 将 `CODEPOET_DATA_DIR` 指向恢复目录，执行 `scripts/migrate.py` 后重启前后端验证历史作品重开与像素复现。

## 依赖与构建说明

- 依赖分别由 `backend/uv.lock` 和 `frontend/package-lock.json` 锁定。环境配置（`.env`）、虚拟环境、依赖目录、本地工作日志及运行/备份数据（`data/`、`backups/`）均由 `.gitignore` 忽略。
- 前端依赖固定具体版本，`@types/node` 对齐 24.x；Next.js 服务通过 rewrites 将 `/api/*` 转发到 FastAPI，使用 Node 服务部署，不使用静态导出。
- 后端依赖限制在已验证系列：FastAPI 0.162、Pydantic 2.14、pydantic-settings 2.16、SQLAlchemy 2.1、Uvicorn 0.54、HTTPX 0.28。本机配置使用清华 PyPI HTTPS 镜像并保留证书校验。

## 目录结构

```text
CodePoet/
├── backend/                    # 后端服务、数据模型、规则/DSL/质量引擎与测试
│   ├── app/
│   │   ├── main.py             # FastAPI 应用入口、生命周期与健康接口
│   │   ├── settings.py         # 环境配置与数据路径（读取根 .env）
│   │   ├── db.py               # 同步 SQLAlchemy / SQLite
│   │   ├── api/                # auth、knowledge、templates、creation、works、admin、sandbox、knowledge_graph、assistant、engine
│   │   ├── models/             # users、knowledge、templates、creations、operations、sandbox、assistant
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── services/           # 权限、审核、上传、快照、导入导出、审计、quality、sandbox、policy、knowledge_graph、assistant
│   │   └── engine/             # capabilities、validate、instantiate、semantic、sample、dsl、dsl_worker、llm_client、assistant_worker
│   ├── migrations/            # 001～014 数据库编号迁移
│   └── tests/                 # 后端全量行为与安全边界测试（496 项）
├── frontend/                   # Next.js 16 页面、新中式工坊界面与多后端渲染器
│   ├── public/                # 公开静态资源
│   ├── src/
│   │   ├── api/               # Cookie / CSRF / 错误处理、渲染上传与沙箱 API
│   │   ├── assets/            # 构建处理的样式与图片
│   │   ├── components/        # 创作台、IR/时间轴、质量报告、沙箱与策略、后台管理等组件
│   │   ├── hooks/             # 可复用 React Hooks
│   │   ├── app/               # App Router 页面与路由（含 /sandbox、/admin/policies 等）
│   │   ├── renderers/         # 统一 Renderer 协议与 canvas / css / webgl 三套实现
│   │   ├── types/             # 前端类型定义
│   │   └── utils/             # 通用工具
│   ├── work-logs/             # 随仓库共享的跨端交接与验收证据索引
│   └── tests/                 # 渲染器、G0/P1、CSS/WebGL、Q1、K1、S1-B、X1 真实浏览器验收脚本
├── shared/
│   ├── schemas/               # 共享数据约束说明
│   └── demo/                  # 演示数据、标注质量样本、外部图谱映射与联调 fixture
│       ├── fixtures/
│       ├── knowledge/
│       ├── quality/
│       ├── templates/
│       └── rules/
├── scripts/                   # 迁移（migrate.py）、种子（seed.py / seed_demo.py）、探针与冒烟脚本
├── docs/                      # 需求、任务看板、执行细则与接口契约
├── data/                      # 本地运行数据（已忽略，不提交 Git）
│   └── artifacts/             # PNG、帧与 manifest，禁止直接静态公开
└── backups/                   # 本地停服备份与恢复目录（已忽略，不提交 Git）
```

## 文档索引

1. [交付计划与任务看板](docs/交付计划与任务看板.md)（项目进度、验收证据与最终边界的单一事实来源）
2. [双人开发总纲与接口约定](docs/双人开发总纲与接口约定.md) / [提分阶段执行细则](docs/提分阶段执行细则.md)
3. [开发者 A 任务书](docs/开发者A-平台后端与Python引擎.md) / [开发者 B 任务书](docs/开发者B-创作前端与浏览器渲染.md)
4. [当前接口文档](docs/接口文档.md) / [提分阶段接口增量约定](docs/提分阶段接口增量约定.md) / [完整 OpenAPI](docs/openapi.json)
5. [前端P1验收与演示交付](docs/前端P1验收与演示交付.md) / [共享交接索引](frontend/work-logs/README.md)
