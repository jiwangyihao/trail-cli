# cw.equipment.compose 复用已有进阶装备 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cw.equipment.compose` 在背包已有高置信目标进阶装备时优先直接装备，只有没有可确认目标装备时才走现有合成链路。

**Architecture:** 在 scene 层先解析目标 recipe，再从初始装备快照中按稳定身份选择已有目标装备；命中时复用现有拖拽、post-equip 验证与 session commit，不进入材料选择和合成拖拽。Renderer 按 response `action` 区分 `equip_existing` 与 `compose`，daemon/RPC 继续依赖现有 journaled mutation、截图必需与 tainted/unknown 语义。

**Tech Stack:** Python 3.12、pytest、uv、Trail CLI daemon/session/output renderer、CW scene helpers。

---

## 工作边界

- Worktree：`C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-compose-reuse-existing`
- Spec：`docs/superpowers/specs/2026-05-02-cw-equipment-compose-reuse-existing-design.md`
- 不创建 git commit；用户没有要求提交。所有 plan 中的验证步骤只运行测试与状态检查。
- 不修改 `README.md`，除非实现期间发现 `cw.equipment.compose` 复用行为已经在普通用户入口公开描述。
- 不新增 CLI 参数，不改变 canonical command、首行字段顺序或 YAML allowlist。
- 不回滚或清理主工作区的并行变更；所有实现只在本 worktree 内进行。

## File Structure

- Modify `trail/scenes/cw/equipment.py`
  - 新增 `select_existing_cw_equipment_target(snapshot, *, recipe)`。
  - 调整 `compose_and_equip_cw_equipment()`：recipe missing 早失败；读取初始背包后先尝试直接装备已有进阶装备；未命中时保留现有合成路径。
  - 复用 `_verify_cw_equipment_post_equip()`，直接装备时把 `initial_snapshot` 作为 `post_compose_snapshot` 传入。
- Modify `trail/output/rendering.py`
  - 扩展 `_render_cw_equipment_compose()` 支持 `data["action"] == "equip_existing"`。
  - 保持默认合成分支输出不变。
- Modify `tests/test_cw_equipment.py`
  - 增加 helper 级选择测试。
  - 增加 direct equip flow、fallback flow、preflight、verification failure 测试。
- Modify `tests/test_output_rendering.py`
  - 增加 direct equip renderer contract 测试。
  - 保留现有 compose renderer、empty equipment section、materials missing failure 测试。
- Modify `tests/test_daemon_protocol.py`
  - 增加 direct equip success data/screenshot contract 测试。
  - 增加 direct equip post-drag failure 通过 command service 变成 recoverable tainted unknown 的 contract 测试，或扩展现有 preflight/drag failure 用例，使名称和断言明确覆盖 `equip_existing`。
- Verify `tests/test_cw_rpc_contracts.py`
  - 现有 `OUTPUT_FORMAT_NOT_SUPPORTED` 对 `cw.equipment.compose` 应继续通过；如果 renderer/command surface 变更影响该测试，只做最小修复。
- Modify `AGENTS.md`
  - 更新 `cw.equipment.compose` 协议说明：优先直接装备已有目标进阶装备；无可确认目标时才合成。
- Modify `skills/trail-cw-prep/SKILL.md`
  - 更新 prep skill 对 `cw.equipment.compose` success 的消费说明，覆盖 `action=equip_existing consumed=0` 和 `action=compose consumed=2`。
- Modify `skills/trail-cw-prep/references/command-surface.md`
  - 更新 command surface 示例或字段说明，锁定 direct equip 与 compose 两类 success body。

---

### Task 1: Existing Target Helper

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: Write failing helper tests**

Add these tests near existing `test_select_compose_materials_*` tests in `tests/test_cw_equipment.py`:

```python
def test_select_existing_equipment_target_chooses_lowest_idx_high_confidence(tmp_path):
    equipment = load_equipment_module()
    recipe = equipment.build_cw_equipment_recipes(_compose_raw_config())["高周波电锯"]
    snapshot = _equipment_snapshot(
        _equipment_item(4, "高周波电锯", "saw", "advanced-saw"),
        _equipment_item(2, "高周波电锯", "saw", "advanced-saw"),
        _equipment_item(1, "基础装甲", "armor", "basic-armor"),
    )

    selected = equipment.select_existing_cw_equipment_target(snapshot, recipe=recipe)

    assert selected["idx"] == 2
    assert selected["pos"] == "equipment:2"


def test_select_existing_equipment_target_ignores_uncertain_match(tmp_path):
    equipment = load_equipment_module()
    recipe = equipment.build_cw_equipment_recipes(_compose_raw_config())["高周波电锯"]
    snapshot = _equipment_snapshot(
        _equipment_item(2, "高周波电锯", "saw", "advanced-saw", uncertain=True, score=0.61, gap=0.01),
        _equipment_item(4, "基础装甲", "armor", "basic-armor"),
    )

    assert equipment.select_existing_cw_equipment_target(snapshot, recipe=recipe) is None


def test_select_existing_equipment_target_does_not_cross_match_same_name_different_cache_key(tmp_path):
    equipment = load_equipment_module()
    raw_config = _compose_raw_config(advanced_id="saw-a")
    recipe = equipment.build_cw_equipment_recipes(raw_config)["高周波电锯"]
    snapshot = _equipment_snapshot(
        _equipment_item(2, "高周波电锯", "saw-b", "advanced-saw-b"),
        _equipment_item(3, "高周波电锯", "saw-a", "advanced-saw-a"),
    )

    selected = equipment.select_existing_cw_equipment_target(snapshot, recipe=recipe)

    assert selected["idx"] == 3


def test_select_existing_equipment_target_does_not_treat_basic_same_id_as_target(tmp_path):
    equipment = load_equipment_module()
    raw_config = _compose_raw_config(advanced_id="same-id")
    recipe = equipment.build_cw_equipment_recipes(raw_config)["高周波电锯"]
    snapshot = _equipment_snapshot(
        _equipment_item(2, "高周波电锯", "same-id", "basic-same-id"),
    )

    assert equipment.select_existing_cw_equipment_target(snapshot, recipe=recipe) is None
```

