# cw.portal.select 备战事实自动收集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cw.portal.select` 在应用攻略后自动收集水晶、读取槽位、扫描商店并关闭商店，同时保持可复用函数边界清晰。

**Architecture:** scene 层保留核心识别/写 session 动作，reader factory 暴露可组合的页面编排开关。`cw.portal.select` 不调用子命令，不新增 preparation helper，而是在 daemon service 中按自己的页面目标组合现有动作并扩展响应 data。输出仍由 `cw.portal.select` renderer 统一渲染，handoff 保持最后一行。

**Tech Stack:** Python, Typer CLI, pytest, Trail daemon service, `trail.output.rendering` 文本协议。

---

## 文件结构

- Modify: `trail/scenes/cw/slots.py`
  - 新增 `dismiss_cw_slots_overlay(runtime)` 作为命令级页面固化动作。
  - 为 `build_cw_slots_reader` 增加 `dismiss_initial_overlay` 参数，默认保持现有行为。
- Modify: `trail/scenes/cw/shop.py`
  - 新增 `build_cw_shop_page_snapshot_reader`，只读取当前商店页。
  - 让 `build_cw_shop_scan_snapshot_reader` 组合 reset/open 和 page reader，保持 `cw.shop.scan` 兼容。
- Modify: `trail/daemon/cw_service.py`
  - 新增 `shop_page_snapshot_reader_factory = build_cw_shop_page_snapshot_reader`，用于组合命令读取已打开商店页。
  - `cw.portal.select` 应用攻略后组合 crystal、slot 页面固化、slots、shop open、shop settle、shop page scan、shop close。
  - 在 close 前缓存 slots/shop 响应数据。
- Modify: `trail/output/rendering.py`
  - 复用现有 `_append_cw_slot_lines`、`_append_cw_slot_trait_summary`、`_append_cw_shop_items`、`_append_cw_shop_snapshot_info` 渲染嵌套 `slots`/`shop`。
- Modify: `tests/test_cw_slots.py`
  - 覆盖 slots reader 初始固化点击可关闭。
- Modify: `tests/test_cw_shop.py`
  - 覆盖商店 page reader 不执行 reset/open，scan reader 仍执行 reset/open。
- Modify: `tests/test_output_rendering.py`
  - 覆盖 `cw.portal.select` 新增输出顺序。
- Modify: `tests/test_daemon_protocol.py`
  - 更新现有 `cw.portal.select` 成功路径与 capture delay 测试，补齐自动收集 patch 和新增 data 断言。
  - 新增自动收集失败后的 taint/recover/followup-blocked 合约测试。
- Modify: `tests/test_cw_rpc_contracts.py` 或 `tests/test_cw_shop.py`
  - 覆盖 daemon service 中 `cw.portal.select` 的组合顺序与缓存输出。
- Modify: `AGENTS.md`
  - 冻结 `cw.portal.select` 自动追加普通备战事实的文本顺序，明确 shop `opened/stale` 不投影到 portal select 输出。
- Modify: `README.md`
  - 更新 `cw.portal.select` 示例与说明。
- Modify: `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-hsr/references/scene-entry-index.md`
  - 同步说明 portal select 成功后已经自动完成普通备战事实初收集。

---

### Task 0: 准备项目内 worktree 与执行约束

**Files:**
- No source edits in this task unless `.worktrees/` is not ignored.

- [ ] **Step 1: 检查项目内 worktree 目录**

Run: `Test-Path .worktrees; Test-Path worktrees`

Expected: `.worktrees` 为可用项目内 worktree 根目录；如果两者都存在，使用 `.worktrees`。

- [ ] **Step 2: 验证项目内 worktree 被忽略**

Run: `git check-ignore -q .worktrees; if ($LASTEXITCODE -eq 0) { "ignored" } else { "not ignored" }`

Expected: 输出 `ignored`。如果输出 `not ignored`，先把 `.worktrees/` 加入 `.gitignore` 并单独提交；没有用户要求时不要提交其它文件。

- [ ] **Step 3: 创建 feature worktree**

Run: `git worktree add ".worktrees/cw-portal-prep-auto-collect" -b "feature/cw-portal-prep-auto-collect"`

