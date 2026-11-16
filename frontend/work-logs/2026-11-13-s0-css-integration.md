# 交接：S0 多入口与 CSS 候选真实联调

日期：2026-11-13（Asia/Shanghai）

任务：S0-A1、S0-B2、R1-B1/B2、S0-A2候选联调。
基线：0dc1e54；工作分支 `feat/s0-b2-css-renderer-20261113`。本记录随实现提交；未合并或改写 PR #18/#20。后端修改已获用户本次明确授权，提交/推送另获授权。

## 接手者需要知道的变化

- 能力接口与服务共用注册表。Canvas版本 `codepoet-canvas-1.0.0`，CSS版本 `codepoet-css-1.0.0`；语义引擎版本仍为 `p0-03.1`。新任务核对generation归属、backend、能力、真实版本与采样时刻。他人资源仍404；未开放能力、未知版本422，不改写历史job/manifest或伪造版本别名。
- **默认仍仅Canvas**。同时交付前后端后，可配置 `CODEPOET_ENABLED_BACKENDS=canvas2d,css` 开启CSS；只配置名字不能启用尚未注册的WebGL/Turtle。候选测试使用独立数据库和产物目录，未修改开发数据或公共部署。
- 部署前按现有方式运行 `scripts/migrate.py`，新增 `012_browser_render_backends.sql`。迁移重建旧render_jobs的Canvas专用CHECK，保留旧行、索引与作品引用；迁移器在事务外临时关闭FK，提交前检查全部FK，失败回滚并恢复约束。已验证带历史版本标签和作品引用的升级、坏引用回滚、空库全量迁移。不要手改004或直接修改生产sqlite_master。
- 模板草稿可声明已实现的Canvas/CSS，重复或未实现声明422。克隆默认保留来源支持集合；已发布版本不可PATCH。公开列表/筛选读取**发布版本**的支持集合，避免容器旧声明漏掉CSS。新部署要从既有模板克隆支持CSS的新版本并正常审核，不回写历史发布版本。
- Creator、模板查询/预览/管理、搜索、三种生成、会话、保存、历史重绘与复现均按选择或历史backend分发。切换后端取消旧请求、使旧响应失效并创建新generation；保存期间锁定选择与IR。上传失败GET并复用原job，成功检查点不重复导出/上传。历史版本不可执行时保留服务器PNG、关闭重绘/复现。
- CSS使用真实SVG世界几何和暂停的WAAPI opacity效果，由页面单一clock驱动。64边圆、数组画序、父子TRS、逐叶祖先透明度、固定世界线宽、viewport裁剪沿用采样语义。导出克隆当前SVG并冻结透明度，Canvas只负责将该SVG栅格化PNG；没有调用Canvas IR绘图冒充CSS。seek/导出串行，dispose取消动画并释放URL。
- server通用轮询仅完成纯状态测试：queued/running、终态、取消/卸载、断网保留jobId重试，server分支不上传PNG。后端仍拒绝server新任务，**不代表Turtle/R3已实现或验收**。

## 证据与验证边界

在本机Windows、Chrome headless、DPR=1、Next生产构建、真实FastAPI/SQLite下验证。必要脱敏结果与PNG在[证据目录](2026-11-13-s0-css-integration)。原始过程、完整截图、测试服务日志不纳入共享记录。

| 检查 | 实际结果 |
| --- | --- |
| 前端typecheck / production build | 通过 |
| 后端 `python -m unittest discover -s backend/tests -q` | 334项，OK；退出码0。旧测试临时SQLite清理仍有WinError32告警，不是全部无警告 |
| Canvas新旧真实浏览器像素回归 | 46帧完全一致，12组协议/采样检查通过 |
| CSS真实浏览器适配器 | 11帧；SVG/WAAPI、世界几何/颜色/裁剪、暂停时刻、重复PNG、并发seek/导出、dispose、零遗留URL通过；server为纯状态测试 |
| CSS真实API/UI | 7项通过，无pageErrors：模板克隆审核且旧版不变；静态保存/重开/复现；4秒动画0/2000/4000保存/重开/复现；会话恢复；语义切后端；真实旧请求延迟竞争；完整IR切后端 |
| CSS帧一致性与重试 | 下载内容SHA256等于manifest，重导出同帧哈希相同；服务端RGBA复现exactMatch=true；静态首次上传断网后复用原job，没有重复创建任务 |
| 既有真实P1 / 模板审核 / 默认Canvas G0 | 13项 / 12项 / 20项通过，无阻断；fixture控件17项通过 |

复跑：在隔离部署准备标准seed账号，通过环境变量提供验收凭据（不写日志），设置对应 `CODEPOET_*_BASE_URL` 和 `CODEPOET_*_OUTPUT_DIR` 到本地日志目录。浏览器脚本需要Node 24与可用的Playwright/Chrome：

```text
node frontend/tests/renderer-browser-check.mjs <local-output>
node frontend/tests/css-renderer-check.mjs <local-output>
node frontend/tests/css-api-check.mjs
node frontend/tests/p1-browser-check.cjs
node frontend/tests/template-review-browser-check.cjs
node frontend/tests/g0-browser-check.cjs
```

CSS闭环脚本要求 `CODEPOET_CSS_BASE_URL`、`CODEPOET_CSS_OUTPUT_DIR`、`CODEPOET_ACCEPTANCE_PASSWORD`；会在隔离库克隆并审核tpl-lotus的新版本。其余脚本环境变量见各脚本开头，禁止用默认共享历史输出目录。Next生产rewrite在**构建时**读取API_PROXY_TARGET，构建和启动必须指向同一测试后端。

## 待办与责任方

- 部署负责人：合并/发布前统一前后端版本与迁移编号（当前012），备份并执行迁移；目标环境复验通过后决定默认开放CSS。当前没有公共发布，也没有将默认启用集合扩大。
- A/B：继续各自后续任务卡；WebGL、Turtle真实server、质量新算法和DSL均不在本轮交付中。server复现空请求及server环境快照仍待R3。
- 验收范围：未覆盖其他浏览器/设备/目标部署；跨后端抗锯齿像素不承诺一致，本轮Canvas自身保持旧像素、CSS自身历史复现精确一致。浏览器上传仍不能证明客户端像素必定忠实于IR，这是既有协议边界。
