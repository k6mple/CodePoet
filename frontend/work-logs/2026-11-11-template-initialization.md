# 工作日志：本地创作台没有模板

日期：2026-11-11（Asia/Shanghai）
任务 ID：local-template-initialization
状态：初始化完成；前后端真实 API 验证通过

## 开始记录

- 目标：解决用户启动前后端后，创作台无法选用模板的问题。
- 允许范围：本日志、.tools 下运行记录/备份，执行现有幂等初始化脚本补齐本地演示数据；不修改前后端源码或契约，不重置密码，不清除用户数据。
- 初始 Git：fix/frontend-main-integration-feedback / 7624ad1；已有未追踪 PR15 审查日志，保留。
- 已确认：8000 后端与 3000 前端代理的 GET /api/v1/templates?pageSize=100 均 HTTP 200、total=0。不是前端代理断开。
- 执行计划：检查现有数据和种子内容 → 备份当前数据库 → 运行既有种子并过滤敏感输出 → 通过前端代理验证模板列表及模板定义。
- 已知信息：此前后端启动只应用迁移，未执行 seed；尚未确认用户页面刷新后的状态。

## 实际改动与结果

- 只读查询确认 templates/template_versions 均无记录，用户表有 11 条；根因是启动时只做迁移，没有导入演示模板。
- 执行 `.tools/backend-runtime-20261111-8000/prepare-database.py`，SQLite backup API 备份当前数据库；随后执行已有 `scripts/seed.py`，不带 --reset-passwords，不改已有账号密码或资料。
- seed stdout 仅在内存接收，过滤后只输出模板创建摘要，未保存演示密码。实际导入「莲」tpl-lotus / tv-lotus-001：published、9 个节点、5 个参数、20 条绑定。
- 真实 GET 8000 后端与 3000 前端代理的 /api/v1/templates?backend=canvas2d&pageSize=100 均 HTTP 200、total=1；两者读取 /api/v1/template-versions/tv-lotus-001 均 200，状态 published，定义可用。
- 没有修改前后端源码、初始化脚本或契约，没有停止现有服务，没有执行前端重设计。未重置密码、删除数据、提交或推送代码。
- 未执行浏览器 UI 和完整生成/保存回归；用户需要刷新已打开的页面以重新获取模板列表。此次验收为真实 API 列表/定义闭环，不将其表述成完整 G0 验收。

## 结束记录

- 已完成：补齐可选用的公开演示模板，验证前端代理与后端一致。
- 下一步：用户刷新 http://127.0.0.1:3000 后选择「莲」；如仍为空再核对用户实际访问地址。
- Git：仅新增本日志，保留已有 PR15 审查日志；未提交、未推送。

## 继续：补齐更多内置模板

- 用户询问还有没有其他模板，继续补齐现有素材，不执行前端重设计。
- 已确认：基础 seed.py 只加载 lotus.template.json；完整素材另有 lotus.json、bamboo.json、narrative.json。seed_demo.py 整体执行会与现有 tpl-lotus 冲突，不使用 --force 或清空。
- 计划：先备份；保留现有「莲」，为「莲·清正」使用独立演示 ID；仅添加缺失知识关联和素材原本标为 published 的模板版本，复用现有导入函数，不将 rejected/archived 版本当成公开版本。通过真实前端代理核对模板列表、定义和生成。
- 状态：进行中。

### 更多模板补齐结果

- 备份当前数据库后，在忽略目录创建纯数据 staging 文件，复用现有 seed_demo.seed_templates 和 import_export 插入函数，仅新增缺失的 8 概念、9 标签、12 知识条目与 3 个演示模板；没有运行 seed_demo --force，没有覆盖任何现有模板或版本。
- 原 tpl-lotus / tv-lotus-001 完整保留；完整莲纹采用独立 tpl-lotus-qingzheng / tv-lotus-qingzheng-1；另有 tpl-bamboo / tv-bamboo-1、tpl-narrative / tv-narrative-1。只选取素材原始 publishedVersionId 对应的 published 版本，未公开 rejected/archived 示例。
- 通过既有真实模板实例化与 IR 校验：莲·清正 17 节点、竹·守正 12 节点、四秒·清莲叙事 16 节点/4000ms，默认参数均合法。该检查不写测试作品或伪造生成结果。
- 运行摘要在 `.tools/backend-runtime-20261111-8000/additional-templates-results.json`。前端代理真实 API 复核列表与全部模板定义，用户刷新页面后可重新获取。
- 本轮没有安装规则或声称语义生成已验收；没有运行浏览器绘制/保存回归。没有修改业务源码、脚本或 shared 素材，未提交、未推送。
- 状态：已完成新增数据与接口/默认参数验证。