Expected: 创建 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-portal-prep-auto-collect`。不要修改或删除主工作区已有未跟踪文档 `docs/superpowers/specs/2026-04-26-cw-catalog-matching-design.md` 和 `docs/superpowers/plans/2026-04-26-cw-catalog-matching.md`。

- [ ] **Step 4: 在 worktree 中保留已批准 spec/plan**

在 worktree 中创建同名文件并保持内容与主工作区一致：

```text
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-portal-prep-auto-collect\docs\superpowers\specs\2026-04-26-cw-portal-prep-auto-collect-design.md
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-portal-prep-auto-collect\docs\superpowers\plans\2026-04-26-cw-portal-prep-auto-collect.md
```

后续所有实现子代理提示词必须同时提供主工作区 spec/plan 完整路径和 worktree 内 spec/plan 完整路径。新启动子代理提示词必须超过 2000 字；复用已有子代理会话时只补充相对上次的变化说明。

- [ ] **Step 5: 运行 baseline 聚焦测试**

Workdir: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-portal-prep-auto-collect`

Run: `pytest tests/test_cw_slots.py tests/test_cw_shop.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q`

Expected: PASS。如果 baseline 已失败，报告失败并先判断是否为既有问题；不要在不理解 baseline 的情况下开始实现。

- [ ] **Step 6: 明确子代理执行顺序**

实现写入任务按以下方式执行：Task 1 和 Task 2 可由两个实现子代理并发；Task 3、Task 4、Task 5、Task 6、Task 7 依赖响应 data shape 和协议文本，必须串行。每个写入任务完成后并发启动 3 到 5 个只读 review 子代理，全部通过后再进入下一写入任务。

---

### Task 1: 让 slots reader 的初始页面固化点击可选

**Files:**
- Modify: `tests/test_cw_slots.py`
- Modify: `trail/scenes/cw/slots.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_cw_slots.py` 增加测试，放在现有 `build_cw_slots_reader` 点击顺序相关测试附近：

```python
def test_build_cw_slots_reader_can_skip_initial_overlay_dismiss(monkeypatch):
    slots_module = load_cw_slots_module()
    events = []

    class Runtime:
        def locate(self, *args, **kwargs):
            return None

        def click_point(self, x, y):
            point = (x, y)
            if point == slots_module.INFO_DISMISS_POINT:
                events.append("click(INFO_DISMISS_POINT)")
            else:
                events.append(f"click({point})")

        def capture_image(self, **kwargs):
            return object()

    class BatchResult:
        by_key = {
            ("stage_status", "level"): type("Ocr", (), {"text": "1"})(),
            ("stage_status", "exp"): type("Ocr", (), {"text": "0/2"})(),
            ("stage_status", "team_size"): type("Ocr", (), {"text": "1/2"})(),
            ("slot", "front", 0): type("Ocr", (), {"text": "希儿"})(),
        }

    monkeypatch.setattr(slots_module, "sleep", lambda seconds: events.append(f"sleep({seconds})"))
    monkeypatch.setattr(slots_module, "run_batch_ocr", lambda runtime, targets, trace_prefix: BatchResult())
    monkeypatch.setattr(slots_module, "_read_slot_star_counts", lambda captures: {("front", 0): 1})

    reader = slots_module.build_cw_slots_reader(Runtime(), targets=["front:0"], dismiss_initial_overlay=False)

    result = reader()

    assert result.front[0] == {"name": "希儿", "star": 1}
    assert events[0] == f"click({slots_module.FRONT_SLOT_POINTS[0]})"
    assert "sleep(1.0)" not in events[:1]
    assert events.count("click(INFO_DISMISS_POINT)") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cw_slots.py::test_build_cw_slots_reader_can_skip_initial_overlay_dismiss -q`

Expected: FAIL，错误包含 `unexpected keyword argument 'dismiss_initial_overlay'`。

- [ ] **Step 3: 实现最小变更**

修改 `trail/scenes/cw/slots.py`，先抽出命令级页面固化动作，再让 reader 默认调用它：

