<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## CodePoet 前端工作日志

本节仅适用于前端开发及前端相关任务，日志只记录前端工作。开始任务先读取仓库根 AGENTS.md 与 `work-logs/README.md`，核对 Git 并阅读相关共享交接和本机近期日志。

- 写操作前先在 `local-work-logs/YYYY-MM-DD-主题.md` 建立普通日志，记录目标、范围、计划、初始分支/状态和阻塞；结束或中断前补充实际改动、检查/真实API结果、未完成事项和实际提交/推送状态。该目录被 Git 忽略，首次使用可创建，不强制提交。
- 仅在需要后端/其他协作者查看时，在 `work-logs/` 提炼共享交接：接口差异、复现证据、影响范围、验收限制、待办及责任方。必要证据随共享记录保留，共享文件不得依赖或链接本地忽略目录。
- 原始截图、过程输出和个人 Git 操作记录默认放本地；运行已有检查脚本时显式指定本地 OUTPUT_DIR，避免默认输出覆盖共享历史证据。历史日志不代表当前代码已验收；本地也禁止保存认证密钥。

自定义要求保留在 Next.js 自动维护区块之外；不要删除或改写上方 nextjs-agent-rules 区块。

## 开发注释

开发过程中适当添加少量注释，在关键处添加即可
