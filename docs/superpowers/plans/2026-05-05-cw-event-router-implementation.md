# CW 事件路由实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `cw.event.handle` 从固定点击特殊事件改为先识别事件、再按类型分发，并新增 `cw.event.reconcile` 处理 unknown 手工恢复后的状态重建。

**架构：** 在 `trail/scenes/cw/events.py` 引入 typed event router，由 router 负责只读检测和必要 UI 点击，由 `handle_cw_event()` 统一负责 session mutation。`CwService` 接入 router 与 reconcile，renderer 固定默认文本协议和 workflow handoff，CLI 做 YAML preflight，skills/docs/tests 同步输出契约。

**技术栈：** Python 3.12、Typer CLI、pytest、basedpyright/LSP、Trail daemon command service、项目默认文本 renderer。

**执行约束：** 本计划不授权创建 git commit；实现子代理不得执行 `git commit`，除非控制者在执行阶段明确追加授权。每个任务完成后必须运行该任务列出的测试与 LSP，再交给规格审查和代码质量审查。

---

## 规格来源

- 设计规格：`C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-05-05-cw-event-router-design.md`
- 项目协议：`C:\Users\34404\source\repos\trail-cli\AGENTS.md`
- 当前实现锚点：`trail/scenes/cw/events.py`、`trail/daemon/cw_service.py`、`trail/daemon/command_service.py`、`trail/commands/cw.py`、`trail/output/rendering.py`

## 文件结构

- 修改：`trail/scenes/cw/events.py`。定义 `CwEventType` / `CwEventOption` / `CwEventDetection` / `CwEventResult` / `CwEventRouter`，实现 `detect_cw_event()`、`build_cw_event_router()`、`mark_cw_event_unknown_stale()`，并重写 `handle_cw_event()`。
- 修改：`trail/scenes/cw/variable_cost.py`。让 `bind_cw_variable_cost_shop_item()` 在 `variable_cost_roles_stale=True` 时不从缓存回填 cost。
- 修改：`trail/scenes/cw/slots.py`。让 slots normalization 在 `variable_cost_roles_stale=True` 时不从缓存回填 LV999 cost/star，并让 fresh slots 观察能清 stale flag。
- 修改：`trail/daemon/cw_service.py`。接入 `event_router_factory`，新增 `cw.event.reconcile` handler，拆出 no-crystals preparation collect helper，处理 shop-page reconcile 分支。
- 修改：`trail/daemon/command_service.py`。把 `cw.event.reconcile` 加入 session-saving mutation 路径，并避免 reconcile fresh equipment 被 mutation wrapper 重标 stale。
- 修改：`trail/commands/cw.py`。新增 `trail cw event reconcile --session <id>`，并对 `cw.event.handle` / `cw.event.reconcile` 做 YAML preflight。
- 修改：`trail/output/rendering.py`。更新 `_render_cw_event_result()`、新增 reconcile renderer、扩展 `_select_workflow_handoff()` 支持 `event_types`，保持 handoff last-line。
- 修改：`skills/registry/workflow-handoffs.yaml`。注册 `cw.event.handle` 的 `event_types.unknown` handoff 到 `trail-cw-event-unknown`。
- 修改：`skills/trail-cw-event-unknown/SKILL.md`、`skills/trail-cw-event-unknown/references/manual-resolution-guide.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`、`AGENTS.md`。同步最终字段：`strategy` stale、`sell_plan` stale、`stale_facts`、`cw.event.reconcile`。
- 测试：`tests/test_cw_events.py`、`tests/test_cw_variable_cost.py`、`tests/test_cw_slots.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_daemon_protocol.py`、`tests/test_output_rendering.py`、`tests/test_skill_structure.py`、`tests/test_skill_routing_contracts.py`。

---

### 任务 1：事件 typed router 与 no-click unknown

**文件：**
- 修改：`trail/scenes/cw/events.py`
- 测试：`tests/test_cw_events.py`

- [ ] **步骤 1：编写失败的 router 单测**

在 `tests/test_cw_events.py` 增加 fake runtime 与核心 router 测试：