```python
def dismiss_cw_slots_overlay(runtime) -> None:
    runtime.click_point(*INFO_DISMISS_POINT)
    sleep(INITIAL_UI_DISMISS_SETTLE_SECONDS)


def build_cw_slots_reader(
    runtime,
    targets: list[str] | None = None,
    *,
    dismiss_initial_overlay: bool = True,
) -> SlotsSnapshotReader:
    parsed_targets = _parse_slot_targets(targets)

    def reader() -> CwSlotsReadResult:
        _collapse_expanded_hand_card(runtime)
        front, back, hand = _empty_slots_snapshot()
        targets_by_area = parsed_targets or {
            "front": set(range(len(FRONT_SLOT_POINTS))),
            "back": set(range(len(BACK_SLOT_POINTS))),
            "hand": set(range(len(HAND_SLOT_POINTS))),
        }

        captures: list[dict[str, Any]] = []
        if dismiss_initial_overlay:
            dismiss_cw_slots_overlay(runtime)
```

保留后续 batch target 与 slot 捕获逻辑不变。

- [ ] **Step 4: 运行相关测试**

Run: `pytest tests/test_cw_slots.py::test_build_cw_slots_reader_can_skip_initial_overlay_dismiss tests/test_cw_slots.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/slots.py tests/test_cw_slots.py
git commit -m "refactor(cw): 拆分槽位读取初始页面固化"
```

---

### Task 2: 暴露当前商店页 OCR reader

**Files:**
- Modify: `tests/test_cw_shop.py`
- Modify: `trail/scenes/cw/shop.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_cw_shop.py` 增加测试，放在 `build_cw_shop_scan_snapshot_reader` 相关测试附近：

```python
def test_build_cw_shop_page_snapshot_reader_does_not_click_reset_or_open(monkeypatch):
    shop_module = load_cw_shop_module()
    events: list[str] = []
    _install_fake_shop_batch_ocr(monkeypatch, shop_module, events=events)
    runtime = _build_cw_shop_scan_runtime(shop_module)

    reader = shop_module.build_cw_shop_page_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == []
    assert events == [("batch_ocr", "cw_shop_batch_ocr", ("items", "coins"))]
    assert snapshot["opened"] is True
    assert snapshot["stale"] is False
    assert "items" in snapshot
    assert "coins" in snapshot
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cw_shop.py::test_build_cw_shop_page_snapshot_reader_does_not_click_reset_or_open -q`

Expected: FAIL，错误包含 `has no attribute 'build_cw_shop_page_snapshot_reader'`。

- [ ] **Step 3: 实现 page reader 并复用到 scan reader**

修改 `trail/scenes/cw/shop.py`：

```python
def build_cw_shop_page_snapshot_reader(runtime, *, read_stage_status: bool = False) -> ShopSnapshotReader:
    def reader() -> dict[str, Any]:
        snapshot = _read_shop_page_snapshot(runtime, read_team_size=read_stage_status)
        return {"opened": True, "stale": False, **snapshot}

    return reader


def build_cw_shop_scan_snapshot_reader(runtime, *, read_stage_status: bool = False) -> ShopSnapshotReader:
    page_reader = build_cw_shop_page_snapshot_reader(runtime, read_stage_status=read_stage_status)

    def reader() -> dict[str, Any]:
        runtime.click_point(*SHOP_SCAN_RESET_POINT)
        sleep(SHOP_SCAN_RESET_SETTLE_SECONDS)
        runtime.click_point(*SHOP_OPEN_POINT)
        sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
        return page_reader()

    return reader
```

- [ ] **Step 4: 运行商店测试**

Run: `pytest tests/test_cw_shop.py::test_build_cw_shop_page_snapshot_reader_does_not_click_reset_or_open tests/test_cw_shop.py::test_build_cw_shop_scan_snapshot_reader_closes_then_reopens_before_scanning -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/shop.py tests/test_cw_shop.py
git commit -m "refactor(cw): 拆分商店页扫描 reader"
```

---

### Task 3: 在 cw.portal.select 中组合备战动作

**Files:**
- Modify: `tests/test_cw_shop.py` 或 `tests/test_cw_rpc_contracts.py`
- Modify: `trail/daemon/cw_service.py`

- [ ] **Step 1: 写失败测试**

优先在 `tests/test_cw_shop.py` 增加 service 级单元测试，复用已有 fake session 与 runtime 模式。测试目标是顺序和 data shape，不依赖真实 OCR：