- [ ] **Step 2: Run helper tests to verify they fail**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_select_existing_equipment_target_chooses_lowest_idx_high_confidence tests/test_cw_equipment.py::test_select_existing_equipment_target_ignores_uncertain_match tests/test_cw_equipment.py::test_select_existing_equipment_target_does_not_cross_match_same_name_different_cache_key tests/test_cw_equipment.py::test_select_existing_equipment_target_does_not_treat_basic_same_id_as_target -q
```

Expected: FAIL because `select_existing_cw_equipment_target` does not exist.

- [ ] **Step 3: Implement helper**

Add this function in `trail/scenes/cw/equipment.py` immediately after `_item_matches_target_recipe()`:

```python
def select_existing_cw_equipment_target(snapshot: dict[str, Any], *, recipe) -> Mapping[str, Any] | None:
    items = sorted(
        [item for item in _as_list(snapshot.get("items")) if isinstance(item, Mapping)],
        key=_equipment_item_idx,
    )
    for item in items:
        if item.get("uncertain") is True:
            continue
        if _item_matches_target_recipe(item, recipe):
            return item
    return None
```

- [ ] **Step 4: Run helper tests to verify they pass**

Run the same command from Step 2.

Expected: PASS for all four helper tests.

---

### Task 2: Scene Direct Equip Flow

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: Write failing direct equip success test**

Add this test near `test_compose_and_equip_cw_equipment_drags_compose_then_equip_and_writes_session`:

```python
def test_compose_and_equip_uses_existing_target_without_composing(tmp_path, monkeypatch):
    from trail.scenes.cw import slots as slots_module
    from trail.scenes.cw.equipment_grid import equipment_slot_center
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(
                _equipment_item(1, "基础装甲", "armor", "basic-armor"),
                _equipment_item(3, "高周波电锯", "saw", "advanced-saw"),
            ),
            _equipment_snapshot(
                _equipment_item(1, "基础装甲", "armor", "basic-armor"),
                recommendations={"priority": []},
            ),
        ],
    )

    result = _compose_and_equip(equipment, session, runtime)

    assert [drag[:4] for drag in runtime.drags] == [
        (*equipment_slot_center("equipment:3"), *slots_module.SLOT_POINTS_BY_AREA["front"][0]),
    ]
    assert result["action"] == "equip_existing"
    assert result["existing_item"] == {
        "idx": 3,
        "pos": "equipment:3",
        "name": "高周波电锯",
        "equipment_id": "saw",
        "cache_key": "advanced-saw",
    }
    assert result["equip_action"] == {"drag_from": "equipment:3", "drag_to": "front:1"}
    assert result["verified"] is True
    assert result["consumed"] == 0
    assert result["post_equip_equipment_count"] == 1
    assert result["equipment_stale"] is True
    assert "compose_action" not in result
    assert "materials" not in result
    assert "result_item" not in result
    assert "post_compose_equipment_count" not in result
    assert result["count"] == 2
    assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册", "高周波电锯"]
    assert ensure_cw_state(session)["equipment"]["stale"] is True
```

- [ ] **Step 2: Write fallback and edge-case flow tests**

Add these tests in the same section:

```python
def test_compose_and_equip_existing_target_works_when_materials_missing(tmp_path, monkeypatch):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw")),
            _equipment_snapshot(recommendations={"priority": []}),
        ],
    )

    result = _compose_and_equip(equipment, session, runtime)

    assert result["action"] == "equip_existing"
    assert result["consumed"] == 0
    assert len(runtime.drags) == 1


def test_compose_and_equip_uncertain_existing_target_falls_back_to_materials_missing(tmp_path, monkeypatch):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [_equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw", uncertain=True, score=0.61, gap=0.01))],
    )

    with pytest.raises(Exception) as exc_info:
        _compose_and_equip(equipment, session, runtime)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_MATERIALS_MISSING"
    assert runtime.drags == []
    assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册"]


def test_compose_and_equip_missing_recipe_does_not_direct_equip_by_name(tmp_path, monkeypatch):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    raw_config = {"rpg_game_big_version": "3.2", "equipment_list": []}
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [_equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw"))],
    )

    with pytest.raises(Exception) as exc_info:
        _compose_and_equip(equipment, session, runtime, raw_config=raw_config)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_RECIPE_MISSING"
    assert runtime.drags == []
    assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册"]


