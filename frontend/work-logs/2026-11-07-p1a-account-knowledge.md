# 工作日志：P1-A 账号与知识管理（第一里程碑）

日期：2026-11-07（GMT+8）
任务 ID：P1A-01、P1A-02（前端部分）
状态：部分完成（P1A-01 前端与真实 auth/profile 验证完成；管理员审批与 P1A-02 被后端接口阻塞）

## 开始记录

- 目标：实现 P1A-01 的注册/角色申请、个人资料、管理员角色审批页；实现 P1A-02 的知识、概念、标签管理页（tabs、列表、表单、CRUD、继承/冲突展示）。继续沿用 P0 的纸白/墨黑/朱红视觉系统。
- 允许修改范围：`frontend/**`。不修改后端、数据库、接口契约或根级规范；不新增 P1-B 功能。
- 初始 Git 状态：P0 视觉重设计的修改尚未提交，工作区包含已修改的前端文件、未跟踪的新组件与 `frontend/work-logs/`；本次在其基础上追加，不覆盖他人的既有改动。
- 执行计划：
  1. 扩展 `types/core.ts` 的 User/RoleApplication/知识/概念/标签模型；补充格式化、分页与树工具。
  2. 扩展 SessionProvider：注册、登录错误归对话框、updateUser/refreshUser、按权限显示知识管理与用户管理入口。
  3. 新增 `/account`：个人资料 PATCH /me、角色申请状态、权限与账号信息。
  4. 新增 `/admin/users`：待审批角色列表、批准/拒绝、自审禁用、分页/搜索/错误状态。
  5. 新增 `/manage`：知识/概念/标签 tabs；知识 CRUD、来源/别名/关系/概念与标签绑定；概念/标签树、父节点选择、冲突与继承展示。
  6. 补齐响应式、键盘可访问性、加载/空/错误状态；运行 typecheck/build。
  7. 若本机后端可启动：应用迁移与 seed，真实验证注册/登录/角色申请/资料保存；P1A-02 与管理员列表接口未实现时如实记录 404/500，不使用 mock。
- 已知阻塞：后端当前只有 health/auth。`/admin/users`、`/admin/users/{id}/role-decision`、`/knowledge`、`/ontology/concepts`、`/tags` 均未注册；数据库迁移也没有 concepts/tags 表。因此 P1A-02 与管理员审批无法真实闭环，只能验证前端页面、权限门和真实错误反馈。
- 设计判断：新增页面继续使用既有 token、宋体标题、等宽技术信息和细线分隔；管理页采用“列表 + 编辑面板”的工作台结构，不引入新组件库，不使用假数据或假成功状态。

## 实际改动与决定

### 账号与登录

- `LoginDialog` 增加登录/注册双模式：用户名、密码、可选昵称、申请角色；注册后不自动登录，切换到登录模式并保留用户名；成功/失败提示在表单附近；角色申请明确说明“审批前仍是普通创作者”。
- 注册请求只发送契约允许的字段；用户名为 3–32 位字母数字下划线，密码至少 10 位；`requestedRole` 仅提供模板开发者/领域专家，管理员账号由 seed 初始化。
- 登录错误改由对话框内处理；SessionProvider 提供 `updateUser`、`refreshUser`，登录成功后关闭对话框；认证失效仍保留现场并在原对话框提示。
- 头部账号区改为指向 `/account` 的链接；按服务器返回的 permissions 条件显示“知识管理”“用户管理”导航，不硬编码角色。
- 新增 `/account`：昵称/简介 PATCH /me（带 expectedRevision）、角色申请状态、账号信息、权限折叠列表、管理员/知识管理入口；409 时可重新加载账号。

### 管理员角色审批

- 新增 `/admin/users`：搜索、待审批/全部用户过滤、分页、待审批列表、行内选择、审批理由、批准/拒绝；禁止审批自己；使用真实 `/admin/users` 与 role-decision 契约，服务端未实现时展示真实错误与技术详情。
- 页面按 `users.approveRole` 权限门控制；普通创作者直接访问显示“无权访问”，不出现管理入口。

### 知识、概念与标签

- 新增 `/manage`：知识/概念/标签三个真实 tabs，支持方向键切换；按 `knowledge.write`、`ontology.write`、`tags.write` 分别渲染面板和导航。
- 知识面板：检索、类型筛选、分页、新增/编辑区；表单覆盖类型、名称、别名、摘要、正文、时间主题、来源引用、关联概念/标签、知识关系、状态；来源 URL 前端校验 http/https；关系目标用 datalist 选择最近 100 条实体或直接输入 ID。
- 概念面板：单父树展示、父节点下拉排除自身及后代、新增/编辑/停用；展示 revision 和更新时间。
- 标签面板：单父树、继承标签、互斥标签多选、新增/编辑/停用；说明互斥对由服务端对称维护。
- 列表/参考数据加载失败不再显示“还没有数据”，分别显示加载、错误、空状态；编辑区即使列表接口失败仍可打开，以便创建/重试并看到真实 API 错误。
- 所有写操作保留服务端校验与 revision；没有本地假数据、mock 成功或前端权威权限判断。

### 共享与样式

