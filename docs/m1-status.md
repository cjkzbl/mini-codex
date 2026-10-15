# M1 · 桌面只读 Agent

此页为 M1 历史验收，当前 v0.3.1 交付见 [M2 记录](m2-status.md)。2026-10-14 用户确认已手动测试真实模型读文件分析，过程正常；该手动验收已记录。

实现日期：2026-10-14，版本 v0.2.2；主平台 Windows 11 x64。

## 已实现

- ThreadManager、InMemoryThreadStore、内存会话创建/恢复与历史配对检查。
- 完整 Turn/Step Agent Loop；串行多工具回执；明确完成/失败/取消/上限状态。
- Provider Protocol、可脚本化 Fake、OpenAI-compatible HTTP Adapter。
- Chat Completions 文本与 Function Calling，SSE 增量多工具参数拼接、非流式、有限重试与断流失败。
- JSON Schema Registry/Router、list_files/read_file/search_code。
- 中文/空格路径、CRLF/常见文本编码、有界输出与扫描、Windows junction/句柄边界检查。
- GUI 模型设置、密钥内存保存、工具进度/结果、停止、新建与切换内存会话。
- 更新依赖锁、Windows CI 和目录包脚本；M0 PATH DLL 隔离修复继续生效。

- DeepSeek 官方地址显式关闭思考模式；验证流式/非流式请求及连续工具步骤都携带 disabled，其他域名不发送此字段。

## 本地验收

| 检查 | 实际结果 |
|---|---|
| Ruff、格式检查 | 已通过 |
| mypy strict | 33 个应用源文件通过 |
| pytest | 清理后 68 项通过，含 Windows junction 与实际句柄路径安全测试 |
| 两轮工具循环 | Mock/Fake + 实际临时仓库通过 |
| 实际 HTTP + GUI + 两轮读取与搜索 | 本机兼容测试服务通过，无外部账户或模型 |
| 源码桌面自检 | 通过，工具实际列出 README，历史含 4 条消息，Worker 正常退出 |
| Windows 程序包离线自检 | 通过，移除 Python/venv PATH，frozen=true，真实工具列出 README，Worker 退出 |
| Windows 程序包 HTTP 自检 | 通过，本机真实 HTTP 传输 2 次请求，完整模型→工具→回执闭环，无外部模型 |
| 真实 DeepSeek API | 模型列表、最小非流式对话、带三个工具定义的 MiniCodex 流式请求通过，直连成功 |

当前保留证据：`artifacts/package-smoke.json`、`artifacts/package-http-smoke.json` 和 `artifacts/package-preview.png`。这些文件由可复现的自检命令产生，后续自检会更新同名记录；旧源码截图和临时自检报告已清理。

测试覆盖多工具配对、未知/拒绝工具、参数校验、步骤/工具预算、任务超时、取消后补回执、损坏/截断流、交错工具增量、429 重试、HTTP 等待取消、断流不重连、密钥错误脱敏和 UI 响应。

## 实际限制

2026-10-14 已用用户提供的 DeepSeek 账户进行实际调用：模型列表 HTTP 200，`deepseek-flash` 与 `deepseek-chat` 的最小对话 HTTP 200，MiniCodex Provider 在非思考模式下流式输出 `OK`，服务接受三个只读工具的定义。此次外部测试未触发工具执行或回执循环，也不验证答案质量；完整工具循环由本机 HTTP 集成测试覆盖。密钥仅用于临时进程内存，不写入项目。

GitHub CI、Python 3.12 和全新 Windows 虚拟机未实际执行；目前本地 Python 3.13.7 验证。UI 自动测试/截图采用 Qt offscreen。会话和密钥仅存内存；没有修改、执行测试、持久化、压缩、AGENTS.md、MCP 或多 Agent。

M1 只读开发主线已实现。M2 原生执行现已交付，Docker 实机验收按用户选择暂缓，详情见 M2 记录。

## v0.2.2 诊断更新

v0.2.2 加入 HTTP 错误分类、错误字段白名单和 DeepSeek 配置按钮；该账户读文件分析已由用户手动验收。当前程序包路径见 README。

## 清理记录

v0.2.0/v0.2.1 程序包、PyInstaller 中间文件、生成的 spec、旧截图与报告、测试缓存和 Python 字节码目录已移入 Windows 回收站，可恢复。旧 runtime 测试合并到 Agent Loop 测试；移除 Fake 固定脚本自检和构造函数基础自检两项，保留会话历史、事件配对、失败、取消及工作区权限等回归覆盖。开发虚拟环境与当前版本程序包保留。清理后的 68 项测试全部通过，Ruff 检查与 44 个 Python 文件的格式检查通过。