```python
class EventRouterRuntime:
    def __init__(self, pieces: list[dict[str, object]] | None = None):
        self.pieces = pieces or []
        self.clicks: list[tuple[int, int]] = []
        self.locate_calls: list[str] = []
        self.wait_calls: list[str] = []

    def ocr(self, **_kwargs):
        return self.pieces

    def locate(self, path: str):
        self.locate_calls.append(path)
        return None

    def wait_img(self, path: str, **_kwargs):
        self.wait_calls.append(path)
        return None

    def click_point(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


def test_detect_cw_event_unknown_does_not_click_runtime():
    events = load_cw_events_module()
    runtime = EventRouterRuntime([_ocr_piece("无法识别的新事件")])
    result = events.build_cw_event_router(runtime)(None)
    assert result["event_type"] == "unknown"
    assert result["handled"] is False
    assert result["next_action"] == "manual"
    assert runtime.clicks == []
```

再加参数化测试，输入 `补给`、`投资事件`、`遭遇事件`、`命运卜者`，分别断言 `event_type` 为 `replenish` / `invest` / `encounter` / `fortune`，`handled=False`，`next_action` 为对应 choose 命令，且 `runtime.clicks == []`。

再加 handle 层测试：`test_handle_cw_event_recognized_unhandled_does_not_mark_stale_or_click`，对 `replenish`、`invest`、`encounter`、`fortune` 参数化，预置 stage/status、slots、shop、equipment、strategy、sell_plan、metrics，调用 `handle_cw_event(... router=lambda _: {"event_type": event_type, "handled": False, "next_action": next_action})`，断言返回 `handled is False`、`next_action` 正确、所有 session facts 未被标 stale、metrics 未变、runtime clicks 为空。

再加 `test_handle_cw_event_lv999_without_choice_does_not_mark_stale`，检测为 `lv999_choice` 但未传 `variable_cost_choice` 时返回 `handled=0 next_action=manual` 或 `next_action="cw.event.handle --variable-cost-choice"`，不得标 stale、不得点击。

再加 `test_special_confirm_router_uses_legacy_confirm_flow`，fake runtime 返回旧特殊确认模板，断言输出 `event_type="special_confirm"`、`handled=True`、`handled_action="confirm"`，并保留旧确认点击行为。

- [ ] **步骤 2：运行 router 红灯测试**

运行：

```powershell
uv run pytest tests/test_cw_events.py::test_detect_cw_event_unknown_does_not_click_runtime tests/test_cw_events.py::test_detect_cw_event_recognized_unhandled_is_no_click tests/test_cw_events.py::test_handle_cw_event_recognized_unhandled_does_not_mark_stale_or_click tests/test_cw_events.py::test_handle_cw_event_lv999_without_choice_does_not_mark_stale tests/test_cw_events.py::test_special_confirm_router_uses_legacy_confirm_flow --basetemp .pytest-tmp/cw-event-router-task1-red
```

预期：失败，错误包含 `AttributeError` 或断言显示 `build_cw_event_router` 不存在 / 旧 handler 仍固定点击。

- [ ] **步骤 3：实现 typed contracts 与最小检测**

在 `trail/scenes/cw/events.py` 添加 `Literal` / `TypedDict` 类型：`CwEventType`、`CwEventOption`、`CwEventDetection`、`CwEventResult`、`CwEventRouter`。实现 `detect_cw_event(runtime) -> CwEventDetection`：只读 OCR / 模板，不点击；识别 `lv999_choice`、`special_confirm`、`replenish`、`invest`、`encounter`、`fortune`；识别不到返回 `unknown`。实现 `build_cw_event_router(runtime)`：`special_confirm` 继续执行旧确认点击并返回 `handled=True handled_action=confirm`；`lv999_choice` 未收到合法 payload 时返回 `handled=False next_action=manual`；`replenish|invest|encounter|fortune` 返回 `handled=False next_action=<对应 choose 命令>` 且不点击；`unknown` 返回 `handled=False next_action=manual` 且不点击。`handle_cw_event()` 只对 `unknown` 调 `mark_cw_event_unknown_stale()`，不得对 recognized-but-unhandled 结果标 stale。

- [ ] **步骤 4：运行 task 1 绿灯测试**

运行：