```python
def test_cw_portal_select_collects_prep_facts_and_closes_shop(tmp_path, monkeypatch):
    from trail.daemon import cw_service
    from tests.conftest import complete_cw_guide_state

    events = []
    runtime = object()
    _registry, service, session, cw_runtime_service, _command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "guide": complete_cw_guide_state(share_code="##code##"),
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "portal": {"cards": [{"card_idx": 2, "portal_title": "击破概念股"}], "stale": False},
    }
    service.save_session(session)

    def fake_select(session, *, card_idx, runtime):
        events.append("portal.select")
        return {"card_idx": card_idx, "portal_title": "击破概念股"}

    def fake_wait(session, *, runtime):
        events.append("portal.wait")

    def fake_apply(session, *, runtime, guide=None):
        events.append("guide.apply")
        return guide

    def fake_collect(session, *, collector):
        events.append("crystal.collect")
        session.scene_state.setdefault("cw", {})["metrics"] = {"last_crystal_collection": "done"}
        return session

    def fake_dismiss(resolved_runtime):
        assert resolved_runtime is runtime
        events.append("slots.dismiss")

    def fake_read_slots(session, *, reader, targets=None, guide_config=None):
        assert targets is None
        assert guide_config == {"roles": [], "traits": []}
        events.append("slots.read")
        session.scene_state.setdefault("cw", {})["slots"] = {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
        session.scene_state["cw"]["stage"] = {"status": {"level": 3, "exp": "0/8", "team_size": "1/2", "stale": False}}
        return session

    def fake_open_shop(session, *, opener=None):
        events.append("shop.open")
        return session

    def fake_scan_shop(session, *, scanner):
        assert scanner == "page-reader"
        events.append("shop.scan")
        session.scene_state.setdefault("cw", {})["shop"] = {"opened": True, "stale": False, "items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False}
        return session

    def fake_project_shop(session):
        events.append("shop.project")
        return {"opened": True, "stale": False, "items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False, "stage_status": {"level": 3, "exp": "0/8", "team_size": "1/2", "stale": False}, "stage_status_stale": False}

    def fake_close_shop(session, *, closer=None):
        events.append("shop.close")
        session.scene_state.setdefault("cw", {})["shop"] = {"opened": False, "stale": True}
        return session

    monkeypatch.setattr(cw_service, "select_cw_portal", fake_select)
    monkeypatch.setattr(cw_service, "wait_cw_portal_preparation", fake_wait)
    monkeypatch.setattr(cw_service, "_apply_selected_guide_via_ui", fake_apply)
    monkeypatch.setattr(cw_service, "collect_cw_crystals", fake_collect)
    monkeypatch.setattr(cw_service, "dismiss_cw_slots_overlay", fake_dismiss)
    monkeypatch.setattr(cw_service, "read_cw_slots", fake_read_slots)
    monkeypatch.setattr(cw_service, "open_cw_shop", fake_open_shop)
    monkeypatch.setattr(cw_service, "scan_cw_shop", fake_scan_shop)
    monkeypatch.setattr(cw_service, "project_cw_shop_snapshot", fake_project_shop)
    monkeypatch.setattr(cw_service, "close_cw_shop", fake_close_shop)
    monkeypatch.setattr(cw_service, "fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr(cw_service, "crystal_collector_factory", lambda runtime: lambda: None)
    monkeypatch.setattr(cw_service, "slots_reader_factory", lambda runtime, **kwargs: events.append(f"slots.reader({kwargs})") or (lambda: ([], [], [])))
    monkeypatch.setattr(cw_service, "shop_opener_factory", lambda runtime: lambda: None)
    monkeypatch.setattr(cw_service, "shop_page_snapshot_reader_factory", lambda runtime, **kwargs: "page-reader")
    monkeypatch.setattr(cw_service, "shop_closer_factory", lambda runtime: lambda: None)
    monkeypatch.setattr(cw_service, "sleep", lambda seconds: events.append(f"sleep({seconds})"))

    result = cw_runtime_service.handle(
        method="cw.portal.select",
        payload={"session_id": session.session_id, "card_idx": 2},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert events[:12] == [
        "portal.select",
        "portal.wait",
        "guide.apply",
        "crystal.collect",
        "slots.dismiss",
        "slots.reader({'dismiss_initial_overlay': False})",
        "slots.read",
        "shop.open",
        "sleep(1.5)",
        "shop.scan",
        "shop.project",
        "shop.close",
    ]
    assert result["slots"] == {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
    assert result["shop"]["items"] == [{"slot": 1, "name": "银狼", "price": 20}]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cw_shop.py::test_cw_portal_select_collects_prep_facts_and_closes_shop -q`

