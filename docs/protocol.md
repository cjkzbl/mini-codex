# M3 协议

## 模型边界

`LLMProvider.generate(messages, tools) -> ModelResponse`；`stream` 返回文本增量，最后必须返回且仅返回一个完整 ModelResponse。工具参数只在完整响应后执行；流式部分参数不能执行。

`Message` 包含 role/content/tool_calls/tool_call_id。标准历史顺序为：

```text
user → assistant(tool_calls: A,B) → tool(A) → tool(B) → assistant(answer)
```

每个 call_id 必须唯一且恰有一条回执。每次模型请求及内存恢复前校验历史；取消、预算耗尽和失败时补齐已记录但未结束的工具调用。拒绝或参数错误也是失败回执，模型可据此调整下一步。

API Adapter 采用 `/chat/completions`；SSE 按 index 拼接多工具 id/name/arguments，支持 JSON 非流式回退。完整结束需正常 finish_reason 与 `[DONE]`，不完整流、损坏参数和 length/content_filter 不执行工具。429/部分 5xx 和连接前失败有限退避；SSE 开始后断连不自动重放。

## 生命周期与命令

PENDING → RUNNING → COMPLETED / FAILED / CANCELLED / LIMIT_EXCEEDED。达到步骤、工具或字符预算不等于完成，任务超时为明确失败。

重启发现 PENDING/RUNNING 则转换为 INTERRUPTED，补齐未确认工具回执为 state=unknown。正常取消保存 CANCELLED。恢复只重建记录，不启动模型、不重放动作、不恢复审批 Future。

Facade 提供 select_workspace/start/cancel/resume/configure/close/respond_approval/rename_thread。start 要求非空输入和匹配当前 Thread；重复取消无副作用。标题为 1–80 个字符的单行文本，修改名称不影响历史和工作区。首次任务只为尚未手动命名的对话生成标题。设置变更要求空闲，并新建当前会话；密钥不进入事件或设置 repr。

## 事件

所有事件包含 kind/thread_id/turn_id/step_id/sequence/text，工具事件额外包含 call_id。序号在 Turn 内单调递增；UI 拒绝跨 Thread、旧 Turn 和重复事件。

| 事件 | 含义 |
|---|---|
| turn.started | 本轮开始 |
| step.started | 新模型请求步骤 |
| model.delta | 可见文本增量 |
| tool.started / tool.finished | 工具开始与实际回执 |
| approval.requested / approval.resolved | 本次操作请求与批准/拒绝/失效 |
| diff.preview | 完整待审批补丁 |
| command.output | 有界 stdout/stderr 实时预览 |
| turn.completed | 最终有效回答 |
| turn.cancelled / turn.failed / turn.limit_exceeded | 取消、失败或预算终止 |
| turn.interrupted | 上次异常退出，执行结果未知 |
| model.request / model.response | 模型输入估算/消息数与输出长度/工具数元数据 |
| context.compact | 压缩前后估算与结构化摘要；完整历史保留 |
| instructions.loaded | 已加载的、带工作区相对路径的项目说明 |

tool.started 的 text 第一行是工具名，后续为脱敏后的 JSON 参数；tool.finished 第一行为工具名与成功/失败，后续为回执。所有展示事件按 Turn 保存，UI 按 step_id 和 call_id 归组。TurnState 的 duration_s 在终止事件前确定；edited_files 去重累计成功补丁和原生命令已确认改变的路径。原生命令回执含 edited_files/file_metrics_complete；统计超出限制时为不完整，不能显示为已确认零改动。UI 展示记录独立于模型 history。

JSON Schema 在执行前验证。只读模式注册 list/read/search/git_status/git_diff；仅修改模式增加 apply_patch，原生/Docker 模式增加 run_command。写与执行必须经审批，未经审批直接调用处理函数会拒绝。工具错误和命令非零退出都作为失败 observation 返回模型。

ApprovalRequest 绑定 approval_id/turn_id/call_id/tool/mode/摘要/fingerprint/过期时间。事件 text 为 JSON，Facade 仅提交当前 Turn 的回应，回应单次消费。批准后参数、原文或允许文件快照再次核验，变化即失效。被拒绝的同一 fingerprint 本轮不再询问。未知结果禁止无条件重放。
