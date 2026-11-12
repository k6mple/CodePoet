# 开发者 B：多后端创作、质量反馈与演示提分计划

> 2026-11-12代码复核补充：具体文件、函数、分步修改、数据迁移、依赖与验收见[提分阶段执行细则](提分阶段执行细则.md)。先读该文件第1～2节，再执行本人任务卡；新增能力仍未实现。

> 修订：2026-11-12。截止11/21 12:00，11/18 18:00功能冻结。先读[总纲](双人开发总纲与接口约定.md)、[进阶验收](进阶功能开发清单.md)、[接口增量](提分阶段接口增量约定.md)。本版把CSS/WebGL升级为有预算的目标，不再只做Canvas。

## 1. 从已有页面与IR继续

负责frontend/**、shared/demo/**、前端验收脚本。backend/**只读，接口缺陷给A复现；不要为赶进度硬编码审核/能力或使用mock成功。先检查最新Git、AGENTS、相关日志，并按前端规范在业务写入前建本次工作日志；读相关本机Next.js文档再改代码。

现有页面/Canvas/IR树/时间轴/保存/复现/六类导出有生产模式主路径记录。主要入口是src/renderers/canvas/renderScene.ts、components/CanvasPreview.tsx、Creator.tsx、TimelinePlayer.tsx、WorkReproduction.tsx、api/render.ts、types/core.ts，逐项以当前文件为准。不重建管理后台，不做全新UI设计。

11/13～18容量30～36小时；无图谱主线26小时，含图谱28小时，余量用于真实集成/坏例/证据。A是S1/R3/K1的容量瓶颈，B的节余不能自动变成修改后端的权限。

## 2. 按顺序交付的切片

| 次序/ID | 本人预算 | 实际动作 | 完成条件 |
| --- | --- | --- | --- |
| 1 S0-B | 4h | 提取共享时间采样/矩阵/世界几何；最小Renderer协议；保存/复现按模式分发 | Canvas旧版本行为不变；不再固定从exportFrame(Canvas)导出所有backend |
| 2 R1-B | 6h | SVG/DOM+CSS/WAAPI，seek/导出、接真实generation/upload | 静态与动画三时刻，真实CSS PNG→保存→刷新→复现，无工具栏/旧帧 |
| 3 R2-B | 10h | 真WebGL几何/描边/透明、时序、帧缓冲导出与资源释放 | 三几何/凹polygon/变换/裁剪，真GPU输出保存，context loss可恢复或明确失败 |
| 4 Q1-B | 3h | 四类指标/解释/建议/节点定位、优化前后比较；准备标注样本配合A校准 | 显示算法版本/适用范围；score不称赛题得分，未评估不填0或满分 |
| 5 S1-B | 3h | 受限DSL小编辑器、run轮询/错误行列、成功IR预览/保存、管理员策略表单 | 能力未开无假入口；合法与拒绝/熔断UI均真实，策略409可恢复 |
| 6 K1-B（有来源） | 2h | 图谱source/entityId输入、关系/属性来源与决策展示 | 与站内entityId入口分清；失败不冒充本地成功，旧作品显示快照 |
| 7 X1或X2 | X1 2h / X2 6h | LLM提案显式应用，或GPU进阶最小闭环 | 11/17准入、二选一；未过18日冻结门槛就关闭 |
| 8 F1-B | 11/19～20预留10～12h | next start验收、四角色/作品、演示录屏、失败与恢复 | 部署版真实API无mock，8～10分钟完整演示、证据可重放 |

Turtle server任务展示/轮询/取消和模式分派包含S0-B。先与A明确RenderJob生命周期再写UI，不为Turtle再做一套上传器。R1/R2每套预算已包含审查/验证/真实联调，不能先用完写代码时间再无上限修。

## 3. 共用Renderer与保存协议

浏览器Renderer最小能力：load(ir)、seek(timeMs)、toPNG()、dispose()。播放使用上层统一clock，Renderer不各开一套rAF；seek完成保证目标帧已实际绘制，导出暂停/锁定generation/hash/version，结束恢复用户时刻。

Canvas/CSS/WebGL共享相同IR采样、父矩阵T×R×S、绘制顺序、逐叶opacity、逻辑坐标、固定圆近似；浏览器本地探针可判断WebGL缺失，但服务端仍做权威参数/能力校验。Renderer版本独立注册，保留Canvas现有版本兼容旧作品。

- browser_upload：按任务backend选真实Renderer、逐时刻PNG、FormData上传；三帧齐全且manifest匹配后保存。不可用后端错误要可重试或显式切换，并创建新generation，不在旧任务内部替换。
- server/Turtle：创建任务后轮询，展示阶段/错误/取消，成功读取manifest与受权PNG；不调用本地Canvas导出、不上传frames。静态/离线采样能力明确标识。
- 复现从作品原backend/mode/version确定流程，不重新解释语义；同后端兼容环境按服务端真实comparison显示结果，不宣传四后端像素一致。
- backend选择写入会话state并可恢复；旧state缺少新字段沿旧逻辑，换后端后重新生成，不复用不匹配的generation/job。
- TemplateManager只给已启用且通过能力校验的supportedBackends，新模板版本走审核。不要改旧发布版本或临时硬塞四backend到旧数据。

## 4. CSS/WebGL需要实际证明的部分

CSS不能是Canvas元素包一层动画。真实SVG/DOM几何，WAAPI/CSS时间受统一clock控制；导出冻结属性/样式后栅格化，Canvas只做最终编码，不重新绘几何。避免外部字体/图片和HTML注入，group opacity按项目逐叶相乘语义实现。

WebGL不能只是画三角形。凹polygon三角剖分、polyline/stroke三角化、透明混合与绘序、y方向/viewport、readPixels翻转与PNG均需同IR对照；离页释放buffer/program/listener，context lost明确处理。无用户GLSL/复杂3D引擎。

每个适配器至少跑：基础几何、父子TRS、透明重叠、裁剪、0/中/末时刻、非法/不支持输入、离页释放、真实保存/刷新/复现。像素差异以有说明的阈值比较，结构一致优先，不能把Canvas截图贴给另一标签。

## 5. 质量/图谱/DSL UI 控制范围

质量：复用现有Evidence/Feedback等组件，展示四类卡片+建议+节点/时刻定位。12个标注样例配合A，至少6坏例；点击优化建议是修改参数/IR形成新generation，不覆盖旧作品。无语义依据/算法不可用/历史未评估有不同状态。

图谱：输入真实sourceId与外部entityId，展示读取时间/内容hash/关系与映射依据；默认不做可视化图谱大编辑器。3实体/2关系和来源变化对画面的影响足够证明链路，静态缓存回放标明来源时间。

DSL：textarea+代码行/错误摘要即可，不装复杂IDE；执行提交/轮询/终态，源码留在用户当前草稿，服务端结果才可替换预览。策略仅管理员可编辑允许函数与有硬上限的数值；固定AST规则只读，不能给「关闭所有防护」按钮。

## 6. 日期与接口等待时的工作

| 日期 | 重点 | 若A接口未就绪 |
| --- | --- | --- |
| 11/13 | S0-B、CSS真预览 | 用同IR fixture验证数值/导出，明确仅本地检查 |
| 11/14 | CSS真实保存/复现、WebGL几何 | 切换到WebGL单元/fixture，不伪造CSS保存成功 |
| 11/15 | WebGL保存/复现、Q1/DSL UI | 按已确认拟议schema写组件，真实验收仍待办 |
| 11/16 | 三浏览器对照、Turtle server展示、条件K1 | 帮A提供请求/错误/节点定位，后端仍由A修改 |
| 11/17 | 全链收敛、18:00主线门槛 | 未过取消X，优先保可演示功能 |
| 11/18 | 已准入X或修缺陷，18:00冻结 | 不临时新增第三方向 |
| 11/19～20 | F1真实生产回归/作品/录屏 | 目标环境失败视为交付阻塞，降低未稳能力 |

每半天交接一次：支持的IR子集、rendererVersion、generation/job/requestId、实际失败请求/响应与复现步骤。A优先S0公共契约时，B可完成纯渲染；不能连续数日只在fixture上累计未联通页面。

## 7. 可直接给agent的第一项任务

```text
任务S0-B：将当前Canvas导出链提取为可扩展Renderer调用，不实现后端。
先读根/frontend AGENTS、日志目录说明、新总纲/接口增量，读取相关Next本机文档。
建立本次前端日志；仅改frontend及必要前端fixture。
阅读当前renderScene、CanvasPreview、api/render和WorkReproduction。
保持现有Canvas rendererVersion及旧作品行为，抽取load/seek/toPNG/dispose最小协议。
保存和复现分browser_upload/server；server仅轮询真实任务，不调用Canvas导出/上传frames。
先保持当前Canvas真实链可用，不新增大框架、不用mock声称新后端已通。
执行类型检查/生产构建及相关真实旧作品路径，给出差异、结果、未完成项，不自动push。
```

后续分别给R1/R2/Q1-B/S1-B，一个切片一个可见验收结果。必要测试优先验证渲染数值、异步导出/错绑、资源释放和真实作品链；静态文案不堆测试。所有新前端日志记录实施、检查、真实API、目标环境四层状态。

## 8. 直接执行时的任务拆分

按[执行细则](提分阶段执行细则.md)逐卡完成。S0-B原4小时仅为基础抽取目标，server真实联调应在R3阶段单独记实际耗时；新增接缝与回归若超预算须更新看板，不能算成零成本。

| 父任务 | B的执行卡 | 明确交付 |
| --- | --- | --- |
| S0 | S0-B1→S0-B2 | 保像素的采样/Renderer提取→类型、模板查询、三种生成、会话、保存与历史全部接入 |
| R1 | R1-B1→R1-B2 | 离线真实SVG/CSS/WAAPI/PNG→候选服务API保存和复现 |
| R2 | R2-B1→R2-B2 | 几何/描边/GPU输出→真实保存、复现和context loss |
| Q1 | Q1-B | 标注样本、生成与像素报告阶段区分、建议定位、新generation前后比较 |
| S1 | S1-B | 片段基础ID/源码保存恢复、提交/终态/错误行列、受限策略表单 |
| R3 | R3-B | 复用通用轮询，真实server取消/刷新恢复/失败重试/三帧预览 |
| K1 | K1-B | sourceId/外部entityId入口、真实关系影响、外部与站内链接分开 |
| X1/X2 | 二选一 | 提案预览/显式apply；或版本化GPU效果/IR交互与实际性能证据 |
| F1 | F1-B | 目标环境next start、独立证据目录、四角色/各backend作品/录屏 |

任务卡示例：「只做S0-B1，保持codepoet-canvas-1.0.0及ir.nodes画序；提取采样/矩阵供Canvas调用，不开放CSS入口；旧fixture及真实Canvas保存/复现仍通过，再报告改动与结果。」调用接口前明确是已实现还是拟议；mock/fixture仅证明局部逻辑。