def test_compose_and_equip_existing_target_bypasses_unsupported_recipe(tmp_path, monkeypatch):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    raw_config = _compose_raw_config(
        basics=[
            {"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"},
            {"id": "battery", "name": "光能电池", "icon": "https://act-webstatic.mihoyo.com/battery.png"},
            {"id": "gear", "name": "机械齿轮", "icon": "https://act-webstatic.mihoyo.com/gear.png"},
        ]
    )
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw")),
            _equipment_snapshot(recommendations={"priority": []}),
        ],
    )

    result = _compose_and_equip(equipment, session, runtime, raw_config=raw_config)

    assert result["action"] == "equip_existing"
    assert result["consumed"] == 0
    assert len(runtime.drags) == 1
```

- [ ] **Step 3: Write verification and preflight-order tests**

Add these tests in the same section:

```python
def test_compose_and_equip_existing_target_allows_duplicate_identity_remaining(tmp_path, monkeypatch):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(
                _equipment_item(2, "高周波电锯", "saw", "advanced-saw"),
                _equipment_item(5, "高周波电锯", "saw", "advanced-saw"),
            ),
            _equipment_snapshot(_equipment_item(5, "高周波电锯", "saw", "advanced-saw"), recommendations={"priority": []}),
        ],
    )

    result = _compose_and_equip(equipment, session, runtime)

    assert result["action"] == "equip_existing"
    assert result["existing_item"]["idx"] == 2
    assert result["post_equip_equipment_count"] == 1


def test_compose_and_equip_existing_target_verify_failure_does_not_write(tmp_path, monkeypatch):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw")),
            _equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw")),
        ],
    )

    with pytest.raises(Exception) as exc_info:
        _compose_and_equip(equipment, session, runtime)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_COMPOSE_EQUIP_VERIFY_FAILED"
    assert len(runtime.drags) == 1
    assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册"]


def test_compose_and_equip_existing_target_preflight_failure_happens_before_drag(tmp_path, monkeypatch):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    ensure_cw_state(session)["slots"]["front"][0]["equipments"] = ["战场手册", "高周波电锯"]
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [_equipment_snapshot(_equipment_item(3, "高周波电锯", "saw", "advanced-saw"))],
    )

    with pytest.raises(Exception) as exc_info:
        _compose_and_equip(equipment, session, runtime)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_ALREADY_HELD"
    assert runtime.drags == []
```

- [ ] **Step 4: Run scene tests to verify they fail**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_compose_and_equip_uses_existing_target_without_composing tests/test_cw_equipment.py::test_compose_and_equip_existing_target_works_when_materials_missing tests/test_cw_equipment.py::test_compose_and_equip_uncertain_existing_target_falls_back_to_materials_missing tests/test_cw_equipment.py::test_compose_and_equip_missing_recipe_does_not_direct_equip_by_name tests/test_cw_equipment.py::test_compose_and_equip_existing_target_bypasses_unsupported_recipe tests/test_cw_equipment.py::test_compose_and_equip_existing_target_allows_duplicate_identity_remaining tests/test_cw_equipment.py::test_compose_and_equip_existing_target_verify_failure_does_not_write tests/test_cw_equipment.py::test_compose_and_equip_existing_target_preflight_failure_happens_before_drag -q
```

Expected: FAIL because `compose_and_equip_cw_equipment()` still always calls `select_cw_equipment_compose_materials()` and performs compose drag.

- [ ] **Step 5: Refactor recipe lookup and implement direct equip branch**

In `trail/scenes/cw/equipment.py`, replace the block starting at current `selection = select_cw_equipment_compose_materials(initial_snapshot, name=preflight["equipment_name"], raw_config=raw_config)` inside `compose_and_equip_cw_equipment()` with this structure:

```python
    recipes = build_cw_equipment_recipes(raw_config)
    recipe = recipes.get(preflight["equipment_name"])
    if recipe is None:
        raise TrailError("CW_EQUIPMENT_RECIPE_MISSING", f"missing equipment recipe: {preflight['equipment_name']}")

    existing_item = select_existing_cw_equipment_target(initial_snapshot, recipe=recipe)
    if existing_item is not None:
        existing_idx = _equipment_item_idx(existing_item)
        equip_from = existing_item.get("pos") if isinstance(existing_item.get("pos"), str) else None
        if not equip_from:
            equip_from = f"equipment:{existing_idx}"
        equip_to = preflight["agent_slot"]
        runtime.drag_to(*equipment_slot_center(equip_from), *SLOT_POINTS_BY_AREA[preflight["area"]][preflight["index"]])

        post_equip_snapshot = apply_cw_equipment_read(
            session,
            runtime,
            workspace_root=workspace_root,
            raw_config=raw_config,
            recognizer=recognizer,
            request_id=request_id,
        )
        _verify_cw_equipment_post_equip(
            post_compose_snapshot=initial_snapshot,
            post_equip_snapshot=post_equip_snapshot,
            result_item=existing_item,
        )

        result = commit_cw_equipment_compose_record(session, preflight)
        result.update(
            {
                "action": "equip_existing",
                "existing_item": _held_compose_item(existing_item),
                "equip_action": {"drag_from": equip_from, "drag_to": equip_to},
                "verified": True,
                "consumed": 0,
                "post_equip_equipment_count": _snapshot_count(post_equip_snapshot),
                "equipment_stale": True,
            }
        )
        return result

    selection = select_cw_equipment_compose_materials(initial_snapshot, name=preflight["equipment_name"], raw_config=raw_config)
```

