# Advanced Command Surface

- `daemon` family 负责控制面 ready 状态、request 查询、session 恢复与 reconcile，不负责替上层决定 scene owner。
- 常见控制面命令包括 `trail daemon install`、`trail daemon status`、`trail daemon start`、`trail daemon request-status --request-id <id>`、`trail daemon reconcile-session --session <id>`。
- `window` family 负责启动、attach、窗口接管和 `channel` / 路径相关恢复，不承担具体玩法流程。
- 常见窗口命令包括 `trail window launch --channel official|bilibili|global` 与 `trail window attach --window-title ...`；它们只处理窗口恢复，不接管上层编排。
- `session` family 用来补建或稳定当前会话，让上层 owner 能拿回一个可继续推进的上下文。
- 需要补建上下文时使用 `trail session create`；恢复后把可复用 `session` 事实返还给上层 owner。
- `screen` 与 `image` family 只在需要借助截图或图像线索确认真实窗口状态时使用。
- 常见观察命令包括 `trail screen shot`、`trail image locate`、`trail image wait`；如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 image / state 压缩文本。
- `state` family 用来回读当前 session / scene 快照，尤其是在 stdout 丢失或结果未知时确认恢复后的落点。
- 需要结构化回读当前上下文时，使用 `trail --format yaml state dump --session <id>`；这是内部恢复兜底，不是默认主通道。
- 需要用 `screen` / `image` / `state` 做高级排障时，如显式开启 `--verbose`，读取 stdout 末尾的 major action trace：`debug kind=trace ...`；finalized 事件至少带 `ts=<UTC RFC3339 毫秒时间戳>` 与 `ok=0|1`，可作为 shared helper 执行证据。
- `trace/context` 边界固定：`trace` 只承载 finalized helper 动作事件，`context` 只承载跨动作请求级事实；OCR 的 mode/retry 事实改看 `debug kind=trace step=ocr ...`，不再看 top-level context。
- 这一层命令面只服务内部恢复；一旦状态稳定，就把控制权交回 `expected_return_owner`，并由 `expected_return_owner_skill` 指向具体 owner skill。
