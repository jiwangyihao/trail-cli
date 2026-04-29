# CW Equipment Session Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `cw.equipment.read` 改成可持久化、可动作引用的装备槽位快照，并收紧默认文本输出。

**Architecture:** 装备网格层负责 `equipment:<idx>` 解析和中心点计算；装备读取层负责生成不含 `box` 的结构化 item；daemon/service 层负责写入 `cw_state.equipment` 和在 CW mutation 后标记 stale；renderer 层只负责默认文本/YAML 展示。

**Tech Stack:** Python 3.12、Typer CLI、pytest、Pillow、现有 `trail.output.rendering` 文本/YAML renderer、现有 `SessionModel.scene_state`。

---

## 文件结构

- 修改 `trail/scenes/cw/equipment_grid.py`：新增 `equipment_slot_center()` 和内部中心点计算 helper。
- 修改 `trail/scenes/cw/equipment.py`：item shape 改为 `pos/center`，移除 `box`，新增 session apply wrapper。
- 修改 `trail/scenes/cw/models.py`：新增 `equipment` 默认 state。
- 修改 `trail/daemon/cw_service.py`：`cw.equipment.read` 改走 session 持久化 wrapper；CW mutation 成功后标记 equipment stale。
- 修改 `trail/output/rendering.py`：`cw.equipment.read` 默认文本改为 `pos/center`，YAML allowlist 加入 `cw.equipment.read`。
- 修改 `tests/test_cw_equipment.py`：TDD 覆盖 helper、item shape、无 `box`。
- 修改 `tests/test_daemon_protocol.py`：TDD 覆盖 capture read 持久化和 mutation stale。
- 修改 `tests/test_output_rendering.py`：TDD 覆盖默认文本字段收敛。
- 修改 `tests/test_cw_rpc_contracts.py`：TDD 覆盖 YAML 从不支持变为支持。
- 修改 `tests/test_output_debug.py`：同步 AGENTS renderer/YAML 协议断言。
- 修改 `tests/test_atomic_commands.py`：覆盖 `state.dump --format yaml` 中 equipment snapshot 可见。
- 修改 `README.md`、`AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`：同步协议。

## Task 1: 装备槽位 helper

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment_grid.py`

- [ ] **Step 1: 写 helper 红灯测试**

在 `tests/test_cw_equipment.py` 的 grid 测试附近新增：

```python
def test_equipment_slot_center_uses_agent_visible_equipment_position():
    grid = load_equipment_grid_module()

    assert grid.equipment_slot_center("equipment:1") == (1855, 275)
    assert grid.equipment_slot_center("equipment:6") == (1855, 663)
    assert grid.equipment_slot_center("equipment:7") == (1775, 275)
    assert grid.equipment_slot_center("equipment:60") == (1137, 663)


@pytest.mark.parametrize("value", ["1", "front:1", "equipment:0", "equipment:61", "equipment:x", "equipment:"])
def test_equipment_slot_center_rejects_invalid_agent_positions(value):
    grid = load_equipment_grid_module()

    with pytest.raises(Exception) as exc_info:
        grid.equipment_slot_center(value)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_SLOT_INVALID"
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_cw_equipment.py::test_equipment_slot_center_uses_agent_visible_equipment_position tests/test_cw_equipment.py::test_equipment_slot_center_rejects_invalid_agent_positions -q --basetemp .trail/pytest-tmp/equipment-slot-center-red -p no:cacheprovider`

Expected: FAIL，报 `AttributeError: module 'trail.scenes.cw.equipment_grid' has no attribute 'equipment_slot_center'`。

- [ ] **Step 3: 实现 helper**

在 `trail/scenes/cw/equipment_grid.py` 加入 `TrailError` import 和 helper：

```python
from trail.core.errors import TrailError


def equipment_cell_center(cell: EquipmentGridCell) -> tuple[int, int]:
    return (
        cell.box["left"] + cell.box["width"] // 2,
        cell.box["top"] + cell.box["height"] // 2,
    )


def equipment_slot_center(
    value: str,
    *,
    profile: EquipmentGridProfile = DEFAULT_EQUIPMENT_GRID_PROFILE,
    columns: int = 10,
    rows: int = 6,
) -> tuple[int, int]:
    prefix, separator, raw_idx = str(value).partition(":")
    if prefix != "equipment" or separator != ":" or not raw_idx.isdecimal():
        raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")
    idx = int(raw_idx)
    if idx < 1 or idx > columns * rows:
        raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")
    for cell in iter_equipment_grid_cells(profile, columns=columns, rows=rows):
        if cell.idx == idx:
            return equipment_cell_center(cell)
    raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")
