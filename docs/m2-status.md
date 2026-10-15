# M2 · Windows 原生执行版

版本 v0.3.1，日期 2026-10-14。M2 原生执行基础上新增对话工作台。用户明确“没有 Docker，先完成 Windows 原生执行”；Docker 代码和协议测试实现，真实环境验收暂缓，不将其记为完全验收完成。

## 实现

- 严格 unified diff：已有目录内新增/修改/删除、多文件、原文校验、编码/BOM/LF/CRLF 保留、已有修改保护。
- 写路径锁与实际句柄核验；拒绝硬链接、设备名、ADS、敏感文件、junction/symlink；冲突失效、取消回滚和未知结果说明。
- git_status/git_diff 与 diff 面板；限制允许文件，禁外部 diff/textconv/fsmonitor，不修改索引。
- 单次审批绑定 turn/call/approval、参数与内容摘要；拒绝、过期、跨 Turn、取消、参数或文件变化不执行。
- 原生 run_command：暂停创建→Job→启动，句柄/环境白名单，stdout/stderr 有界、实时显示，退出码/耗时/平台回执。
- 停止、超时、正常退出都清理 Job 内后代并确认；关闭开始后不接受新任务。
- 测试失败交回模型继续修复，沿用步骤/工具/时限预算；默认只读、原生需显式开启和逐次审批。
- Docker 代码：过滤副本、固定本地 image ID、资源/网络限制、容器和匿名卷清理；环境缺失不降级宿主。
- 对话工作台：独立命名/重命名的列表、用户/AI 图案、按 step/call 分组的文字和工具卡片、中间审批、可收起的检查面板。
- 会话内恢复展示事件和统计；每轮显示用时及成功补丁/原生命令改变的不同项目文件数，重复路径去重。统计不完整明确提示。

## 本地验证

| 检查 | 结果 |
|---|---|
| pytest | 138 项通过 |
| mypy strict | 40 个源文件通过 |
| Ruff / 格式 | 通过 |
| 多文件和编码 | UTF-8/BOM、UTF-16 LE/BE BOM、GB18030、CRLF、新建/删除通过 |
| 路径/并发 | 越界、ADS、设备名、敏感文件、junction、硬链接、占用、批准后原文变化拒绝 |
| 取消回滚 | 中途取消恢复原文件；晚期冲突移除尚未提交的新文件 |
| 审批/GUI | 批准、拒绝、停止、过期、跨 Turn、参数变化、同轮拒绝后不再询问 |
| 原生命令 | 真实 stdout/stderr、非零退出、环境过滤、大输出 |
| 清理 | 超时、取消、正常父进程退出、Job 分配失败均验证无后续文件写入 |
| 整轮超时 | 最后步骤和普通步骤清理后明确失败 |
| 10 个 Python bug | 脚本化决策 + 实际读取/补丁/原生 unittest：修复前失败、修复后通过 |
| Docker 协议 | 模拟 CLI 验证过滤副本、退出码、image ID、取消清理、连接失败不能误判清理成功 |
| 源码 GUI 自检 | 修改、两次审批、原生测试与 Worker 退出通过 |
| 对话/指标 | 命名隔离、手动标题保留、步骤/多工具恢复、滚动阅读、增删/重复修改/拒绝/冲突/预算、命令失败或取消前已改动及不完整统计通过 |
| frozen 程序包 | v0.3.1 离线、本地 HTTP、审批→补丁→原生测试自检全部通过；验证步骤和轮次统计，清理 Worker，移除 Python PATH 可启动 |
| UI 截图检查 | 默认窗口、960×640 审批按钮/预览及展开检查面板通过 |

10 个任务验证 Harness，不是付费模型自动修复成功率。本轮没有复用用户在聊天中提供的密钥或写入任何密钥。

## 边界

原生项目需用户明确可信，脚本具有当前用户权限；Job 是生命周期管理，不是 OS 沙箱。补丁带异常回滚，但不保证断电/崩溃时跨文件原子性；未知结果不自动无条件重试。

不支持补丁重命名、创建缺失父目录、二进制、混合换行、模糊匹配。快照单文件 1 MiB、累计 64 MiB、扫描 10000 条，命令每通道 32000 字节。Git diff 为允许文件的未暂存修改，未跟踪内容用 read_file 查看。

Docker 实机验收按用户选择暂缓；GitHub CI、Python 3.12、干净 Windows、真实模型修复 Benchmark 未验证。M3 持久化、压缩和 AGENTS.md 未纳入。

依据：[Microsoft Job 分配](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)、[继承句柄](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[Docker create](https://docs.docker.com/reference/cli/docker/container/create/)。

当前证据：artifacts/m2-validation.json、m2-package-smoke.json、m2-package-preview.png，以及 package-smoke.json、package-http-smoke.json。当前交付路径为 dist/0.3.1/MiniCodex；旧程序包与构建缓存使用 Windows 回收站清理。
