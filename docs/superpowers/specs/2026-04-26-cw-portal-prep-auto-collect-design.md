# cw.portal.select 备战事实自动收集设计

## 背景

`cw.portal.select` 当前负责选择投资环境、等待进入普通备战阶段，并自动应用当前已选攻略。进入普通备战后，Agent 下一步通常需要连续执行收集水晶、读取阵容槽位、打开商店、扫描商店等动作。现有 `cw.slots.read` 和 `cw.shop.scan` 已经具备主要识别与写入 session 的能力，但部分页面稳定点击被包在 reader wrapper 中，直接组合会产生重复点击和页面状态不够明确的问题。

## 目标

- `cw.portal.select` 在成功应用攻略后自动执行：`cw.crystals.collect` 等价动作 -> `cw.slots.read` 等价动作 -> 打开商店 -> `cw.shop.scan` 等价动作 -> 关闭商店。
- 最终截图停留在无浮层的普通备战页，而不是商店页或角色详情浮层。
- 保留 `cw.portal.select` 首行协议：`ok cw.portal.select idx=... 投资环境=...`。
- 在 `cw.portal.select` 输出中追加槽位、羁绊、商店和 stage 投影事实，让 Agent 可以直接消费本次响应里的普通备战事实；后续购买或重新查询商店仍以 session 中的 stale 状态为准。
- 将可复用边界收紧为“核心识别/写 session 动作”和“命令级页面编排”分离，避免新增通用 `preparation helper` 固化某一个命令的流程。

## 非目标

- 不新增新的 public CLI 命令。
- 不新增 YAML allowlist；`cw.portal.select` 仍保持默认文本协议。
- 不要求最终截图可人工复核 slot 名称；slot 名称本来来自点击详情后的 OCR，最终无浮层备战页不会显示完整角色名。
- 不把 `cw.slots.read`、`cw.shop.scan` 的 renderer 当作子命令输出直接拼接；输出仍由 `cw.portal.select` renderer 统一渲染。

## 当前代码边界

- `trail/scenes/cw/slots.py` 中 `build_cw_slots_reader(runtime, targets)` 同时做初始页面固化点击、逐 slot 点击详情、截图、批量 OCR 和 stage OCR。
- `read_cw_slots(session, reader=..., targets=..., guide_config=...)` 负责写 `cw_state["slots"]`、合并局部 slot、补角色羁绊、刷新 `cw_state.stage.status`、清理 `sell_plan`。
- `_capture_slot_panel_images(...)` 点击 slot 后读取详情，finally 中点击 `INFO_DISMISS_POINT` 关闭详情浮层。这个关闭动作是单 slot 读取的必要内部流程，应保留在核心读取流程内。
- `trail/scenes/cw/shop.py` 中 `_read_shop_page_snapshot(runtime, read_team_size=...)` 只读当前商店页 OCR，不做 reset/open/close。
- `build_cw_shop_scan_snapshot_reader(runtime, read_stage_status=False)` 负责 `SHOP_SCAN_RESET_POINT` -> `SHOP_OPEN_POINT` -> `_read_shop_page_snapshot(...)`。
- `scan_cw_shop(session, scanner=...)` 负责把商店快照写入 `cw_state["shop"]`。
- `project_cw_shop_snapshot(session)` 负责输出 shop 状态并投影当前 `cw_state.stage.status`。

## 设计

### 可复用动作单元

保留 scene 层的状态写入函数作为主要复用点：

- `read_cw_slots(...)` 继续作为“读取槽位并写 session”的动作函数。
- `scan_cw_shop(...)` 继续作为“读取商店并写 session”的动作函数。
- `collect_cw_crystals(...)` 继续作为“收集水晶并写 metrics”的动作函数。

新增或调整小型页面动作与 reader factory，使页面编排由命令自行决定：

- `dismiss_cw_slots_overlay(runtime)`：只负责点击中心并等待，用于关闭可能残留的详情/提示浮层。这是命令级页面固化动作。
- `build_cw_slots_reader(runtime, targets=None, dismiss_initial_overlay=True)`：默认行为保持不变，内部调用 `dismiss_cw_slots_overlay(...)`；组合命令可先显式调用页面固化动作，再传 `dismiss_initial_overlay=False`，避免重复点击。`_capture_slot_panel_images(...)` 结束时关闭单个 slot 详情浮层仍保留在核心读取流程内。
- `build_cw_shop_page_snapshot_reader(runtime, read_stage_status=False)`：只读当前商店页并返回 `{"opened": True, "stale": False, ...snapshot}`，不执行 reset/open。
- `build_cw_shop_scan_snapshot_reader(...)` 改为组合 wrapper：先点 `SHOP_SCAN_RESET_POINT`，再点 `SHOP_OPEN_POINT`，然后调用 `build_cw_shop_page_snapshot_reader(...)`。现有 `cw.shop.scan` 行为保持不变。

### cw.portal.select 流程

`cw.portal.select` 在 `_select_portal_and_apply_selected_guide(...)` 内完成现有动作后，按命令自身需要顺序组合：

