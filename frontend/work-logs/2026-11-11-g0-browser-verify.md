# 工作日志：G0 真实浏览器复验（后端 BE-G0-001 修复后）

日期：2026-11-11（GMT+8）
任务：在 `main` 内容上跑标准 G0 浏览器检查，确认 BE-G0-001 是否真的解除
状态：**通过（g0Passed=true）**

## 背景

11/09 的两次 G0 复验都停在同一个地方：`POST /render-jobs` 返回
`queued / awaiting_frames`、`expiresAt=null`，而契约 7.1 要求
`running / awaiting_frames` + 五分钟有效期。前端因此拒绝上传帧，
`g0Passed=false`、退出码 2。这就是问题单 **BE-G0-001**。

该问题随后在 PR #10 修复（创建即 `running`，并带 5 分钟 `expiresAt`），
#10 已于 11/11 12:35 合入 `main`。本轮是对修复结果的实际复验。

## 环境与边界

- 被测代码：`9688850`（= `main` 合入 #12 后的树，与 `main` 内容一致）
- 隔离数据：`D:/Projects/_smoke/g0-20261111-01/data`，独立库、独立端口，
  未触碰任何现有业务数据
- 后端 `uvicorn` 18100；前端 `next dev` 13100，`API_PROXY_TARGET=http://127.0.0.1:18100`
- 浏览器：系统 Microsoft Edge 157.0.4341.46，由 Playwright `channel=msedge` 驱动（headless）
- 本轮**未修改**前端与后端任何业务源码、测试脚本或接口契约

## 实际命令

```bash
# 隔离库
CODEPOET_DATA_DIR=D:/Projects/_smoke/g0-20261111-01/data PYTHONPATH=backend \
  backend/.venv/Scripts/python.exe scripts/migrate.py
CODEPOET_DATA_DIR=D:/Projects/_smoke/g0-20261111-01/data PYTHONPATH=backend \
  backend/.venv/Scripts/python.exe scripts/seed.py

# 服务
CODEPOET_DATA_DIR=D:/Projects/_smoke/g0-20261111-01/data PYTHONPATH=. \
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18100
API_PROXY_TARGET=http://127.0.0.1:18100 \
  node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 13100

# 检查（标准模式，不带诊断上传）
NODE_PATH=<playwright> PLAYWRIGHT_CHROME_PATH=msedge \
CODEPOET_G0_BASE_URL=http://127.0.0.1:13100 \
CODEPOET_G0_OUTPUT_DIR=frontend/work-logs/2026-11-11-g0 \
  node frontend/tests/g0-browser-check.cjs
```

退出码 **0**，输出：

```json
{ "status": "passed", "g0Passed": true, "checks": 20, "blockers": [] }
```

## 结果：20 项全过

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 注册登录 + 默认模板出图 | 通过 |
| 2 | 调参后画布像素真实改变 | 通过 |
| 3 | 单字段越界：保留旧画布、禁用保存 | 通过 |
| 4 | 跨字段校验真实返回 422 | 通过 |
| 5 | 会话失效 401 → 重登保留现场 | 通过 |
| 6 | 慢响应不能覆盖更新的画面 | 通过 |
| 7 | 390px 无横向溢出 | 通过 |
| 8 | 网络断开不出现假成功 | 通过 |
| 9 | **创建上传任务 → `running / awaiting_frames`** | **通过（BE-G0-001 已解除）** |
| 10 | 保存私有作品（无需诊断上传） | 通过 |
| 11 | 新标签重开拿到真实服务器 PNG | 通过 |
| 12 | 刷新后仍拿到真实服务器 PNG | 通过 |
| 13 | 服务器 PNG 的 SHA256 与清单一致 | 通过 |
| 14–18 | 他人私有资源（work / version / generation / job / artifact）均 404 | 通过 |
| 19 | 他人页面不暴露私有 PNG | 通过 |
| 20 | 认证信息不进 Web Storage | 通过 |

页面错误 `pageErrors`：0 条。

## 证据

- `g0-check-results.json`：20 项逐条结果、49 条请求（只记 actor / method / path /
  status / requestId，不含 Cookie、CSRF 值或密码）
- `automated-creator-390.png`：移动宽度页面
- `automated-reopened-1440.png`：重开作品页，可见真实渲染的莲花与服务器 PNG
- `automated-private-denied-1440.png`：第二账号访问私有资源的拒绝态

## 仍未覆盖（不作为 G0 通过的一部分）

- 帧上传中断后对原任务的重试
- 真实任务到期与「每用户两个未完成上传」限额
- 动画三帧保存
- 本次只证明「保存/重开/隔离/错误恢复」这条 G0 链路；语义、子树、审核、
  会话、收藏等不属于 G0 判据

## 结论

BE-G0-001 在真实浏览器下确认解除，G0 标准流程 `g0Passed=true`。