Expected: FAIL，`events` 中没有 crystal/slots/shop 步骤，或 `result` 缺少 `slots`/`shop`。

- [ ] **Step 3: 实现组合流程**

修改 `trail/daemon/cw_service.py`：

1. 在 shop import 列表补充 `SHOP_SCAN_OPEN_SETTLE_SECONDS` 和 `build_cw_shop_page_snapshot_reader`；在 slots import 列表补充 `dismiss_cw_slots_overlay`。
2. 修改 `_select_portal_and_apply_selected_guide` 签名：

```python
def _select_portal_and_apply_selected_guide(
    session,
    *,
    runtime,
    card_idx: int,
    guide: dict | None = None,
    workspace_root: str | None = None,
) -> dict:
```

3. 在 module-level factory 区域增加：

```python
shop_page_snapshot_reader_factory = build_cw_shop_page_snapshot_reader
```

4. 在 `_context.run_portal_select` 中传入 `workspace_root=workspace_root`。
5. 在 `_select_portal_and_apply_selected_guide` 应用攻略后追加：

```python
    collect_cw_crystals(session, collector=crystal_collector_factory(runtime))
    dismiss_cw_slots_overlay(runtime)
    guide_config = fetch_cw_guide_config(workspace_root=workspace_root)
    read_cw_slots(
        session,
        reader=slots_reader_factory(runtime, dismiss_initial_overlay=False),
        guide_config=guide_config,
    )
    open_cw_shop(session, opener=shop_opener_factory(runtime))
    sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
    scan_cw_shop(
        session,
        scanner=shop_page_snapshot_reader_factory(runtime),
    )
    shop_snapshot = project_cw_shop_snapshot(session)
    slots_snapshot = deepcopy(ensure_cw_state(session).get("slots") or {})
    close_cw_shop(session, closer=shop_closer_factory(runtime))

    selected_data["crystals"] = deepcopy(ensure_cw_state(session).get("metrics") or {})
    selected_data["slots"] = slots_snapshot
    selected_data["shop"] = shop_snapshot
```

- [ ] **Step 4: 运行组合测试**

Run: `pytest tests/test_cw_shop.py::test_cw_portal_select_collects_prep_facts_and_closes_shop -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add trail/daemon/cw_service.py tests/test_cw_shop.py
git commit -m "feat(cw): portal 选择后自动收集备战事实"
```

---

### Task 4: 锁定 command_service 成功与失败语义

**Files:**
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 更新现有成功路径测试**

在 `test_command_service_handles_cw_portal_select_and_auto_applies_selected_guide` 中补齐自动收集 patch，并断言 close 前缓存响应、close 后截图。核心 patch 片段：

```python
events: list[str] = []

monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: events.append("crystal") or session)
monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: events.append("slots.dismiss"))
monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, **kwargs: events.append(f"slots.reader({kwargs})") or object())
monkeypatch.setattr(
    "trail.daemon.cw_service.read_cw_slots",
    lambda session, reader, targets=None, guide_config=None: events.append("slots.read")
    or session.scene_state["cw"].__setitem__("slots", {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False})
    or session,
)
monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: events.append("shop.open") or session)
monkeypatch.setattr("trail.daemon.cw_service.shop_page_snapshot_reader_factory", lambda runtime, **kwargs: "page-reader")
monkeypatch.setattr(
    "trail.daemon.cw_service.scan_cw_shop",
    lambda session, scanner: events.append(f"shop.scan({scanner})")
    or session.scene_state["cw"].__setitem__("shop", {"opened": True, "stale": False, "items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False})
    or session,
)
monkeypatch.setattr(
    "trail.daemon.cw_service.project_cw_shop_snapshot",
    lambda session: events.append("shop.project") or {"opened": True, "stale": False, "items": [{"slot": 1, "name": "银狼", "price": 20}], "coins": 40, "reserve_full": False, "stage_status_stale": True},
)
monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: events.append("shop.close") or session)
```