```

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_cw_equipment.py::test_equipment_slot_center_uses_agent_visible_equipment_position tests/test_cw_equipment.py::test_equipment_slot_center_rejects_invalid_agent_positions -q --basetemp .trail/pytest-tmp/equipment-slot-center-green -p no:cacheprovider`

Expected: PASS。

## Task 2: 结构化 item shape

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: 写 item shape 红灯测试**

更新 `test_read_cw_equipment_keeps_non_empty_uncertain_without_candidates` 中对 item 的断言：

```python
assert item["idx"] == 1
assert item["pos"] == "equipment:1"
assert item["row"] == 1
assert item["col"] == 1
assert item["center"] == {"x": 1855, "y": 275}
assert "box" not in item
assert item["name"] is None
assert item["equipment_id"] is None
assert item["cache_key"] is None
assert item["alt"] is None
assert item["alt_score"] is None
assert item["score"] is None
assert item["gap"] is None
assert item["candidates"] == []
```

在 `test_read_cw_equipment_recognizes_best_variants_and_counts` 增加：

```python
assert result["items"][0]["pos"] == "equipment:1"
assert result["items"][0]["center"] == {"x": 1855, "y": 275}
assert "box" not in result["items"][0]
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_cw_equipment.py::test_read_cw_equipment_recognizes_best_variants_and_counts tests/test_cw_equipment.py::test_read_cw_equipment_keeps_non_empty_uncertain_without_candidates -q --basetemp .trail/pytest-tmp/equipment-item-shape-red -p no:cacheprovider`

Expected: FAIL，因为当前 item 没有 `pos/center` 且仍有 `box`。

- [ ] **Step 3: 实现 item shape**

在 `trail/scenes/cw/equipment.py` 导入 `equipment_cell_center`，并修改 `_item_from_result`：

```python
center_x, center_y = equipment_cell_center(crop.cell)
item = {
    "idx": crop.cell.idx,
    "pos": f"equipment:{crop.cell.idx}",
    "row": crop.cell.row,
    "col": crop.cell.col,
    "center": {"x": center_x, "y": center_y},
    "name": top.name if top is not None else None,
    "equipment_id": top.equipment_id if top is not None else None,
    "cache_key": top.cache_key if top is not None else None,
    "score": result.score,
    "gap": result.gap,
    "uncertain": bool(result.uncertain),
    "alt": alt.name if alt is not None else None,
    "alt_score": alt.score if alt is not None else None,
    "candidates": [asdict(candidate) for candidate in result.candidates],
}
```

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_cw_equipment.py::test_read_cw_equipment_recognizes_best_variants_and_counts tests/test_cw_equipment.py::test_read_cw_equipment_keeps_non_empty_uncertain_without_candidates -q --basetemp .trail/pytest-tmp/equipment-item-shape-green -p no:cacheprovider`

Expected: PASS。

## Task 3: Session 持久化与 stale

**Files:**
- Modify: `trail/scenes/cw/models.py`
- Modify: `trail/scenes/cw/equipment.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写持久化红灯测试**

扩展 `tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture` 的 fake `read_cw_equipment`，让它返回一个 item：

```python
def fake_read(session, runtime, workspace_root=None):
    calls.append("read")
    snapshot = {
        "count": 1,
        "uncertain": 0,
        "empty": 59,
        "items": [{"pos": "equipment:1", "center": {"x": 1855, "y": 275}, "row": 1, "col": 1, "name": "生命之花", "score": 0.93, "uncertain": False}],
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }
    session.scene_state.setdefault("cw", {})["equipment"] = snapshot
    return snapshot
```

断言：

```python
persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
assert persisted["stale"] is False
assert persisted["items"][0]["pos"] == "equipment:1"
assert persisted["items"][0]["center"] == {"x": 1855, "y": 275}
```

- [ ] **Step 2: 写 mutation stale 红灯测试**

新增：

```python
def test_command_service_marks_cw_equipment_snapshot_stale_after_mutation(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("cw", {})["equipment"] = {"items": [{"pos": "equipment:1"}], "stale": False}
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            return tmp_path / ".trail" / "shots" / "collect.png"
        def collect_warnings(self):
            return []
        def match_references(self, screenshot_path, limit: int = 3):
            return []

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: session)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=CwService(runtime_service=runtime_service))

    response = command_service.handle(DaemonRequest(request_id="req-collect", protocol_version=PROTOCOL_VERSION, workspace_root=str(tmp_path), session_id=session.session_id, verbose=False, method="cw.crystals.collect", payload={}))

    assert response["ok"] is True
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is True
    assert persisted["items"] == [{"pos": "equipment:1"}]
```

