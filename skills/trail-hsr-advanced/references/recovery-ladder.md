# Recovery Ladder

1. 先核对失败症状、最近动作、可复用上下文，以及 `expected_return_owner` / `expected_return_owner_skill` 是否齐全。
2. 如果上一条命令属于 unknown result，先保留默认文本中的 `request id`，不要直接假设它失败或成功。
3. 先做 request-status / state 回读，再决定要恢复启动、窗口还是 session；对应命令可用 `trail daemon request-status --request-id <id>` / `trail state dump --session <id> --format yaml`，不要跳过状态确认直接连发动作。
4. 只要恢复出了可继续推进的稳定上下文，就立即把控制权交回 `expected_return_owner`，并按 `expected_return_owner_skill` 交回具体 skill。
5. 如果 request-status 已确认终态但 session 仍 tainted，可补一次 `trail daemon reconcile-session --session <id>`；如果 `request id` 不存在、恢复结果仍不确定，或缺少关键路径 / 窗口信息，就停止自动重试并把阻塞事实上抛。
