# MiniCodex · Windows 编程助手

当前版本 **v0.5.3 / 当前阶段收尾版**。新增模型等待响应时的流光文字与呼吸光点；输出、工具调用或本轮结束后停止。沿用对话行菜单、输入框三档权限、Enter 发送/Shift+Enter 换行、配置保存与会话恢复。按用户要求暂时结束功能迭代，Docker 实机功能继续暂缓。

## 开始使用

GitHub 源码仓库不包含本机的 `dist/` 安装包或 `.venv/` 开发环境。首次从源码运行，请按下方“开发与验证”安装依赖；Windows 构建产物可在成功运行的 GitHub Actions 中下载。

运行 `dist/0.5.3/MiniCodex-0.5.3-Setup.exe` 安装，或双击 `dist/0.5.3/MiniCodex/MiniCodex.exe`（保留整个目录及 `_internal`）。安装包供当前用户使用，开始菜单提供启动和卸载入口，桌面快捷方式可选。升级前关闭客户端；默认卸载保留用户数据，详见 [M4 验收](docs/m4-status.md)。当前未做代码签名。

1. 打开本地项目，在模型设置中填写地址、模型 ID 与 Key。DeepSeek 可点击“填入 DeepSeek 官方配置”，地址 `https://api.deepseek.com`、模型 `deepseek-flash`。OpenAI 地址为 `https://api.openai.com/v1`。
2. 输入框左下角选择权限：只读仅查看；默认权限允许修改和原生命令、逐次审批；完全访问自动执行现有工具操作。原生命令具有当前用户权限，无 OS 沙箱。修改权限沿用当前对话，从下一轮生效；运行中不能切换。
3. 输入“修复这个边界错误，并运行测试”。查看 diff 后批准补丁；核对完整 argv、cwd、执行模式和超时后批准命令。
4. 中间按步骤显示模型回答和工具状态；点击工具行展开参数、结果、diff 或命令日志。默认权限的审批预览和批准/拒绝按钮位于输入框上方。测试失败会交回模型继续修复，受预算限制；停止按钮等待清理后终止。输入框右下角显示模型名称，点击可打开模型设置。

左侧“新建对话”使用同一项目、独立历史；首次任务自动生成标题。每条对话右侧的三个点菜单提供重命名和删除；列表内 F2 也可重命名。手动名称不会被任务覆盖。删除前确认，运行中禁用；删除当前对话后切换到其他可用对话，删除最后一条后可新建对话。用户头像与 AI 图案分开显示，步骤、工具结果和轮次统计随会话保留。

每轮结束，包括失败、停止或达到上限，显示用时、编辑文件数、模型和工具调用次数。用时包含审批等待。文件数为本轮成功补丁与原生命令前后内容变化的不同项目文件，包含新增/删除；同一文件重复修改只计一次，拒绝或未应用的补丁不计。原生命令非零退出或取消前的已确认改动仍计入。统计沿用受保护项目快照的范围和大小限制，不含过滤目录、敏感文件及工作区外文件；统计失败显示“至少已编辑”及统计不完整提示。命令前后比较不能区分同时发生的外部编辑。

默认离线模式只演示真实列文件流程，不理解任务。自动修复需配置支持工具调用的真实模型。项目 Python、Git、pytest 等工具链须另行安装，客户端运行时不等于目标项目工具链。

模型接收对话、读取内容和工具回执，按服务计费。会话和普通设置默认保存在 `%LOCALAPPDATA%/MiniCodex`，重启后恢复。Key 默认仅在内存，勾选“保存到 Windows 凭据管理器”才跨重启保存，JSON 不含明文 Key。设置中可忘记凭据或重置配置；权限和审批策略也会恢复。更换服务、模型、Key 或连接参数时新建会话；权限和预算变更继续当前对话。恢复旧会话沿用当前设置。远程 HTTPS、TLS 验证、无重定向，不继承系统/环境代理。

