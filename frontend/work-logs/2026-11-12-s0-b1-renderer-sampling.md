# 交接：S0-B1 共享采样与 Canvas Renderer

日期：2026-11-13（任务始于11/12）
任务 ID：S0-B1
基线：a90faf1；实现分支：feat/s0-b1-renderer-sampling-20261112。实际提交与合并状态以Git记录为准。
状态：前端已实现，类型/构建及本机生产模式选定Canvas主路径通过。后端与接口契约未修改。

## 接手者需要知道的变化

- `frontend/src/renderers/scene.ts` 提供无DOM依赖的 `sampleScene(ir,timeMs)` 与 `sampleTimes(ir)`。输出世界坐标顶点、原数组画序、有效逐叶opacity、样式、viewport与背景。保留父子TRS、64段圆和原动画截断规则；viewport偏移在绘制时扣除，描边宽度保持逻辑像素。有效opacity已包含祖先值，适配器不能再次乘style.opacity。
- `renderers/types.ts` 定义 `load/seek/toPNG/dispose`；`registry.create(backend,host)` 当前只构造真实Canvas实例。所有操作排队，无内置播放时钟；导出使用独立实例并释放，保留现有预览clock。
- 原 `canvas/renderScene.ts` 的同步绘制、采样、PNG导出及版本入口保留；版本仍为 **codepoet-canvas-1.0.0**，所测旧像素与PNG字节不变。新后端未开放，页面后端选择/会话/任务分派尚属S0-B2。

## 证据与验证边界

- `./scripts/npm.ps1 run typecheck`、`run build`均退出0；生产构建转发至本轮隔离后端18170，前端next start端口13170。测试服务已停止。
- [采样/Renderer结果](2026-11-12-s0-b1-renderer-sampling/renderer-results.json)：12项，46组场景/时刻与Git基线a90faf1旧实现的原始像素、解码PNG及PNG字节精确一致；含串行导出、预览隔离、失败恢复与释放检查。
- [G0真实API与浏览器结果摘录](2026-11-12-s0-b1-renderer-sampling/g0-check-results.json)：20项通过，含生成→PNG上传→保存→刷新重开→复现和私有资源404。
- [P1选定真实路径结果摘录](2026-11-12-s0-b1-renderer-sampling/p1-results.json)：13项，含语义动画、四秒模板三帧保存/重开/复现；blockers/failures为空。仅selected-paths，gaPassed/gbPassed仍false，不代表完整GA/GB矩阵通过。
- 原有Canvas检查10项与fixture控件17项通过；fixture不算真实API证明。共享结果摘录保留检查项/请求ID，省略截图清单与完整请求流水；原始过程/附件留在本机，不作为阅读本交接的前提。
- 未执行本轮完整后端测试/HTTP smoke、正式部署或其他后端探针。透明PNG解码存在原有舍入差异，所以分别比较新旧原始像素及新旧PNG，而不把原始像素与解码值混作同一基准。

## 待办与责任方

- **A / S0-A1**：能力查询仍将rendererVersion声明为语义引擎的p0-03.1，与Canvas真实版本不同；统一注册与校验时需兼容现有作品，不能直接严格比较导致保存失败。本卡没有改后端声明或增加严格保存校验。
- **A / Q1-A1**：Python `engine/sample.py` 仍待实现。实现时与前端相同fixture核对世界坐标/opacity/时间，不能直接跨语言import。
- **B / S0-B2**：按generation/job/历史backend接入预览、会话、保存与复现；server轮询到R3真实验证，不能调用Canvas伪造其他backend产物。