- [ ] **Step 3: 运行红灯测试**

Run: `uv run pytest tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture tests/test_daemon_protocol.py::test_command_service_marks_cw_equipment_snapshot_stale_after_mutation -q --basetemp .trail/pytest-tmp/equipment-persist-red -p no:cacheprovider`

Expected: FAIL，当前 handler 不接收 session，且 mutation 不标记 equipment stale。

- [ ] **Step 4: 实现持久化和 stale**

在 `trail/scenes/cw/models.py` 增加 `equipment` 字段，并加入 `model_dump()`。

在 `trail/scenes/cw/equipment.py` 加：

```python
from copy import deepcopy
from trail.session.models import SessionModel
from trail.scenes.cw.models import ensure_cw_state


def apply_cw_equipment_read(session: SessionModel, runtime, *, workspace_root: str | Path | None = None) -> dict[str, Any]:
    snapshot = read_cw_equipment(runtime, workspace_root=workspace_root)
    ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
    return snapshot
```

在 `read_cw_equipment` 返回值加入 `columns=10`、`rows=6`、`stale=False`。

在 `trail/daemon/cw_service.py` 中导入 `apply_cw_equipment_read`，`cw.equipment.read` handler 改为：

```python
"cw.equipment.read": lambda: apply_cw_equipment_read(session, runtime(), workspace_root=workspace_root),
```

增加 helper：

```python
def _mark_cw_equipment_stale(session) -> None:
    equipment = ensure_cw_state(session).get("equipment")
    if isinstance(equipment, dict) and equipment.get("stale") is not True:
        equipment["stale"] = True
```

在 `handle_mutation` 成功得到 `result, scene_warnings = _pop_scene_warnings(result)` 之后、`session_service.save_session(session)` 之前调用 `_mark_cw_equipment_stale(session)`。

- [ ] **Step 5: 运行绿灯测试**

Run: `uv run pytest tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture tests/test_daemon_protocol.py::test_command_service_marks_cw_equipment_snapshot_stale_after_mutation -q --basetemp .trail/pytest-tmp/equipment-persist-green -p no:cacheprovider`

Expected: PASS。

## Task 4: Renderer 与 YAML contract

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 写 renderer 红灯测试**

改 `test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref` 的 payload 包含两个 item：一个确定、一个不确定。期望：

```python
assert render_output("cw.equipment.read", payload).splitlines() == [
    "ok cw.equipment.read count=2 uncertain=1 empty=58",
    "shot path=.trail/shots/req-equipment.png",
    "info read_image_first=1",
    "item pos=equipment:1 center=1855,275 name=幸运星 score=0.88 uncertain=0",
    "item pos=equipment:7 center=1775,275 name=蓝钻 score=0.78 uncertain=1 gap=0.03 alt=光能电池 alt_score=0.75",
    "info backend=vector layout=default",
    'warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"',
    "ref path=trail/references/cw/equipment.png sim=0.9",
]
```

- [ ] **Step 2: 写 YAML contract 红灯测试**

把 `test_cw_equipment_read_maps_to_canonical_command_and_rejects_yaml` 改名为 `test_cw_equipment_read_maps_to_canonical_command_and_supports_yaml`。YAML 断言：

```python
assert yaml_result.exit_code == 0
yaml_lines = yaml_result.stdout.splitlines()
assert yaml_lines[:3] == [
    "ok cw.equipment.read count=0 uncertain=0 empty=60",
    "shot path=.trail/shots/req-equipment-read.png",
    "info read_image_first=1",
]
assert "items: []" in yaml_result.stdout
assert "backend: vector" in yaml_result.stdout
```

- [ ] **Step 3: 运行红灯测试**

Run: `uv run pytest tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref tests/test_cw_rpc_contracts.py::test_cw_equipment_read_maps_to_canonical_command_and_supports_yaml -q --basetemp .trail/pytest-tmp/equipment-render-red -p no:cacheprovider`

Expected: FAIL，当前 renderer 仍输出旧字段且 YAML 不支持。

- [ ] **Step 4: 实现 renderer**

在 `trail/output/rendering.py`：

```python
YAML_ALLOWLIST = {"daemon.status", "state.dump", "guide.fetch.cw", "guide.config.cw", "cw.equipment.read"}
```

新增：

```python
def _format_center(center: Any) -> str | None:
    if not isinstance(center, dict):
        return None
    x = center.get("x")
    y = center.get("y")
    if x is None or y is None:
        return None
    return f"{x},{y}"
```