成功断言应包含：

```python
assert payload["data"]["slots"] == {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
assert payload["data"]["shop"]["items"] == [{"slot": 1, "name": "银狼", "price": 20}]
assert events[-2:] == ["shop.project", "shop.close"]
assert runtime.capture_requests == [(False, "req-cw-portal-select")]
```

- [ ] **Step 2: 更新 capture delay 测试**

在 `test_command_service_handles_cw_portal_select_waits_extra_before_capture` 中复用同一套自动收集 patch，并把 sleep 断言更新为：

```python
assert sleeps == [1.5, 2.0]
assert runtime.capture_requests == [(False, "req-cw-portal-select-delay")]
```

`1.5` 来自 `SHOP_SCAN_OPEN_SETTLE_SECONDS`，`2.0` 来自 `PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS`。

- [ ] **Step 3: 新增自动收集失败 taint 测试**

新增测试 `test_command_service_marks_cw_portal_select_auto_collect_failure_as_recoverable_and_blocks_followup_mutations`。关键结构：

```python
def test_command_service_marks_cw_portal_select_auto_collect_failure_as_recoverable_and_blocks_followup_mutations(tmp_path: Path, monkeypatch):
    from trail.core.errors import TrailError
    from trail.daemon.cw_service import CwService

    session_services = SessionServiceRegistry()
    service = session_services.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "stale": False},
    }
    service.save_session(session)

    class Runtime:
        def click_point(self, x, y):
            return None

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-auto-collect-fail.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            return []

    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **_: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_services, cw_service=cw_service)

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, card_idx, runtime: runtime.click_point(1, 1) or {"card_idx": card_idx, "portal_title": "商店"})
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: session)
    monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: None)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, **kwargs: object())
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", lambda session, reader, targets=None, guide_config=None: (_ for _ in ()).throw(TrailError("SLOTS_READ_EMPTY", "empty")))

    response = command_service.handle(DaemonRequest(request_id="req-cw-portal-select-auto-collect-fail", protocol_version=PROTOCOL_VERSION, workspace_root=str(tmp_path), session_id=session.session_id, verbose=False, method="cw.portal.select", payload={"session_id": session.session_id, "card_idx": 1}))

    assert response["ok"] is False
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert service.request_status("req-cw-portal-select-auto-collect-fail")["final_state"] == "applied_but_not_persisted"
```

复用现有 follow-up mutation 阻断断言模式，验证后续 CW mutation 被 tainted session 阻断。

- [ ] **Step 4: 运行 daemon protocol 聚焦测试**

Run: `pytest tests/test_daemon_protocol.py::test_command_service_handles_cw_portal_select_and_auto_applies_selected_guide tests/test_daemon_protocol.py::test_command_service_handles_cw_portal_select_waits_extra_before_capture tests/test_daemon_protocol.py::test_command_service_marks_cw_portal_select_auto_collect_failure_as_recoverable_and_blocks_followup_mutations -q`

Expected: PASS。

---

### Task 5: 渲染 cw.portal.select 的嵌套 slots/shop 信息

**Files:**
- Modify: `tests/test_output_rendering.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_output_rendering.py` 的 portal select 测试附近增加：

```python
def test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff(capsys) -> None:
    print_output(
        "cw.portal.select",
        {
            "ok": True,
            "screenshot": ".trail/shots/portal-prep.png",
            "data": {
                "card_idx": 1,
                "portal_title": "击破概念股",
                "skill_info": [{"name": "运营思路", "text": "先收集事实"}],
                "slots": {
                    "front": [{"name": "希儿", "star": 1, "traits": ["巡猎"]}],
                    "back": [],
                    "hand": [{"name": "停云"}],
                    "stale": False,
                    "trait_summary": [{"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5}],
                },
                "shop": {
                    "opened": True,
                    "stale": False,
                    "items": [{"slot": 1, "name": "银狼", "price": 20}],
                    "coins": 40,
                    "reserve_full": False,
                    "stage_status": {"level": 3, "exp": "0/8", "team_size": "1/2", "stale": False},
                    "stage_status_stale": False,
                },
            },
            "warnings": [{"code": "W", "message": "warn text"}],
            "references": [{"path": "p", "similarity": 0.9}],
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines == [
        "ok cw.portal.select idx=1 投资环境=击破概念股",
        "shot path=.trail/shots/portal-prep.png",
        "info read_image_first=1",
        "info skill_info=运营思路 text=先收集事实",
        "slot pos=front:0 name=希儿 star=1 traits=巡猎",
        "slot pos=hand:0 name=停云",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        'warn code=W msg="warn text"',
        "ref path=p sim=0.9",
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
    ]
    assert not any(" opened=" in line or " stale=" in line for line in lines)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_output_rendering.py::test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff -q`