Then remove the later duplicate line:

```python
    recipe = build_cw_equipment_recipes(raw_config)[preflight["equipment_name"]]
```

Do not change the existing compose branch except for using the `recipe` variable created above.

- [ ] **Step 6: Run focused scene tests**

Run the command from Step 4 again.

Expected: PASS for all direct equip tests.

- [ ] **Step 7: Run existing compose fallback tests**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_compose_and_equip_cw_equipment_drags_compose_then_equip_and_writes_session tests/test_cw_equipment.py::test_compose_and_equip_materials_missing_does_not_drag_or_write tests/test_cw_equipment.py::test_compose_and_equip_missing_recipe_does_not_drag_or_write tests/test_cw_equipment.py::test_compose_and_equip_unsupported_recipe_does_not_drag_or_write -q
```

Expected: PASS. The missing recipe test must still fail before drag. The unsupported recipe test must still fail before drag when no reusable target exists.

- [ ] **Step 8: Preserve existing post-equip duplicate-target compose coverage**

The existing `tests/test_cw_equipment.py::test_compose_and_equip_post_equip_allows_other_same_named_target` currently has an initial snapshot that includes `_equipment_item(6, "高周波电锯", "saw", "advanced-saw")`. After direct-equip behavior lands, that fixture would naturally choose `equip_existing` and stop covering the compose branch. Modify that existing test so the initial same-identity target is still present for identity-counter verification but has `uncertain=True`, which direct-equip selection must ignore. The post-compose and post-equip snapshots then continue to prove another same-identity target can remain after equipping the newly composed item:

```python
def test_compose_and_equip_post_equip_allows_other_same_named_target(tmp_path, monkeypatch):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    runtime = _ComposeRuntime()
    _install_compose_equipment_reads(
        monkeypatch,
        equipment,
        session,
        [
            _equipment_snapshot(
                _equipment_item(2, "基础装甲", "armor", "basic-armor"),
                _equipment_item(5, "光能电池", "battery", "basic-battery"),
                _equipment_item(6, "高周波电锯", "saw", "advanced-saw", uncertain=True, score=0.61, gap=0.01),
            ),
            _equipment_snapshot(
                _equipment_item(2, "高周波电锯", "saw", "advanced-saw"),
                _equipment_item(5, "高周波电锯", "saw", "advanced-saw"),
            ),
            _equipment_snapshot(_equipment_item(5, "高周波电锯", "saw", "advanced-saw")),
        ],
    )

    result = _compose_and_equip(equipment, session, runtime)

    assert result.get("action") != "equip_existing"
    assert "compose_action" in result
    assert len(runtime.drags) == 2
    assert result["count"] == 2
    assert result["post_compose_equipment_count"] == 2
    assert result["post_equip_equipment_count"] == 1
    assert ensure_cw_state(session)["slots"]["front"][0]["equipments"] == ["战场手册", "高周波电锯"]
```

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_compose_and_equip_post_equip_allows_other_same_named_target -q
```

Expected: PASS and still exercises compose branch, not direct equip.

---

### Task 3: Renderer Contract

**Files:**
- Modify: `tests/test_output_rendering.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: Write failing renderer test for `equip_existing`**

Add this test after `test_render_output_cw_equipment_compose_summary()`:

```python
def test_render_output_cw_equipment_compose_existing_target_summary():
    payload = {
        "ok": True,
        "data": {
            "action": "equip_existing",
            "pos": "front:1",
            "name": "希儿",
            "equipment": "高周波电锯",
            "count": 2,
            "existing_item": {
                "idx": 3,
                "pos": "equipment:3",
                "name": "高周波电锯",
                "equipment_id": "saw",
                "cache_key": "advanced-saw",
            },
            "equip_action": {"drag_from": "equipment:3", "drag_to": "front:1"},
            "verified": True,
            "consumed": 0,
            "post_equip_equipment_count": 1,
            "equipment_stale": True,
        },
        "screenshot": ".trail/shots/req-compose-existing.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.equipment.compose", payload).splitlines()

    assert lines == [
        "ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2",
        "shot path=.trail/shots/req-compose-existing.png",
        "info read_image_first=1",
        "# 综合信息",
        "info action=equip_existing drag_from=equipment:3 drag_to=front:1 verified=1 consumed=0 post_equip_equipment_count=1 equipment_stale=1",
        "# 装备信息",
        "item kind=existing phase=pre_equip idx=3 pos=equipment:3 name=高周波电锯",
        "# 角色信息",
        "slot pos=front:1 name=希儿 装备=高周波电锯 count=2",
    ]
    rendered = "\n".join(lines)
    assert "action=compose" not in rendered
    assert "consumed=2" not in rendered
    assert "post_compose_equipment_count" not in rendered
    assert "kind=result phase=post_compose" not in rendered