修改 `_render_cw_equipment_read` item 逻辑：默认 facts 为 `pos/center/name/score/uncertain`；只有 `item.get("uncertain") is True` 时追加 `gap/alt/alt_score`。

- [ ] **Step 5: 运行绿灯测试**

Run: `uv run pytest tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref tests/test_cw_rpc_contracts.py::test_cw_equipment_read_maps_to_canonical_command_and_supports_yaml -q --basetemp .trail/pytest-tmp/equipment-render-green -p no:cacheprovider`

Expected: PASS。

## Task 5: State dump 与协议文档同步

**Files:**
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_debug.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`

- [ ] **Step 1: 写 docs/state 红灯测试**

在 `tests/test_atomic_commands.py` 新增 `test_state_dump_yaml_includes_cw_equipment_snapshot`，fake daemon data 中加入：

```python
"scene_state": {
    "cw": {
        "equipment": {
            "items": [{"pos": "equipment:1", "center": {"x": 1855, "y": 275}, "row": 1, "col": 1, "name": "生命之花"}],
            "stale": False,
        }
    }
}
```

断言 yaml stdout 包含 `equipment:`、`pos: equipment:1`、`center:`、`row: 1`、`col: 1`，且不包含 `box:`。

在 `tests/test_output_debug.py::test_project_agents_declares_renderer_contracts` 更新 allowlist 断言为：

```python
assert "当前 YAML allowlist 是 `cw.equipment.read`、`daemon.status`、`state.dump`、`guide.fetch.cw`、`guide.config.cw`。" in agents
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_atomic_commands.py::test_state_dump_yaml_includes_cw_equipment_snapshot tests/test_output_debug.py::test_project_agents_declares_renderer_contracts -q --basetemp .trail/pytest-tmp/equipment-docs-red -p no:cacheprovider`

Expected: FAIL，新增测试未实现或 AGENTS 仍是旧文案。

- [ ] **Step 3: 更新文档**

必须同步：

```text
AGENTS.md:
- YAML allowlist 增加 `cw.equipment.read`
- `cw.equipment.read` item 字段改为 `pos/center/name/score/uncertain/gap/alt/alt_score`
- 说明 row/col 只在结构化 data/YAML/session 中出现，默认文本不输出
- 删除“不写入 cw_state / 不加入 YAML allowlist”的旧语义，改为写入 `cw_state.equipment`

README.md:
- 装备 read 示例改为 `item pos=equipment:1 center=1855,275 name=生命之花 score=0.93 uncertain=0`
- 说明确定结果隐藏 `gap/alt/alt_score`，低置信才输出
- 说明 `--format yaml` 或 `state.dump --format yaml` 查看 row/col 诊断

skills/trail-cw-prep/SKILL.md:
- 说明装备 item 使用 `pos=equipment:<idx>` 和 `center=x,y`
- 说明低置信才看 `gap/alt/alt_score`

skills/trail-cw-prep/references/command-surface.md:
- 同步 `trail cw equipment read` 输出描述
```

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_atomic_commands.py::test_state_dump_yaml_includes_cw_equipment_snapshot tests/test_output_debug.py::test_project_agents_declares_renderer_contracts -q --basetemp .trail/pytest-tmp/equipment-docs-green -p no:cacheprovider`

Expected: PASS。

## Task 6: 聚焦验证

**Files:**
- No code changes unless verification reveals a bug.

- [ ] **Step 1: 运行完整聚焦验证**

Run: `uv run pytest tests/test_cw_equipment.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture tests/test_daemon_protocol.py::test_cw_equipment_methods_are_classified_for_command_routing tests/test_daemon_protocol.py::test_command_service_marks_cw_equipment_snapshot_stale_after_mutation tests/test_output_debug.py::test_project_agents_declares_renderer_contracts tests/test_atomic_commands.py::test_state_dump_renders_summary_before_yaml tests/test_atomic_commands.py::test_state_dump_yaml_includes_cw_equipment_snapshot -q --basetemp .trail/pytest-tmp/equipment-session-output -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 2: 运行 diff whitespace 检查**

Run: `rtk git diff --check`

Expected: no output, exit code 0。

- [ ] **Step 3: 检查工作区**

Run: `rtk git status --short --branch`

Expected: 显示 `## feature/cw-equipment-session-output` 和本需求修改文件。不要提交，除非用户明确要求。

## 自审结果

- Spec coverage: 所有规格目标映射到 Task 1-6。
- Placeholder scan: 本计划不包含占位章节、未完成标记或未命名测试。
- Type consistency: `pos`、`center`、`equipment_slot_center`、`apply_cw_equipment_read`、`cw_state.equipment` 命名在任务间一致。
