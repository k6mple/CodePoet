# 交接：WebGL 真实 API 闭环（2026-11-17）

日期 / 任务 ID：2026-11-17 / 2026-11-17-p0-regression-webgl
代码基线：`origin/main 7bc48a7`（PR #28 已登记 `codepoet-webgl-1.0.0`）
当前状态：**真实 API 闭环通过（带像素差异边界）**；未修改后端、接口契约或默认部署配置；已随 `dec93b8` 提交并推送至 `origin/feat/g-main-20261117`。

## 结论

在独立 SQLite、真实 FastAPI、Next 生产构建和 Chrome headless 下验证：

- 从 `tpl-lotus` 历史版本克隆并审核发布新版本，`supportedBackends: ["canvas2d","webgl"]`；旧版本支持集合未被改写。
- 隔离实例开启 `CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl`；能力接口返回 `webgl.available=true`、`rendererVersion=codepoet-webgl-1.0.0`。
- Creator 选择 webgl 生成成功；修改参数后 IR 哈希变化。
- 静态作品 `[0]` 与动画作品 `[0, 2000, 4000]` 均完成：创建 render job → 浏览器导出/上传三帧 → 保存私有作品 → 刷新重开 → 服务端复现。
- 每个 artifact 的 SHA256 与 render job manifest 帧哈希一致；复现页无 pageErrors。
- **边界**：服务端像素比较 `exactMatch=false`，`mae=0`，`differentPixelRatio` 为 0.0050%（静态）和 0.0029%（动画）。UI 显示“检测到像素差异”。原因是 WebGL 多采样抗锯齿在不同 context 之间的边缘像素差异，不是保存、重开或 manifest 失败。**不能对外宣称“像素一致”**；若要严格 exactMatch，需要另行决定是否关闭导出抗锯齿或引入确定性软件光栅化。

## 证据

- [result.json](2026-11-17-webgl-real-api/result.json)：5 项检查、每帧 artifact/重导出哈希、像素差异、job/workVersion id。
- 静态：[原始 PNG](2026-11-17-webgl-real-api/static-0.png)、[复现页截图](2026-11-17-webgl-real-api/static-reproduction.png)。
- 动画三帧：[0ms](2026-11-17-webgl-real-api/animated-0.png)、[2000ms](2026-11-17-webgl-real-api/animated-2000.png)、[4000ms](2026-11-17-webgl-real-api/animated-4000.png)、[复现页截图](2026-11-17-webgl-real-api/animated-reproduction.png)。

关键结果摘要：

| 检查 | 结果 |
| --- | --- |
| 模板克隆/审核/发布含 webgl | 通过；旧版本 supportedBackends 不变 |
| 生成 + 参数变化 | 通过；`backend=webgl` |
| 静态 `[0]` 保存/重开/复现 | 通过；manifest SHA256 一致；MAE 0%，差异像素 0.0050% |
| 动画 `[0,2000,4000]` 保存/重开/复现 | 通过；3 帧 manifest SHA256 一致；MAE 0%，差异像素 0.0029% |
| pageErrors | 0 |

## 复跑方式

1. 前端生产构建（构建时固定代理目标）：

```text
API_PROXY_TARGET=http://127.0.0.1:8000 npm run build
```

2. 独立数据目录执行迁移与 seed；后端以三后端启动：

```text
CODEPOET_DATA_DIR=<isolated-data-dir>
CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

3. 验收脚本（Playwright 需在前端环境可解析；若未安装，可在 `frontend` 下临时安装，勿改锁文件）：

```text
CODEPOET_WEBGL_BASE_URL=http://127.0.0.1:3000
CODEPOET_WEBGL_OUTPUT_DIR=<frontend/local-work-logs/...>
CODEPOET_ACCEPTANCE_PASSWORD=<seed password via env>
node frontend/tests/webgl-api-check.mjs
```

4. 脚本会在隔离库中克隆 `tpl-lotus` 并审核新版本；输出目录必须是本地 ignored 目录，脚本会自行创建。

## 未验证与下一步

- 未验证目标部署、其他浏览器/GPU、Firefox/Safari；仅本机 Windows + Chrome headless。
- 未验证 context loss 的真实画面恢复；该部分由 `frontend/tests/webgl-renderer-check.mjs` 的 15 项/17 PNG 浏览器检查覆盖，不属于本次真实 API 脚本。
- 默认部署仍只开 `canvas2d`；演示/验收要显式开启 webgl，并先准备含 webgl 的模板版本。
- 需要负责人决定：接受“MAE 0、差异像素 ≤0.005%”作为 WebGL 复现判据，还是修改 WebGL 导出以追求 exactMatch。在决定前，看板 R2 记为“真实集成通过（带像素差异边界）”，不记为“像素一致”。
- PR #28 复审遗留的 P2/P3：`.env.example` 缺 `CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE`；`backend/app/api/engine.py` 的 webgl 不可用文案、旧测试名/搜索示例仍按“未实现”写。