1. `select_cw_portal(...)`
2. `wait_cw_portal_preparation(...)`
3. `_apply_selected_guide_via_ui(...)`
4. `collect_cw_crystals(session, collector=crystal_collector_factory(runtime))`
5. `dismiss_cw_slots_overlay(runtime)`，由 `cw.portal.select` 自己执行 slot 读取前的页面固化。
6. `read_cw_slots(session, reader=build_cw_slots_reader(runtime, dismiss_initial_overlay=False), guide_config=fetch_cw_guide_config(...))`
7. `open_cw_shop(session, opener=shop_opener_factory(runtime))`
8. `sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)`，由 `cw.portal.select` 自己等待商店页稳定。
9. `scan_cw_shop(session, scanner=build_cw_shop_page_snapshot_reader(runtime))`
10. 在关闭商店前缓存 `slots` 和 `project_cw_shop_snapshot(session)`。
11. `close_cw_shop(session, closer=shop_closer_factory(runtime))`

最后一步会让最终页面回到无浮层普通备战页。关闭商店会把 session 中的 shop 标记为 `opened=False` 且 `stale=True`，因此 `cw.portal.select` 只保证本次响应里的 `data.shop` 是刚扫描到的缓存事实；后续命令若需要 fresh shop，仍应按 session stale 语义重新扫描。

### 响应数据

`cw.portal.select` 返回 data 保持 portal 字段，并追加：

- `skill_info`: 现有运营思路信息。
- `crystals`: `collect_cw_crystals(...)` 返回的 metrics，可用于后续需要时输出或调试。
- `slots`: 关闭商店前已写入 session 的 `cw_state["slots"]` 深拷贝。
- `shop`: 关闭商店前缓存的 `project_cw_shop_snapshot(session)`，用于渲染刚扫描到的商店事实。`opened/stale` 只保留在结构化 data 中，不进入 `cw.portal.select` 文本输出，避免和最终已关闭商店的页面状态冲突。

### 文本输出

`cw.portal.select` renderer 顺序保持项目协议：

1. 首行：`ok cw.portal.select idx=... 投资环境=...`
2. `shot path=...`
3. `info read_image_first=1`
4. `info skill_info=... text=...`
5. `slot ...` 槽位行，与 `cw.slots.read` 的 slot 行格式一致。
6. `info 羁绊=...` 羁绊摘要行，与 `cw.slots.read` 一致。
7. `item ...` 商店商品行，与 `cw.shop.scan` 一致。
8. `info coins=... reserve_full=...` 和 `info stage_level=... stage_exp=... stage_team_size=... stage_status_stale=...`，与 `cw.shop.scan` 一致。
9. `warn ...`
10. `ref ...`
11. 最后一行继续是 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。

### 失败语义

自动收集动作是 `cw.portal.select` 成功后的一部分，不做 partial success。若收集水晶、读取 slot、打开/扫描/关闭商店任一步失败，整体 `cw.portal.select` 失败，并沿用 mutation side-effect/taint 恢复语义。这样避免返回“已选环境但备战事实不完整”的成功输出。

唯一例外是关闭商店失败：由于此前已经完成选环境、应用攻略、收集与扫描，失败仍应走现有 mutation taint 语义，而不是吞成 warning。用户或 Agent 可通过 `daemon.request_status`/重新识别恢复。

`CwSideEffectAppliedError` 仍只用于包住 `_apply_selected_guide_via_ui(...)` 的异常，因为该 helper 已经有专门的“攻略应用副作用可能已经发生”语义。应用攻略之后新增的点击、拖拽、OCR 与关闭商店动作依赖 `handle_mutation` 的 `_SideEffectTrackingRuntime` 和 `side_effect_applied` 分支处理；相关测试必须覆盖自动收集失败后的 taint、request status 和后续 mutation 阻断。

## 测试策略

- `tests/test_cw_slots.py`：验证 `build_cw_slots_reader(..., dismiss_initial_overlay=False)` 不执行初始 `INFO_DISMISS_POINT` 点击，但仍会在每个 slot 详情读取后关闭详情浮层。
- `tests/test_cw_shop.py`：验证 `build_cw_shop_page_snapshot_reader(...)` 只 OCR 当前商店页，不点击 reset/open；验证 `build_cw_shop_scan_snapshot_reader(...)` 仍保持原有 reset/open 行为。
- `tests/test_cw_shop.py` 或 `tests/test_cw_rpc_contracts.py`：验证 `cw.portal.select` 在 daemon service 中按 crystal -> slots -> open -> scan -> close 顺序组合，并在 close 前缓存 shop 输出。
- `tests/test_daemon_protocol.py`：更新现有 `cw.portal.select` 成功路径测试，补自动收集依赖的 patch 和新增 data 断言；新增自动收集失败后 taint/recover/followup-blocked 测试。
- `tests/test_output_rendering.py`：验证 `cw.portal.select` 渲染 skill_info、slot、羁绊、item、shop/stage info，warn/ref 之前输出，handoff 仍为最后一行。
- `AGENTS.md`、`README.md` 与相关 `skills/*/SKILL.md`：同步说明 `cw.portal.select` 成功后会自动收集普通备战事实，且最终截图停留在无浮层备战页；冻结新增 body 顺序和 shop `opened/stale` 不渲染到默认文本的约束。
