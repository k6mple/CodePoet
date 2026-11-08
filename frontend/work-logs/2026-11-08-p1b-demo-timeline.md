# 工作日志：P1-B 演示数据与叙事时间轴

日期：2026-11-08（GMT+8）
任务 ID：P1B-05、P1B-03（前端可独立验证部分）
状态：部分完成（P1B-05 演示数据/本地校验完成；P1B-03 时间轴播放器已接入 fixture 与作品 IR 重绘；Creator 时间轴和后端导入/生成验收待后续）

## 开始记录

- 目标：补齐 `shared/demo` 的廉洁文化演示内容与 IR fixtures（12 条知识、3 个模板、8 条规则、叙事/动画/裁剪样例），并实现可复用的 Canvas 时间轴播放器（play/pause/seek、叙事标记/caption、单一 clock），在 `/fixture` 和作品详情的“按保存 IR 重绘”区域真实验证。
- 允许修改范围：`frontend/**`、`shared/demo/**`。不修改后端、数据库、接口契约或根级规范。
- 初始 Git 状态：分支 `feat/frontend`；最近提交 `13211a9`、`daaaa3b`；工作区干净。新的 P1-B 内容在此基础上追加，不切到 `main`，不触碰后端。
- 执行计划：
  1. 读取 MappingRule/TemplateExport 契约，设计 demo 内容格式。
  2. 编写知识/概念/标签、莲/竹/4 秒叙事模板、8 条规则、动画/叙事/裁剪 fixtures；用脚本校验 JSON/IR/参数绑定基本约束。
  3. 新增 `TimelinePlayer`：自有 canvas + renderScene、唯一 rAF、play/pause/seek、结尾停在 duration、重播从 0、叙事标记和 caption；清理 rAF。
  4. 接入 `/fixture`（选择新增 fixtures/模板 IR，真实播放/定位/导出）和作品详情 IR 重绘区；不改变服务器 PNG 与保存协议。
  5. typecheck/build；真实浏览器检查 fixture 播放/定位/叙事和导出；记录未验证项。
- 已知阻塞：后端模板/生成/知识/规则接口未实现，demo 内容无法通过真实导入/生成验收；本次只做可由 A 后续校验导入的共享数据，以及不依赖后端的 Canvas 时间轴。不得伪造史实来源或假作品图片。
- 设计判断：时间轴作为“作品/模板原始 IR”播放器，与服务器 PNG 明确分离；演示内容引用公版经典文本，艺术隐喻标注，不编造出处。

## 实际改动与决定

### P1B-05 演示数据

- 新增 `shared/demo/knowledge/concepts.json`：8 个单父概念。
- 新增 `shared/demo/knowledge/tags.json`：9 个标签，含清白/浮华互斥对和单父继承。
- 新增 `shared/demo/knowledge/knowledge.json`：12 条知识（莲、竹、水、石灰、墨梅、包拯、海瑞、羊续悬鱼、杨震四知、陶母退鱼、于成龙、廉洁），含别名、摘要、正文、公版经典来源、概念/标签和知识关系。
- 新增 `shared/demo/templates/lotus.json`、`bamboo.json`、`narrative.json`：
  - 莲·清正：八瓣莲、清涟水线、花茎、叶片；5 个参数、29 个声明式绑定；published v1 + archived v2。
  - 竹·守正：三竿修竹、六片竹叶、山石；5 个参数、18 个绑定；published v1 + rejected v2。
  - 四秒·清莲叙事：水线、莲开、日轮移动、4 个 narrative 时间点；4 个参数、14 个绑定；duration 4000。
- 新增 `shared/demo/rules/rules.json`：8 条规则，覆盖莲/竹/水/石灰/墨梅/家风/四知/叙事，含 any/all 匹配、优先级、符号/色板/构图/运动/caption。
- 新增 `shared/demo/fixtures/animation_sequence.json`、`narrative_sequence.json`、`parent_child_crop.json`；保留 `basic_shapes`/`canvas_probe`。
- 新增 `frontend/tests/demo-data-check.mjs`：本地静态检查 JSON 语法、ID/引用、单父树、互斥对称、参数默认值/范围、bindings、IR 节点/深度/动画/叙事时间和规则效果。
- 更新 `shared/demo/README.md`，说明这是 seed/demo 而非已导入数据；A 负责权威校验和导入；陶母条目等来源需内容审核核对。

### P1B-03 时间轴

