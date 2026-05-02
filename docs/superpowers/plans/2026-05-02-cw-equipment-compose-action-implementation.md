# CW Equipment Compose Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `cw.equipment.compose` 从只写 session 的记录命令改为真实执行装备合成、验证并装备到目标角色的 UI mutation。

**Architecture:** Scene 层新增 `compose_and_equip_cw_equipment()`，集中处理 preflight read、材料选择、拖动、验证和 session 写入。Daemon 层注入 runtime、slots reader、guide config、raw config、recognizer 与 preflight read scope，并把命令接入真实 mutation journal。Renderer 保持旧首行，追加截图和固定分段事实。

**Tech Stack:** Python 3.12、Typer、pytest、项目内 CW scene helpers、daemon mutation journal、text renderer。

---

## 执行约束

- Worktree：`C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-compose-action`。
- Spec：`C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-05-02-cw-equipment-compose-action-design.md`。
- Plan：`C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-compose-action\docs\superpowers\plans\2026-05-02-cw-equipment-compose-action-implementation.md`。
- 基线：`uv run pytest` 已通过，`1908 passed, 29 skipped`。
- 所有新启动子代理提示词必须包含上面三个完整路径，且正文不少于 2000 字。
- 不要修改主工作区已有无关变更；不要提交 git commit，除非用户明确要求。

## 文件职责

- `trail/scenes/cw/equipment.py`：新增 preflight helper、材料选择 helper、身份多重集合验证 helper、`compose_and_equip_cw_equipment()`。
- `trail/daemon/cw_service.py`：调用新 scene helper；资源和 reader 注入；preflight read 不计 side effect；success 截图必需。
- `trail/daemon/command_service.py`：迁移 `cw.equipment.compose` 到 `CW_MUTATING_METHODS`；提升 `error.warnings`。
- `trail/output/rendering.py`：扩展 compose success 分段和材料不足 warning。
- `trail/commands/cw.py`：更新 help。
- `AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`：同步 active 协议。
- `tests/test_cw_equipment.py`、`tests/test_daemon_protocol.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_output_rendering.py`、`tests/test_output_debug.py`、`tests/test_atomic_commands.py`：对应契约测试。

---

### Task 1: Scene Material Selection TDD

**Files:**
- Modify: `tests/test_cw_equipment.py`

- [ ] **Step 1: Add compose test helpers**

Add after `_equipment_raw_config_for_recommendations()`:

```python
class _ComposeRuntime:
    def __init__(self):
        self.drags = []

    def drag_to(self, from_x, from_y, to_x, to_y, **kwargs):
        self.drags.append((from_x, from_y, to_x, to_y, kwargs))


def _equipment_item(idx, name, equipment_id, cache_key, *, uncertain=False, score=0.99, gap=0.50):
    return {
        "idx": idx,
        "pos": f"equipment:{idx}",
        "name": name,
        "equipment_id": equipment_id,
        "cache_key": cache_key,
        "score": score,
        "gap": gap,
        "uncertain": uncertain,
        "center": {"x": 1000 + idx, "y": 500 + idx},
    }


def _equipment_snapshot(*items, recommendations=None):
    snapshot = {
        "count": len(items),
        "uncertain": sum(1 for item in items if item.get("uncertain")),
        "empty": 60 - len(items),
        "items": list(items),
        "backend": "vector",
        "layout": "default",
        "stale": False,
    }
    if recommendations is not None:
        snapshot["recommendations"] = recommendations
    return snapshot


def _slots_reader_for(front=None, back=None, hand=None):
    return lambda: (
        front if front is not None else [{"name": "希儿"}],
        back if back is not None else ["佩拉"],
        hand if hand is not None else [],
    )


def _compose_raw_config(*, basics=None, advanced_id="saw", advanced_name="高周波电锯"):
    basics = basics or [
        {"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"},
        {"id": "battery", "name": "光能电池", "icon": "https://act-webstatic.mihoyo.com/battery.png"},
    ]
    return {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": advanced_id, "name": advanced_name, "icon": "https://act-webstatic.mihoyo.com/saw.png", "compose_list": [{"childrens": list(basics)}]}],
    }
```