SQLite 保存本机对话正文、工具调用、展示事件与统计；JSONL 默认只追加事件元数据，不含 Prompt/正文。已知 API Key 和常见 `sk-` 密钥会脱敏，本机数据库不是加密存储。可通过 `--data-dir` 指定数据目录；同一目录禁止桌面端双开。Ctrl+Shift+E 可导出当前对话的事件元数据；界面不再提供正文导出勾选框。

上次进程异常退出的任务显示“结果未知”，补齐工具回执后才允许继续；不沿用审批，也不自动重放工具。请先检查文件/diff 再发起新任务。正常停止和正常关窗保存为“已停止”。旧 v0.3.1 的内存会话无法自动迁移。

## 工具与执行

| 工具 | 行为 |
|---|---|
| list_files / read_file / search_code | 相对工作区列、按行读、字面搜索，保持 M1 路径和输出限制 |
| git_status / git_diff | 查看允许文件的状态和未暂存 diff，不修改索引 |
| apply_patch | 仅写入模式开放，标准 unified diff，按权限审批或自动执行，严格校验原文并应用 |
| run_command | 仅原生/Docker 模式开放，明确 executable + argv；默认权限逐次审批，完全访问自动执行 |

补丁支持已有目录内的文件新增、修改、删除与多文件变更；最多 20 个文件、100000 字符。保留编码/BOM/LF/CRLF；拒绝二进制、混合换行、重命名、缺失父目录、模糊匹配与重复目标。审批后原文变化失效；异常/取消尝试回滚，回滚不明则报未知结果。不是跨文件崩溃原子事务，也不提供无条件一键回滚。

读写拒绝路径穿越、绝对/UNC/ADS、敏感路径、symlink/junction/reparse point；写入额外拒绝 Windows 设备名、末尾点/空格和硬链接。写入持有父目录与文件锁并验证实际句柄。已有用户修改按原文核对，绝不调用 reset/checkout 清除。

原生执行使用 Windows Job Object：暂停创建→加入 Job→启动；超时、取消和正常退出都清理 Job 内后代。stdout/stderr 分别保留 32000 字节并持续排空超限输出，实时显示日志；终止前确认清理。不继承 API Key、凭据/Python 注入环境变量。项目脚本仍具有当前用户权限，可能访问其他宿主文件和网络；Job Object 不是 OS 沙箱。

命令审批绑定 argv/cwd/模式/时限及允许项目文件摘要，批准后源文件变化需重新审批。快照单文件 1 MiB、累计 64 MiB、扫描 10000 条，大仓库需缩小工作区。.bat/.cmd 不隐式执行；需要脚本时显式指定解释器并审批完整参数。

Docker 需本地 Linux 后端和预先准备的镜像，批准前固定本地 image ID，不自动拉取或降级宿主。容器挂载过滤后的临时项目副本，改动不写回源项目；非 root、禁网络、只读根、移除 capabilities、no-new-privileges、CPU/内存/PID 限制。停止按唯一名称清理容器及匿名卷，明确对象不存在才确认。此分支未在真实 Docker 环境验收。

## 预算与上下文

每轮默认 20 个模型步骤、50 次工具尝试、180 秒总时限；HTTP 默认 45 秒、命令最多 60 秒，可在设置调整。每次 HTTP 最多三次尝试，不额外计入模型步骤，不重放工具。每轮模型文字最多 32000 字符。

M3 上下文默认 32000 估算 Token、回答预留 4096、整轮累计输入预算 512000。按 UTF-8 字节与消息开销保守估算，包含工具 Schema、参数和结果，不等于模型实际计费 Token。超限生成结构化提取摘要并保留最近完整调用组和当前任务；SQLite 中的完整历史不被压缩覆盖。当前任务本身超过预算时明确停止，不默默截掉任务。

大于 12000 字节的工具结果保留首尾和归档 ID，模型可用 `read_observation` 分页回读当前会话完整脱敏原件；单项上限 16 MiB，单次运行归档预算 64 MiB、重复内容去重。加载根目录 AGENTS.md，以及已读取文件目录的祖先链约定；深层优先，不覆盖系统要求或工具权限。说明文件加载和模型视图均有大小预算。

