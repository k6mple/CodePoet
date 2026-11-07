# 工作日志：P1-A 模板库、模板版本与审核台

日期：2026-11-07（GMT+8）
任务 ID：P1A-03、P1A-04（前端部分）
状态：部分完成（前端与真实权限门/404 错误验证完成；模板 CRUD、审核和工作送审成功链路待后端）

## 开始记录

- 目标：实现公开模板库/详情、模板管理与版本编辑（JSON IR/参数、绑定、约束、新建/克隆/送审/归档、版本对比），以及审核台的待审核队列、模板/作品目标预览、批准/拒绝和作品送审入口。继续沿用 P0 的纸白/墨黑/朱红视觉系统。
- 允许修改范围：`frontend/**`。不修改后端、数据库、接口契约或根级规范。
- 初始 Git 状态：分支 `feat/frontend`，工作区干净；最近提交 `e4bf170`、`fbd0e68`。在其基础上继续，不切到 `main`，不触碰后端。
- 执行计划：
  1. 扩展 Template/TemplateVersion/WorkVersion/Review 类型；补模板 JSON 解析、默认值校验、版本对比工具。
  2. 新增 `/templates` 公开模板库与 `/templates/[id]` 详情；支持符号/时间主题检索、参数表、原始 IR 预览、使用模板。
  3. 新增 `/manage/templates` 管理列表与新建模板；`/manage/templates/[id]` 元数据、版本列表、新建/克隆/编辑/送审/归档、版本对比。
  4. 新增 `/reviews` 审核台；加载审核队列、模板/作品目标预览、批准/拒绝、自审禁用。
  5. 在作品详情增加作者送审入口；在 Creator 支持 `?template=` 预选模板；导航与账号页补模板管理/审核台入口。
  6. typecheck/build；用真实 auth 后端验证权限门和真实 404 错误；记录未验证项。
- 已知阻塞：后端当前只有 health/auth；`/templates`、`/template-versions`、`/reviews`、`/work-versions/{id}/submit` 均未实现，数据库没有对应迁移。公开库、管理页和审核台只能验证前端结构、权限门和真实错误反馈，不能宣称 CRUD/审核闭环通过。
- 设计判断：模板库延续“作品档案/展架”语言；管理页采用“列表 + 编辑面板”的工作台结构；审核台采用队列 + 目标预览 + 决定面板；不引入新组件库或假数据。

## 实际改动与决定

### 类型与工具

- 扩展 `Template`（revision/ownerId/workingMetadata/latestVersionId/thumbnailArtifactId/permissions）、`TemplateVersion`（版本号/状态/revision/绑定/约束/审核记录/时间）、`WorkVersion`（revision/createdBy/submittedBy/changeNote/lastReview）和新增 `Review`、`ReviewQueueItem`、`ReviewCreateInput`、`VersionCreateInput` 等契约类型。
- 新增 `utils/template.ts`：元数据草稿转换、文化符号选项、starter IR/参数/绑定、参数 schema 校验、模板版本状态/审核标签、版本内容 diff。
- 新增 `TemplateMetadataForm`：名称、说明、符号复选、时间主题、实体/标签 ID，供新建与元数据编辑复用。

### 模板库与模板管理

- `/templates`：真实 `GET /templates?backend=canvas2d`，检索、时间主题、符号筛片、分页；只展示有 `publishedVersionId` 的 Canvas2D 模板；有真实缩略图时读取受保护 artifact，否则使用由模板符号/名称构成的明确占位，不伪造图片。
- `/templates/[id]`：读取模板与已发布版本，展示元数据、参数表、原始 IR 预览（明确标注未应用参数/绑定）、版本/发布时间；提供“使用此模板创作”链接到 `/?template=<id>`；无公开版本时引导到模板管理。
- `/manage/templates`：按 `templates.create/editOwn/editAny` 权限门；读取本人模板、搜索/分页、新建模板容器；新建失败保留表单并显示真实 API 错误。
- `/manage/templates/[id]`：模板元数据编辑（PATCH /templates，expectedRevision）、重置为服务器值、版本列表；新建版本（starter IR/参数/绑定/约束 JSON）、编辑 draft/rejected、克隆新草稿、送审、发布版本归档、版本对比（变更字段 + 左右 JSON）；JSON 格式化、IR 预览、参数 schema 解析/默认值校验；仅草稿/退回可编辑。
- `Creator` 支持读取 `?template=` 并预选模板；模板详情“使用此模板”进入创作台。

### 审核台与作品送审