- [ ] **Step 2: Add failing material tests**

Add tests for these exact behaviors:

```python
def test_select_compose_materials_chooses_lowest_idx_for_two_different_basics(tmp_path):
    equipment = load_equipment_module()
    snapshot = _equipment_snapshot(
        _equipment_item(4, "基础装甲", "armor", "basic-armor"),
        _equipment_item(2, "基础装甲", "armor", "basic-armor"),
        _equipment_item(5, "光能电池", "battery", "basic-battery"),
    )
    result = equipment.select_cw_equipment_compose_materials(snapshot, name="高周波电锯", raw_config=_compose_raw_config())
    assert [item["idx"] for item in result["materials"]] == [2, 5]
    assert result["needed"] == [{"name": "基础装甲", "need": 1, "have": 2}, {"name": "光能电池", "need": 1, "have": 1}]


def test_select_compose_materials_need_two_uses_two_distinct_lowest_items(tmp_path):
    equipment = load_equipment_module()
    raw_config = _compose_raw_config(basics=[{"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"}, {"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"}])
    snapshot = _equipment_snapshot(_equipment_item(3, "基础装甲", "armor", "basic-armor"), _equipment_item(1, "基础装甲", "armor", "basic-armor"), _equipment_item(2, "基础装甲", "armor", "basic-armor"))
    result = equipment.select_cw_equipment_compose_materials(snapshot, name="高周波电锯", raw_config=raw_config)
    assert [item["idx"] for item in result["materials"]] == [1, 2]


def test_select_compose_materials_rejects_missing_materials_with_needed_and_held(tmp_path):
    equipment = load_equipment_module()
    snapshot = _equipment_snapshot(_equipment_item(1, "基础装甲", "armor", "basic-armor"))
    with pytest.raises(Exception) as exc_info:
        equipment.select_cw_equipment_compose_materials(snapshot, name="高周波电锯", raw_config=_compose_raw_config())
    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_MATERIALS_MISSING"
    assert exc_info.value.warnings == [{"code": "CW_EQUIPMENT_MATERIALS_MISSING", "message": "合成 高周波电锯 的基础装备不足", "需求": "基础装甲:1/1|光能电池:0/1", "持有": "equipment:1:基础装甲"}]


def test_select_compose_materials_rejects_uncertain_material_before_drag(tmp_path):
    equipment = load_equipment_module()
    snapshot = _equipment_snapshot(_equipment_item(1, "基础装甲", "armor", "basic-armor", uncertain=True, score=0.61, gap=0.02), _equipment_item(2, "光能电池", "battery", "basic-battery"))
    with pytest.raises(Exception) as exc_info:
        equipment.select_cw_equipment_compose_materials(snapshot, name="高周波电锯", raw_config=_compose_raw_config())
    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_MATERIAL_UNCERTAIN"
    assert exc_info.value.data["material"]["idx"] == 1


def test_select_compose_materials_does_not_use_advanced_item_when_id_matches_basic(tmp_path):
    equipment = load_equipment_module()
    raw_config = _compose_raw_config(advanced_id="same-id", basics=[{"id": "same-id", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/basic.png"}, {"id": "battery", "name": "光能电池", "icon": "https://act-webstatic.mihoyo.com/battery.png"}])
    snapshot = _equipment_snapshot(_equipment_item(1, "高周波电锯", "same-id", "advanced-same-id"), _equipment_item(2, "光能电池", "battery", "basic-battery"))
    with pytest.raises(Exception) as exc_info:
        equipment.select_cw_equipment_compose_materials(snapshot, name="高周波电锯", raw_config=raw_config)
    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_MATERIALS_MISSING"
```

- [ ] **Step 3: Run and verify failure**

