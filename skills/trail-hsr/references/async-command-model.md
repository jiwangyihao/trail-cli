# Async Command Model

- daemon-backed 业务命令默认使用 daemon 端异步单例 job 模型；同一 daemon 同一时间只允许一个游戏操作运行，`trail start`、`trail ocr read`、`trail input ...`、`trail cw ...` 都可能先返回 `state=running request=<job_id> waited=<n>`。
- `request=<job_id>` 是后台 job id，不是本次 socket call id；它主要用于恢复、排障、取消或跨命令查询。常规续查不要求 Agent 手动填写这个 id。
- 常规续查方式是原样重发同一条业务命令；不要把 running 当作失败，也不要换成不同业务命令重新执行。
- `trail start` running 后的常规续查示例：继续运行 `trail start`。
- session-bound 命令 running 后的常规续查示例：继续运行 `trail cw battle run --session <session_id>`；原业务命令需要的 `--session`、目标参数和其它业务参数仍要保留。
- 如果再次返回 running，继续原业务命令；如果返回终态业务输出（例如 `status=...`、`shot path=...`），再按该命令所属 skill 的正常输出协议继续。
- `info next_action=<command> request=<job_id>` 是强提示：下一步优先照它重发同一业务命令；若同一行带 `session=<id>`，续查命令必须带该 session。通常无需把 `request=<job_id>` 改写成 `--request-id`。
- `DAEMON_BUSY` 表示已有不同游戏操作正在运行；不要排队或另起动作。按输出里的 `recover action=<command> request=<job_id> [session=<id>]` 重发对应业务命令；只有需要跨命令恢复或排障时才把 `request=<job_id>` 用于控制面命令。
- `trail daemon request-status --request-id <id>`、`trail daemon request-result --request-id <job_id>` 和 `trail daemon request-cancel --request-id <id>` 是控制面 / 恢复面命令，不是常规轮询首选。只有在 stdout 丢失、跨命令恢复、busy/recover 无法重发原命令、unknown result、tainted 或需要取消时使用。
- `trail daemon request-result --request-id <job_id>` 只用于已知 job id 的终态结果回放；业务命令常规路径仍优先重发原命令。
- `trail daemon request-cancel --request-id <id>` 是 soft cancel。若取消发生在 UI side effect 之后，可能返回 `REQUEST_CANCEL_UNKNOWN` / `tainted=1`；此时停止普通操作，按输出 `recover action=daemon.reconcile_session session=<id>` 或 advanced 恢复链路收敛上下文。
- 没有显式 `--request-id` 时，重发同 payload 会附着该业务命令的最新 singleton job；旧 job 已终态时也回放终态业务输出，不重新执行同 payload mutation。
