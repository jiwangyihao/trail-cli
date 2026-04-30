# cw.portal.select Equipment Auto-Collect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cw.portal.select` 在进入普通备战并读取 fresh slots 后自动读取装备背包与装备推荐，并把装备事实渲染在商店事实之前。

**Architecture:** 复用现有 `apply_cw_equipment_read(...)`，不新增命令、不改识别算法、不做截图复用。daemon 在 portal select 内部保存局部 `equipment_snapshot`，renderer 抽出装备输出 helper 供独立 `cw.equipment.read` 与 `cw.portal.select` 共用，文档和契约测试锁定新增 `# 装备信息` 固定标题、soft warning 与 stale 例外。

**Tech Stack:** Python 3、Typer CLI、pytest、现有 Trail daemon/session/output renderer、Markdown skills 文档。

**Commit Policy:** 本仓库当前会话不要自动提交。只有用户明确要求提交时，才按 Conventional Commits 提交；实现任务中的“检查变更范围”步骤用于替代自动 commit。

---

## Files

- Modify: `trail/daemon/cw_service.py`
  - 在 `_select_portal_and_apply_selected_guide(...)` 中插入 equipment 自动读取。
  - 添加 `_cw_equipment_auto_collect_warning(...)` 和 `_has_fresh_portal_select_equipment(...)` 这类小 helper。
  - 修改 `handle_mutation(...)` 中 `_mark_cw_equipment_stale(session)` 调用条件。
- Modify: `trail/output/rendering.py`
  - 从 `_render_cw_equipment_read(...)` 抽出装备 item/info helper 与低置信 warning helper。
  - 在 `_render_cw_portal_select(...)` 的 traits 后、shop 前输出 `# 装备信息`、装备 item、summary info、推荐分块。
- Modify: `trail/commands/cw.py`
  - 更新 `cw portal select --help` 文本，说明自动收集 stage/slots/equipment/shop facts、装备失败是 soft warning、handoff 不变。
- Modify: `AGENTS.md`
  - 新增固定标题 `# 装备信息`。
  - 更新 `cw.portal.select` 自动收集顺序、输出顺序、soft warning、stale 例外。
