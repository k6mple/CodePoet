# 交接：S1 沙箱模板基底触发后端 AttributeError（2026-11-17）

日期 / 任务 ID：2026-11-17（2026-11-18 已修复并复验） / 2026-11-17-p0-regression-webgl（S1-B 联调发现）
代码基线：发现于 `origin/main 7bc48a7`；修复于 PR #30 `a735960`
当前状态：**已由后端 PR #30 (`ff6f9d7` / `a735960`) 修复，并在 2026-11-18 通过后端全量 484 项测试与前端生产 UI 10 项真实验收**（见 [S1-B UI](2026-11-17-s1b-ui.md)）。下方保留原始排查记录备查。

## 影响

任何 `POST /sandbox-runs` 只要 base generation 是模板来源（`mode="template"`、带 `template_version_id`），都会在 worker 启动前失败：

- 受理返回 `202`；
- 轮询终态 `failed`；
- `error.code = DSL_EXECUTION_FAILED`，`message = 执行线程异常终止`；
- `metrics=null`、`auditFindings=[]`，没有任何用户可解释的 DSL 错误。

因此 `set_param` 和“基于模板作品的 DSL 二次创作”当前不可用；IR/子图/fragment 基底不受影响。

## 根因

`backend/app/services/sandbox.py` 的 `_param_context()` 在模板基底分支执行：

```python
ParameterDefinition.model_validate(item).model_dump(mode="json")
```

但 `ParameterDefinition` 从 `app.schemas.generation` 导入的是类型别名（`Annotated`/联合类型），不是 Pydantic 模型类，因此抛：

```text
AttributeError: model_validate
```

`execute_run()` 里包住 `_worker_payload()`/`_run_worker()` 的 `try` 只捕获 `OSError`；`AttributeError` 冒泡到 `SandboxExecutor._run_safely()` 的通用 `except`，被 `_mark_crashed()` 记为“执行线程异常终止”，所以 HTTP 响应看不到真实原因。

## 复现

环境：隔离 SQLite、`CODEPOET_ENABLED_BACKENDS=canvas2d,css,webgl`、seed 账号。

1. 取一个 `creator_demo` 自己拥有、`mode="template"` 的 generation 作为 base。
2. 提交合法 DSL：

```json
{"baseGenerationId":"gen-<template-owned>","source":"{\"version\":1,\"ops\":[{\"op\":\"circle\",\"cx\":400,\"cy\":300,\"r\":90,\"fill\":\"#E8A020\"}]}"}
```

3. 轮询 `GET /sandbox-runs/{id}`，得到 `failed / DSL_EXECUTION_FAILED / 执行线程异常终止`。

脱敏结果见 [s1b-api-result-template-base.json](2026-11-17-s1-sandbox/s1b-api-result-template-base.json)（2/10 项通过，两个 run 均失败）。

独立复现脚本直接调用 `_worker_payload()`，得到完整 traceback（本地日志，未复制路径）：

```text
File "backend/app/services/sandbox.py", line 771, in _worker_payload
    "paramContext": _param_context(db, base),
File "backend/app/services/sandbox.py", line 726, in _param_context
    ParameterDefinition.model_validate(item).model_dump(mode="json")
AttributeError: model_validate
```

同一脚本对 `mode="ir"` 的 base 不进入该分支，worker 正常执行。

## 已验证不受影响

用 `creator_demo` 自己的 IR 基底重跑同一套检查，**11/11 通过**：

- 合法 DSL → `succeeded`，`metrics.drawCalls=2`、`steps=5`、`peakMemoryMiB=31`；
- fragment generation 可读，`mode=fragment`、`backend=webgl`、12 个节点、`provenance.kind=fragment`、`fragmentSource=null`；
- 非法 op `eval` → `failed / DSL_AUDIT_REJECTED`，`auditFindings=[{ruleId:"DSL_OP_WHITELIST",line:1,column:0,...}]`；
- 管理员读取/更新策略成功；旧 revision → `409 REVISION_CONFLICT`；改只读字段 → `422 POLICY_FIELD_READ_ONLY`。

脱敏结果见 [s1b-api-result.json](2026-11-17-s1-sandbox/s1b-api-result.json)。

## 建议修复（后端负责人）

1. 用实际参数定义模型/适配器解析：例如 `TypeAdapter(ParameterDefinition).validate_python(item)`，或复用模板服务里已有的解析函数；不要假设类型别名有 `.model_validate`。
2. 在 `_run_safely()` 的通用 `except` 中记录异常类型/消息（至少到服务日志），否则所有内部 bug 都会被伪装成同一句“执行线程异常终止”，前端和验收无法定位。
3. 补一条真实 API/`execute_run` 测试：base 为模板版本，`paramContext` 非空，验证 worker 能收到 `provided`/`templateIr`；当前 `test_dsl.SetParamTests` 只覆盖无上下文失败，未覆盖这条。
4. 修复前 S1-B 演示只能用 IR/fragment 基底，并避免 `set_param`；不要用模板基底宣称 DSL 闭环。

## 边界

- 本轮未修改任何后端代码，未重启用户的其他服务；只使用隔离库。
- 未验证目标 OS 的 CPU/内存/超时熔断；本次成功 run 的 `metrics` 来自真实 worker，但预算熔断仍需单独测试。
- 前端 S1-B 页面已用 typecheck 验证；真实 UI 验收在本记录之后继续，不把本后端缺陷算作前端失败。
