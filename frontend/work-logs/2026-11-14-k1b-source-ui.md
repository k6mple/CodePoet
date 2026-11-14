# 交接：K1-B 外部图谱入口与来源快照展示

日期 / 任务 ID：2026-11-14 / 2026-11-14-k1b-source-ui
代码基线：`origin/pr-23` `d6e92d3` + 本分支 R2/K1 前端提交。已 push 并开 PR [#27](https://github.com/k6mple/CodePoet/pull/27)，未合并。
当前状态：**前端入口与来源快照展示已实现；真实外部来源生成/3实体2关系未联调**（本机没有配置 `CODEPOET_KNOWLEDGE_GRAPH_SOURCES` 的可用来源）。不把 fixture 渲染当作真实 K1 验收。

## 前端变化

- Creator 语义模式新增“外部知识图谱”表单：来源键（默认 `wikidata`，可改）+ 外部实体 ID（如 `Q16528`），调用既有 `POST /knowledge-graph/generations`；入口受 `capabilities.features.knowledgeGraph` 控制，未配置来源时禁用并提示。
- 生成结果沿用现有 `acceptGeneration`，因此来源快照进入 `generation.provenance.externalGraph`；不新增后端接口或字段。
- `GenerationEvidence` 增加外部来源快照展示：来源键、外部实体、抓取时间、数据版本、内容哈希、实体/属性、一跳关系、`mappingDecisions` 实际采纳事实。
- 外部 ID 不写入站内 `entityIds`，也不伪装成 `/knowledge/{id}` 链接；仅 `sourceId=wikidata` 且 ID 形如 `Q\d+` 时提供受控的 `https://www.wikidata.org/wiki/...` 外链，其他来源保持纯文本。
- 明确文案：属性/关系影响叙事、规则或符号，**不承诺改变 PNG 像素**。`Provenance.externalGraph` 与 `Capabilities.features.knowledgeGraph` 已补前端类型。

## 证据与验证边界

- `npm run typecheck`、production build 通过（临时 fixture 页面构建后已删除，最终构建只含正式路由）。
- 用临时 SSR fixture 页面渲染 `GenerationEvidence`，确认来源键、`Q16528`、实体/属性、关系、`mappingDecisions`、受控 Wikidata 外链及“不承诺改变 PNG 像素”均出现；截图/HTML 见 [K1 fixture 截图](2026-11-14-k1b-source-ui/k1-fixture.png) 与 [HTML](2026-11-14-k1b-source-ui/k1-fixture.html)。
- **未执行**：真实 `POST /knowledge-graph/generations`、真实来源（3 实体/2 关系）、作品保存/重开/断网复现、审核/权限链路。fixture 不是真实 API 响应，不能替代 K1-B 验收。

## 待办与责任方

- 部署/后端（A）：配置 `CODEPOET_KNOWLEDGE_GRAPH_SOURCES=wikidata`（及可选映射文件），用真实外部实体跑通 3 实体/2 关系；确认 `externalGraph` 写入 provenance、`dependencySnapshotHash` 包含外部快照、旧作品断网复现不联网。
- 前端（B）：真实来源可用后验证 Creator 入口、错误码（503/504/422/NO_SEMANTIC_MATCH）展示、作品详情来源快照与重开复现；如来源键不是 `wikidata`，确认外链回退为纯文本。
- 集成负责人：K1-B 仍按 C 类条件项记录；未完成真实来源验收前不要把 `knowledgeGraph` 声明为已闭环。

本轮未修改 `backend/`；已 push 分支并开 PR #27，未合并；K1-B 的真实来源验收按职责交由 A/部署负责人。
