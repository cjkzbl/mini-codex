# 项目架构

源码按核心后端与桌面前端分开，入口 `python -m minicodex` 保持不变。两者在同一进程中通过 `ApplicationFacade` 的命令、事件和审批接口协作。

```text
minicodex/
├── __init__.py               # 版本信息
├── __main__.py               # 桌面 / trace 命令分发
├── backend/                  # 不依赖 Qt 或 frontend
│   ├── application/          # Facade、后台 Worker、配置模型、命令及工具发现
│   ├── core/                 # Agent 循环、会话、上下文、事件、脱敏
│   ├── execution/            # 工作区边界、补丁、命令、Windows/Docker 执行
│   ├── llm/                  # 模型协议及 HTTP/SSE 适配
│   ├── persistence/          # SQLite、配置、凭据及观察结果归档
│   ├── tools/                # 工具注册、路由及实现
│   └── cli.py                # 无界面的 trace 查看
└── frontend/
    ├── desktop.py            # Qt 应用启动与桌面自检
    ├── windows_client.py     # 桌面互斥、用户数据清理与 Qt 目录锁
    └── ui/                   # 窗口、对话组件、样式、字体、事件桥
```

依赖方向为 `frontend → backend`；后端内部保留既有领域模块关系。Qt 导入与桌面维护代码只在 `frontend` 中出现。前端通过 Facade 发起操作，通过 `RuntimeEvent`、会话模型和配置模型显示状态；不直接执行模型请求或工作区工具。`tests/unit/test_architecture.py` 禁用 Qt 和前端导入后加载全部后端模块，并执行一次真实的离线工具轮次，防止后端重新依赖界面。

`tests/` 保存单元、集成、安全、GUI 和端到端测试；`packaging/` 保存 Windows 构建、安装器及发行包自检脚本；`docs/` 保存设计与历史验收说明。`.venv/` 和 `.tools/` 是本地开发依赖，`artifacts/`、缓存及旧版 `dist/` 是可再生成的验证或构建输出。

```text
Qt 主线程：MainWindow / ConversationView / SettingsDialog
  ↓ StartTurn / cancel / configure / resume / rename_thread
ApplicationFacade → ThreadManager → SQLiteThreadStore（临时模式可用 InMemoryThreadStore）
  ↓ 后台 asyncio Worker
AgentRuntime：Turn → Step 循环
  ├─ AGENTS.md → ContextBuilder / TokenCounter → Provider.stream(messages, tools)
  │    ├─ FakeLLMProvider：固定流程，离线工具演示
  │    └─ OpenAICompatibleProvider：HTTP/SSE、增量拼接、有限重试
  ├─ assistant(tool_calls) 写入内存历史
  ├─ ToolRouter → Schema 验证 → Workspace 策略 → 只读/Git 工具
  ├─ patch/command → diff/快照 → ApprovalManager → Qt 批准/拒绝
  ├─ 批准后复核 → 锁定补丁 / Windows Job 原生执行 / Docker 副本
  └─ tool(call_id) 写入历史 → 下一步模型请求
  ↓ 类型化事件 / 512 条有界队列
EventBridge：每 16ms 最多处理 64 条 → UI
```

核心层不依赖 Qt。UI 不直接调用模型或工具；Runtime 请求审批，Qt 通过 Facade 回应当前审批。没有 HTTP App Server，模型 HTTP Adapter 不等于应用服务。

同步 I/O 在 Worker 线程池执行，取消用共享 Event；副作用工具等待线程/进程清理后才终止。命令 stdout/stderr 经有界预览队列和事件队列，限长回执交回模型。终止事件是清理完成的依据。

一个桌面实例一次运行一个 Turn，运行期间禁止切换工作区、会话或模型。模型工具 Schema 每一步深复制，Workspace 固定在当前 Thread。全链路 thread/turn/step/call ID 和序号用于关联。

桌面启动先由 ConfigurationStore 加载普通设置和可选 Windows 凭据，再创建 SQLiteThreadStore 和 Worker。普通设置用版本化 JSON + 原子替换；Key 默认仅内存，勾选后用 Windows Generic Credentials 按数据目录/服务隔离保存，配置只存引用。Facade 不传 data_dir 时保留临时内存后端用于测试。

ThreadState 的独立标题、模型 history 与展示 turns 保存在 SQLite WAL；TurnState 保存脱敏事件、用时、编辑路径及估算 Token。每个工具启动前完成持久化检查点；重启补齐未完成回执并标记 interrupted/unknown，不自动重放工具或延续审批。Qt 通过进程锁避免同一目录双开；应用/维护命名互斥对象协调安装和显式数据清理。

完整历史和模型视图分离：ContextBuilder 保留系统要求、当前任务与最近完整工具组，按预算生成确定性结构化摘要，不覆盖归档。根/已访问目录祖先链 AGENTS.md 作为带来源的参考数据。TokenCounter 使用 UTF-8 字节与消息开销保守估算；单次上下文和整轮累计输入预算独立限制。模型提供商实际 Token/费用不由该估算替代。

ObservationArchive 原子保存脱敏大结果，回执带摘要与 SHA-256 ID；read_observation 只允许当前会话分页回读并验证摘要。SQLite 是恢复依据，JSONL 默认仅写事件元数据；导出正文需要显式勾选。CLI trace show 以只读事务查看一致快照，不触发恢复。

命令在 Job 清理完成后比较受保护的有界项目前后快照，将实际内容变化路径交回 Runtime。补丁只在成功应用后累计路径；Windows 大小写等价路径只算一次。终止时记录单调时钟用时；统计不完整不会把未知结果显示为零改动。Docker 临时副本改动不写回，也不计作宿主文件编辑。
