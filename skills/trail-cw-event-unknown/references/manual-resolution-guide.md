# Unknown Event Manual Resolution Guide

## 入口识别

- 只在 `cw.event.handle` 输出 `event_type=unknown`、`handled=0` 或 `next_action=manual` 后使用本指南。
- 若输出带 `shot path=...`，下一行必须有 `info read_image_first=1`；Agent 必须先读原始截图，再消费文本事实。
- unknown 路径的固定 stale 合同是 `stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles`，同时保留 `crystals_stale=0`。

## 手工处理未知事件

- 手工处理未知事件时，只根据截图中明确可见的选项、按钮和阶段文案行动。
- 不把未知事件当作普通备战、投资环境页或 daemon 恢复；也不编造事件策略。
- 若看到 `CW_EVENT_LV999_STATE_STALE`，说明 `银狼LV.999` 相关 `cost` / `star` / slot 事实需要后续恢复，不得用普通 `银狼` 事实代替。

## Reconcile 后续

- 离开未知事件页后运行 `trail cw event reconcile --session <id>`。
- reconcile 若确认仍有 `strategy`、`sell_plan`、slots、shop 或 equipment stale，按输出补跑对应命令后再回到 `trail-cw-prep`。
- reconcile 若仍返回 `next_action=manual`、`recover` 或 request id，继续按输出协议处理，不自行宣布恢复完成。