```

- [ ] **Step 2: Run renderer direct equip test to verify it fails**

Run:

```powershell
uv run pytest tests/test_output_rendering.py::test_render_output_cw_equipment_compose_existing_target_summary -q
```

Expected: FAIL because renderer currently always emits `action=compose` and reads `compose_action`.

- [ ] **Step 3: Implement renderer branch**

In `trail/output/rendering.py`, update `_render_cw_equipment_compose()` after `_append_success_capture_block(lines, payload)` so it branches on action:

```python
    action = data.get("action")
    equip_action = _as_dict(data.get("equip_action"))
    _append_section(lines, "综合信息")
    if action == "equip_existing":
        _append_fact_line(
            lines,
            "info",
            ("action", "equip_existing"),
            ("drag_from", equip_action.get("drag_from")),
            ("drag_to", equip_action.get("drag_to")),
            ("verified", data.get("verified")),
            ("consumed", data.get("consumed") if "consumed" in data else 0),
            ("post_equip_equipment_count", data.get("post_equip_equipment_count")),
            ("equipment_stale", data.get("equipment_stale") if "equipment_stale" in data else True),
        )
    else:
        compose_action = _as_dict(data.get("compose_action"))
        _append_fact_line(
            lines,
            "info",
            ("action", "compose"),
            ("drag_from", compose_action.get("drag_from")),
            ("drag_to", compose_action.get("drag_to")),
            ("verified", data.get("verified")),
            ("consumed", data.get("consumed")),
            ("post_compose_equipment_count", data.get("post_compose_equipment_count")),
            ("verified_shift", data.get("verified_shift")),
        )
        _append_fact_line(
            lines,
            "info",
            ("action", "equip"),
            ("drag_from", equip_action.get("drag_from")),
            ("drag_to", equip_action.get("drag_to")),
            ("verified", data.get("verified")),
            ("post_equip_equipment_count", data.get("post_equip_equipment_count")),
            ("equipment_stale", True),
        )
```

Then update equipment item rendering inside the same function so direct equip adds existing item lines before material/result handling:

```python
    equipment_lines: list[str] = []
    existing_item = _as_dict(data.get("existing_item"))
    if action == "equip_existing" and existing_item:
        _append_fact_line(
            equipment_lines,
            "item",
            ("kind", "existing"),
            ("phase", "pre_equip"),
            ("idx", existing_item.get("idx")),
            ("pos", existing_item.get("pos")),
            ("name", existing_item.get("name")),
        )
    if action != "equip_existing":
        for item in _as_list(data.get("materials")):
            if not isinstance(item, dict):
                continue
            _append_fact_line(
                equipment_lines,
                "item",
                ("kind", "material"),
                ("phase", "pre_compose"),
                ("idx", item.get("idx")),
                ("pos", item.get("pos")),
                ("name", item.get("name")),
            )
        result_item = _as_dict(data.get("result_item"))
        if result_item:
            _append_fact_line(
                equipment_lines,
                "item",
                ("kind", "result"),
                ("phase", "post_compose"),
                ("idx", result_item.get("idx")),
                ("pos", result_item.get("pos")),
                ("name", result_item.get("name")),
            )
```

Keep the existing material/result `_append_fact_line()` bodies unchanged under `if action != "equip_existing"`.

- [ ] **Step 4: Run renderer focused tests**

Run:

```powershell
uv run pytest tests/test_output_rendering.py::test_render_output_cw_equipment_compose_existing_target_summary tests/test_output_rendering.py::test_render_output_cw_equipment_compose_summary tests/test_output_rendering.py::test_render_output_cw_equipment_compose_omits_empty_equipment_section tests/test_output_rendering.py::test_render_output_cw_equipment_compose_materials_missing_warning -q
```

Expected: PASS. Existing compose output must remain byte-for-byte compatible with current expectations.

---

### Task 4: Daemon and RPC Contracts

**Files:**
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: Add direct equip success contract test**

Add this test near `test_cw_service_equipment_compose_uses_resource_service_and_injections()`:

```python
def test_cw_service_equipment_compose_direct_equip_success_requires_screenshot(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return str(tmp_path / ".trail" / "shots" / f"{request_id}.png")

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_compose(session_arg, runtime, **kwargs):
        del session_arg, runtime, kwargs
        return {
            "action": "equip_existing",
            "pos": "front:1",
            "name": "希儿",
            "equipment": "高周波电锯",
            "count": 2,
            "existing_item": {"idx": 3, "pos": "equipment:3", "name": "高周波电锯"},
            "equip_action": {"drag_from": "equipment:3", "drag_to": "front:1"},
            "verified": True,
            "consumed": 0,
            "post_equip_equipment_count": 1,
            "equipment_stale": True,
        }

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()), cw_resource_service=ResourceService())

    response = cw_service.handle_mutation(
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-equipment-compose-existing-success",
        verbose=False,
    )

    assert response["ok"] is True
    assert response["data"]["action"] == "equip_existing"
    assert response["data"]["consumed"] == 0
    assert response["screenshot"].endswith("req-equipment-compose-existing-success.png")
    assert response["image_guidance"] == {"read_image_first": True}
