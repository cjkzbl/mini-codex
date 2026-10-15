# 上游源码参考基线

- 仓库：https://github.com/openai/codex
- 固定 commit：`2f05c0a71f59fb2f05800846351f3e9926c3e52b`
- 记录日期：2026-10-14（项目用户时区 Asia/Shanghai）
- 来源：GitHub API `repos/openai/codex/commits/main` 返回的 SHA；今后阅读使用固定 commit 链接，不继续追随 main。
- 链接：https://github.com/openai/codex/commit/2f05c0a71f59fb2f05800846351f3e9926c3e52b

M0 固定参考版本，M1 完成独立 Python 只读工具循环；没有复制上游代码，也未宣称对所有 Rust 文件/函数完成分析。原计划书第 9 节的路径是阅读候选，源码函数逐项对照保留为后续学习任务。

| 领域 | 当前 Python 位置 | 阶段 |
|---|---|---|
| Thread / Turn 与事件 | `minicodex/backend/core/models.py`, `events.py`, `thread_manager.py` | M1 内存会话与生命周期 |
| Runtime | `minicodex/backend/core/runtime.py`, `history.py` | M1 工具循环与配对检查 |
| Provider | `minicodex/backend/llm/base.py`, `fake.py`, `openai_compatible.py` | M1 离线与 API 适配 |
| 工具与工作区边界 | `minicodex/backend/tools/`, `execution/workspace.py` | M1 只读 |
| 应用服务 | `minicodex/backend/application/` | 独立设计，无 Qt 依赖 |
| 桌面界面与启动 | `minicodex/frontend/ui/`, `minicodex/frontend/desktop.py` | 独立设计 |