Expected: FAIL，缺少 slot/item/info 行。

- [ ] **Step 3: 实现 renderer**

修改 `trail/output/rendering.py` 的 `_render_cw_portal_select`：

```python
def _render_cw_portal_select(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("idx", data.get("card_idx")),
        ("投资环境", _non_empty(data.get("portal_title"))),
    )
    lines = [
        f"ok {command} {summary}" if summary else f"ok {command}"
    ]
    _append_success_capture_block(lines, payload)
    _append_skill_info(lines, data)
    slots = _as_dict(data.get("slots"))
    if slots:
        _append_cw_slot_lines(lines, slots)
        _append_cw_slot_trait_summary(lines, slots)
    shop = _as_dict(data.get("shop"))
    if shop:
        _append_cw_shop_items(lines, shop)
        _append_cw_shop_snapshot_info(lines, shop)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

- [ ] **Step 4: 运行输出测试**

Run: `pytest tests/test_output_rendering.py::test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff tests/test_output_rendering.py::test_portal_select_renders_skill_info_before_warn_ref_and_handoff_last -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add trail/output/rendering.py tests/test_output_rendering.py
git commit -m "feat(output): 渲染 portal 选择后的备战事实"
```

---

### Task 6: 更新文档与 skill 指引

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-hsr/references/scene-entry-index.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_output_debug.py`

- [ ] **Step 1: 定位现有说明**

Run: `rg "cw\.portal\.select|trail-cw-prep|普通备战|shop scan|slots read|收集事实" README.md AGENTS.md skills tests/test_output_rendering.py tests/test_output_debug.py -n`

Expected: 输出包含 `cw.portal.select` 示例和 `trail-cw-prep` handoff 说明。

- [ ] **Step 2: 更新 README 示例**

把 `cw.portal.select` 成功示例改为包含 slot/item 信息，保持 handoff 最后一行：

```text
ok cw.portal.select idx=1 投资环境=击破概念股
shot path=.trail/shots/req-portal-select.png
info read_image_first=1
info skill_info=运营思路 text="前期：先收集事实，再按后续策略处理"
slot pos=front:0 name=希儿 star=1 traits=巡猎
item idx=1 slot=1 name=银狼 cost=20
info coins=40 reserve_full=0
info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0
info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered
```

同时补一句：`cw.portal.select` 应用攻略后会自动收集水晶、读取槽位、扫描商店并关闭商店，最终截图停留在无浮层普通备战页。

- [ ] **Step 3: 更新 AGENTS 协议约束**

在 `AGENTS.md` 的 `cw.portal.select` 约束后补充：

```markdown
- `cw.portal.select` 若响应 `data.slots` 非空，默认正文在 `info skill_info=...` 之后、`warn/ref` 之前复用 `cw.slots.read` 的 `slot` 行与羁绊 `info` 摘要。
- `cw.portal.select` 若响应 `data.shop` 非空，默认正文在槽位/羁绊事实之后、`warn/ref` 之前复用商店 `item` 行与 `info coins/reserve_full/stage_level/stage_exp/stage_team_size/stage_status_stale` 投影。
- `cw.portal.select` 的首行仍固定为 `ok cw.portal.select idx=... 投资环境=...`；自动收集得到的 shop `opened/stale` 不进入首行，也不作为 body 事实渲染，避免与最终已关闭商店的页面状态冲突。
```

- [ ] **Step 4: 更新 skill 文档**

在 `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-hsr/references/scene-entry-index.md` 中把“portal select 后进入 prep 再收集事实”的旧描述改为：