审批单次消费，120 秒失效；取消、跨 Turn、重复回应、参数/文件变化不沿用批准。同轮拒绝的相同动作不再询问。测试非零退出码交回模型，任务“完成”表示模型给出了最终回答，不等于测试全通过。

设置中可检测 Python/Git/Docker 路径并选择项目 Python；检测不执行程序、不自动安装项目依赖。左侧最近项目最多 10 个。Ctrl+O 打开项目，Ctrl+N 新对话，Ctrl+, 设置，Ctrl+L 聚焦输入，Enter 或 Ctrl+Enter 发送，Shift+Enter 换行，Escape 停止，Ctrl+Shift+E 导出，列表中 F2 重命名。MCP/Skills/多 Agent 为后续。

## 开发与验证（PowerShell）

Python 3.12+，本机验证 3.13.7。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m minicodex

.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -DistributionSubdir dist/0.5.3
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/smoke_windows.ps1 -BundleDirectory dist/0.5.3/MiniCodex
.\.venv\Scripts\python.exe packaging/smoke_http.py --bundle dist/0.5.3/MiniCodex
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/smoke_m2.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_installer.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_installer.ps1 -TestBuild
.\.venv\Scripts\python.exe packaging/smoke_m4.py
.\.venv\Scripts\python.exe -m minicodex trace show <thread_id> --data-dir <目录>
```

201 项测试通过，不需要外部 Key。本版新增 3 项动画生命周期回归，覆盖模型等待时画面变化、隐藏/显示、输出后停止、历史恢复静止、下一步重启、停止和失败。目录整理另增加 1 项后端独立运行验证，在禁止导入 Qt 和前端的子进程中执行离线工具任务。既有会话删除/故障回滚、三档权限真实读改测、配置/凭据、迁移、进程清理、上下文压缩和恢复测试保留。Ruff 检查/格式覆盖 90 个 Python 文件，mypy strict 覆盖 57 个源文件。程序包另做离线、本机 HTTP、原生读改测自检；构建安装器需要 Inno Setup 6.7.3。10 个脚本化 bug 验证框架闭环，不是模型能力 Benchmark。

DeepSeek 对话与工具定义已实测，用户确认手动读文件分析通过。真实模型 M2 修复、真实 Docker、GitHub CI、Python 3.12 和干净 Windows 仍需分别验收。

## 文档与源码

源码分为 [核心后端](minicodex/backend/) 和 [桌面前端](minicodex/frontend/)。后端包含 Agent 运行时、模型接入、工具执行、应用服务和存储；前端包含 Qt 界面、桌面启动与窗口维护逻辑。前端通过 `ApplicationFacade` 调用后端，后端不导入 Qt 或前端模块。启动命令仍为 `python -m minicodex`，追踪查看仍为 `python -m minicodex trace show ...`。完整目录与依赖边界见 [架构](docs/architecture.md)。

`tests/` 是正式测试源码，`packaging/` 是构建与发行包自检脚本，`docs/` 是设计和验收文档。测试截图、日志与报告写入 `artifacts/`，构建输出写入 `dist/`，两者均不纳入版本管理。当前发行包位于 `dist/0.5.3/`。

- [v0.5.3 界面更新](docs/ui-v0.5.3.md)、[M4 验收](docs/m4-status.md)、[M3 历史验收](docs/m3-status.md)、[M2 历史验收](docs/m2-status.md)、[M1 历史验收](docs/m1-status.md)
- [架构](docs/architecture.md)、[协议](docs/protocol.md)、[执行边界](docs/security.md)
- [主循环](minicodex/backend/core/runtime.py)、[工具路由](minicodex/backend/tools/router.py)
- [补丁](minicodex/backend/execution/patch.py)、[命令](minicodex/backend/execution/commands.py)、[Windows 句柄/进程](minicodex/backend/execution/windows.py)、[审批](minicodex/backend/execution/approval.py)

独立 Python 实现，不承诺与官方 Codex 等价。