Run: `uv run pytest tests/test_cw_equipment.py -k "select_compose_materials" -v`

Expected: FAIL because `select_cw_equipment_compose_materials` is missing.

---

### Task 2: Scene Material And Preflight Implementation

**Files:**
- Modify: `trail/scenes/cw/equipment.py`
- Test: `tests/test_cw_equipment.py`

- [ ] **Step 1: Add imports**

Update imports in `trail/scenes/cw/equipment.py`:

```python
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import nullcontext
```

Extend imports with `equipment_slot_center`, `SLOT_POINTS_BY_AREA`, and `read_cw_slots`.

- [ ] **Step 2: Split existing record logic**

Extract current `record_cw_equipment_compose()` validation into:

```python
def validate_cw_equipment_compose_preflight(session: SessionModel, *, name: str, slot: str, role: str, raw_config: dict[str, Any] | None = None, workspace_root: str | Path | None = None) -> dict[str, Any]:
    # Move the current validation body from record_cw_equipment_compose here and return the validated state dict.
    return preflight

def commit_cw_equipment_compose_record(session: SessionModel, preflight: dict[str, Any]) -> dict[str, Any]:
    # Move only the final equipments append, role value replacement, stale marking, and result dict here.
    return {"pos": preflight["agent_slot"], "name": preflight["role_name"], "equipment": preflight["equipment_name"], "count": len(equipments)}
```

The preflight dict must include `equipment_name`、`role_name`、`area`、`index`、`agent_slot`、`values`、`value`、`equipments`. `record_cw_equipment_compose()` must call these two helpers and keep all existing error codes unchanged.

- [ ] **Step 3: Implement material selection helper**

Add `_as_list()`、`_item_identity_keys()`、`_recipe_child_match_key()`、`_equipment_identity_counter()` and `select_cw_equipment_compose_materials(snapshot, *, name, raw_config)`.

Implementation requirements:

