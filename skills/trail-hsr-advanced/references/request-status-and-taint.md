# Request Status And Taint

- 遇到 unknown result 时，先记录默认文本里的 `request id`，再查询 request-status，确认请求终态后再决定下一步恢复动作。
- 对应的恢复查询命令是 `trail daemon request-status --request-id <id>`；先查终态，再决定是否继续恢复，不要直接盲猜成功或失败。
- `tainted` 表示当前 session 或上下文已经不适合继续盲动；需要先确认请求终态，再决定是否 reconcile。
- 如果请求终态已经明确，但 session 事实仍不稳定，使用 `trail daemon reconcile-session --session <id>` 收敛上下文，再视需要补一次 `trail --format yaml state dump --session <id>`。
- 如果 request-status 已经给出稳定终态，可以结合 `state` 回读判断当前 scene / session 是否已恢复。
- 如果 `tainted` 在 reconcile 后仍存在，advanced 应停止自动重试，并把阻塞事实返还给上层 owner。
