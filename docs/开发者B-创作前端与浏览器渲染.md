# 开发者 B：页面与 Canvas 开发任务书（基础演示版）

> 修订：2026-11-05。适用：基础 CRUD 开发者使用 AI 辅助，截止 11/21 12:00 GMT+8。
>
> 必读：[任务看板](./交付计划与任务看板.md) → [总纲](./双人开发总纲与接口约定.md) → [接口文档](./接口文档.md) / [核心 OpenAPI](./openapi-core.json)。本文替代原“三浏览器后端和无头导出必须完成”的安排。

## 1. 职责和交付边界

你负责 frontend/**、shared/demo/**、前端检查脚本：业务页面、Canvas、参数交互、JSON IR、时间轴、PNG 导出上传、复现 UI、演示数据/操作说明。

A 负责所有 HTTP API、数据库、权限/审核、规则推理、IR 校验、图片落盘和像素比较。不要在前端再写一套权威审核/推理/参数校验服务。

基础版**只完成 Canvas**。不必做 WebGL、CSS 渲染器、Turtle、Monaco、Playwright CLI、无限撤销、复杂图层编辑器。对应能力不可用时显示说明或隐藏入口，不放假按钮/假效果。

## 2. 页面缩减为可复用的几组

| 页面 | 必做内容 | 阶段 |
| --- | --- | --- |
| 登录/注册 | Cookie/CSRF、申请角色、退出 | P0/P1-A |
| 模板库/详情 | 组合筛选、当前发布版本/参数、使用模板 | P0/P1-A |
| 创作台 | 模板参数/语义/IR 三 tabs、Canvas、图层/裁剪、时间轴、保存 | P0/P1-B |
| 知识库/详情 | 检索、正文/来源/关联实体、从知识创作 | P1-A/P1-B |
| 个人空间 | 作品、收藏、编辑会话、历史/恢复、个人信息 tabs | P1-A |
| 作品详情 | 输出/时间轴/IR/来源、审核状态、复现对比 | P0/P1-B |
| 内容管理 | 知识/概念/标签 tabs，表格+表单即可 | P1-A |
| 模板管理与审核 | 模板版本/JSON/参数、送审/差异、审核台 | P1-A |
| 管理员 | 角色申请、映射规则、导入导出、日志 tabs | P1-A/P1-B |

Next.js 16 App Router + React 19 + TypeScript + 一个 UI 组件库（2026-11-06 按开发者熟悉度调整）。路由使用 src/app 文件路由，Canvas/表单/时间轴交互放客户端组件；通过 /api rewrites 调用 FastAPI，不重复实现后端业务。复杂表单先使用 JSON textarea+格式化/错误提示，基本路径稳定后再美化。

## 3. P0：先让真实作品保存下来

### P0-01/02：框架、API client 与登录

- 集中 API client 管 Cookie、CSRF、分页、错误和 requestId，不在每页散落 fetch。
- 首次 GET /auth/csrf，登录成功用新 token；所有写请求带 X-CSRF-Token。
- 401 提示重新登录并保留编辑现场，403 权限提示，404 不可访问，409 重新加载版本，422 对应字段错误。
- 登录/退出/刷新真实可用，密码/令牌不存 localStorage/URL。
- 菜单依据 role/permissions；直接输入无权路由显示无权，不在页面硬编码管理员。

### P0-03：默认模板、参数、Canvas

- 接受 A 的固定 IR fixture，先画圆/多边形，再接 GET 模板和 POST generations。
- 默认参数一键生成；控件按 number/integer/enum/boolean/color 映射。
- 前端给范围提示，最终以后端返回为准；300ms 防抖和请求序号/取消，旧请求不能覆盖新画面。
- 当前参数无效时保留上一帧并注明“修改未应用”，不要把旧图说成新结果。
- backend 固定 canvas2d。其他后端不可用时不让用户误选。

### P0-04：用 toBlob 保存，不等待无头渲染服务

1. 用户点击保存，固定 generationId/irHash/backend/rendererVersion 和当前 IR。
2. POST /render-jobs：executionMode=browser_upload、静态 timesMs=[0]，传 rendererVersion 和 clientEnvironment={userAgent,deviceScaleFactor:1}。
3. 返回 running/awaiting_frames 后，调用本地 seek(0)，确认绘制完成，再 Canvas.toBlob('image/png')。
4. FormData 包含 file、timeMs、irHash；POST /render-jobs/{id}/frames，带 Cookie/CSRF，**不要手动设置 multipart Content-Type**。
5. 上传响应带最新 RenderJob，全部帧成功才 status=succeeded；然后 POST /works 创建作品。
6. 打开作品页 GET /work-versions/{id}，显示 A 存储的真实 PNG 和 IR；刷新后也能重开。
7. 上传/保存失败保留编辑内容与任务 ID，给重试或重新建任务；不显示假成功。

保存期间可暂时禁用调参/模板切换，这是最简单可靠的方案。不要一边修改 generation 一边上传旧任务。重复点击只处理一次。

验收：全程真实 API，无 mock；能新建并重开；保存错误可恢复；A 用户不能看到 B 的私有产物。

## 4. Canvas 只做一套清楚的渲染器

建议纯函数 renderScene(ctx,ir,timeMs) + 一层 Renderer 封装，不混进页面模板。

```ts
interface Renderer {
  load(ir: DrawingIR): Promise<void>;
  seek(timeMs: number): Promise<void>;
  play(): void;
  pause(): void;
  toPNG(): Promise<Blob>;
  dispose(): void;
}
```

- 固定逻辑画布 800×600，实际导出 backing store 使用 viewport 宽高、DPR=1；页面用 CSS 等比缩放显示。
- 左上原点，x右/y下，正旋转顺时针；T×R×S，父矩阵乘子矩阵。
- group只影响后代，不直接画；按规范深度优先顺序画叶节点。
- circle固定64段，polygon/polyline顶点先变换到世界坐标，再 fill/stroke；线宽是最终逻辑像素，不被缩放加粗。
- opacity沿父链相乘后应用到叶节点。fill/stroke=null 时跳过，不当成黑色。
- viewport只裁剪/偏移，不改构图比例；画布背景要填实，导出为不透明 PNG。
- 当前最多200节点/2000顶点/4层；静态transform支持缩放，动画只 x/y/rotation/opacity、linear、≤5秒。
- 可缓存局部几何和矩阵；变化子树失效即可。小场景允许每帧清屏重画，不开发复杂脏矩形/渲染图。
- seek完成必须保证目标帧已绘制。导出时暂停clock，绘完/toBlob后可恢复原时刻；也可复用纯函数画到独立导出Canvas。
- 导出的Canvas不能含工具栏/选择框/调试信息。高亮用独立覆盖层或直接暂不画框。
- dispose取消rAF/监听器；切页不能越运行越快。

验收 fixture：基本图形、父子旋转缩放、透明重叠、viewport裁剪。不要先追求优美粒子效果。

## 5. P1-A：普通页面和管理功能

### P1A-01/02：角色、个人资料和知识维护

- 注册requestedRole只是申请，页面说明批准前仍creator；admin审批他人，不能自批。
- 个人信息只改昵称/简介。
- 知识表格/表单覆盖类型、名称、别名、正文、来源、概念/标签、关系、timeTheme。
- 概念/标签单父树：先取完分页，在前端建树；父节点下拉足够，不做拖拽树编辑。
- 直接标签与继承标签分开显示；A 返回冲突对/防环错误时给具体说明。
- 知识详情展示参考来源；不要 v-html 注入用户内容，不编造历史出处。

### P1A-03/04：模板与审核

- 元数据表单、IR/参数/绑定JSON编辑、默认值预览、版本列表、克隆/送审/退回/归档。
- 参数编辑先textarea足够；不要做可视化参数设计器。
- 基础版generatorRef=ir、fragmentSource=null；不显示Python/JS源码编辑器。
- 版本比对采用左右JSON+变更字段列表，不做在线Git界面。
- submitted/published只读，拒审理由可见；提交/审核用目标expectedRevision。
- 审核台展示作者、具体版本、参数/IR和预览，通过/退回；自审按钮禁用，服务端仍保护。
- 草稿预览unpublishedSource=true时提示“仅私有预览”，禁止作品送审；发布后需重新生成。
- 作品新版本待审时公共页继续显示旧发布版；不能从latestVersionId误取私有内容。

### P1A-05：个人空间和历史

- 我的作品/收藏/会话/个人信息tabs；分页和可理解的状态。
- “保存进度”写session/state；“保存作品”必须先上传成功帧，两种按钮分清。
- 点击保存进度PATCH会话时由A追加快照；恢复走restore，未保存内容提示取舍，不自行覆盖历史。
- WorkVersion列表和SessionSnapshot列表分开显示；基于旧版继续编辑产生新generation/version。
- 收藏走幂等PUT/DELETE，失败回退UI；公共不可见作品不泄露在收藏列表。

### P1A-06：组合筛选

关键词、实体、标签、视觉符号、backend、timeTheme条件同步URL；跨维度AND、维度内部OR，数组用重复query参数。切条件重置页码，清空可恢复默认。

当前只有Canvas，其他后端筛选可以显示无结果/未启用，不是假装可用。无结果展示当前条件。

### P1A-07：导入导出与日志

- 管理员全库按类型JSON导出；当前不做任意ID挑选和合并向导。
- 导入JSON→validate→逐项错误/数量→通过后apply，错误包不允许执行；workSnapshots暂只导出，不在导入界面声称能恢复图片/公开作品。
- 日志表格按actor/action/type/time/requestId查询，展开必要摘要。
- 表单/日志都是标准CRUD组件，不单独设计华丽仪表盘。

## 6. P1-B：四个必须可演示的特色

### P1B-01：语义创作与依据

文本/实体输入调用semantic模式。显示匹配实体、规则、符号、色板/构图/动画依据、冲突和fallback；完整JSON可折叠。

- “莲花清正”和“竹节守正”必须由A返回不同IR。
- 无匹配给明确建议，不前端关键词换图。
- 从知识详情“基于此知识创作”携带entityId调用同一接口。
- 默认只需要莲/竹/叙事三类模板，但规则要可组合，不整句写死。

### P1B-02：JSON IR、子树与裁剪

- JSON textarea支持格式化/语法错误定位，提交完整IR由A校验，成功才替换当前generation。
- 图层树按parentId生成；选一个rootNodeId，显示允许编辑的子树JSON。
- 提交replacementNodes，根ID/parent不改，保留只读祖先。收到changedNodeIds后失效缓存。
- 裁剪用x/y/width/height四字段，首版不要求拖拽框选。
- 出错保留旧画面并提示未应用。禁止eval/new Function；JSON IR不是Python沙盒。

### P1B-03：时间轴、叙事和三帧保存

- 单clock/rAF管play/pause/seek，拖动后固定时刻，结束停在duration，重播从0。
- narrative.timeMs做标记、caption、实体链接；知识停用后展示当时来源快照。
- 保存动画使用[0,floor(duration/2),duration]，动画时长当前1000～5000ms；seek完成再toBlob，每帧上传。
- 播放是从完整IR实时采样，保存3帧是预览/备份；不能宣传为高帧率视频。
- 进入导出时暂停、锁定输入，进度显示1/3、2/3、3/3，完成或失败恢复可编辑状态。

### P1B-04：追溯和一键复现

- 作品详情可展开完整IR、参数、模板版本、知识/规则内容快照，不能只显示当前知识名称。
- 点击复现，传rendererVersion/clientEnvironment给reproduce，加载旧IR/原时刻，本地重绘后上传新任务。
- A返回comparison，原/新图并排显示compatible、exactMatch、mae、differentPixelRatio及原因。
- 只有A实际返回compatible=true且exactMatch=true才能显示“像素一致”；其他情况如实显示差异或环境限制。
- 不重新发送旧语义文本生成，那个操作是“重新创作”，不是复现。
- 声明的UA/DPR/版本只是环境提示，不能声称硬件完全一致。

### P1B-05：质量提示和演示内容

展示parameterValid、nodeCount、完全出界数、空白比例警告，不虚构艺术质量分。整理shared/demo下12条知识、3模板、8规则内容，由A导入检查。

模板必须看得出莲/竹等文化符号，含清楚配色和叙事文字，不用一个圆冒充完整艺术模板。

至少准备：四角色操作、一个拒审模板、一个归档模板、三件作品、两组语义差异、一段4秒叙事和一次恢复/复现。史实出处需要核对，艺术隐喻明确标注。

## 7. 日期和交接

| 时间 | 重点 | 共同完成标准 |
| --- | --- | --- |
| 11/06 | API client/登录、Canvas fixture | 能从真实API取基本数据 |
| 11/07～08 | 参数、toBlob/上传、作品重开 | G0：真实生成→保存→刷新重开 |
| 11/09～10 | 知识/模板/标签CRUD、检索 | 元数据和约束真实可维护 |
| 11/11～12 | 审核/个人空间/历史、运维tabs | GA：P1-A主路径可演示 |
| 11/13～14 | 语义依据、JSON/子图/裁剪、时间轴 | 两组不同语义，动画定位关联知识 |
| 11/15～16 | 复现/报告、演示数据与CRUD收尾 | GB：P1-A/P1-B全过 |
| 11/17 | 优先修缺口 | 全过才挑一个选做，不能默认为WebGL开发日 |
| 11/18～20 | 构建、错误/空态、回归、录屏 | 无默认mock的完整演示 |

遇到接口问题附URL/JSON/generationId/jobId/requestId和步骤。A修权限/业务，你修页面状态/采样/导出；不要直接改数据库或硬编码状态绕过。

## 8. 给Codex的任务模板

```text
阅读任务看板、双人总纲、开发者B任务书、接口文档和openapi-core.json。
本次仅完成[任务ID]：[一个具体交互]。只改frontend/shared/demo/前端脚本。
使用core-demo：只Canvas，不实现CSS/WebGL/Turtle/Playwright/DSL。
接口输入输出：[实际JSON]。验收：[操作步骤]。失败：[401/409/422/上传失败等]。
复用现有组件和renderScene，优先端到端可用，不增加复杂编辑器/通用架构。
实际构建并走流程，报告修改、运行结果、未完成事项，不用mock宣称完成。
```

导出任务额外要求：FormData不手设Content-Type，锁定generation/hash/时间，seek完成才toBlob，所有帧成功才保存。动画任务额外要求：唯一clock、暂停/离页取消rAF、固定时刻可重复采样。

## 9. 最终验收清单

- [ ] 登录/角色菜单/CSRF/401恢复/403/409/422提示正确。
- [ ] 参数合法与越界、请求防抖/乱序、默认生成均真实。
- [ ] Canvas父子矩阵、透明度、裁剪、时间采样和导出fixture通过。
- [ ] PNG上传/帧进度/失败重试/保存和刷新重开无错绑。
- [ ] 知识/标签/模板/审核/个人空间/历史/收藏流程完整。
- [ ] 语义依据、IR片段/子树、时间轴知识跳转、旧来源复现可演示。
- [ ] 只对服务端实际比较结果显示像素一致，能力未实现不假标启用。
- [ ] 导入导出/日志和无数据/失败/重试状态可用。
- [ ] 页面离开无多余rAF/监听器，构建和必要的交互检查通过。
- [ ] 写docs/前端启动与验收.md：实际命令、演示步骤/截图、未完成基础评分点和选做状态。

测试集中在采样/矩阵、异步请求/导出与一个真实主要流程。静态文案不堆测试；有时间先补体验缺陷，再按[进阶清单](./进阶功能开发清单.md)选做。