```

- [ ] **Step 2: Add direct equip drag failure command-service test**

Add this test near `test_command_service_equipment_compose_missing_screenshot_returns_recoverable_unknown()`:

```python
def test_command_service_equipment_compose_existing_equip_drag_failure_is_recoverable_tainted(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def drag_to(self, *args, **kwargs):
            del args, kwargs

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return None

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_compose(session_arg, runtime, *, preflight_read_scope, **kwargs):
        del session_arg, kwargs
        with preflight_read_scope():
            pass
        runtime.drag_to(1, 2, 3, 4)
        raise TrailError("CW_EQUIPMENT_COMPOSE_EQUIP_VERIFY_FAILED", "装备给角色后背包验证失败")

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service, cw_resource_service=ResourceService())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-equipment-compose-existing-verify-failed",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
    )

    response = command_service.handle(request)
    status = session_service.request_status(request.request_id)

    assert response["ok"] is False
    assert response["error"] == {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"}
    assert response["request_id"] == request.request_id
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert session_service.load_session(session.session_id).scene_state["daemon"]["tainted"] is True
```

- [ ] **Step 3: Run daemon tests to verify failure or pass depending on existing generic coverage**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_service_equipment_compose_direct_equip_success_requires_screenshot tests/test_daemon_protocol.py::test_command_service_equipment_compose_existing_equip_drag_failure_is_recoverable_tainted -q
```

Expected: The success test may pass immediately because daemon success handling is already generic; the failure test should pass if side-effect tracking already marks `runtime.drag_to()` as mutation side effect. The failure test must assert `final_state=applied_but_not_persisted`, `last_known_stage=side_effect_applied`, and persisted session tainted state.

- [ ] **Step 4: Add direct equip missing-screenshot unknown test**

Add this test near `test_cw_service_equipment_compose_success_without_screenshot_is_persisted_unknown()` and make the fake compose return the direct-equip response shape:

```python
def test_cw_service_equipment_compose_existing_success_without_screenshot_is_persisted_unknown(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return None

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_compose(session_arg, runtime, **kwargs):
        del runtime, kwargs
        session_arg.scene_state.setdefault("cw", {})["compose_marker"] = "existing_persisted"
        return {
            "action": "equip_existing",
            "pos": "front:1",
            "name": "希儿",
            "equipment": "高周波电锯",
            "count": 2,
            "existing_item": {"idx": 3, "pos": "equipment:3", "name": "高周波电锯"},
            "equip_action": {"drag_from": "equipment:3", "drag_to": "front:1"},
            "verified": True,
            "consumed": 0,
            "post_equip_equipment_count": 1,
            "equipment_stale": True,
        }

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()), cw_resource_service=ResourceService())

    with pytest.raises(PersistedButResponseUnknown) as unknown_error:
        cw_service.handle_mutation(
            method="cw.equipment.compose",
            payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
            workspace_root=str(tmp_path),
            session_service=session_service,
            request_id="req-equipment-compose-existing-missing-screenshot",
            verbose=False,
        )

    assert session_service.load_session(session.session_id).scene_state["cw"]["compose_marker"] == "existing_persisted"
    assert unknown_error.value.envelope["ok"] is False
    assert unknown_error.value.envelope["screenshot"] is None
    assert unknown_error.value.envelope["error"] == {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"}
    assert unknown_error.value.envelope["debug"] == {
        "detail": "TrailError: cw.equipment.compose success requires screenshot",
        "last_known_stage": "state_persisted",
    }
```

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_service_equipment_compose_existing_success_without_screenshot_is_persisted_unknown -q
```

Expected: PASS and locks direct-equip `PersistedButResponseUnknown` behavior.

- [ ] **Step 5: Add drag-call exception coverage**

Add a second direct-equip unknown test where the runtime `drag_to()` itself raises after marking side effect:

```python
def test_command_service_equipment_compose_existing_equip_drag_exception_is_recoverable_tainted(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def drag_to(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("direct equip drag failed")

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return None

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_compose(session_arg, runtime, *, preflight_read_scope, **kwargs):
        del session_arg, kwargs
        with preflight_read_scope():
            pass
        runtime.drag_to(1, 2, 3, 4)

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service, cw_resource_service=ResourceService())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-equipment-compose-existing-drag-failed",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
    )

    response = command_service.handle(request)
    status = session_service.request_status(request.request_id)

    assert response["ok"] is False
    assert response["error"] == {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"}
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert session_service.load_session(session.session_id).scene_state["daemon"]["tainted"] is True
```

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_command_service_equipment_compose_existing_equip_drag_exception_is_recoverable_tainted -q
```

Expected: PASS and proves direct equip drag exceptions share the same tainted unknown path as post-equip verification failures.

- [ ] **Step 6: Add direct equip CLI stdout contract**

Add this test near `tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_rpc_contract`:

```python
def test_cw_equipment_compose_existing_target_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose-existing",
                data={
                    "action": "equip_existing",
                    "pos": "front:1",
                    "name": "希儿",
                    "equipment": "高周波电锯",
                    "count": 2,
                    "existing_item": {"idx": 3, "pos": "equipment:3", "name": "高周波电锯"},
                    "equip_action": {"drag_from": "equipment:3", "drag_to": "front:1"},
                    "verified": True,
                    "consumed": 0,
                    "post_equip_equipment_count": 1,
                    "equipment_stale": True,
                },
                screenshot=".trail/shots/req-cw-equipment-compose-existing.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2",
        "shot path=.trail/shots/req-cw-equipment-compose-existing.png",
        "info read_image_first=1",
        "# 综合信息",
        "info action=equip_existing drag_from=equipment:3 drag_to=front:1 verified=1 consumed=0 post_equip_equipment_count=1 equipment_stale=1",
        "# 装备信息",
        "item kind=existing phase=pre_equip idx=3 pos=equipment:3 name=高周波电锯",
        "# 角色信息",
        "slot pos=front:1 name=希儿 装备=高周波电锯 count=2",
    ]
    _assert_single_call(
        client,
        method="cw.equipment.compose",
        payload={"name": "高周波电锯", "slot": "front:0", "role": "希儿"},
        tmp_path=tmp_path,
    )
```

Run:

```powershell
uv run pytest tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_existing_target_rpc_contract -q
```

Expected: PASS and locks CLI-facing stdout order for direct equip.

- [ ] **Step 7: Add unknown/recover CLI contract for compose**

Add this test near other unknown-result CLI contracts in `tests/test_cw_rpc_contracts.py`:

```python
def test_cw_equipment_compose_renders_unknown_recover_contract(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": {
                "request_id": "req-cw-equipment-compose-unknown",
                "ok": False,
                "data": {},
                "screenshot": ".trail/shots/req-cw-equipment-compose-unknown.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"last_known_stage": "side_effect_applied", "tainted": True},
                "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
            }
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.equipment.compose code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-equipment-compose-unknown",
        "shot path=.trail/shots/req-cw-equipment-compose-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-equipment-compose-unknown",
    ]
    assert client.calls[0].method == "cw.equipment.compose"
```

Run:

```powershell
uv run pytest tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_renders_unknown_recover_contract -q
```

Expected: PASS and locks recover line behavior for direct-equip or compose unknown failures.

- [ ] **Step 8: Verify YAML remains unsupported**

Run:

```powershell
uv run pytest tests/test_cw_rpc_contracts.py::test_cli_yaml_rejects_non_allowlisted_cw_equipment_compose -q
```

Expected: PASS. If the exact test name differs, locate it in `tests/test_cw_rpc_contracts.py` around line 799 and run `test_cw_equipment_compose_rejects_yaml_output`.

---

### Task 5: Documentation Sync

**Files:**
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `tests/test_output_debug.py`

- [ ] **Step 1: Update `AGENTS.md` compose protocol bullets**

In `AGENTS.md`, replace the current `cw.equipment.compose` success bullets with text equivalent to:

```markdown
- `cw.equipment.compose` 归入检测/状态摘要 renderer 家族；canonical command 固定为 `cw.equipment.compose`；success 首行固定为 `ok cw.equipment.compose pos=<agent-visible-slot> name=<角色名> 装备=<装备名> count=<角色装备数>`，字段顺序固定为 `pos/name/装备/count`，其中 `count` 是写入后该角色已记录装备数量。
- `cw.equipment.compose` 是真实 UI mutation，会优先复用背包中已有的高置信目标进阶装备；只有没有可确认目标装备时才执行合成并装备到目标角色。success 必须输出 `shot path=<截图路径>`，并在其后立即输出 `info read_image_first=1`。
- `cw.equipment.compose` success 正文分段固定为 `# 综合信息`、`# 装备信息`、`# 角色信息`；复用已有装备时 `# 综合信息` 完整事实行为 `info action=equip_existing drag_from=<equipment-slot> drag_to=<agent-visible-slot> verified=1 consumed=0 post_equip_equipment_count=<n> equipment_stale=1`，且不输出合成事实；执行合成时继续输出 `info action=compose drag_from=<equipment-slot> drag_to=<equipment-slot> verified=1 consumed=2 post_compose_equipment_count=<n> verified_shift=<0|1>` 和 `info action=equip drag_from=<equipment-slot> drag_to=<agent-visible-slot> verified=1 post_equip_equipment_count=<n> equipment_stale=1`。
- `cw.equipment.compose` 的 `# 装备信息` 在复用已有装备时输出 `item kind=existing phase=pre_equip idx=<idx> pos=<equipment-slot> name=<装备名>`；执行合成时输出 `item kind=material phase=pre_compose idx=<idx> pos=<equipment-slot> name=<基础装备名>` 与 `item kind=result phase=post_compose idx=<idx> pos=<equipment-slot> name=<装备名>`；`# 角色信息` 输出 `slot pos=<角色槽位> name=<角色名> 装备=<装备名> count=<角色装备数>`。
```

Keep existing bullets for materials missing, slot/role validation, and YAML unsupported. Do not delete the canonical first-line bullet while adding direct-equip details.

- [ ] **Step 2: Update prep skill docs**

In `skills/trail-cw-prep/SKILL.md`, find the `cw.equipment.compose` usage section and ensure it says:

```markdown
`cw.equipment.compose` 成功后先读截图，再根据 `# 综合信息` 判断路径：`info action=equip_existing drag_from=<equipment-slot> drag_to=<agent-visible-slot> verified=1 consumed=0 post_equip_equipment_count=<n> equipment_stale=1` 表示直接装备了已有目标进阶装备，没有消耗基础材料；`info action=compose drag_from=<equipment-slot> drag_to=<equipment-slot> verified=1 consumed=2 post_compose_equipment_count=<n> verified_shift=<0|1>` 后跟 `info action=equip drag_from=<equipment-slot> drag_to=<agent-visible-slot> verified=1 post_equip_equipment_count=<n> equipment_stale=1` 表示执行了合成并装备。两种路径都只在 success 且 post-equip 验证通过后更新角色装备记录，并会令装备快照 stale。
```

- [ ] **Step 3: Update command surface reference**

In `skills/trail-cw-prep/references/command-surface.md`, update the `cw.equipment.compose` output example to include both forms:

````markdown
Existing target path:

```text
ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2
shot path=.trail/shots/req.png
info read_image_first=1
# 综合信息
info action=equip_existing drag_from=equipment:3 drag_to=front:1 verified=1 consumed=0 post_equip_equipment_count=1 equipment_stale=1
# 装备信息
item kind=existing phase=pre_equip idx=3 pos=equipment:3 name=高周波电锯
# 角色信息
slot pos=front:1 name=希儿 装备=高周波电锯 count=2
```

Compose path continues to use:

```text
info action=compose drag_from=equipment:5 drag_to=equipment:2 verified=1 consumed=2 post_compose_equipment_count=2 verified_shift=1
info action=equip drag_from=equipment:2 drag_to=front:1 verified=1 post_equip_equipment_count=1 equipment_stale=1
```
````

Ensure fenced code blocks are balanced in the final markdown file and that both direct-equip and compose examples include complete `info action=equip_existing` and `info action=compose` fact lines.

- [ ] **Step 4: Add active docs token assertions**

Find the active-skill/AGENTS protocol assertions in `tests/test_output_debug.py` and add assertions that lock direct-equip tokens in both `AGENTS.md` and `skills/trail-cw-prep/SKILL.md`. If the file uses helper functions for reading docs, follow the existing style. The new assertions should include these exact tokens:

```python
def test_active_docs_describe_cw_equipment_compose_existing_path():
    agents_text = Path("AGENTS.md").read_text(encoding="utf-8")
    prep_text = Path("skills/trail-cw-prep/SKILL.md").read_text(encoding="utf-8")
    command_surface_text = Path("skills/trail-cw-prep/references/command-surface.md").read_text(encoding="utf-8")
    combined = "\n".join([agents_text, prep_text, command_surface_text])

    assert "action=equip_existing" in combined
    assert "consumed=0" in combined
    assert "equipment_stale=1" in combined
    assert "item kind=existing phase=pre_equip" in combined
    assert "action=compose" in combined
    assert "consumed=2" in combined
```

If `tests/test_output_debug.py` already has a broader docs test, add these assertions there instead of creating a separate test, but keep all six token checks.

- [ ] **Step 5: Run docs contract tests**

Run:

```powershell
uv run pytest tests/test_output_debug.py -q
```

Expected: PASS. This catches active skill/AGENTS output-protocol references that tests currently enforce.

---

### Task 6: Full Verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run focused regression suite**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py tests/test_output_rendering.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_debug.py -q
```

Expected: PASS. If failures appear outside modified behavior, inspect whether they are legitimate regressions before changing tests.

- [ ] **Step 2: Run default quick regression**

Run:

```powershell
uv run pytest
```

Expected: PASS with the project default slow-test skips. Baseline before this plan was `2010 passed, 30 skipped` in this worktree; the final count should increase by the new tests.

- [ ] **Step 3: Run whitespace diff check**

Run:

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Inspect worktree status**

Run:

```powershell
git status --short
```

Expected: only intentional changes in `AGENTS.md`, `skills/trail-cw-prep/SKILL.md`, `skills/trail-cw-prep/references/command-surface.md`, `trail/scenes/cw/equipment.py`, `trail/output/rendering.py`, relevant tests, `docs/superpowers/specs/2026-05-02-cw-equipment-compose-reuse-existing-design.md`, and `docs/superpowers/plans/2026-05-02-cw-equipment-compose-reuse-existing-implementation.md`.

---

## Self-Review

**Spec coverage:**
- Existing target lookup by stable target identity: Task 1.
- Direct equip before compose, with materials missing bypass: Task 2.
- Uncertain target fallback, missing recipe no fallback, unsupported recipe bypass only when existing target exists: Task 2.
- Preflight before drag and post-equip verification no-write on failure: Task 2.
- Duplicate target identity remaining after direct equip: Task 2.
- Renderer order and must-keep `consumed=0`: Task 3.
- YAML unsupported, screenshot required, recover/tainted unknown: Task 4.
- AGENTS and active skill documentation sync: Task 5.
- Full regression and diff checks: Task 6.

**Placeholder scan:**
- No unfilled placeholder sections and no unspecified error handling instructions.
- Every code-changing step names exact files and provides concrete code or concrete expected text.

**Type consistency:**
- Helper signature is `select_existing_cw_equipment_target(snapshot: dict[str, Any], *, recipe) -> Mapping[str, Any] | None` and all tests call that exact name.
- Direct equip scene response uses `action`, `existing_item`, `equip_action`, `verified`, `consumed`, `post_equip_equipment_count`, and `equipment_stale`; renderer test and implementation use the same keys.
- Existing compose response keeps `materials`, `result_item`, `compose_action`, `equip_action`, `consumed`, `post_compose_equipment_count`, `post_equip_equipment_count`, and `verified_shift` unchanged.
