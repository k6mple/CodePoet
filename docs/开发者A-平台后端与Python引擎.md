# 开发者 A：后端与规则开发任务书（基础演示版）

> 修订：2026-11-05。适用：基础 CRUD 开发者使用 AI 辅助，11/21 12:00 GMT+8 前交付。
>
> 必读：[任务看板](./交付计划与任务看板.md) → [总纲](./双人开发总纲与接口约定.md) → [当前接口](./接口文档.md) / [核心 OpenAPI](./openapi-core.json)。本文取代旧的全量后端/四后端/沙盒必做任务书。

## 1. 你负责什么，先不要做什么

负责 backend/**、shared/schemas/** 和后端脚本：登录权限、普通业务 CRUD、审核/版本、IR/参数校验、小词典规则、上传图片、快照与复现、简单导入导出/审计。

B 负责页面、Canvas、时间轴、PNG 导出上传，并在 shared/demo/** 准备演示知识/模板/规则数据。你提供格式和种子入口，检查关联和约束，不把全部文案准备也压到自己身上。

**主线不实现** Turtle、AST/DSL、进程沙盒、WebGL、无头浏览器调度、通用安全策略编辑器。对应 P1-C/P2 见 [进阶清单](./进阶功能开发清单.md)，不能让 Codex 因完整 OpenAPI 中存在它们就提前开发。

## 2. 足够简单的结构

FastAPI + Pydantic + 同步 SQLAlchemy + SQLite；PNG 放 data/artifacts，数据库保存路径和 hash。路由 → 服务函数 → 数据库/纯计算三层足够，不建立通用 Repository/事件总线。

建议按业务合并文件：

```text
backend/app/
  main.py settings.py db.py
  api/{auth,knowledge,templates,creation,works,admin}.py
  models/{users,knowledge,templates,creations,operations}.py
  services/{permissions,reviews,uploads,snapshots,import_export,audit}.py
  engine/{validate,instantiate,infer,subgraph,sample}.py
backend/tests/
scripts/seed.py
shared/schemas/
```

不强制文件名一一相同；只要 B 能根据核心接口调用。少量数据库迁移，幂等种子，配置从环境读；data、密码、数据库不入 Git。基础版不要求 Docker 或 Linux。

## 3. P0：11 月 8 日前打通保存闭环

### P0-01/02：启动、登录与隔离

- 建库、健康检查、Cookie 会话、CSRF、登录/退出、me；提供四角色种子账号。
- 注册始终 creator；其他 requestedRole 记录申请，审批在 P1A-01 补齐。
- 固定角色动作白名单与 ownerId 检查。私有 generation/job/work/session/artifact 通过猜 ID 不可访问。
- 密码哈希使用成熟库；会话服务端撤销；写接口验 CSRF；不记录密码/Cookie。
- 给 B 真实可调用样例，先做登录/两用户隔离，不急于用户管理大后台。

验收：登录态刷新有效；退出后访问本人私有数据失败；A 用户读 B 私有资源返回 404，creator 调模板管理返回 403。

### P0-03：默认模板与参数

- 提供一个已发布种子模板：IR 骨架、参数定义、声明式 bindings，generatorRef=ir。
- instantiate 先补默认值，再校验类型/范围/未知参数，绑定 nodeId 的白名单属性，输出规范 IR。
- generation 不可变，保存完整 IR/参数/seed/模板版本和来源快照；它不代表图片已经完成。
- core-demo 固定 800×600，≤200 节点/2000 顶点/4 层，≤16 参数，材质 flat。
- 动画先静态，P1-B 补 ≤5 秒 x/y/rotation/opacity；schema 和返回字段可先固定。

验收：默认参数能生成；半径调节改变 IR；越界/未知参数拒绝；相同输入输出 hash 稳定。

### P0-04：浏览器上传图片并保存作品

本期不由后端启动 Chromium。B 绘制，A 验证文件并持久化：

1. POST /render-jobs 接 generationId、backend=canvas2d、executionMode=browser_upload、timesMs、rendererVersion、clientEnvironment。
2. 基础 generation 必须本人拥有，后端/版本必须与 capabilities 声明匹配；创建 running/awaiting_frames 任务，expiresAt=创建后 5 分钟。
3. 每用户最多两个未完成上传任务。检查时顺便将过期任务标 timed_out，code=UPLOAD_EXPIRED；不需要 Celery/定时服务。
4. POST /render-jobs/{id}/frames 接 multipart file/timeMs/irHash，Cookie/CSRF 和所有权仍必检。
5. 文件≤5 MiB，整个 multipart≤6 MiB。忽略客户端文件名；只接受真实 PNG，先检查尺寸/像素总量，再解码；尺寸必须等于 viewport。
6. timeMs 必须在任务预期时刻；irHash 必须等于数据库 generation；hash 服务端计算。不能相信客户端传的文件路径/hash。
7. 同时刻相同内容重复上传幂等返回当前任务，不同内容返回 FRAME_ALREADY_EXISTS/409。
8. 帧齐全后生成 manifest 和质量统计，状态变 succeeded/completed；不完整时不返回成功作品。
9. POST /works 在事务内核对本人 generation 与成功任务、IR hash/帧/尺寸，创建 Work + private 首个版本，返回 {work,version}。
10. GET /work-versions/{id} 返回版本、generation、renderJob；GET /artifacts/{id} 受权限保护，不能直接暴露静态 data 目录。

静态只取 [0]。动态在 P1-B 扩成 [0,floor(duration/2),duration]；最多 3 帧。失败不留下可公开的半成品。文件先写临时位置，DB 提交失败清理本次文件，不删除别人的目录。

验收：浏览器绘制的真实 PNG 可保存；刷新重开；绑定别人的 job 或错误 generation 返回 409/404；假 PNG、尺寸错误、超限文件被拒绝。

## 4. P1-A：核心 CRUD 和高覆盖业务

这部分优先复用表/服务函数，不发明复杂基础设施。

### P1A-01：角色申请和个人资料

admin 用户列表/待审批申请，role-decision 带 expectedRevision、approved、role、reason。role 必须匹配待申请角色，禁止自己批准。PATCH /me 只改 nickname/bio；不允许通过请求字段提权。

四角色：creator 普通创作；developer 自己模板编辑；expert 知识维护与他人审核；admin 管理与审批。所有角色可普通创作，审核禁止自审。

### P1A-02：知识、本体和标签

- person/story/concept/symbol，名称、别名、简介/正文、来源、conceptIds/tagIds、关系、timeTheme、status。
- 概念/标签单父树，改父节点前防环；实体关联概念，不只写无引用的文字。
- effectiveTagIds=直接标签+祖先；维护互斥标签对，保存时返回具体冲突。
- 小数据量允许在服务中遍历全部 active 节点做检查，不用图数据库/递归推理框架。
- active 子节点存在时不能停用父概念/标签；知识/标签引用采用软停用，历史 generation 快照不修改。
- 写入均审计；来源为说明性记录，不做自动抓取或 AI 生成史实。

验收：一个真实典故可以录入、改标签/关系；继承/互斥、防环可演示；旧作品仍显示旧知识内容。

### P1A-03/04：模板版本和审核

- Template 保存工作元数据；TemplateVersion 保存当时 metadata、IR、parameterSchema、bindings、constraints。
- 参数≤16：number/integer/enum/boolean/color；min/max、enum、lessThan、布尔互斥即可。
- generatorRef 仅 ir，fragmentSource=null。不必编写内置模板生成器或动态表达式解释器。
- draft/rejected 可编辑；submitted 不可修改；published 只读，修改必须克隆新版本。
- submit 重新校验默认值、IR、参数绑定、知识引用。审核在事务内保存记录/修改状态/公开指针，拒绝原因必填。
- 禁止审本人提交版本；批准新版本不覆盖旧版快照；archive 清除当前公开指针，不破坏已有作品。
- A提供两份只读版本JSON，B在页面比对字段/节点/参数；专用compare接口为P1-C，不必先实现。
- 作者/管理员可私有预览 draft/rejected，generation.unpublishedSource=true，派生保持该标记，禁止提交公开作品。
- 作品审核复用 Review：targetType=work_version，目标为确定版本；新版本待审时旧公开版继续展示。

验收：未审核模板普通用户不能用；退回→修订→送审→他人通过；published PATCH 失败；旧作品不被新版本修改。

### P1A-05：个人空间、快照与收藏

- Session 保存完整编辑现场，Snapshot 保存不可变 JSON；每次用户显式保存session时由服务端追加snapshot，不做每次击键历史；不必实现独立POST snapshots接口。
- PATCH 带 expectedRevision；显式PATCH保存现场时同事务复制state为快照；restore 追加恢复前/后快照，更新当前现场，不覆盖旧记录。
- WorkVersion 固定 generation+renderJob；基于旧版继续编辑创建新 generation/版本。
- 收藏 userId/workId 唯一；PUT/DELETE 幂等。私有列表只返回本人，public 只返回审核通过版本。
- 所有编辑对象 revision；冲突返回 409，不让最后一个请求悄悄覆盖历史。

验收：保存两次参数，能恢复前一次，旧历史还在；收藏刷新存在；A 不能读 B 的快照/历史。

### P1A-06：组合检索

关键词、知识实体、继承标签、视觉符号、backend、timeTheme；跨维度 AND、维度内部 OR。只按当前可见/已发布版本检索，分页20最大100。当前 backend=canvas2d 才有可用模板，其他后端筛选为空，不伪造结果。

### P1A-07：有限导入导出与审计

- 小 JSON 包≤5 MiB，类型固定 concepts/tags/entities/rules/templates/workSnapshots；当前一次最多 100 条/类型。
- 基础版仅全库按类型导出，不做 ids 选择/自动跨库合并；导出前把本库相关引用一并包含，实际数量记 manifest。
- 导入支持 concepts/tags/entities/rules/templates，ID 冲突整批拒绝，模板落 draft、owner 为操作管理员；workSnapshots 只导出备份，暂不导入成品。
- validate 结构/引用/树/标签/IR/参数并暂存内容 hash；apply 用 validationId+expectedDataRevision，10 分钟内、同管理员、事务重验，失败无部分写入。
- 当前数据量小，可在一个 SQLite 事务内完成。不要开发智能覆盖、压缩包解压、跨用户账户恢复。
- 审计写 actor/action/target/version/requestId、输入/变更摘要、结果，并关联 generation/job/work；登录密码/令牌永不写入。

验收：正确小包可在干净测试库导入；非法引用或重复 ID 不部分写入；用 requestId 找到知识修改/模板审核/作品保存。

## 5. P1-B：必须保留的关键特色

### P1B-01：小型确定性语义推理

实现简单可测的流水线，不依赖 LLM：

1. text/entityIds 至少一项有效，长度受限。
2. 名称/别名最长匹配，取实体概念/有效标签；未知文本返回 NO_SEMANTIC_MATCH。
3. 规则 match 关键词/实体/概念/标签，priority 降序、ruleId 升序。
4. 选择符号模板、主/辅色参数、构图参数；冲突按优先级并记原因。
5. motion=none/sway/reveal 转为有限的合法 IR 轨道，≤5秒。
6. 实例化后再次校验，保存命中实体/规则的当时内容、版本和可读依据。

三类模板莲/竹/叙事即可。必须证明实体、理念规则能组合，不能硬编码整句文本→一张固定图；规则禁用/改优先级影响新生成但不改旧快照。

### P1B-02：IR 片段、子树与裁剪

纯函数 validate/instantiate/sample/extract_subgraph/replace_subgraph：

- group/circle/polygon/polyline，严格 ID/parent/深度/图形/动画引用检查；规范化默认值。
- replacementNodes 保留旧根 ID/parent，其余只引用新子树，不能撞外部 ID。
- 被删除节点仍有动画引用返回 IR_DANGLING_REFERENCE，用户在完整 IR 编辑里清理；不做自动修复。
- 子图返回必要祖先，防止局部坐标移动；viewport 只裁剪，不缩放。
- 返回 changedNodeIds/removedNodeIds。只重算被替换子树和依赖数据，B 可清屏重绘整个小画布；不要声称完整增量绘制优化。
- JSON IR 受数据校验，不启动用户代码解释器。

### P1B-03/04：叙事与历史复现

- narrative 含 timeMs/entityIds/caption，实体内容存来源快照；B 连续播放/seek。
- 作品动画保存3采样帧和完整时间轴，PNG/ZIP不是高帧率视频。
- reproduce 接 rendererVersion/clientEnvironment，从原 WorkVersion 旧 IR 和原时刻创建新 browser_upload 任务，不重新推理。
- 新任务属于当前操作者；公开他人作品可复现，但直接保存成自己的作品需先派生本人的 generation。
- 上传帧齐全后 comparison 用 Pillow 解码 RGBA：exactMatch、MAE/255、不同像素比例；按相同时刻配对。
- 环境声明/版本/尺寸不一致返回 compatible=false、reason、指标null；一致也要真算，不按 hash/肉眼直接说通过。
- clientEnvironment 仅浏览器声明，不能声称能证明硬件完全一致；不维护旧二进制运行环境。

验收：改当前知识/规则后，旧作品的 IR/来源仍不变；复现真实比较；人为改一个像素的测试不能被判完全一致。

### P1B-05：最低质量报告

validationReport 记参数合法、节点数量、t=0完全出界节点；上传后 qualityReport 算与背景 RGB 差>3 的像素比例和 emptyOutput。空白给警告，不自动当艺术质量评分。B 展示即可。

## 6. 日期与实际交接

| 时间 | 只做的重点 | 给 B 的具体输入 |
| --- | --- | --- |
| 11/06 | 骨架/登录/IR fixture | 登录格式、默认 IR、错误码、权限 |
| 11/07～08 | 参数、上传帧、作品首版 | generation、renderJob、multipart、work 实际样例 |
| 11/09～10 | 知识/树/标签、模板版本 | CRUD 与约束/继承/版本 JSON |
| 11/11～12 | 审核、个人历史、导入导出/日志 | 状态/冲突、快照恢复和错误包 |
| 11/13～14 | 规则语义、子树/裁剪 | 两组真实推理、修改节点清单 |
| 11/15～16 | 来源/复现/比较、质量/演示数据 | 复现上传任务、差异报告 |
| 11/17 | 修缺口；全过才选做 | 当前验收记录，不默认开新功能 |
| 11/18～20 | 回归、部署/备份 | 最终启动/种子/测试说明 |

任务量不足时先停 P1-C/P2，不砍登录/权限/审核/来源/真实保存。所有阶段以看板ID和验收行为对齐，不按创建了多少空路由算完成。

## 7. 给 Codex 的任务模板

```text
阅读 docs/交付计划与任务看板.md、双人总纲、开发者A任务书、接口文档和 openapi-core.json。
本次只完成 [任务ID]：[具体行为]。允许修改 backend、shared/schemas、后端脚本。
遵循 core-demo，不实现 P1-C/P2，不添加 Turtle/DSL/无头浏览器/Redis。
请求响应例子：[JSON]。成功条件：[可复现步骤]。失败条件：[权限/非法参数/状态冲突]。
在现有结构内实现最小闭环，实际运行相关检查，报告修改、命令、结果和未完成事项。
不要改 frontend 或 shared/demo 内容来掩盖接口问题。
```

上传切片额外要求：校验真实PNG、尺寸/归属/IR hash/时刻和幂等，所有帧完成才 succeeded，不相信客户端路径。审核切片额外要求：禁止自审/公开版本原地修改，事务记录状态和日志。

## 8. 最终验收清单

- [ ] P0 全闭环和两用户隔离，保存后刷新可重开。
- [ ] 四角色/CSRF/审批/自审保护/版本并发正常。
- [ ] 知识/标签树/冲突、模板参数与审核、历史恢复/收藏完整。
- [ ] 语义规则真实可改输出，IR子树/裁剪合法，历史来源不变。
- [ ] PNG上传大小/尺寸/文件类型/重复帧/过期任务可控。
- [ ] 旧 IR 复现和 RGBA 比较真实，不假报同环境完全一致。
- [ ] 导入非法包无部分写入，关键操作可追溯，备份在干净库验证。
- [ ] 核心接口在干净环境启动，锁文件/迁移/种子/命令可用。
- [ ] 写 docs/后端启动与验收.md，记录真实测试、缺口与选做状态。

必要测试优先覆盖权限、状态/版本、参数/IR、上传/持久化、规则和复现；简单静态字段不堆重复测试。选做未完成时，不注册假成功接口，不把 capabilities 改为可用。