```powershell
uv run pytest tests/test_cw_events.py::test_detect_cw_event_unknown_does_not_click_runtime tests/test_cw_events.py::test_detect_cw_event_recognized_unhandled_is_no_click tests/test_cw_events.py::test_handle_cw_event_recognized_unhandled_does_not_mark_stale_or_click tests/test_cw_events.py::test_handle_cw_event_lv999_without_choice_does_not_mark_stale tests/test_cw_events.py::test_special_confirm_router_uses_legacy_confirm_flow --basetemp .pytest-tmp/cw-event-router-task1-green
```

预期：通过。

---

### 任务 2：LV999 choice 分发与 unknown stale helper

**文件：**
- 修改：`trail/scenes/cw/events.py`
- 修改：`trail/scenes/cw/variable_cost.py`
- 修改：`trail/scenes/cw/slots.py`
- 测试：`tests/test_cw_events.py`
- 测试：`tests/test_cw_variable_cost.py`
- 测试：`tests/test_cw_slots.py`

- [ ] **步骤 1：编写失败的 stale helper 与 LV999 tests**

在 `tests/test_cw_events.py` 增加 `test_handle_cw_event_unknown_marks_readable_facts_stale_without_touching_metrics`：构造 fake CW session，预置 `stage/status`、`slots`、`shop`、`equipment`、`strategy`、`sell_plan`、`metrics`、`last_result`、`last_screenshot`、`last_stage`；调用 `handle_cw_event(session, router=lambda _choice: {"event_type": "unknown", "handled": False, "next_action": "manual"})`；断言 stage/status、slots、shop、equipment、strategy、sell_plan 均 `stale=True`，`variable_cost_roles_stale=True`，metrics/last_result/last_screenshot 保持，last_stage 为 `None`。

在 `tests/test_cw_variable_cost.py` 增加 `test_bind_variable_cost_shop_item_does_not_backfill_when_roles_stale`：`cw_state={"variable_cost_roles_stale": True, "variable_cost_roles": {"银狼LV.999": {"cost": 4}}}`，绑定 `{"name": "银狼LV.999"}` 后不得输出 `cost`，必须 `stale=True`、`uncertain=True`。

在 `tests/test_cw_slots.py` 通过现有 `read_cw_slots` / `_read_cw_slots_variable_cost` 路径新增 `test_read_cw_slots_does_not_backfill_lv999_cost_when_variable_cost_roles_stale`，不要直接调用 `_known_variable_cost_phase` 或 `_normalize_variable_cost_slot_value`。当 `variable_cost_roles_stale=True` 且 slot 值只有 `{"name": "银狼LV.999"}` 时，断言 response slot 没有 `cost`，有 `uncertain=True`、`stale=True`，warnings 包含 `CW_SLOTS_LV999_COST_UNKNOWN`，且 stale flag 未被“未可靠观察”清除。

增加 LV999 前置校验测试：

- `test_handle_cw_event_rejects_variable_choice_on_non_lv999_detection_without_click`：页面检测不是 `lv999_choice` 但传入 `variable_cost_choice`，抛 `CW_EVENT_CHOICE_MISMATCH`，runtime clicks 为空，状态未变。
- `test_handle_cw_event_rejects_lv999_missing_option_center_without_click`：检测为 `lv999_choice` 但目标 option 无 `center`，抛 `CW_EVENT_CHOICE_TARGET_MISSING`，不点击、不调用 `apply_cw_variable_cost_choice()`。
- `test_handle_cw_event_rejects_lv999_stale_state_before_click_or_apply`：`variable_cost_roles_stale=True` 且没有 fresh direct cost/star observation，抛 `CW_EVENT_LV999_STATE_STALE`，不点击、不应用状态转移。
- `test_handle_cw_event_rejects_invalid_lv999_choice_payload_before_click`：缺 `role_name=银狼LV.999`、`choice_available=True` 或 choice id 不匹配时均失败，且失败发生在任何点击前。

- [ ] **步骤 2：运行 stale/LV999 红灯测试**

运行：

