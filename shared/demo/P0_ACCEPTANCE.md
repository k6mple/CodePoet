# P0 前端验收记录

日期：2026-11-07（GMT+8）。分支：`feat/frontend`。

**前端实现、构建和真实 Canvas 检查通过；P0 全栈闭环尚未验收通过。没有 mock API 或假作品。**

## 修改与任务状态

仅修改 `frontend/**` 和本记录（`shared/demo/**`）。保留开始前已暂存的 `frontend/src/api/login.ts` 删除，未修改后端、根脚本、OpenAPI、看板或总纲。

| 任务 | 前端实现 | 实际验收 |
| --- | --- | --- |
| P0-01 | 复用启动检查，新增唯一 renderScene、fixture/PNG | 构建/真实 Canvas 通过，后端启动受阻 |
| P0-02 | Cookie/CSRF、登录/退出、me恢复、错误/requestId | 代码完成，真实认证/两用户隔离未验证 |
| P0-03 | 已发布模板、默认生成、五类参数、300ms防抖、取消/序号保护、保留旧图 | 代码完成，后端能力/模板/生成接口未注册 |
| P0-04 | toBlob、逐帧上传进度、原任务重试、成功后保存、PNG/IR重开 | 代码完成，后端上传/作品/产物接口未注册 |

仓库原先没有 renderScene，因此新增并共用于预览、fixture和PNG导出。复用 StartupStatus、原生表单；没有复杂编辑器、通用框架、新运行依赖或Playwright实现。

## 实际运行

- `scripts/npm.ps1 run typecheck`：通过。
- `scripts/npm.ps1 run build`：通过。首次构建的共享fixture解析失败，设置Turbopack/文件追踪根目录为仓库根后解决。
- `scripts/npm.ps1 run start`：生产服务127.0.0.1:3000启动；首页、fixture、作品详情路由可访问。
- `/fixture`：画布800×600，定位像素 `(10,10)=[55,138,221,255]`、`(760,560)=[216,90,48,255]`、`(410,320)=[29,158,117,255]`。实际toBlob得到15572字节PNG，签名 `[137,80,78,71,13,10,26,10]`，PNG头尺寸800×600。
- Canvas浏览器检查10/10通过：父T×R×S/子平移、祖先opacity乘积alpha=64、线宽不随缩放、动画0/1000/2000ms、采样时刻、viewport偏移、真实PNG尺寸40×30（425字节）、null fill/stroke透明并清除旧帧。
- 窄窗口实际innerWidth=501、documentWidth=486，无横向溢出；浏览器最小窗口限制，未宣称390px设备验证。localStorage为空。
- 后端断开时，首页和作品详情真实显示HTTP500，未显示伪造产物或保存成功。

后端启动实际失败：

```text
backend/.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
error: uv trampoline failed to spawn Python child process
Caused by: permission denied (os error 5)

scripts/dev-backend.ps1
Failed to initialize cache at C:\Users\hp\AppData\Local\uv\cache
failed to create directory ... (os error 183)
```

源码核对：main.py只注册health/auth；api/templates.py、creation.py、works.py仍是待实现占位。修复本机运行时后仍需A实现这些P0接口与模板seed。本次遵守目录边界，未修改后端。

## 实际输入输出

以下为生产浏览器经同源代理实际请求。后端未启动，响应是纯文本，不能当作业务JSON：

| 实际GET | 状态 | 实际响应体 |
| --- | --- | --- |
| `/api/v1/health` | 500 | `Internal Server Error` |
| `/api/v1/auth/csrf` | 500 | `Internal Server Error` |
| `/api/v1/templates?backend=canvas2d&pageSize=100` | 500 | `Internal Server Error` |
| `/api/v1/engine/capabilities` | 500 | `Internal Server Error` |

本次没有成功generationId/renderJobId/workVersionId，未编造成功响应。以下是已连接的契约请求结构，**不是本次API成功记录**，ID取自真实前序响应：

```json
{"mode":"template","templateVersionId":"<publishedVersionId>","params":{},"backend":"canvas2d","seed":42}
```

params来自TemplateVersion.parameterSchema默认值/控件；模板名称来自metadata.name。

```json
{"generationId":"<generation.id>","backend":"canvas2d","timesMs":[0],"executionMode":"browser_upload","rendererVersion":"codepoet-canvas-1.0.0","clientEnvironment":{"userAgent":"<navigator.userAgent>","deviceScaleFactor":1}}
```

静态timesMs=[0]，动画=[0,floor(duration/2),duration]。上传是multipart：file=真实PNG、timeMs=采样时刻、irHash=generation.irHash；附Cookie/CSRF，不手动设置Content-Type。

```json
{"title":"我的 Canvas 作品","generationId":"<generation.id>","renderJobId":"<成功任务ID>"}
```

## 后端恢复后的验收步骤（待执行）

1. 启动/迁移/seed后端，首页登录creator_demo（密码见scripts/seed.py）；刷新由me恢复，退出后不能保存/读取本人列表。检查HttpOnly Cookie和CSRF轮换。
2. 选已发布Canvas模板默认生成；操作number/integer/enum/boolean/color，确认300ms后真实IR和画面变化。快速连续修改并减慢网络，旧响应不得覆盖新画面。
3. 输入越界/非整数，旧帧保留并提示“修改未应用”，禁止保存；恢复默认可重试。对跨字段422核对fieldErrors/requestId。
4. 保存：核对render-jobs中的generation/times/hash/rendererVersion，frames为真实PNG multipart；全部帧成功才POST works。保存时参数/模板禁用、重复点击不并发。
5. 打开返回作品版本链接，复制到新标签并刷新；真实GET work-versions/artifacts，显示服务端PNG、完整generation/IR，PNG尺寸等于viewport。
6. 上传断网后不显示成功；恢复后查询并重试原任务，同hash重传幂等。失败/过期/409可选“下次新建上传任务”。works请求超时先查我的作品，避免重复创建。
7. 会话过期401后，本页参数/任务ID保留，同用户登录可重试；403权限提示、404不可访问、409冲突、422字段错误。以上失败恢复均需真实API验证。
8. 第二用户访问第一用户的generation/job/work-version/artifact应由后端返回404。不能用前端按钮隐藏代替权限验收。

## 未完成与限制

- Git：已检查status/log/diff，尝试仅暂存本次文件时被环境拒绝：`Unable to create .git/index.lock: Permission denied`。当前.git只读，未暂存/提交本次变更；既有login.ts暂存删除保持原样。
- 登录、参数生成、上传持久化、刷新重开、两用户隔离和401/403/404/409/422/上传失败恢复的真实联调受阻，不能宣布G0/P0通过。
- 未保存编辑现场/checkpoint仅存当前页内存，刷新会丢失；只有服务器确认保存的作品可重开。
- works创建契约无幂等键，响应丢失后需先查本人作品；没有承诺自动消除重复作品。
- 注册/角色申请/管理、语义、IR编辑、连续时间轴、复现比较和完整演示数据为后续任务。无DSL、CSS/WebGL/Turtle渲染器或无头渲染。
