# M4 · Windows 客户端配置与分发

版本 v0.5.0，2026-10-15。Task-023 已实现；Task-024 的客户端、安装器和本机验收已实现，独立的干净 Windows 11 VM 验收仍待执行。Docker 实机功能按用户选择继续暂缓。

## 配置与凭据

- 模型地址/ID、流式输出、执行模式、各项预算、目标项目 Python、最近 10 个项目和窗口尺寸保存在当前数据目录的 `settings.json`。桌面默认目录为 `%LOCALAPPDATA%/MiniCodex`，`--data-dir` 可隔离。重启恢复设置；模式恢复不恢复任何补丁或命令审批。
- API Key 默认只在进程内存；在设置中勾选后，使用当前用户 Windows Generic Credentials 保存。JSON 只保存凭据引用，没有 Key 字段或明文回退。凭据按数据目录和服务地址隔离，切换服务不复用其他地址的凭据；改变服务主机时清除设置框内自动加载的旧 Key。
- Windows 凭据保存失败则不提交新配置；JSON 原子替换失败清除本次新增凭据，保留旧配置/凭据。成功替换后清理本数据目录旧凭据；清理失败提示重试。凭据不是项目文件，也不意味着防护同一 Windows 用户下所有程序；Python 内存不能保证物理清零。
- “忘记保存的 Key”删除本数据目录管理的凭据；当前进程已加载的 Key 会留到退出，界面明确说明。取消保存并应用设置也会清除持久化凭据。配置重置需界面确认，保留 SQLite 会话。
- 配置版本 2；支持版本 1 的扁平/嵌套设置迁移。若旧配置含明文 Key，重写移除且不创建明文备份，当前进程可继续使用，跨重启保存须重新勾选。未知版本、损坏配置保留原文件并使用离线/只读默认值，只有显式重置才覆盖。
- API Key 不进入 dataclass repr；已加载 Key 在会话、工具回执与导出中沿用 M3 脱敏。粘贴到模型/地址/工具路径的已知 Key 被拒绝。无法识别任意业务秘密；会话正文是本机未加密数据。

## 桌面与工具链

最近项目可从左栏重开，缺失路径明确报错。最近会话、独立命名、记录导出沿用 M3。

| 快捷键 | 操作 |
|---|---|
| Ctrl+O | 打开项目 |
| Ctrl+N | 当前项目新对话 |
| Ctrl+, | 模型设置 |
| Ctrl+L | 聚焦输入框 |
| Ctrl+Enter | 发送 |
| Escape | 停止当前轮次 |
| F2 | 对话列表内重命名 |
| Ctrl+Shift+E | 导出当前任务记录 |

设置显示项目 Python、Git 和 Docker 路径，不执行所发现的程序。Python 按显式配置、项目 `.venv/Scripts/python.exe`、PATH 顺序发现，过滤 WindowsApps 占位；可选择解释器路径。找到路径不表示已验证版本或安装 pytest。原生模式将候选路径加入模型上下文，命令仍逐次审批；缺失工具不会触发自动安装。客户端嵌入运行时不代替项目 Python。

Qt 6 使用逻辑像素与 PassThrough 缩放策略。启动窗口按可用屏幕收缩，窄窗口简化标题，侧栏和设置可滚动。已验证逻辑 960×540（1080p/200%）下输入/发送/设置入口可见。100/150/200% Qt 缩放的程序包截图与运行检查不等于跨显示器拖动、真实多屏或所有 Windows 缩放组合验收。

## 安装与卸载

交付 `dist/0.5.0/MiniCodex-0.5.0-Setup.exe` 和完整目录包 `dist/0.5.0/MiniCodex`。Inno Setup 6.7.3，当前用户安装，默认 `%LOCALAPPDATA%/Programs/MiniCodex`，无需管理员权限。添加开始菜单启动/卸载快捷方式，可选桌面快捷方式；Windows 设置中有卸载登记。

安装程序替换程序目录文件，数据目录独立保留。升级或卸载前关闭所有 MiniCodex 窗口；不会主动终止用户应用。

交互卸载默认保留设置、会话与凭据。选择清理时，仅处理当前用户默认 MiniCodex 数据目录：验证应用标记、路径和链接，检查应用互斥对象与数据锁，删除该目录管理的 Windows 凭据，再将数据移至回收站。自定义 `--data-dir` 始终保留。链接/锁定/凭据或回收失败会中止卸载；凭据删除不是可回收操作，失败可能已删除部分凭据，数据目录会保留。静默卸载始终保留用户数据。

安装器和应用目前未做代码签名，Windows 可能显示未知发布者。编译器仅用于构建、不随应用分发。目录包包含依赖许可证材料。默认数据被不同版本共用，旧版本不理解 M4 配置时不会替你重置文件；回退前自行备份数据。

## 验证与边界

182 项测试覆盖 M0–M4；其中新增 27 项 M4 测试，覆盖配置重启、凭据读写/清理、保存故障回滚、迁移、损坏和未来版本、字段类型、地址隔离、Key 误填、最近项目、快捷键、缺失依赖、缩放布局及数据清理。Ruff 检查/格式覆盖 79 个 Python 文件，mypy strict 覆盖 52 个源文件。

构建与本机验收命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -DistributionSubdir dist/0.5.0
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_installer.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_installer.ps1 -TestBuild
.\.venv\Scripts\python.exe packaging/smoke_m4.py
```

安装器脚本在 `.tools/inno-compiler/ISCC.exe` 或 PATH 查找编译器，也可传 `-Compiler`。测试构建使用不同 AppId/快捷方式组，在独立中文空格路径安装、覆盖重装、恢复配置/合成凭据、卸载并验证数据保留，最后清理合成凭据；不会操作真实默认数据。覆盖重装验证升级所用的文件替换/数据保留路径，不代表每个历史安装包的版本组合测试。

证据见 `artifacts/m4-validation.json`、`artifacts/m4-package-smoke.json` 和缩放截图。没有使用外部 Key 或计费模型。干净 Windows 11 x64 上无 Python 的完整安装/配置/卸载及真实多显示器测试需单独执行，本机移除 Python PATH 只验证包独立启动。M5 模型能力 Benchmark 不包含在本阶段。

## 实现参考

- [Windows CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)、[CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
- [Qt for Python 高 DPI](https://doc.qt.io/qtforpython-6/overviews/qtdoc-highdpi.html)
- [Inno Setup 官方下载](https://jrsoftware.org/isdl.php)、[当前用户安装权限](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)