```powershell
uv run pytest tests/test_cw_events.py::test_handle_cw_event_unknown_marks_readable_facts_stale_without_touching_metrics tests/test_cw_events.py::test_handle_cw_event_rejects_variable_choice_on_non_lv999_detection_without_click tests/test_cw_events.py::test_handle_cw_event_rejects_lv999_missing_option_center_without_click tests/test_cw_events.py::test_handle_cw_event_rejects_lv999_stale_state_before_click_or_apply tests/test_cw_events.py::test_handle_cw_event_rejects_invalid_lv999_choice_payload_before_click tests/test_cw_variable_cost.py::test_bind_variable_cost_shop_item_does_not_backfill_when_roles_stale tests/test_cw_slots.py::test_read_cw_slots_does_not_backfill_lv999_cost_when_variable_cost_roles_stale --basetemp .pytest-tmp/cw-event-router-task2-red
```

预期：失败，原因是 `handle_cw_event()` 仍使用 `handler`，没有 stale helper；variable cost 仍从缓存回填。

- [ ] **步骤 3：实现 unknown stale helper**

在 `events.py` 引入 `mark_cw_stage_status_stale` 与 `ensure_cw_state`，实现 `mark_cw_event_unknown_stale(session)`。该 helper 调用 `mark_cw_stage_stale(session)` 后调用 `mark_cw_stage_status_stale(session)`，再把 `slots`、`shop`、`equipment`、`strategy`、`sell_plan` 子树保留并设 `stale=True`；缺失 `strategy` 写 `{"cards": [], "stale": True}`，其它缺失写 `{"stale": True}`；最后设置 `cw_state["variable_cost_roles_stale"] = True`。

- [ ] **步骤 4：实现 variable-cost stale reader rules**

在 `variable_cost.py` 添加 `is_variable_cost_roles_stale(cw_state)`，缺失按 false。在 `bind_cw_variable_cost_shop_item()` 中，只有 flag 非 true 时才允许从 `_variable_state()` 回填 cost。在 `slots.py` 中复用该 helper；`_known_variable_cost_phase()` 若 stale flag true，返回 `None`；`_update_variable_cost_roles_from_slots()` 成功观察 reliable LV999 entries 后设置 `cw_state["variable_cost_roles_stale"] = False`。

- [ ] **步骤 5：实现 LV999 state-stale failure**

在 `events.py` 的 LV999 分发中，先校验 session 中 `variable_cost_roles_stale`。当 flag true 且 detection 没有 fresh direct cost/star 时，抛出 `TrailError("CW_EVENT_LV999_STATE_STALE", "银狼LV.999 状态已过期，请先运行 cw.event.reconcile")`。该错误必须发生在任何 `runtime.click_point()` 之前。缺 option center 时抛 `CW_EVENT_CHOICE_TARGET_MISSING`，页面不是 `lv999_choice` 但带 payload 时抛 `CW_EVENT_CHOICE_MISMATCH`。

- [ ] **步骤 6：运行 task 2 绿灯测试**

运行：

```powershell
uv run pytest tests/test_cw_events.py tests/test_cw_variable_cost.py tests/test_cw_slots.py --basetemp .pytest-tmp/cw-event-router-task2-green
```

预期：相关测试通过；若大文件既有 unrelated 失败，记录失败测试名并先运行本任务新增测试确认绿灯。

---

### 任务 3：renderer、workflow handoff 与 YAML preflight

**文件：**
- 修改：`trail/output/rendering.py`
- 修改：`trail/commands/cw.py`
- 修改：`skills/registry/workflow-handoffs.yaml`
- 测试：`tests/test_output_rendering.py`
- 测试：`tests/test_cw_rpc_contracts.py`

- [ ] **步骤 1：编写 renderer / handoff 红灯测试**

在 `tests/test_output_rendering.py` 增加 `test_render_cw_event_handle_unknown_handoff_is_last_line`。测试 monkeypatch `_load_workflow_handoffs()` 返回：

```python
{"cw.event.handle": {"event_types": {"unknown": {"handoff_skill": "trail-cw-event-unknown", "handoff_strength": "strong", "handoff_reason": "event_unknown_manual_required"}}}}
```

payload 使用 `data={"event_type": "unknown", "handled": False, "next_action": "manual", "stale": True, "stale_facts": "stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles", "crystals_stale": False, "reconcile_action": "cw.event.reconcile"}` 和 screenshot。断言输出顺序：首行、`shot path=...`、`info read_image_first=1`、完整实体行 `info stale=1 stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles crystals_stale=0 reconcile_action=cw.event.reconcile`、最后一行为 `info handoff_skill=trail-cw-event-unknown ...`。

