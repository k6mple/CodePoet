# 演示数据与联调 fixture（开发者 B）

当前已提供 P1-B 演示内容和本地 Canvas 校验数据。内容按 `docs/openapi-core.json` 和 `docs/接口文档.md` 编写，但**尚未由后端导入或验收**；A 的幂等 seed/导入器负责生成服务端 ID、时间戳、owner、revision、审核记录并做权威校验。

## 当前内容

| 路径 | 内容 |
| --- | --- |
| `knowledge/concepts.json` | 8 个单父本体概念（廉洁文化、清正廉洁、人物典范、典故故事、视觉符号、为民理政、家风家训、慎独修身） |
| `knowledge/tags.json` | 9 个标签，含清白/浮华互斥对、父标签和继承关系 |
| `knowledge/knowledge.json` | 12 条知识：莲、竹、水、石灰、墨梅、包拯、海瑞、羊续悬鱼、杨震四知、陶母退鱼、于成龙、廉洁 |
| `templates/lotus.json` | 莲·清正：八瓣莲、清涟水线、花茎；published 版本 + archived 旧版 |
| `templates/bamboo.json` | 竹·守正：三竿修竹、竹叶、山石；published 版本 + rejected 修订版 |
| `templates/narrative.json` | 四秒·清莲叙事：水线、莲开、日轮移动、4 个 narrative 时间点 |
| `rules/rules.json` | 8 条确定性映射规则，含 any/all 匹配、莲/竹/水/石灰/墨梅/家风/四知/叙事效果 |
| `fixtures/` | `basic_shapes`、`canvas_probe`、`animation_sequence`、`narrative_sequence`、`parent_child_crop` |

## 格式说明

- 这是**演示/seed 数据**，不是 HTTP API 响应，也不是已导入数据库的数据。
- 知识/概念/标签文件保留稳定演示 ID，便于模板、规则和 narrative 交叉引用；实际数据库 ID、owner、revision、时间戳由 A 的导入器生成/映射。
- 模板文件格式为 `{ "template": {...}, "versions": [...] }`。版本包含 metadata、irTemplate、parameterSchema、bindings、constraints 和 status；`publishedVersionId`/`latestVersionId` 是演示指针，A 需按审核/发布规则校正。
- `bamboo.json` 的 v2 为 rejected、`lotus.json` 的 v2 为 archived，用于审核/归档演示；A 需补齐对应 Review 记录，不能只改 status 冒充审核结果。
- 来源引用使用公版经典文本的篇名和短注，`url` 为 null，避免编造链接；`陶母退鱼` 的不同版本文字有差异，导入前请内容审核核对。艺术隐喻和民间叙事（如包拯/海瑞的戏曲化形象）已注明文化概述，不当作未经核对的具体史实。
- 当前 `workSnapshots` 不包含在演示导入中；作品 IR 只作为后续真实生成/保存的说明，不伪造 PNG 或已发布作品。

## 本地检查

```powershell
node frontend/tests/demo-data-check.mjs
```

该脚本检查 JSON 语法、ID/引用、单父树、互斥对称、参数默认值/范围、bindings 节点/属性、IR 节点/深度/动画/叙事时间和规则效果等。它**不能替代 A 的后端校验、数据库导入和真实模板生成**。

## 边界

- 12 条知识、3 个模板、8 条规则是 B 的演示内容职责；知识/规则/模板的权威校验、审核和导入由 A 负责。
- 当前后端只有 health/auth，模板/生成/知识/规则接口尚未实现；不能在验收中宣称这些数据已被真实生成或保存。
- 新增或修改内容时，先运行本地检查，再由 A 用真实导入/审核流程验收。
