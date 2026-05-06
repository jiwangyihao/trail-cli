# Stage Boundaries

- `preparation` / `shop`：普通备战事实收集范围；`cw.portal.select` 进入首轮普通备战或 `cw.battle.run` 返回 `status=completed result=win stage=preparation` 后，优先复用同次 handoff 附带的 stage/slots/equipment/shop facts。
- `replenish` / `invest` / `encounter` / `fortune` / `event`：特殊事件范围，停止并交给后续专用 skill 或上游。
- `boss_preview`：真正本场对局首领 / BOSS 前流程，停止并交给后续单独 skill。
- `layer_transition`：整层结束后的点击空白 / 位面过场，仍属于 battle flow，默认继续 `trail cw battle run --session <id>`，不是普通稳定阶段，也不是手工中断点，不由本 skill 自治。
- `settle` / `game_over`：结算或结束，不由本 skill 自治；若 `cw.battle.run` 已输出 `game_over=1 end_reason=global_battle_failed restart_candidate=1 returned_home=1`，本局已失败结束且命令已回到货币战争主页，不再继续 battle flow，由上游决定是否重开。
- `unknown`：不猜测，停止普通 prep 自治；若来自 `cw.event.handle event_type=unknown` / `next_action=manual`，切到 `trail-cw-event-unknown` 手工处理未知事件，处理后必须运行 `cw.event.reconcile` / `trail cw event reconcile --session <id>` 恢复 stage/slots/shop/equipment 等事实；本 internal skill 不直接升级到恢复层。