```markdown
`cw.portal.select` 成功后已经自动执行水晶收集、槽位读取、商店扫描并关闭商店。进入 `trail-cw-prep` 后应先消费 `cw.portal.select` 输出的 `slot`、`item`、`info stage_*` 事实；只有这些事实缺失、过期或与当前截图明显不一致时，才重新运行 `cw.slots.read` 或 `cw.shop.scan`。
```

- [ ] **Step 5: 更新并运行文档相关断言测试**

在 `tests/test_output_rendering.py` 中更新 `test_readme_locks_cw_portal_select_success_screenshot_order`，让 README 示例包含 `slot`、`item`、shop/stage `info`，并仍断言 handoff 是最后一行。

新增或更新 `tests/test_output_rendering.py::test_agents_document_cw_portal_select_auto_collect_contract`，断言 `AGENTS.md` 明确：`cw.portal.select` 首行不变、`slot`/羁绊 `info`/商店 `item`/shop-stage `info` 在 `warn/ref` 前、handoff 最后一行、shop `opened/stale` 不渲染。

更新 `tests/test_output_debug.py` 中对 `AGENTS.md` portal select 协议文本的断言，保留既有 `skill_info` 与 handoff 断言，并新增自动收集 body 顺序断言。

Run: `pytest tests/test_output_rendering.py::test_readme_locks_cw_portal_select_success_screenshot_order tests/test_output_rendering.py::test_agents_document_cw_portal_select_auto_collect_contract tests/test_output_debug.py::test_project_agents_declares_renderer_contracts -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add AGENTS.md README.md skills/trail-cw-entry/SKILL.md skills/trail-cw-portal/SKILL.md skills/trail-cw-prep/SKILL.md skills/trail-hsr/references/scene-entry-index.md tests/test_output_rendering.py tests/test_output_debug.py
git commit -m "docs(cw): 说明 portal 选择自动收集备战事实"
```

---

### Task 7: 全量验证

**Files:**
- No source edits unless verification exposes failures.

- [ ] **Step 1: 运行聚焦测试**

Run: `pytest tests/test_cw_slots.py tests/test_cw_shop.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py -q`

Expected: PASS。

- [ ] **Step 2: 运行 lint 或项目默认测试命令**

先查看项目命令：

Run: `python -m pytest --version`

Expected: pytest 可用。

如果仓库有固定 lint/test 命令，按 README 或 CI 配置执行；否则至少保留 Step 1 的 pytest 结果。

- [ ] **Step 3: 检查 worktree**

Run: `git status --short`

Expected: 只包含本计划涉及的源代码、测试、README、skill 文档变更，以及本 spec/plan 文档。不要修改或回退用户已有的未跟踪文件 `docs/superpowers/specs/2026-04-26-cw-catalog-matching-design.md` 和 `docs/superpowers/plans/2026-04-26-cw-catalog-matching.md`。

- [ ] **Step 4: 最终提交**

如果用户要求提交，且前面任务没有逐步提交，则执行：

```bash
git add AGENTS.md README.md trail/scenes/cw/slots.py trail/scenes/cw/shop.py trail/daemon/cw_service.py trail/output/rendering.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py skills/trail-cw-entry/SKILL.md skills/trail-cw-portal/SKILL.md skills/trail-cw-prep/SKILL.md skills/trail-hsr/references/scene-entry-index.md docs/superpowers/specs/2026-04-26-cw-portal-prep-auto-collect-design.md docs/superpowers/plans/2026-04-26-cw-portal-prep-auto-collect.md
git commit -m "feat(cw): portal 选择后自动收集备战事实"
```

---

## 自检

- Spec coverage: 计划覆盖 worktree 安全流程、reader 边界拆分、`cw.portal.select` 组合顺序、最终截图停留无浮层备战页、嵌套输出渲染、失败/taint 语义、AGENTS/README 与 skill 文档同步。
- Placeholder scan: 文档没有未完成占位或延后实现描述。
- Type consistency: 使用 `dismiss_initial_overlay`、`build_cw_shop_page_snapshot_reader`、`slots`、`shop`、`stage_status_stale` 等名称在任务间保持一致。
- Git safety: 提交步骤均标注“如果用户要求提交”，当前不会自动提交。