- 新增 `api/pages.ts`（分页取全量参考数据）、`utils/tree.ts`（单父树扁平化、后代计算）、`Feedback` 复用、`format` 增加角色/状态/知识类型/关系谓词标签。
- 扩展 `types/core.ts` 的 User/RoleApplication 与知识/概念/标签模型；修改 `api/client.ts` 的 404/409 错误建议为通用文案。
- `main.css` 追加账号、审批、知识管理工作台样式和 1180/1000/760 响应式规则；沿用既有 token，不新增设计世界。
- 未修改后端、数据库 schema、接口契约或 P0 的 `renderScene`/上传保存逻辑；没有新增运行依赖。
- 为真实验证 auth/profile，在本地忽略的 `data/codepoet.sqlite3` 上应用了现有迁移和 seed，并启动本地 FastAPI；这是本地运行数据，不是代码修改。`concepts`/`tags` 表仍不存在。

## 验证结果

### 构建

| 命令 | 结果 |
| --- | --- |
| `.\scripts\npm.ps1 run typecheck` | 通过 |
| `.\scripts\npm.ps1 run build` | 通过；新增 `/account`、`/admin/users`、`/manage` 路由，构建输出 9 个页面 |
| `backend\.venv\Scripts\python.exe scripts\migrate.py` | 成功应用 001/002；当前表 users/auth_sessions/knowledge_entities/templates/template_versions |
| `backend\.venv\Scripts\python.exe scripts\seed.py` | 成功创建四个演示账号（本地忽略数据库） |
| `backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend` | 启动成功；`GET /api/v1/health` 返回 200 |

### 真实浏览器检查

命令（Playwright 从本机 npx 缓存加载，不写入项目依赖；脚本从本地 seed 源读取演示密码，不保存密码）：

```powershell
$env:NODE_PATH="C:\Users\hp\AppData\Local\npm-cache\_npx\e41f203b7505f1fb\node_modules"
& ".\.tools\node-v24.22.2-win-x64\node.exe" "frontend/work-logs/2026-11-07-p1a-account-knowledge/p1a-auth-check.cjs"
```

结果：18 个步骤通过；19 张截图；`pageErrors` 为空；`consoleErrors=20`，均为后端未实现 `/admin/users`、`/knowledge`、`/ontology/concepts`、`/tags` 的真实 404；所有检查宽度无横向溢出；Impeccable detector 返回 `[]`。

实际验证通过的链路：

1. 注册新用户并申请模板开发者：成功，提示“角色申请已提交，管理员审批前你仍是普通创作者”。
2. 登录新账号：成功。
3. `/account`：显示当前角色普通创作者、申请角色模板开发者、状态待审批。
4. 修改昵称/简介：PATCH /me 成功；刷新页面后昵称和简介仍保留，revision 从 1 变为 2。
5. 退出登录：成功。
6. 管理员 admin 登录：成功；头部出现“知识管理”“用户管理”。
7. 权限门：创作者直接访问 `/manage`、`/admin/users` 显示“无权访问”，导航无管理入口；管理员可进入。
8. `/admin/users`：页面和权限门渲染；`GET /admin/users` 返回真实 404，显示“用户列表加载失败”与 requestId。
9. `/manage`：三个 tabs 渲染并可切换；知识/概念/标签列表与参考数据返回真实 404，显示错误而非空数据。
10. 知识表单：打开新增编辑器；来源 URL 填 `not-a-url` 被前端拒绝；改成 `https://example.com/source` 后提交得到真实 404/requestId，表单保持可编辑。
11. 概念/标签表单：可打开、填写、提交；返回真实 404 并保留错误提示。

### 未执行/未验证

- `GET /admin/users` 与 role-decision 未实现：管理员审批列表、批准/拒绝、自审服务端拦截未验证；审批表单的“有用户”状态未渲染。
- `/knowledge`、`/ontology/concepts`、`/tags` 未实现，数据库也没有 concepts/tags 表：知识/概念/标签的真实创建、编辑、停用、继承标签、互斥冲突、父树防环、分页筛选未验证。
- 没有任何 mock 数据或本地假成功；截图中的列表/表单错误是真实后端 404。
- 未验证两用户私有隔离、角色审批后权限变化、会话过期后保留现场重试等跨用户场景。
- 本地验证创建了临时测试账号；只在被 Git 忽略的本地 SQLite 中，不进入版本库，也不在日志中记录密码。

## 结束记录

- 状态：部分完成（P1A-01 前端与真实 auth/profile 验证完成；管理员审批与 P1A-02 被后端接口阻塞）。
- 未完成事项与下一步：
  1. A 实现 `/admin/users`、`/admin/users/{id}/role-decision`，并补齐 `/knowledge`、`/ontology/concepts`、`/tags` 及 concepts/tags 迁移。
  2. 用真实数据验证角色审批、知识/概念/标签 CRUD、继承/互斥/防环和分页筛选。
  3. 更新 `shared/demo` 的知识/概念/标签示例，按核心契约由 A 校验导入。
  4. 重新运行同一 Playwright 脚本并补充有数据成功状态截图。
- Git 提交/推送情况：功能代码、前端工作日志与浏览器证据已提交到 `feat/frontend` 的 `fbd0e68`；本日志的提交状态更新随后以 docs 提交记录。未推送、未合并，未修改 backend/、shared/、scripts/、docs/ 或 main 分支。