- 新增 `frontend/src/components/TimelinePlayer.tsx`：
  - 自有 canvas，使用共享 `renderScene`；不把控件、题记画进导出帧。
  - 单一 `requestAnimationFrame` clock；播放/暂停/拖动/回到开始；结束停在 duration，再次播放从 0 开始；组件卸载清理 rAF。
  - 叙事时间点按钮、当前 caption、知识 entity ID；作品详情传入 `linkEntities` 可跳转 `/knowledge/{id}`。
  - 静态 IR 显示“静态画面 · 无时间轴”。
- `/fixture` 改为时间轴预览：新增 animation/narrative/crop fixtures 和三个模板原始 IR 选项；导出仍使用 `exportFrame(ir, durationMs)`，明确导出结束帧、不保存作品。
- 作品详情“按保存 IR 重绘”区域改用 `TimelinePlayer`，与上方服务器保存 PNG 明确分离；保留完整 generation/renderJob 技术档案。
- 新增时间轴 CSS 和 390px 响应式；控件与叙事标记不进入 Canvas 导出。

### 边界

- 没有修改后端、数据库 schema、接口契约或根级规范；没有新增运行依赖；没有 mock 服务器或假作品。
- 未把 `shared/demo` 标为已导入；模板 status/审核记录需 A 的真实 seed/审核流程补齐。

## 验证结果

### 本地数据检查

`node frontend/tests/demo-data-check.mjs`：

```text
demo-data-check: OK
concepts=8 tags=9 knowledge=12 templates=3 rules=8 fixtures=5
```

### 构建

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck`（项目 Node） | 通过 |
| `npm run build`（项目 Node） | 通过；18 个路由，`/fixture` 正常构建 |

### 真实浏览器检查

命令（Playwright 从本机 npx 缓存加载；不启动后端）：

```powershell
$env:NODE_PATH="C:\Users\hp\AppData\Local\npm-cache\_npx\e41f203b7505f1fb\node_modules"
& ".\.tools\node-v24.22.2-win-x64\node.exe" "frontend/work-logs/2026-11-08-p1b-demo-timeline/timeline-check.cjs"
```

结果：7 个步骤、8 张截图；`pageErrors` 为空；4 条 console 错误均为后端未启动的 health/auth 请求；无横向溢出。

实际验证：

1. 默认 probe：canvas 800×600，显示“静态画面 · 无时间轴”。
2. animation_sequence：播放 500ms 时间前进；暂停后 seek 到 1500ms；canvas 像素 `(400,300)=[168,62,50,255]`，与圆点 1500ms 位置一致。
3. narrative_sequence：4 个叙事标记；0ms caption “水润万物，清而不争。”；点击 1500ms 后 caption “出淤泥而不染。”。
4. 结束帧导出：真实 PNG 31527 字节，签名 `[137,80,78,71,13,10,26,10]`，800×600。
5. parent_child_crop：canvas 400×300，viewport 200,150 · 400×300。
6. 莲模板原始 IR：canvas 800×600，静态时间轴。
7. 四秒叙事模板：4 个标记；点击 1200ms 显示“出淤泥而不染，濯清涟而不妖。”。
8. 390px：fixture 页面与动画时间轴控件无横向溢出。

### 未执行/未验证

- A 尚未导入/校验 shared/demo；模板 published/rejected/archived 状态、Review 记录、真实 generation 参数实例化未验证。
- 作品详情 TimelinePlayer 没有真实作品数据可渲染；只通过 fixture 验证组件本身。
- Creator 暂未接入时间轴（仍显示结束帧）；三帧保存的连续性/播放未端到端验证。
- 知识 entity 链接在 fixture 页面不启用；作品详情使用真实 API 后才有可用知识页。
- 陶母退鱼等条目的版本差异需内容审核核对；没有编造 URL 或具体史实细节。

## 结束记录

- 状态：部分完成（P1B-05 演示数据/本地校验完成；P1B-03 时间轴组件与 fixture/作品 IR 接入完成；后端导入/生成和 Creator 时间轴待后续）。
- 未完成事项与下一步：
  1. A 校验/导入 shared/demo，生成模板/规则/知识种子和 rejected/archived 审核记录。
  2. 真实 generation 后验证模板参数实例化、作品详情时间轴和知识跳转。
  3. 将 TimelinePlayer 接入创作台 stage（动画模板），并核对保存三帧与播放一致。
  4. 继续 P1B-01/02/04 或处理 A 的导入反馈。
- Git 提交/推送情况：功能代码、演示数据、本地校验、工作日志与浏览器证据已提交到 `feat/frontend` 的 `0596f7a`；本次日志状态更新随后以 docs 提交记录。未推送、未合并，未修改 `backend/`、`shared/schemas/`、`scripts/`、`docs/` 或 `main`。本地验证服务已停止。