- Modify: `README.md`
  - 若根 README 有 `cw.portal.select` 示例或开局链路说明，更新装备板块与装备推荐示例。
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-cw-portal/references/portal-command-surface.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-guide/references/command-surface.md`
- Modify: every `skills/trail-cw-guide/references/*.md` file that describes `cw.portal.select` or `portal select --card-idx` follow-up semantics.
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `skills/trail-hsr/references/scene-entry-index.md`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_skill_structure.py`
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_skill_routing_contracts.py` if the existing routing smoke lives there; otherwise add the assertion to the current routing contract test file that owns `scene-entry-index` handoff checks.

---

### Task 1: Daemon Orchestration Tests

**Files:**
- Modify: `tests/test_daemon_protocol.py:1-5`
- Modify: `tests/test_daemon_protocol.py:4613-4681`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Add the import needed by new fake snapshots**

Add `deepcopy` near the top of `tests/test_daemon_protocol.py`:

```python
import json
import socket
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
```

- [ ] **Step 2: Extend `_patch_cw_portal_select_auto_collect_success` with equipment behavior**

Replace the helper body around `tests/test_daemon_protocol.py:4613-4681` with this shape. Keep existing fake collect/slots/shop behavior and add the equipment fake between `fake_read_slots` and `fake_scan_shop`:

```python
def _patch_cw_portal_select_auto_collect_success(monkeypatch, events: list[str], *, sleeps: list[float] | None = None):
    def fake_collect(session, collector):
        events.append("crystal.collect")
        session.scene_state.setdefault("cw", {})["metrics"] = {"last_crystal_collection": "done"}
        return session

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        assert reader == "slots-reader"
        assert targets is None
        assert guide_config == {"roles": [], "traits": []}
        events.append("slots.read")
        session.scene_state.setdefault("cw", {})["slots"] = {
            "front": [{"name": "希儿"}],
            "back": [],
            "hand": [],
            "stale": False,
        }
        return session

    def fake_apply_equipment(session, runtime, workspace_root=None):
        del runtime
        assert workspace_root is not None
        events.append("equipment.read")
        snapshot = {
            "count": 1,
            "uncertain": 0,
            "empty": 59,
            "backend": "vector",
            "layout": "default",
            "items": [{"pos": "equipment:1", "name": "基础装甲", "score": 0.9, "uncertain": False}],
            "recommendations": {
                "priority": [{"idx": 1, "name": "高周波电锯", "basics": [], "required_roles": ["希儿"], "acquired_roles": [], "missing_roles": ["希儿"]}],
                "role_missing": [{"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}],
                "todos": [],
            },
            "stale": False,
        }
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return snapshot

    def fake_scan_shop(session, scanner, guide_config=None):
        assert scanner == "page-reader"
        assert guide_config == {"roles": [], "traits": []}
        events.append("shop.scan")
        session.scene_state.setdefault("cw", {})["shop"] = {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
        }
        return session

    def fake_project_shop(session):
        events.append("shop.project")
        return {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
            "stage_status_stale": True,
        }

    def fake_close_shop(session, closer):
        assert closer == "shop-closer"
        events.append("shop.close")
        session.scene_state.setdefault("cw", {})["shop"] = {"opened": False, "stale": True}
        return session

    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", fake_collect)
    monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: events.append("slots.dismiss"))
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, **kwargs: events.append(f"slots.reader({kwargs})") or "slots-reader",
    )
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fake_apply_equipment)
    monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: events.append("shop.open") or session)
    monkeypatch.setattr("trail.daemon.cw_service.shop_page_snapshot_reader_factory", lambda runtime, **kwargs: "page-reader")
    monkeypatch.setattr("trail.daemon.cw_service.scan_cw_shop", fake_scan_shop)
    monkeypatch.setattr("trail.daemon.cw_service.project_cw_shop_snapshot", fake_project_shop)
    monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", fake_close_shop)
    monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: "crystal-collector")
    monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: "shop-opener")
    monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: "shop-closer")
    if sleeps is None:
        monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None)
    else:
        monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: sleeps.append(seconds))
```

- [ ] **Step 3: Add a failing success-path test for ordering, response data, and persisted stale state**

Add this test near other `cw.portal.select` daemon tests:

```python
def test_command_service_cw_portal_select_auto_collects_equipment_before_shop(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    events: list[str] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(operation_guide="前期 先读图"),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "A"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=CwService(runtime_service=runtime_service))

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-equipment",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is True
    assert events.index("slots.read") < events.index("equipment.read") < events.index("shop.open")
    assert response["data"]["equipment"]["stale"] is False
    assert response["data"]["equipment"]["items"][0]["name"] == "基础装甲"
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is False
    assert persisted["items"][0]["name"] == "基础装甲"
```

- [ ] **Step 4: Add a failing soft-failure test**

Add this test near the success-path test:

```python
def test_command_service_cw_portal_select_equipment_failure_is_soft_warning_and_stales_old_snapshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    events: list[str] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(operation_guide=""),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
        "equipment": {"items": [{"pos": "equipment:1", "name": "旧装备"}], "stale": False},
    }
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fail_equipment(session, runtime, workspace_root=None):
        del session, runtime, workspace_root
        events.append("equipment.read")
        raise TrailError("CW_EQUIPMENT_LAYOUT_MISMATCH", "equipment grid requires canonical 1920x1080 screenshot")

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "A"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fail_equipment)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=CwService(runtime_service=runtime_service))

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-equipment-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is True
    assert "equipment" not in response["data"]
    assert "shop" in response["data"]
    assert any(warning["code"] == "CW_EQUIPMENT_AUTO_COLLECT_FAILED" for warning in response["warnings"])
    assert events.index("equipment.read") < events.index("shop.open")
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is True
    assert persisted["items"] == [{"pos": "equipment:1", "name": "旧装备"}]
```

- [ ] **Step 5: Verify the new daemon tests fail for the expected reason**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_daemon_protocol.py::test_command_service_cw_portal_select_auto_collects_equipment_before_shop tests/test_daemon_protocol.py::test_command_service_cw_portal_select_equipment_failure_is_soft_warning_and_stales_old_snapshot -q
```

Expected: both tests fail because `cw.portal.select` does not yet call `apply_cw_equipment_read(...)`, and the response does not yet contain `data.equipment` or the soft warning.

---

### Task 2: Daemon Implementation

**Files:**
- Modify: `trail/daemon/cw_service.py:266-386`
- Modify: `trail/daemon/cw_service.py:710-714`
- Modify: `trail/daemon/cw_service.py:814-861`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Add helpers for equipment warning and stale finalizer skip**

Add these helpers near `_mark_cw_equipment_stale(...)`:

```python
def _cw_equipment_auto_collect_warning(error: Exception) -> dict:
    warning = {
        "code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED",
        "message": format_exception_detail(error),
    }
    detail_code = getattr(error, "code", None)
    if isinstance(detail_code, str) and detail_code:
        warning["detail_code"] = detail_code
    return warning


def _has_fresh_portal_select_equipment(method: str, result: object) -> bool:
    if method != "cw.portal.select" or not isinstance(result, dict):
        return False
    equipment = result.get("equipment")
    return isinstance(equipment, dict) and equipment.get("stale") is False
```

- [ ] **Step 2: Change mutation finalizer to preserve same-command fresh portal equipment**

Replace:

```python
result, scene_warnings = _pop_scene_warnings(result)
_mark_cw_equipment_stale(session)
```

with:

```python
result, scene_warnings = _pop_scene_warnings(result)
if not _has_fresh_portal_select_equipment(method, result):
    _mark_cw_equipment_stale(session)
```

This preserves `cw.portal.select` fresh `data.equipment.stale=False` and keeps every other CW mutation stale behavior unchanged.

- [ ] **Step 3: Insert equipment auto-collect after slots and before shop**

In `_select_portal_and_apply_selected_guide(...)`, replace the block after `slot_warnings = _pop_response_snapshot_warnings(slots_snapshot)` with this structure:

```python
    equipment_snapshot = None
    equipment_warnings: list[dict] = []
    try:
        equipment_snapshot = deepcopy(
            apply_cw_equipment_read(session, runtime, workspace_root=workspace_root)
        )
    except Exception as error:
        equipment_warnings.append(_cw_equipment_auto_collect_warning(error))
    open_cw_shop(session, opener=shop_opener_factory(runtime))
    sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
    shop_result = scan_cw_shop(
        session,
        scanner=shop_page_snapshot_reader_factory(runtime),
        guide_config=guide_config,
    )
    shop_projection = project_cw_shop_snapshot(session)
    shop_response = _response_snapshot_or_fallback(shop_result, {})
    shop_warnings = _pop_response_snapshot_warnings(shop_response)
    shop_snapshot = {**shop_projection, **shop_response}
    close_cw_shop(session, closer=shop_closer_factory(runtime))
    selected_data["crystals"] = deepcopy(ensure_cw_state(session).get("metrics") or {})
    selected_data["slots"] = slots_snapshot
    if equipment_snapshot is not None:
        selected_data["equipment"] = deepcopy(equipment_snapshot)
    selected_data["shop"] = shop_snapshot
    response_warnings = [*slot_warnings, *equipment_warnings, *shop_warnings]
```

- [ ] **Step 4: Run daemon tests and confirm pass**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_daemon_protocol.py::test_command_service_cw_portal_select_auto_collects_equipment_before_shop tests/test_daemon_protocol.py::test_command_service_cw_portal_select_equipment_failure_is_soft_warning_and_stales_old_snapshot tests/test_daemon_protocol.py::test_command_service_marks_cw_equipment_snapshot_stale_after_mutation -q
```

Expected: all selected daemon tests pass.

- [ ] **Step 5: Check changed files without committing**

Run:

```powershell
rtk git status --short
```

Expected: `trail/daemon/cw_service.py` and `tests/test_daemon_protocol.py` are modified; unrelated `docs/superpowers/plans/2026-04-30-cw-static-resource-bundle.md` remains untouched if it was already present.

---

### Task 3: Renderer Tests And Implementation

**Files:**
- Modify: `tests/test_output_rendering.py:1122-1269`
- Modify: `tests/test_output_rendering.py:3191-3257`
- Modify: `trail/output/rendering.py:833-853`
- Modify: `trail/output/rendering.py:1222-1314`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: Add a failing renderer test for full portal equipment ordering**

Add a new test after `test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff`:

```python
def test_portal_select_renders_equipment_between_traits_and_shop(capsys) -> None:
    print_output(
        "cw.portal.select",
        {
            "ok": True,
            "screenshot": ".trail/shots/portal-prep.png",
            "data": {
                "card_idx": 1,
                "portal_title": "击破概念股",
                "slots": {
                    "front": [{"name": "希儿", "star": 1, "traits": ["巡猎"]}],
                    "back": [],
                    "hand": [],
                    "stale": False,
                    "stage": "preparation",
                    "stage_stale": False,
                    "trait_summary": [{"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5}],
                },
                "equipment": {
                    "count": 1,
                    "uncertain": 0,
                    "empty": 59,
                    "backend": "vector",
                    "layout": "default",
                    "items": [{"pos": "equipment:1", "center": {"x": 100, "y": 200}, "name": "基础装甲", "score": 0.9, "uncertain": False}],
                    "recommendations": {
                        "priority": [{"idx": 1, "name": "高周波电锯", "basics": [{"name": "基础装甲", "have": 1, "need": 1}], "required_roles": ["希儿"], "acquired_roles": [], "missing_roles": ["希儿"]}],
                        "role_missing": [{"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}],
                        "todos": [],
                    },
                    "stale": False,
                },
                "shop": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False},
            },
            "warnings": [],
            "references": [],
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines == [
        "ok cw.portal.select idx=1 投资环境=击破概念股",
        "shot path=.trail/shots/portal-prep.png",
        "info read_image_first=1",
        "# 综合信息",
        "info stage=preparation stale=0",
        "# 角色信息",
        "slot pos=front:1 name=希儿 star=1 traits=巡猎",
        "# 羁绊信息",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
        "# 装备信息",
        "item pos=equipment:1 center=100,200 name=基础装甲 score=0.90 uncertain=0",
        "info count=1 uncertain=0 empty=59 backend=vector layout=default",
        "# 装备优先级",
        "guide idx=1 装备=高周波电锯 基础装备=基础装甲:1/1 需求角色=希儿 已获取数=0 未获取数=1 未获取角色=希儿",
        "# 角色装备需求",
        "slot pos=front:1 name=希儿 装备=高周波电锯 分类=优选",
        "# 商店信息",
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
    ]
```

- [ ] **Step 2: Add failing renderer tests for soft failure and low confidence distinction**

Add these two tests after the ordering test:

```python
def test_portal_select_equipment_auto_collect_failed_warns_without_empty_equipment_sections() -> None:
    payload = {
        "ok": True,
        "data": {
            "card_idx": 1,
            "portal_title": "击破概念股",
            "shop": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False},
        },
        "warnings": [{"code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED", "message": "equipment grid requires canonical 1920x1080 screenshot", "detail_code": "CW_EQUIPMENT_LAYOUT_MISMATCH"}],
        "references": [],
    }

    lines = render_output("cw.portal.select", payload).splitlines()

    assert "# 装备信息" not in lines
    assert "# 装备优先级" not in lines
    assert "# 角色装备需求" not in lines
    assert not any("LOW_CONFIDENCE" in line for line in lines)
    assert "# 商店信息" in lines
    assert 'warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED msg="equipment grid requires canonical 1920x1080 screenshot"' in lines
    assert lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"


def test_portal_select_low_confidence_equipment_warns_without_auto_collect_failure() -> None:
    payload = {
        "ok": True,
        "data": {
            "card_idx": 1,
            "portal_title": "击破概念股",
            "equipment": {
                "count": 1,
                "uncertain": 1,
                "empty": 59,
                "backend": "vector",
                "layout": "default",
                "items": [{"pos": "equipment:1", "name": "蓝钻", "score": 0.78, "uncertain": True, "gap": 0.03, "alt": "光能电池", "alt_score": 0.75}],
            },
            "shop": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False},
        },
        "warnings": [],
        "references": [],
    }

    lines = render_output("cw.portal.select", payload).splitlines()

    assert 'warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"' in lines
    assert not any("CW_EQUIPMENT_AUTO_COLLECT_FAILED" in line for line in lines)
    assert lines.index('warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"') > lines.index("info coins=40 reserve_full=0")
    assert lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
```

- [ ] **Step 3: Run renderer tests to verify failure**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_output_rendering.py::test_portal_select_renders_equipment_between_traits_and_shop tests/test_output_rendering.py::test_portal_select_equipment_auto_collect_failed_warns_without_empty_equipment_sections tests/test_output_rendering.py::test_portal_select_low_confidence_equipment_warns_without_auto_collect_failure -q
```

Expected: tests fail because renderer does not yet render portal equipment sections or portal equipment low-confidence warning.

- [ ] **Step 4: Extract equipment line helpers and update portal renderer**

In `trail/output/rendering.py`, add these helpers before `_append_cw_equipment_recommendation_lines(...)`:

```python
def _append_cw_equipment_item_lines(lines: list[str], data: dict[str, Any]) -> None:
    for item in _as_list(data.get("items")):
        if not isinstance(item, dict):
            continue
        line = "item"
        head = _format_fact_sequence(("pos", item.get("pos")))
        if head:
            line += f" {head}"
        center = _format_center(item.get("center"))
        if center is not None:
            line += f" center={center}"
        facts = _format_fact_sequence(
            ("name", item.get("name")),
            ("score", _format_score_value(item.get("score"))),
            ("uncertain", bool(item.get("uncertain")) if "uncertain" in item else None),
        )
        if facts:
            line += f" {facts}"
        if item.get("uncertain") is True:
            diagnostics = _format_fact_sequence(
                ("gap", _format_score_value(item.get("gap"))),
                ("alt", item.get("alt")),
                ("alt_score", _format_score_value(item.get("alt_score"))),
            )
            if diagnostics:
                line += f" {diagnostics}"
        lines.append(line)


def _append_cw_equipment_lines(lines: list[str], data: dict[str, Any], *, include_summary: bool = False) -> None:
    _append_cw_equipment_item_lines(lines, data)
    if include_summary:
        _append_fact_line(
            lines,
            "info",
            ("count", data.get("count") if "count" in data else 0),
            ("uncertain", data.get("uncertain") if "uncertain" in data else 0),
            ("empty", data.get("empty") if "empty" in data else 0),
            ("backend", data.get("backend")),
            ("layout", data.get("layout")),
        )
        return
    _append_fact_line(lines, "info", ("backend", data.get("backend")), ("layout", data.get("layout")))


def _append_cw_equipment_low_confidence_warning(lines: list[str], data: dict[str, Any]) -> None:
    uncertain_count = _coerce_int(data.get("uncertain")) or 0
    if uncertain_count > 0:
        lines.append(
            "warn "
            + _format_fact_sequence(("code", "LOW_CONFIDENCE"), ("count", uncertain_count))
            + f" msg={_quote('装备图标低置信，请先看截图确认')}"
        )


def _append_cw_equipment_section(lines: list[str], data: dict[str, Any]) -> None:
    if not data:
        return
    _append_section(lines, "装备信息")
    _append_cw_equipment_lines(lines, data, include_summary=True)
    _append_cw_equipment_recommendation_lines(lines, data)
```

Then replace duplicate item-loop code in `_render_cw_equipment_read(...)` with:

```python
    _append_success_capture_block(lines, payload)
    _append_cw_equipment_lines(lines, data)
    _append_cw_equipment_recommendation_lines(lines, data)
    _append_cw_equipment_low_confidence_warning(lines, data)
    _append_warnings(lines, payload)
```

Update `_render_cw_portal_select(...)` to use equipment between traits and shop:

```python
    slots = _as_dict(data.get("slots"))
    equipment = _as_dict(data.get("equipment"))
    shop = _as_dict(data.get("shop"))
    status_source = _merge_cw_status_projection(slots, shop, data)
    _append_cw_status_section(lines, status_source)
    _append_skill_info_section(lines, data)
    _append_cw_slot_section(lines, slots)
    _append_cw_trait_section(lines, slots)
    _append_cw_equipment_section(lines, equipment)
    _append_cw_shop_section(lines, shop)
    _append_cw_equipment_low_confidence_warning(lines, equipment)
    _append_warnings(lines, payload)
```

- [ ] **Step 5: Run focused renderer tests**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref tests/test_output_rendering.py::test_render_output_cw_equipment_read_appends_recommendation_sections_before_warn_ref tests/test_output_rendering.py::test_render_output_cw_equipment_read_stale_slots_outputs_todo_without_current_role_counts tests/test_output_rendering.py::test_portal_select_renders_equipment_between_traits_and_shop tests/test_output_rendering.py::test_portal_select_equipment_auto_collect_failed_warns_without_empty_equipment_sections tests/test_output_rendering.py::test_portal_select_low_confidence_equipment_warns_without_auto_collect_failure -q
```

Expected: all selected renderer tests pass.

---

### Task 4: CLI/RPC Contract And Help

**Files:**
- Modify: `trail/commands/cw.py:27-31`
- Modify: `tests/test_atomic_commands.py:1908-1918`
- Modify: `tests/test_cw_rpc_contracts.py:260-284`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: Update help test before help text**

Modify `test_cw_portal_select_help_mentions_selected_guide_auto_apply` assertions:

```python
    assert "成功后会自动应用当前已选攻略" in normalized
    assert "自动收集" in normalized
    assert "stage/slots/equipment/shop" in normalized
    assert "装备失败" in normalized
    assert "soft warning" in normalized
    assert "handoff" in normalized
```

- [ ] **Step 2: Update `CW_PORTAL_HELP` text**

In `trail/commands/cw.py`, change `CW_PORTAL_HELP` to mention select side effects:

```python
CW_PORTAL_HELP = (
    "投资环境页上的识别/选择/刷新/重开动作。"
    "detect 重新识别并保存当前三张卡；refresh 点击刷新后生成新的三张卡。"
    "投资环境卡片会输出 投资环境、说明、待收集、score，以及下挂攻略摘要。"
    "select 成功后会自动应用当前已选攻略，并自动收集 stage/slots/equipment/shop 预备事实；"
    "装备读取失败会作为 soft warning 返回，不改变 cw.portal.select -> trail-cw-prep handoff。"
)
```

- [ ] **Step 3: Add CLI/RPC contract for equipment warning and final handoff**

Add this test after `test_cw_portal_select_renders_selected_card_summary`:

```python
def test_cw_portal_select_renders_equipment_warning_shop_and_handoff_last(cli_runner, fake_daemon_client, tmp_path):
    fake_daemon_client(
        {
            "cw.portal.select": build_success_response(
                request_id="req-cw-portal-select-equipment-warning",
                data={
                    "card_idx": 2,
                    "portal_title": "Beta Portal",
                    "shop": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False},
                },
                screenshot=".trail/shots/req-cw-portal-select.png",
            )
            | {"warnings": [{"code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED", "message": "equipment grid requires canonical 1920x1080 screenshot"}]}
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", SESSION_ID, "--card-idx", "2"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert 'warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED msg="equipment grid requires canonical 1920x1080 screenshot"' in lines
    assert "# 商店信息" in lines
    assert "item idx=1 slot=1 name=银狼 cost=20" in lines
    assert result.stdout.rstrip().endswith(
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
    )
```

- [ ] **Step 4: Extend existing portal select RPC contract with equipment data shape**

Either modify `test_cw_portal_select_renders_selected_card_summary` or add a separate success test so `data.equipment` includes this minimal shape and stdout includes `# 装备信息` before `# 商店信息`:

```python
"equipment": {
    "count": 0,
    "uncertain": 0,
    "empty": 60,
    "backend": "vector",
    "layout": "default",
    "items": [],
    "stale": False,
},
```

Expected stdout includes:

```text
# 装备信息
info count=0 uncertain=0 empty=60 backend=vector layout=default
```

- [ ] **Step 5: Run CLI/RPC focused tests**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_atomic_commands.py::test_cw_portal_select_help_mentions_selected_guide_auto_apply tests/test_cw_rpc_contracts.py::test_cw_portal_select_renders_selected_card_summary tests/test_cw_rpc_contracts.py::test_cw_portal_select_renders_equipment_warning_shop_and_handoff_last tests/test_cw_rpc_contracts.py::test_cw_equipment_read_maps_to_canonical_command_and_supports_yaml -q
```

Expected: all selected tests pass.

---

### Task 5: Protocol And Skill Documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-cw-portal/references/portal-command-surface.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-guide/references/command-surface.md`
- Modify: `skills/trail-cw-guide/references/confirmation-checklist.md` if it describes `cw.portal.select` follow-up semantics.
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `skills/trail-hsr/references/scene-entry-index.md`
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_skill_structure.py`
- Modify: `tests/test_skill_routing_contracts.py` if present and responsible for handoff smoke.

- [ ] **Step 1: Update `AGENTS.md` protocol text**

Make these content changes:

```markdown
- 首批固定标题为 `# 综合信息`、`# 攻略提示`、`# 角色信息`、`# 羁绊信息`、`# 商店信息`；当前新增固定标题包括 `# 装备信息`、`# 装备优先级`、`# 角色装备需求`；新增标题必须同步更新 renderer、skills 与测试；只有影响普通安装、用户入口或公开定位时才更新根目录 `README.md`。
- `# 装备信息`、`# 装备优先级` 与 `# 角色装备需求` 只用于分组，不承载 must-keep 事实；装备背包事实仍必须落在 `item` 或 `info` 行中，推荐事实仍必须落在 `guide`、`slot` 或 `info` 行中。
```

Update portal select bullets so they state:

```markdown
- `cw.portal.select` 成功进入备战页后会自动收集 slots/equipment/shop 预备事实：收集水晶、关闭初始槽位面板、读取 slots、读取 equipment、打开商店、等待商店稳定、扫描 shop、缓存 slots+equipment+shop 并关闭商店。
- `cw.portal.select` 自动收集后的正文标题顺序固定为按事实存在输出：`# 综合信息` -> `# 攻略提示` -> `# 角色信息` -> `# 羁绊信息` -> `# 装备信息` -> `# 装备优先级` -> `# 角色装备需求` -> `# 商店信息`；标题只分组，不改变事实行前缀。
- `cw.portal.select` 若响应 `data.equipment` 非空，默认正文在 `# 装备信息` 下复用装备 `item` 行与 `info count/uncertain/empty/backend/layout`，再复用 `cw.equipment.read` 的 `# 装备优先级` 与 `# 角色装备需求` 推荐分块。
- `cw.portal.select` 装备自动收集失败时仍保持 success，并输出 `warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED ...`；不输出空装备标题，不阻断 shop 自动收集，也不改变最后 handoff 行。
```

Update stale rule:

```markdown
- `cw.equipment.read` 成功写入 `cw_state.equipment` 最近快照，并加入 YAML allowlist；除 `cw.equipment.prepare` 以及 `cw.portal.select` 同次自动收集成功并返回 `data.equipment.stale=False` 的 fresh snapshot 外，成功进入 CW mutation 处理的命令应保留最近装备 items 并将该快照标记为 `stale=True`。
```

Update prep topology bullet:

```markdown
- `trail-cw-prep` 接收 `cw.portal.select` handoff 时，应先读截图并消费该响应自动收集的 slots/equipment/shop/stage facts；只有事实缺失、stale 或页面已变化时才重跑 slots/equipment/shop 扫描。
```

- [ ] **Step 2: Update skill docs and references**

Use these content requirements in every file that currently says portal select only auto-collects slots/shop:

```markdown
消费 `cw.portal.select` 带截图 success 时必须先读截图；`# ` 行只是板块标题，不是事实行，不要当作 action/prefix。读完截图后，再消费这些标题下的事实：`# 综合信息` 下看 stage/status，`# 攻略提示` 下看 skill_info，`# 角色信息` 下看 slot，`# 羁绊信息` 下看 trait summary，`# 装备信息` 下看装备 item 与 count/uncertain/empty/backend/layout，`# 装备优先级` 下看 guide 推荐，`# 角色装备需求` 下看 slot/info 缺口，`# 商店信息` 下看 item/coins/reserve facts。
```

For `skills/trail-cw-prep/SKILL.md` and `skills/trail-cw-prep/references/command-surface.md`, include:

```markdown
接收 `cw.portal.select` handoff 时，优先复用该响应中的 stage/slots/equipment/shop facts；只有装备事实缺失、stale、页面变化，或需要刷新低置信识别时，才主动调用 `trail cw equipment read`。
```

For guide references that say “自动应用当前已选攻略”, extend the sentence:

```markdown
返回开局链路后，`cw.portal.select` 成功时会自动应用当前已选攻略，并携带 stage/slots/equipment/shop 预备事实交给 `trail-cw-prep`。
```

For `skills/trail-hsr/references/scene-entry-index.md`, update the handoff note:

```markdown
`cw.portal.select -> trail-cw-prep` success 会自动收集 slots/equipment/shop 初始快照并把 stage facts 放入本次输出；这不会改变 `trail-cw-prep` 的 internal 身份，也不要求入口、portal 或 prep 立即重复扫描。
```

- [ ] **Step 3: Update README only if it contains user-facing portal flow examples**

Search first:

```powershell
Select-String -Path README.md -Pattern "cw portal select|cw.portal.select|portal select" -CaseSensitive:$false
```

If matches exist, add a concise example fragment near the existing flow:

```markdown
`cw portal select` 成功进入普通备战后，会在 handoff 前带回 stage/slots/equipment/shop 初始事实；装备读取失败只作为 `warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED` 暴露，不阻断进入 `trail-cw-prep`。
```

If no matches exist, do not create a new README section solely for this internal protocol detail.

- [ ] **Step 4: Update documentation tests**

Adjust `tests/test_output_debug.py` so the AGENTS assertions include:

```python
assert "当前新增固定标题包括 `# 装备信息`、`# 装备优先级`、`# 角色装备需求`" in agents
assert "读取 equipment、打开商店" in agents
assert "`# 装备信息` -> `# 装备优先级` -> `# 角色装备需求` -> `# 商店信息`" in agents
assert "warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED" in agents
assert "`cw.portal.select` 同次自动收集成功并返回 `data.equipment.stale=False`" in agents
```

Update `tests/test_skill_structure.py::test_active_cw_skills_document_portal_select_auto_collect_prep_facts`:

```python
    guide_text = CW_GUIDE_SKILL.read_text(encoding="utf-8")
    guide_command_surface_text = CW_GUIDE_COMMAND_SURFACE.read_text(encoding="utf-8")
    section_titles = ("# 综合信息", "# 攻略提示", "# 角色信息", "# 羁绊信息", "# 装备信息", "# 装备优先级", "# 角色装备需求", "# 商店信息")

    for skill_text in (entry_text, portal_text, guide_text, prep_text, guide_command_surface_text):
        assert "`# ` 行只是板块标题" in skill_text
        assert "不是事实行" in skill_text
        assert "不要当作 action/prefix" in skill_text
        for title in section_titles:
            assert title in skill_text
```

Add a small scan for guide references containing portal follow-up semantics:

```python
    for path in (CW_GUIDE_COMMAND_SURFACE, CW_GUIDE_CONFIRMATION_CHECKLIST):
        text = path.read_text(encoding="utf-8")
        if "cw.portal.select" in text or "portal select --card-idx" in text:
            assert "stage/slots/equipment/shop" in text
```

If `tests/test_skill_routing_contracts.py` exists and owns handoff smoke, add:

```python
assert "cw.portal.select -> trail-cw-prep" in scene_index_text
assert "slots/equipment/shop" in scene_index_text
assert "stage facts" in scene_index_text
```

- [ ] **Step 5: Run documentation tests**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_output_debug.py::test_agents_document_cw_equipment_read_contract tests/test_output_rendering.py::test_agents_document_cw_portal_select_auto_collect_contract tests/test_skill_structure.py::test_active_cw_skills_document_portal_select_auto_collect_prep_facts -q
```

If `tests/test_skill_routing_contracts.py` was modified, append its focused test node to the command.

Expected: all selected docs/skill tests pass.

---

### Task 6: Full Focused Regression

**Files:**
- Verify: `trail/daemon/cw_service.py`
- Verify: `trail/output/rendering.py`
- Verify: `trail/commands/cw.py`
- Verify: `AGENTS.md`
- Verify: `skills/**`
- Verify: `tests/**`

- [ ] **Step 1: Run focused CW portal/equipment regression**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_atomic_commands.py tests/test_skill_structure.py tests/test_output_debug.py tests/test_cw_equipment.py -q
```

Expected: all selected files pass.

- [ ] **Step 2: Run full suite if focused regression is green**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; pytest -q
```

Expected: full suite exits with code `0`.

- [ ] **Step 3: Inspect final worktree scope without committing**

Run:

```powershell
rtk git status --short
rtk git diff --stat
```

Expected: changes are limited to this plan's files plus the already-existing unrelated `docs/superpowers/plans/2026-04-30-cw-static-resource-bundle.md`. Do not stage or modify that unrelated static resource plan.

- [ ] **Step 4: Request code review before completion**

Use superpowers:requesting-code-review with this review scope:

```text
Implemented cw.portal.select equipment auto-collect per docs/superpowers/specs/2026-04-30-cw-portal-equipment-auto-collect-design.md and docs/superpowers/plans/2026-04-30-cw-portal-equipment-auto-collect.md. Review daemon ordering/stale behavior, renderer output contract, CLI help, AGENTS/skill docs, and tests.
```

Expected: reviewer returns PASS or actionable issues. Fix Critical/Important issues before claiming complete.

---

## Self-Review

- Spec coverage: Tasks 1-2 cover daemon ordering, local `equipment_snapshot`, soft warning, stale finalizer exception, and later mutation stale behavior. Task 3 covers renderer output order, `# 装备信息`, low confidence, failure without empty sections, and independent `cw.equipment.read` compatibility. Task 4 covers CLI/RPC and help. Task 5 covers AGENTS, README, skills, references, and title consumption rules. Task 6 covers regression and code review.
- Placeholder scan: No unresolved placeholder text is present. The exact string `info todo=slots` appears only as an existing output protocol fact in tests/spec context, not as an implementation placeholder.
- Type consistency: `equipment_snapshot` is a `dict | None`, warnings are `list[dict]`, renderer helper signatures use `list[str]` and `dict[str, Any]`, and command names stay canonical dot names.
