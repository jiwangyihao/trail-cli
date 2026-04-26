# Stage Boundaries

- `preparation` / `shop`：普通备战事实收集范围。
- `replenish` / `invest` / `encounter` / `fortune` / `event`：特殊事件范围，停止并交给后续专用 skill 或上游。
- `boss_preview`：BOSS 前流程，停止并交给后续单独 skill。
- `settle` / `game_over`：结算或结束，不由本 skill 自治。
- `unknown`：不猜测，停止自治并交回上游 scene entry；本 internal skill 不直接升级到恢复层。