- Use `build_cw_equipment_recipes(raw_config)`.
- Raise `CW_EQUIPMENT_RECIPE_MISSING` when the target recipe is absent.
- Raise `CW_EQUIPMENT_RECIPE_UNSUPPORTED` unless total basic need is exactly `2`.
- Match materials by `cache_key`, then `basic:<id>`, then `name`.
- Choose lowest `idx`; remove each selected item from availability.
- On missing materials, raise `TrailError("CW_EQUIPMENT_MATERIALS_MISSING", f"合成 {equipment_name} 的基础装备不足")` with `error.data={"needed": needed, "held": held}` and `error.warnings=[{"code": "CW_EQUIPMENT_MATERIALS_MISSING", "message": f"合成 {equipment_name} 的基础装备不足", "需求": need_text, "持有": held_text}]`.
- On selected material `uncertain=True`, raise `TrailError("CW_EQUIPMENT_MATERIAL_UNCERTAIN", f"基础装备识别低置信: {material.get('pos')}")` with `error.data={"material": material}`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cw_equipment.py -k "select_compose_materials or record_equipment_compose" -v`

Expected: PASS.

---

### Task 3: Scene End-To-End Compose And Equip

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: Add success test**

Add a test named `test_compose_and_equip_cw_equipment_drags_compose_then_equip_and_writes_session`. It must monkeypatch `equipment.apply_cw_equipment_read` to return three snapshots in order: initial backpack with `equipment:2` 基础装甲 and `equipment:5` 光能电池, post-compose backpack with `equipment:2` 高周波电锯, and post-equip backpack with the result removed. Assert:

```python
assert [drag[:4] for drag in runtime.drags] == [(1005, 505, 1002, 502), (1002, 502, 761, 694)]
assert result["compose_action"] == {"drag_from": "equipment:5", "drag_to": "equipment:2"}
assert result["equip_action"] == {"drag_from": "equipment:2", "drag_to": "front:1"}
assert result["post_compose_equipment_count"] == 2
assert result["post_equip_equipment_count"] == 1
assert result["count"] == 2
assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册", "高周波电锯"]
assert ensure_cw_state(session)["equipment"]["stale"] is True
assert "recommendations" not in ensure_cw_state(session)["equipment"]
```

- [ ] **Step 2: Add failure tests**

Add tests for these cases:

- `test_compose_and_equip_materials_missing_does_not_drag_or_write`：initial snapshot only has 基础装甲; expect `CW_EQUIPMENT_MATERIALS_MISSING`, `runtime.drags == []`, role equipments unchanged.
- `test_compose_and_equip_uncertain_material_does_not_drag_or_write`：selected material has `uncertain=True`; expect `CW_EQUIPMENT_MATERIAL_UNCERTAIN`, no drag.
- `test_compose_and_equip_preflight_role_errors_happen_before_drag`：parametrize existing duplicate/full/invalid equipment state; expect existing role error code and no drag.
- `test_compose_and_equip_slots_read_failure_does_not_drag_or_write`：`slots_reader` raises `TrailError("SLOTS_READ_EMPTY", "未读取到任何货币战争槽位角色，请确认当前在编队界面")`; expect no drag and role unchanged.
- `test_compose_and_equip_equipment_read_failure_does_not_drag_or_write`：initial `apply_cw_equipment_read` raises; expect no drag and role unchanged.
- `test_compose_and_equip_post_compose_verify_failure_does_not_write_role`：post-compose snapshot fails target/count validation; expect `CW_EQUIPMENT_COMPOSE_VERIFY_FAILED`, first drag happened, role unchanged.
- `test_compose_and_equip_post_equip_allows_other_same_named_target`：post-compose has two 高周波电锯 and post-equip removes only the dragged result item; expect success.

- [ ] **Step 3: Implement end-to-end helper**

Implement:

```python
def compose_and_equip_cw_equipment(
    session: SessionModel,
    runtime,
    *,
    name: str,
    slot: str,
    role: str,
    raw_config: dict[str, Any],
    recognizer=None,
    workspace_root: str | Path | None = None,
    request_id: str | None = None,
    slots_reader: Callable[[], Any],
    guide_config: dict[str, Any] | None = None,
    preflight_read_scope: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    read_scope = preflight_read_scope or nullcontext
    # The body follows the required behavior list below: preflight read, validate, initial read, material selection, compose drag, post-compose verify, equip drag, post-equip verify, commit record.
    return result
```

Required behavior:

- Wrap `read_cw_slots()` and the first `apply_cw_equipment_read()` in `preflight_read_scope()` or `nullcontext()`.
- Call `validate_cw_equipment_compose_preflight()` before any `drag_to`.
- Use `select_cw_equipment_compose_materials()`.
- Compose drag from `max(material idx)` to `min(material idx)` using `equipment_slot_center()`.
- Post-compose verify `count == initial - 1`, target high-confidence item at `to_idx`, and identity multiset equals “remove two selected basics, add one advanced target”. Use helper errors `CW_EQUIPMENT_COMPOSE_VERIFY_FAILED` / `CW_EQUIPMENT_COMPOSE_VERIFY_UNCERTAIN`.
- Equip drag from `equipment:<to_idx>` to `SLOT_POINTS_BY_AREA[area][index]`.
- Post-equip verify `count == post_compose - 1` and identity multiset equals “remove the dragged target result item”; allow other same-name target items.
- Only after post-equip verification call `commit_cw_equipment_compose_record()`.
- Return old fields plus `materials`、`needed`、`result_item`、`compose_action`、`equip_action`、`verified=True`、`consumed=2`、`post_compose_equipment_count`、`post_equip_equipment_count`、`verified_shift`.

- [ ] **Step 4: Run scene tests**

Run: `uv run pytest tests/test_cw_equipment.py -k "compose" -v`

Expected: PASS.

---

### Task 4: Daemon Routing And Capture Semantics

**Files:**
- Modify: `tests/test_daemon_protocol.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/cw_service.py`

- [ ] **Step 1: Update command-service tests**

Replace the old session-only route assertion with:

```python
def test_command_service_routes_cw_equipment_compose_through_journaled_mutation():
    from trail.daemon.command_service import CW_MUTATING_METHODS, CW_SESSION_ONLY_MUTATION_METHODS
    assert "cw.equipment.compose" in CW_MUTATING_METHODS
    assert "cw.equipment.compose" not in CW_SESSION_ONLY_MUTATION_METHODS
```

Add:

```python
def test_failure_envelope_promotes_error_warnings():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    error = TrailError("CW_EQUIPMENT_MATERIALS_MISSING", "missing")
    error.warnings = [{"code": "CW_EQUIPMENT_MATERIALS_MISSING", "需求": "A:0/1", "持有": "equipment:1:B"}]
    envelope = command_service._failure_envelope(error=error)
    assert envelope["warnings"] == [{"code": "CW_EQUIPMENT_MATERIALS_MISSING", "需求": "A:0/1", "持有": "equipment:1:B"}]
    assert envelope["data"] == {}
```

- [ ] **Step 2: Add cw_service tests**

Add daemon tests that monkeypatch `trail.daemon.cw_service.compose_and_equip_cw_equipment` and verify:

- `raw_config` and `recognizer` come from `cw_resource_service.equipment_read_resources(workspace_root=workspace_root)`.
- `slots_reader` is provided and `guide_config` uses `enrich_traits=True`.
- The injected `preflight_read_scope` suppresses `click_point` side-effect tracking but real `drag_to` still makes post-side-effect failures unknown/tainted.
- If scene returns success but `capture_after_action()` returns `None`, `handle_mutation()` raises/returns persisted-but-response-unknown semantics, not ok success. In direct `CwService.handle_mutation()` tests, assert `PersistedButResponseUnknown` is raised; in `CommandService.handle()` integration tests, assert `ok=False`, `error.code == "DAEMON_UNAVAILABLE"`, and recoverable journal state.

- [ ] **Step 3: Implement command_service changes**

In `trail/daemon/command_service.py`:

- Add `"cw.equipment.compose"` to `CW_MUTATING_METHODS`.
- Remove `"cw.equipment.compose"` from `CW_SESSION_ONLY_MUTATION_METHODS`.
- In `_failure_envelope()`, set warnings from error attribute only when it is a list:

```python
raw_warnings = self._error_attr(error, "warnings")
warnings = to_jsonable(raw_warnings) if isinstance(raw_warnings, list) else []
```

Use `warnings` in the returned envelope.

- [ ] **Step 4: Implement cw_service changes**

In `trail/daemon/cw_service.py`:

- Import `compose_and_equip_cw_equipment`.
- Add side-effect suppression to `_SideEffectTrackingRuntime`, for example a boolean `_suppress_side_effects` and context manager method `suppress_side_effect_tracking()`.
- Ensure wrapped runtime methods only call `tracker.mark_applied()` when suppression is false.
- Add helper `run_equipment_compose()` in `_context()` that calls `compose_and_equip_cw_equipment(session, runtime(), name=payload["name"], slot=payload["slot"], role=payload["role"], workspace_root=workspace_root, request_id=request_id, raw_config=raw_config, recognizer=recognizer, slots_reader=slots_reader_factory(runtime()), guide_config=guide_config(enrich_traits=True), preflight_read_scope=runtime().suppress_side_effect_tracking)`.
- Replace the handler for `cw.equipment.compose` with `run_equipment_compose`.
- After `with_auto_capture()` for this method, if response is ok but has no `screenshot`, create `TrailError("CW_EQUIPMENT_COMPOSE_SCREENSHOT_REQUIRED", "cw.equipment.compose success requires screenshot")` and raise `PersistedButResponseUnknown(unknown_result_envelope(error, last_known_stage="state_persisted", screenshot=None, warnings=_safe_collect_warnings(capture_runtime), references=[], debug_runtime=capture_runtime))`.

- [ ] **Step 5: Run daemon tests**

Run: `uv run pytest tests/test_daemon_protocol.py -k "equipment_compose or failure_envelope_promotes" -v`

Expected: PASS.

---

### Task 5: Renderer And CLI Contract

**Files:**
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: Update renderer tests**

Replace `test_render_output_cw_equipment_compose_summary()` with a success case containing `screenshot`, `materials`, `result_item`, `compose_action`, `equip_action`, `post_compose_equipment_count`, `post_equip_equipment_count`, `verified_shift`. Expected lines:

```text
ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2
shot path=.trail/shots/req-compose.png
info read_image_first=1
# 综合信息
info action=compose drag_from=equipment:5 drag_to=equipment:2 verified=1 consumed=2 post_compose_equipment_count=2 verified_shift=0
info action=equip drag_from=equipment:2 drag_to=front:1 verified=1 post_equip_equipment_count=1 equipment_stale=1
# 装备信息
item kind=material phase=pre_compose idx=2 pos=equipment:2 name=基础装甲
item kind=material phase=pre_compose idx=5 pos=equipment:5 name=光能电池
item kind=result phase=post_compose idx=2 pos=equipment:2 name=高周波电锯
# 角色信息
slot pos=front:1 name=希儿 装备=高周波电锯 count=2
```

Add failure test where payload has top-level warning `{"code":"CW_EQUIPMENT_MATERIALS_MISSING","需求":"基础装甲:1/1|光能电池:0/1","持有":"equipment:1:基础装甲","message":"合成 高周波电锯 的基础装备不足"}`; expected warning appears after `why`.

- [ ] **Step 2: Update RPC contract tests**

In `tests/test_cw_rpc_contracts.py`, update `test_cw_equipment_compose_rpc_contract()` to use a success response with screenshot and the same detail fields. Assert output includes `shot`, `info read_image_first=1`, `# 综合信息`, `# 装备信息`, `# 角色信息`. Keep payload assertion unchanged: `{"name":"高周波电锯", "slot":"front:0", "role":"希儿"}`. Keep YAML rejection test unchanged except response data may include detail fields if needed; it must still make no RPC call.

- [ ] **Step 3: Implement renderer**

In `trail/output/rendering.py`:

- Add `_append_cw_equipment_material_missing_warning()` and call it from `_append_warnings()` before the generic warning branch.
- Extend `_render_cw_equipment_compose()` to build the old summary line, call `_append_success_capture_block()`, then `_append_section(lines, "综合信息")`, `_append_section(lines, "装备信息")`, `_append_section(lines, "角色信息")`, then warnings/references.
- Use existing prefixes only: `info`、`item`、`slot`.
- Material/result item facts must include `kind` and `phase`.

- [ ] **Step 4: Run renderer/RPC tests**

Run: `uv run pytest tests/test_output_rendering.py::test_render_output_cw_equipment_compose_summary tests/test_output_rendering.py -k "MATERIALS_MISSING or equipment_compose" tests/test_cw_rpc_contracts.py -k "equipment_compose" -v`

Expected: PASS.

---

### Task 6: CLI Help And Active Documentation

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `tests/test_output_debug.py`

- [ ] **Step 1: Update help tests first**

Change `test_cw_equipment_help_mentions_compose_session_record()` to assert `compose` is still registered, help contains `真实合成` or `执行真实合成`, and help does not contain `只写 session` or `不执行真实 UI 合成`.

- [ ] **Step 2: Update docs assertions**

Update `tests/test_output_debug.py` so active docs assert:

- `cw.equipment.compose` is mentioned in `AGENTS.md` and command surface.
- `只写 session` is not present in active prep skill / command surface for compose.
- `shot path` or `info read_image_first=1` and `材料不足` are documented for compose.

- [ ] **Step 3: Update command help and docs**

Update `CW_EQUIPMENT_HELP` in `trail/commands/cw.py` to say compose executes real UI compose/equip and returns screenshot.

Update `AGENTS.md` compose section to match spec:

- success first line remains `ok cw.equipment.compose pos=<slot> name=<角色名> 装备=<装备名> count=<角色装备数>`.
- command now screenshots; `shot path` followed by `info read_image_first=1` is mandatory on success.
- success sections are `# 综合信息`、`# 装备信息`、`# 角色信息`.
- material missing failure uses `warn code=CW_EQUIPMENT_MATERIALS_MISSING 需求=<基础装备:have/need> 持有=<equipment:N:装备名>`.
- YAML remains unsupported.

Update `skills/trail-cw-prep/SKILL.md` and `skills/trail-cw-prep/references/command-surface.md` with the same user-facing behavior.

- [ ] **Step 4: Run docs/help tests**

Run: `uv run pytest tests/test_atomic_commands.py -k "equipment" tests/test_output_debug.py -v`

Expected: PASS.

---

### Task 7: Integrated Edge Cases And Regression

**Files:**
- Modify only files needed by failing tests from prior tasks.

- [ ] **Step 1: Add missing edge tests if not already covered**

Before final regression, confirm `tests/test_cw_equipment.py` explicitly covers:

- Recipe missing and unsupported recipe return `CW_EQUIPMENT_RECIPE_MISSING` / `CW_EQUIPMENT_RECIPE_UNSUPPORTED` before drag.
- Same-name but different `cache_key` basics do not cross-match.
- Post-compose target item `uncertain=True` returns `CW_EQUIPMENT_COMPOSE_VERIFY_UNCERTAIN` and does not write role.
- Shift cannot be proven returns success with `verified_shift=0`.
- Post-equip verification failure does not write role.

Add concise tests if any of these are missing.

- [ ] **Step 2: Run targeted suite**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py tests/test_daemon_protocol.py -k "equipment_compose or compose_and_equip or select_compose_materials or failure_envelope_promotes" -v
```

Expected: PASS.

- [ ] **Step 3: Run contract suite**

Run:

```powershell
uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_atomic_commands.py tests/test_output_debug.py -k "equipment_compose or equipment or AGENTS or skill" -v
```

Expected: PASS.

---

### Task 8: Full Verification And Handoff

**Files:**
- No planned edits unless verification finds bugs.

- [ ] **Step 1: Run full fast regression**

Run from worktree:

```powershell
uv run pytest
```

Expected: all fast tests pass. Baseline before edits was `1908 passed, 29 skipped`.

- [ ] **Step 2: Inspect git diff**

Run:

```powershell
git status --short
git diff -- trail/scenes/cw/equipment.py trail/daemon/cw_service.py trail/daemon/command_service.py trail/output/rendering.py trail/commands/cw.py AGENTS.md skills/trail-cw-prep/SKILL.md skills/trail-cw-prep/references/command-surface.md tests/test_cw_equipment.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_atomic_commands.py
```

Expected: only task-related files changed.

- [ ] **Step 3: Prepare final summary**

Report:

- Worktree path.
- Tests run and results.
- Behavior changed for `cw.equipment.compose`.
- Any residual risk, especially live UI timing or screenshot capture assumptions.

Do not claim completion unless `uv run pytest` has passed after implementation.

---

## Self-Review

- Spec coverage: tasks cover scene flow, material matching, preflight failures, post-side-effect verification, daemon routing, screenshot requirement, renderer contract, CLI help, active docs, and regression.
- Placeholder scan: no unfinished placeholder markers remain; each task names exact files and test commands.
- Type consistency: public planned names are `validate_cw_equipment_compose_preflight`、`commit_cw_equipment_compose_record`、`select_cw_equipment_compose_materials`、`compose_and_equip_cw_equipment`; later tasks use the same names.
- Execution mode: user already approved subagent-driven development after review loops; use subagents for implementation tasks, with 2000+ character prompts for new subagent sessions.
