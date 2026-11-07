# 项目脚本

在仓库根目录分别打开两个 PowerShell 终端，运行 `./scripts/dev-backend.ps1` 和 `./scripts/dev-frontend.ps1`。前者在 backend 目录通过 uv 启动 Uvicorn，后者在 frontend 目录启动 Next.js（3000 端口）；均前台运行，Ctrl+C 停止。首次安装步骤见根 README。生产模式先构建，再执行 `./scripts/npm.ps1 run start`。

`setup-node.ps1` 安装 `.node-version` 指定的官方 Node 24 便携版到 `.tools/`，验证校验和与数字签名。`npm.ps1` 优先使用项目 Node，在 frontend 目录执行传入的 npm 参数，例如 `./scripts/npm.ps1 ci`、`./scripts/npm.ps1 run build`；完成后恢复 PATH 和当前目录。前端启动脚本也通过它运行。

开发者 A 后续添加 `seed.py` 幂等种子入口，以及按需的建库、后端检查、备份脚本；开发者 B 添加前端检查或演示辅助脚本。脚本只解决当前任务，不引入额外任务队列或浏览器导出服务。

所有脚本应明确输入、运行目录和失败退出码；数据库、密码、PNG 与备份不提交 Git。种子使用 `shared/demo/` 中经校验的内容，不用自动生成的密码或虚假成功输出冒充完成。