在 `tests/test_cw_rpc_contracts.py` 更新 event handle 既有测试：known success 首行应为 `ok cw.event.handle event_type=special_confirm handled=1 handled_action=confirm`；变量选择 success 保留 `variable_cost_choice=cost_up`；unknown success stdout 包含 screenshot/read-image/stale info/handoff last line。新增 YAML preflight 测试，断言 `trail --format yaml cw event handle ...` 与 `trail --format yaml cw event reconcile ...` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED` 且 fake daemon 未收到调用。

在 `tests/test_output_rendering.py` 增加 `test_render_cw_event_reconcile_outputs_capture_and_falsy_fields_by_key_presence`：payload `data={"stage": "preparation", "stale": False, "reconciled": "stage|slots|shop|equipment", "stale_facts": "none"}` 且带 screenshot，断言首行包含 `stale=0 stale_facts=none`，`shot path=...` 后紧跟 `info read_image_first=1`。

在 `tests/test_cw_rpc_contracts.py` 增加 `test_cw_event_reconcile_cli_calls_daemon_with_empty_payload`：执行 `trail cw event reconcile --session sess-1`，断言 fake daemon 收到 method `cw.event.reconcile`、payload `{}`。

- [ ] **步骤 2：运行 renderer 红灯测试**

运行：

```powershell
uv run pytest tests/test_output_rendering.py::test_render_cw_event_handle_unknown_handoff_is_last_line tests/test_cw_rpc_contracts.py::test_cw_event_handle_renders_event_result --basetemp .pytest-tmp/cw-event-router-task3-red
```

预期：失败，原因是 `_select_workflow_handoff()` 不支持 `event_types`，renderer 不输出 `handled` / stale info。

- [ ] **步骤 3：实现 renderer 与 handoff**

在 `_select_workflow_handoff()` 中读取 `data.event_type`，若 registry 有 `event_types` 且命中则返回该 handoff。保留现有 status/stage/default 行为。

重写 `_render_cw_event_result()`：不用 `_render_success_summary()`；按顺序构造首行、capture block、`info stale=...`、warnings、references。`handled`、`stale`、`crystals_stale` 必须按 key presence 渲染，不能因 `False` 被省略。

新增 `_render_cw_event_reconcile_result()`，首行字段顺序固定为：`stage`、`stale`、`reconciled`、`stale_facts`、`next_action`；body 输出 `info stale=... stale_facts=... todo=... why=...`。在 renderer dispatch 中注册 `cw.event.reconcile`。

- [ ] **步骤 4：实现 YAML preflight 与 registry**

在 `trail/commands/cw.py` 添加 `_reject_yaml_for_cw_mutation(command: str) -> bool`：当 `current_output_format() is OutputFormat.YAML` 时调用 `print_output(command, _output_format_not_supported_response(command))` 并返回 `True`；否则返回 `False`。调用方必须 `return`，不要 `raise typer.Exit(1)`，保持现有协议 failure text、`exit_code == 0` 与 no-RPC 行为。在 `cw_event_handle()` 与新增 `cw_event_reconcile()` 调用 `_rpc_cw()` 前执行该 helper。

更新 `skills/registry/workflow-handoffs.yaml`：

```yaml
  cw.event.handle:
    event_types:
      unknown:
        handoff_skill: trail-cw-event-unknown
        handoff_strength: strong
        handoff_reason: event_unknown_manual_required
```

- [ ] **步骤 5：运行 task 3 绿灯测试**

运行：

```powershell
uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py --basetemp .pytest-tmp/cw-event-router-task3-green
```

预期：相关 event/rendering tests 通过；不允许新增 YAML allowlist。

---

### 任务 4：daemon 接入 router 与 `cw.event.reconcile`

**文件：**
- 修改：`trail/daemon/cw_service.py`
- 修改：`trail/daemon/command_service.py`
- 修改：`trail/commands/cw.py`
- 测试：`tests/test_daemon_protocol.py`
- 测试：`tests/test_cw_events.py`

- [ ] **步骤 1：编写 daemon / reconcile 红灯测试**

在 `tests/test_daemon_protocol.py` 增加：

```python
def test_command_service_cw_event_reconcile_is_mutating_method():
    from trail.daemon.command_service import CW_MUTATING_METHODS
    assert "cw.event.reconcile" in CW_MUTATING_METHODS