- `/reviews`：按 `reviews.decide` 权限门；加载 `/reviews` 队列，待审核/已处理筛选；队列显示目标类型、标题、提交者、提交时间、状态和 `canReview`。
- 审核目标详情：模板版本读取 `/template-versions/{id}`，显示参数/IR 预览；作品版本读取 `/work-versions/{id}`，显示服务器 PNG（若 manifest 有帧）、按保存 IR 重绘、生成/变更信息；失败显示真实错误。
- 审核决定：POST `/reviews`，使用队列 targetRevision；退回必须填写理由；自审由 `canReview` 和用户 ID 双重禁用；成功刷新队列。
- `Works` 作品详情：作者本人且状态 private/rejected 时显示“提交作品审核”，POST `/work-versions/{id}/submit`（expectedRevision）；展示最近审核决定/理由。
- 导航按 permissions 增加“模板库”“模板管理”“审核台”；账号页增加模板管理/审核台入口；在 ≤1180px 把导航收拢到第二行，避免新增入口造成横向溢出。

### 边界

- 未修改后端、数据库 schema、接口契约或根级规范；未新增运行依赖；未使用 mock 成功数据。
- 本地 auth 后端与既有 seed 账号用于验证；数据保存在 Git 忽略的 `data/codepoet.sqlite3`。

## 验证结果

### 构建

| 命令 | 结果 |
| --- | --- |
| `.\scripts\npm.ps1 run typecheck` | 通过 |
| `.\scripts\npm.ps1 run build` | 通过；新增 `/templates`、`/templates/[id]`、`/manage/templates`、`/manage/templates/[id]`、`/reviews`，构建共 14 个路由 |
| Impeccable detector | `[]` |

### 真实浏览器检查

命令（Playwright 从本机 npx 缓存加载；脚本从 `scripts/seed.py` 读取本地演示密码，不保存密码）：

```powershell
$env:NODE_PATH="C:\Users\hp\AppData\Local\npm-cache\_npx\e41f203b7505f1fb\node_modules"
& ".\.tools\node-v24.22.2-win-x64\node.exe" "frontend/work-logs/2026-11-07-p1a-templates-review/p1a-templates-check.cjs"
```

结果：8 个步骤通过；14 张截图；`pageErrors` 为空；`consoleErrors=17`，均为缺失 `/templates`、`/reviews` 等接口的真实 404；所有检查宽度无横向溢出。

实际验证：

1. `developer_demo` 登录后导航显示 模板库/模板管理/知识管理，不显示 审核台/用户管理。
2. `/templates`：表单、符号筛选和真实 404 错误状态渲染；无假模板。
3. `/manage/templates`：本人模板列表请求真实 404；打开“新建模板”，填写名称/说明/选择“莲”，提交后得到真实 404，表单保持可编辑并显示 requestId。
4. `/manage/templates/test-template` 与 `/templates/test-template`：真实 404 错误页和重试。
5. `developer_demo` 访问 `/reviews`：显示“无权访问”，导航无审核台；账号页仅有模板管理/知识管理入口。
6. `expert_demo` 登录后导航增加 审核台，仍无 用户管理；`/reviews` 队列真实 404，待审核/已处理 tabs 显示 `—` 而非假 0，错误可重试。
7. 1024/390 审核台与 390 模板管理新建表单：无横向溢出，导航按断点收拢；专家账号页显示模板管理/审核台入口。

### 未执行/未验证

- `/templates`、`/template-versions`、`/reviews`、`/work-versions/{id}/submit` 未实现：模板公开详情成功状态、模板 CRUD、版本新建/克隆/编辑/送审/归档、版本对比、审核目标预览/批准/拒绝、作品送审成功链路均未验证。
- 版本编辑器、对比面板、审核决定表单和作品送审按钮在真实数据下未渲染；源码与类型检查通过，但不能替代端到端验收。
- 模板缩略图、公开模板卡片、`?template=` 预选在缺少模板数据时无法验证。
- 未使用 mock 或本地假成功。

## 结束记录

- 状态：部分完成（前端与真实权限门/404 错误验证完成；模板 CRUD、审核和工作送审成功链路待后端）。
- 未完成事项与下一步：
  1. A 实现模板/版本、审核队列与决定、作品送审接口及迁移。
  2. 用真实模板和审核数据验证公开库、版本生命周期、对比、自审拦截、退回修订和发布后公开展示。
  3. 重新运行本脚本并补充成功状态截图。
  4. 继续 P1A-05/06/07 或 P1-B。
- Git 提交/推送情况：功能代码、工作日志与浏览器证据已提交到 `feat/frontend` 的 `13211a9`；本次日志状态更新随后以 docs 提交记录。未推送、未合并，未修改 `main`、`backend/`、`shared/`、`scripts/`、`docs/` 或本地运行数据库（Git 忽略）。上一里程碑提交为 `fbd0e68`、`e4bf170`。