```

在 `tests/test_cw_events.py` 增加 `test_cw_event_reconcile_unknown_stage_keeps_stale_and_metrics`：构造 harness，预置 metrics 和 stale facts，monkeypatch stage detector 返回 `None`；运行 `_run_cw_mutation(... method="cw.event.reconcile", payload={})`；断言 envelope ok，data `stage="unknown"`、`stale=True`、`stale_facts` 包含 stage/status 等，data 没有 `crystals`，persisted metrics 不变，`last_stage is None`。

新增 no-crystals 与 reader side-effect 测试：

- `test_collect_cw_preparation_facts_include_crystals_false_omits_crystals_key`：直接调用 preparation collect helper，传 `include_crystals=False`、`order=()`，monkeypatch `collect_cw_crystals` 为会失败的函数，断言 response 无 `crystals` 且 metrics 不变。
- `test_cw_event_reconcile_preparation_restores_stale_sell_plan_after_reader_side_effect`：stage detector 返回 `preparation`，monkeypatch preparation collect helper 模拟 reader 清空 `cw_state["sell_plan"]` 并返回 fresh slots；断言 persisted old `sell_plan` 被恢复且 `stale=True`，`strategy` 仍 stale，`stale_facts` / `todo` 包含 `strategy|sell_plan`，response 无 `crystals`。

补充 reconcile 行为测试：

- `test_cw_event_reconcile_preparation_collects_no_crystals_and_only_fresh_reconciled`：preparation 阶段执行 slots -> shop -> equipment，response 无 `crystals`，metrics 不变，只有成功且 fresh 的 facts 进入 `reconciled`。
- `test_cw_event_reconcile_shop_scans_current_shop_before_close_without_open_shop`：shop 阶段先调用 scan，不调用 open-shop；若 close 后 shop fresh 才把 `shop` 放入 `reconciled`，否则保留在 `stale_facts`。
- `test_cw_event_reconcile_reader_failure_goes_to_attempted_todo_not_reconciled`：reader 报错时只进入 `attempted` / `todo`，不得进入 `reconciled`。
- `test_cw_event_reconcile_preserves_stale_strategy_and_sell_plan`：未显式运行 strategy reader 或 `cw.hand.sell_plan` 时，`strategy` / `sell_plan` 保持 `stale=True`，并出现在 `stale_facts` / `todo`。
- `test_cw_event_reconcile_restores_sell_plan_if_reader_clears_it`：复用 reader 清除旧 sell_plan 时，reconcile 恢复旧 sell_plan 并保持 `stale=True`。
- `test_cw_event_reconcile_keeps_fresh_equipment_after_mutation_wrapper`：reconcile 返回 fresh equipment 后，command service 收尾不得重新标记 equipment stale。
- `test_cw_event_reconcile_variable_cost_roles_stale_rules`：只有 fresh slots/shop 可靠观察到所有可见 LV999 cost/star 且无低置信/费用未知 warning 时清除 `variable_cost_roles_stale`；未看见 LV999 且 session 仍有历史记录时不得清除。

- [ ] **步骤 2：运行 daemon 红灯测试**

运行：

```powershell
uv run pytest tests/test_daemon_protocol.py::test_command_service_cw_event_reconcile_is_mutating_method tests/test_cw_events.py::test_cw_event_reconcile_unknown_stage_keeps_stale_and_metrics --basetemp .pytest-tmp/cw-event-router-task4-red
```

预期：失败，`cw.event.reconcile` 不存在或 daemon method unsupported。

- [ ] **步骤 3：接入 router factory**

在 `cw_service.py` 将 import/factory 改为 `build_cw_event_router`。handlers 中 `cw.event.handle` 调用：

```python
"cw.event.handle": lambda: _call_with_supported_keywords(
    handle_cw_event,
    session,
    router=event_router_factory(runtime()),
    variable_cost_choice=payload.get("variable_cost_choice"),
),
```

保留旧 `event_handler_factory` 名称只作为兼容 alias 时，不让新 daemon 继续传 `handler=`。

- [ ] **步骤 4：实现 no-crystals collect helper**

修改 `_collect_cw_preparation_facts()` 增加 `include_crystals: bool = True`。只有 `include_crystals` 为 true 时才写 `data["crystals"] = deepcopy(ensure_cw_state(session).get("metrics") or {})`。`cw.portal.select` 与 `cw.battle.run` 保持默认 true；`cw.event.reconcile` 使用 false。

- [ ] **步骤 5：实现 `cw.event.reconcile` handler**

在 `cw_service.py` 增加 `run_event_reconcile()`：

1. 保存旧 `sell_plan`，用于 reader 清除时恢复 stale sell_plan。
2. 调用 stage detector 安全包装；unknown / `None` 时只返回 `{"stage": "unknown", "stale": True, "reconciled": "none", "stale_facts": "stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles", "next_action": "manual"}`。
3. known stage 只把 `stage` 放入 `reconciled`，stage.status 仍 stale，直到 slots/status reader 刷新。
4. `preparation` 调 `_collect_cw_preparation_facts(... order=("slots", "shop", "equipment"), include_crystals=False)`。
5. `shop` 先 scan 当前 shop，不调用 open-shop；若 close 后 persisted shop stale，则不要把 `shop` 放入 `reconciled`。
6. 恢复 stale sell_plan；保持 strategy stale，除非当前页明确运行 strategy reader。
7. 计算 `stale`、`stale_facts`、`reconciled`、`attempted`、`todo`。

在 handlers 中注册 `"cw.event.reconcile": run_event_reconcile`。

- [ ] **步骤 6：更新 command service mutation 集合与 equipment guard**

在 `CW_MUTATING_METHODS` 加入 `cw.event.reconcile`。更新 `_has_fresh_auto_collect_equipment()` 或等价收尾判断，让本次 reconcile 返回 `equipment.stale=0` 时不被通用 mutation 收尾重标 stale。

- [ ] **步骤 7：运行 task 4 绿灯测试**

运行：

```powershell
uv run pytest tests/test_daemon_protocol.py tests/test_cw_events.py --basetemp .pytest-tmp/cw-event-router-task4-green
```

预期：event/reconcile 相关 tests 通过；metrics 不变；response data 无 `crystals`。

---

### 任务 5：skill / 文档同步与契约补全

**文件：**
- 修改：`AGENTS.md`
- 修改：`skills/trail-cw-event-unknown/SKILL.md`
- 修改：`skills/trail-cw-event-unknown/references/manual-resolution-guide.md`
- 修改：`skills/trail-cw-event-unknown/evals/triggers.json`
- 修改：`skills/trail-cw-prep/SKILL.md`
- 修改：`skills/trail-cw-prep/references/command-surface.md`
- 检查/必要时修改：`skills/registry/scene-entries.yaml`
- 测试：`tests/test_skill_structure.py`
- 测试：`tests/test_skill_routing_contracts.py`
- 测试：`tests/test_skill_registry.py`

- [ ] **步骤 1：编写文档约束测试**

更新 `tests/test_skill_structure.py` 对 `trail-cw-event-unknown` 的断言，要求文档包含：`strategy`、`sell_plan`、`stale_facts`、`CW_EVENT_LV999_STATE_STALE`、`cw.hand.sell_plan`、`cw.event.reconcile`。更新 prep 相关断言，要求普通 prep 遇到 unknown 时交给 `trail-cw-event-unknown`，且 reconcile 后按 stale_facts 补跑。增加 `skills/trail-cw-event-unknown/evals/triggers.json` 覆盖，确保 unknown event 触发专用 skill，且不把 `trail-cw-events` 写回 active 路由。

在 `tests/test_skill_registry.py` 增加断言：`trail-cw-event-unknown` 保持 `status=active`、`exposure=internal`，不是 public scene entry；active registry、skills、handoffs 与 trigger fixture 不得引用 archive skill `trail-cw-events`。

- [ ] **步骤 2：运行文档红灯测试**

运行：

```powershell
uv run pytest tests/test_skill_structure.py tests/test_skill_routing_contracts.py tests/test_skill_registry.py --basetemp .pytest-tmp/cw-event-router-task5-red
```

预期：失败，缺少新增字段或文档尚未同步。

- [ ] **步骤 3：同步文档**

在 `AGENTS.md` 和 skill 文档中写明：unknown stale facts 为 `stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles`；`crystals_stale=0`；手工处理后先运行 `trail cw event reconcile --session <id>`；若 `stale_facts` 仍含 `sell_plan`，使用 `cw.hand.sell_plan` 重算；若含 `strategy`，先运行 strategy detect/select 流程；LV999 stale 状态报 `CW_EVENT_LV999_STATE_STALE` 时先 reconcile。

- [ ] **步骤 4：运行文档绿灯测试**

运行：

```powershell
uv run pytest tests/test_skill_structure.py tests/test_skill_routing_contracts.py tests/test_skill_registry.py --basetemp .pytest-tmp/cw-event-router-task5-green
```

预期：通过。

---

### 任务 6：集成验证与手工 QA

**文件：**
- 检查全部本计划修改文件
- 测试：相关 targeted 与 full suite

- [ ] **步骤 1：运行 targeted regression**

运行：

```powershell
uv run pytest tests/test_cw_events.py tests/test_cw_variable_cost.py tests/test_cw_slots.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py tests/test_skill_registry.py --basetemp .pytest-tmp/cw-event-router-targeted
```

预期：通过。

- [ ] **步骤 2：运行 LSP error diagnostics**

对以下文件运行 error 级 LSP diagnostics：

```text
trail/scenes/cw/events.py
trail/scenes/cw/variable_cost.py
trail/scenes/cw/slots.py
trail/daemon/cw_service.py
trail/daemon/command_service.py
trail/commands/cw.py
trail/output/rendering.py
tests/test_cw_events.py
tests/test_cw_variable_cost.py
tests/test_cw_slots.py
tests/test_cw_rpc_contracts.py
tests/test_daemon_protocol.py
tests/test_output_rendering.py
tests/test_skill_structure.py
tests/test_skill_routing_contracts.py
```

预期：源文件 error 为 0；测试文件若有既有 basedpyright 债，记录具体首个错误类别，不在本任务中无关修复。

- [ ] **步骤 3：运行 full regression**

运行：

```powershell
uv run pytest --basetemp .pytest-tmp/cw-event-router-full
```

预期：通过，或只剩与本计划无关且有证据的既有失败。

- [ ] **步骤 4：手工 QA CLI surface**

用 fake daemon client 或最小 driver 驱动默认文本 surface，至少覆盖：

```powershell
uv run python -c "from trail.output.rendering import render_output; payload={'ok': True, 'screenshot': '.trail/shots/req-cw-event.png', 'data': {'event_type': 'unknown', 'handled': False, 'next_action': 'manual', 'stale': True, 'stale_facts': 'stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles', 'crystals_stale': False, 'reconcile_action': 'cw.event.reconcile'}, 'warnings': [], 'references': []}; print(render_output('cw.event.handle', payload))"
uv run python -c "from trail.output.rendering import render_output; payload={'ok': True, 'screenshot': '.trail/shots/req-cw-event-reconcile.png', 'data': {'stage': 'unknown', 'stale': True, 'reconciled': 'none', 'stale_facts': 'stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles', 'next_action': 'manual'}, 'warnings': [], 'references': []}; print(render_output('cw.event.reconcile', payload))"
uv run trail --format yaml cw event handle --session aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
uv run trail --format yaml cw event reconcile --session aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

确认 handle / reconcile 输出首行、截图顺序、`info read_image_first=1`、stale facts 符合协议；确认只有 `cw.event.handle event_type=unknown` 因 registry 配置输出 handoff 最后一行，`cw.event.reconcile` 不应输出 handoff；确认两个 YAML 命令返回 `OUTPUT_FORMAT_NOT_SUPPORTED` 且不要求 daemon 可用。

- [ ] **步骤 5：最终自检**

检查：无新增 YAML allowlist；无 archive skill 名称 `trail-cw-events` 回流；`docs/superpowers/specs` 与 `.sisyphus/plans` 未被测试扫描；工作树中非本任务改动（已知 `tests/test_session_store.py`、`trail/session/models.py`）未被修改或回退。
