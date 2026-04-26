# 输出板块标题与 CW 综合信息拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为多板块默认文本输出加入 `# 标题` 分组，并把 `cw.slots.read` 的综合状态读取从角色槽位读取中拆出，同时额外识别当前阶段。

**Architecture:** 首批只覆盖 CW 备战链路相关多板块命令：`cw.slots.read`、`cw.shop.scan`、`cw.shop.status`、`cw.shop.buy_exp`、`cw.portal.select`。读取层拆分为 CW 综合状态 reader 与角色槽位 reader；输出层新增标题 helper 与 CW section helper，所有业务事实仍保持紧凑 `prefix key=value` 行。

**Tech Stack:** Python, pytest, existing `trail.scenes.cw` services, `trail.output.rendering`, daemon protocol tests, README/AGENTS/skills doc assertions.

---

## Context

Approved spec: `docs/superpowers/specs/2026-04-27-output-section-headings-and-cw-status-design.md`.

Windows pytest must use a repository-local temp directory:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py -q
```

Do not touch unrelated untracked docs:

- `docs/superpowers/plans/2026-04-26-cw-catalog-matching.md`
- `docs/superpowers/specs/2026-04-26-cw-catalog-matching-design.md`
- `docs/superpowers/specs/2026-04-26-cw-equipment-icon-recognition-design.md`

## Execution Preconditions

- Controller must create a project-local git worktree before implementation, and implementation must happen inside that worktree.
- Every newly started implementation or review subagent prompt must include the absolute paths to this plan and the approved spec, plus enough context for the task. User requirement: new subagent prompts must be more than 2000 Chinese characters.
- Controller dispatches one fresh implementer subagent per task, then read-only review subagents before moving to the next task.
- Review prompts may reuse an existing subagent session; reuse prompts only need to describe changes since that subagent's last review.
- Task subagents do not create or copy worktrees themselves. The controller provides the worktree path and confirms the spec/plan paths in the prompt.

## File Map

- `trail/scenes/cw/stage.py`: existing stage/status parsers and stage detector semantics.
- `trail/scenes/cw/slots.py`: split status reader from role slot reader; return slots plus status/stage projection.
- `trail/scenes/cw/shop.py`: normalize shop/status projection so renderer can reuse the CW status section helper, while preserving `buy_exp` must-keep null semantics.
- `trail/daemon/cw_service.py`: return `cw.slots.read` data that includes stage/status projection; keep portal auto-collect compatible.
- `trail/output/rendering.py`: add `# 标题` helper and CW section helpers; update first-batch renderers.
- `tests/test_cw_slots.py`: status-reader split, directed slot behavior, response/session state behavior.
- `tests/test_cw_stage.py`: stage detector error/None expectations used by slots status reader tests.
- `tests/test_cw_shop.py`: shop projection compatibility and buy-exp null preservation if production shape changes.
- `tests/test_daemon_protocol.py`: `cw.slots.read` payload contains stage/status projection.
- `tests/test_cw_rpc_contracts.py`: RPC contract includes actual read facts needed by renderer.
- `tests/test_output_rendering.py`: section headings and output order.
- `tests/test_output_debug.py`: AGENTS protocol assertions.
- `tests/test_skill_structure.py`: active skill docs consume section headings correctly.
- `AGENTS.md`, `README.md`, `skills/trail-cw-prep/SKILL.md`, `skills/trail-cw-portal/SKILL.md`: protocol and Agent guidance updates.

---

### Task 1: Add Renderer Section Helpers

**Files:**
- Modify: `trail/output/rendering.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: Write failing renderer helper tests**

Add tests near existing `cw.slots.read` renderer tests, and update `tests/test_cw_rpc_contracts.py::test_cw_rpc_wrapper_matrix[cw_slots_read]` expected stdout to include the new `# 角色信息` heading for the wrapped `cw.slots.read` output. Do not expect `# 羁绊信息` in that matrix case unless the fixture is also updated to include `trait_summary`:

```python
def test_render_output_adds_cw_slots_sections_without_breaking_screenshot_order():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 1, "traits": ["巡猎"]}],
            "back": [],
            "hand": [{"name": "停云"}],
            "stale": False,
            "stage": "preparation",
            "stage_stale": False,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
            "stage_status_stale": False,
            "trait_summary": [
                {"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5},
            ],
        },
        "screenshot": ".trail/shots/req-slots.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=0 hand=1 stale=0",
        "shot path=.trail/shots/req-slots.png",
        "info read_image_first=1",
        "# 综合信息",
        "info stage=preparation stale=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        "# 角色信息",
        "slot pos=front:0 name=希儿 star=1 traits=巡猎",
        "slot pos=hand:0 name=停云",
        "# 羁绊信息",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
    ]


def test_render_output_does_not_add_sections_to_single_body_commands():
    payload = {"ok": True, "data": {"value": "preparation", "stale": False}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    lines = render_output("cw.stage.detect", payload).splitlines()

    assert lines == ["ok cw.stage.detect stage=preparation stale=0"]
    assert not any(line.startswith("# ") for line in lines)


def test_render_output_does_not_add_sections_to_failures_or_single_action_outputs():
    failure = {"ok": False, "data": {}, "timing": {}, "warnings": [], "references": [], "debug": {"request_id": "req-1"}, "error": {"code": "X", "message": "failed"}}
    assert not any(line.startswith("# ") for line in render_output("cw.slots.read", failure).splitlines())

    place = {"ok": True, "data": {"front": ["希儿"], "back": [], "hand": [], "stale": False}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}
    assert not any(line.startswith("# ") for line in render_output("cw.slots.place", place).splitlines())

    strategy = {"ok": True, "data": {"cards": [{"name": "存钱"}]}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}
    assert not any(line.startswith("# ") for line in render_output("cw.strategy.detect", strategy).splitlines())
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py::test_render_output_adds_cw_slots_sections_without_breaking_screenshot_order tests/test_output_rendering.py::test_render_output_does_not_add_sections_to_single_body_commands tests/test_output_rendering.py::test_render_output_does_not_add_sections_to_failures_or_single_action_outputs "tests/test_cw_rpc_contracts.py::test_cw_rpc_wrapper_matrix[cw_slots_read]" -q
```

Expected: first test fails because headings and status lines are missing.

- [ ] **Step 3: Implement section helpers**

In `trail/output/rendering.py`, add helpers near existing CW append helpers:

```python
def _append_section(lines: list[str], title: str) -> None:
    lines.append(f"# {title}")


def _append_cw_status_section(lines: list[str], data: dict[str, Any]) -> None:
    stage_value = data.get("stage")
    has_stage = stage_value is not None
    stage_status = _as_dict(data.get("stage_status"))
    has_stage_status = "stage_status_stale" in data or bool(stage_status)
    if not has_stage and not has_stage_status:
        return
    _append_section(lines, "综合信息")
    if has_stage:
        _append_fact_line(lines, "info", ("stage", stage_value), ("stale", bool(data.get("stage_stale")) if "stage_stale" in data else None))
    if has_stage_status:
        stage_status_stale = bool(data["stage_status_stale"]) if "stage_status_stale" in data else bool(stage_status.get("stale", True))
        _append_fact_line(
            lines,
            "info",
            ("stage_level", stage_status.get("level") if not stage_status_stale else None),
            ("stage_exp", stage_status.get("exp") if not stage_status_stale else None),
            ("stage_team_size", stage_status.get("team_size") if not stage_status_stale else None),
            ("stage_status_stale", stage_status_stale),
        )
```

Then wrap existing slot and trait appenders:

```python
def _append_cw_slot_section(lines: list[str], data: dict[str, Any]) -> None:
    has_slots = any(_as_list(data.get(zone)) for zone in ("front", "back", "hand"))
    if not has_slots:
        return
    _append_section(lines, "角色信息")
    _append_cw_slot_lines(lines, data)


def _append_cw_trait_section(lines: list[str], data: dict[str, Any]) -> None:
    if not _as_list(data.get("trait_summary")):
        return
    _append_section(lines, "羁绊信息")
    _append_cw_slot_trait_summary(lines, data)


def _append_skill_info_section(lines: list[str], data: dict[str, Any]) -> None:
    if not _as_list(data.get("skill_info")):
        return
    before = len(lines)
    _append_section(lines, "攻略提示")
    _append_skill_info(lines, data)
    if len(lines) == before + 1:
        lines.pop()
```

Update `_render_cw_slots_read` to call status, slot, trait sections after the screenshot block and before warnings/references.

- [ ] **Step 4: Run tests to verify GREEN**

Update existing exact-output `cw.slots.read` tests in `tests/test_output_rendering.py` to include the new `# 角色信息` and `# 羁绊信息` headings where those facts are present. Then run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py "tests/test_cw_rpc_contracts.py::test_cw_rpc_wrapper_matrix[cw_slots_read]" -q
```

Expected: all output rendering tests pass.

- [ ] **Step 5: Commit**

```powershell
rtk git add trail/output/rendering.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py
rtk git commit -m "feat(output): 新增默认文本板块标题"
```

---

### Task 2: Split CW Slots Status Reader

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Test: `tests/test_cw_slots.py`

- [ ] **Step 1: Write failing tests for split responsibilities**

Add tests near existing `build_cw_slots_reader` tests:

```python
def test_build_cw_slot_roles_reader_does_not_capture_status_regions(monkeypatch):
    slots_module = load_cw_slots_module()
    captures: list[dict] = []
    monkeypatch.setattr(slots_module, "run_batch_ocr", lambda runtime, targets, trace_prefix: type("Result", (), {"by_key": {target.key: type("Ocr", (), {"text": "希儿"})() for target in targets}})())
    monkeypatch.setattr(slots_module, "_read_slot_star_counts", lambda captures: {})
    monkeypatch.setattr(slots_module, "_capture_slot_panel_images", lambda runtime, point: {"name_image": f"img-{point}"})

    class Runtime:
        def capture_image(self, **kwargs):
            captures.append(kwargs)
            return "status-image"

        def click_point(self, *args):
            pass

    reader = slots_module.build_cw_slot_roles_reader(Runtime(), targets=["front:0"])
    reader()

    assert captures == []
```

Add status reader test:

```python
def test_build_cw_status_reader_reads_stage_and_status(monkeypatch):
    slots_module = load_cw_slots_module()
    seen: list[str] = []
    captured_regions: list[dict] = []

    def fake_batch(runtime, targets, trace_prefix):
        assert [target.key for target in targets] == [("stage_status", "level"), ("stage_status", "exp"), ("stage_status", "team_size")]
        return type("Result", (), {"by_key": {
            ("stage_status", "level"): type("Ocr", (), {"pieces": ["LV.3"]})(),
            ("stage_status", "exp"): type("Ocr", (), {"pieces": ["0/8"]})(),
            ("stage_status", "team_size"): type("Ocr", (), {"pieces": ["2/2"]})(),
        }})()

    monkeypatch.setattr(slots_module, "run_batch_ocr", fake_batch)
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: seen.append("stage") or "preparation")

    class Runtime:
        def capture_image(self, **kwargs):
            captured_regions.append(kwargs)
            return kwargs

    result = slots_module.build_cw_status_reader(Runtime())()

    assert seen == ["stage"]
    assert captured_regions == [
        {**slots_module.stage.CW_STATUS_LEVEL_REGION, "normalize": False},
        {**slots_module.stage.CW_STATUS_EXP_REGION, "normalize": False},
        {**slots_module.stage.CW_STATUS_TEAM_SIZE_REGION, "normalize": False},
    ]
    assert result.stage == "preparation"
    assert result.stage_status == {"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"}
```

Add detector edge tests:

```python
def test_build_cw_status_reader_keeps_status_when_stage_unknown(monkeypatch):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: None)
    monkeypatch.setattr(slots_module, "run_batch_ocr", lambda runtime, targets, trace_prefix: type("Result", (), {"by_key": {
        ("stage_status", "level"): type("Ocr", (), {"pieces": ["LV.3"]})(),
        ("stage_status", "exp"): type("Ocr", (), {"pieces": ["0/8"]})(),
        ("stage_status", "team_size"): type("Ocr", (), {"pieces": ["2/2"]})(),
    }})())

    class Runtime:
        def capture_image(self, **kwargs):
            return kwargs

    result = slots_module.build_cw_status_reader(Runtime())()

    assert result.stage is None
    assert result.stage_status["level"] == 3

```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_cw_slots.py::test_build_cw_slot_roles_reader_does_not_capture_status_regions tests/test_cw_slots.py::test_build_cw_status_reader_reads_stage_and_status tests/test_cw_slots.py::test_build_cw_status_reader_keeps_status_when_stage_unknown -q
```

Expected: fail because functions do not exist.

- [ ] **Step 3: Implement minimal split**

In `slots.py`, introduce a result type:

```python
@dataclass(frozen=True)
class CwStatusReadResult:
    stage: str | None
    stage_status: dict[str, Any]
```

Add `build_cw_status_reader(runtime)` that calls `stage.build_cw_stage_detector(runtime)`, captures the three status OCR regions, runs batch OCR, and returns `CwStatusReadResult(stage=detected_stage, stage_status=stage.parse_cw_stage_status(batch_result.by_key))`.

Add `build_cw_slot_roles_reader(runtime, targets=None)` by moving the role-slot portion of `build_cw_slots_reader` into a separate function. It should not capture status OCR regions and should return `(front, back, hand)`.

Update `build_cw_slots_reader` to orchestrate:

```python
status_reader = build_cw_status_reader(runtime)
roles_reader = build_cw_slot_roles_reader(runtime, targets=targets)

def reader() -> CwSlotsReadResult:
    _collapse_expanded_hand_card(runtime)
    if dismiss_initial_overlay:
        dismiss_cw_slots_overlay(runtime)
    status_result = status_reader()
    front, back, hand = roles_reader()
    return CwSlotsReadResult(front=front, back=back, hand=hand, stage_status=status_result.stage_status, stage=status_result.stage)
```

Extend `CwSlotsReadResult` by appending `stage: str | None = None` after the existing `stage_status` field, preserving positional compatibility: `front, back, hand, stage_status=None, stage=None`.

- [ ] **Step 4: Run tests to verify GREEN**

Run the same command from Step 2. Expected: all three focused tests pass.

- [ ] **Step 5: Run slots regression tests**

Before running the full file, update existing `tests/test_cw_slots.py` assertions that currently expect one combined batch OCR call. After the split, status OCR and role OCR are separate calls; the intended order is status reader before role slot panel OCR. Keep tests focused on observable order and targets rather than preserving the old single-batch implementation.

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_cw_slots.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
rtk git add trail/scenes/cw/slots.py tests/test_cw_slots.py
rtk git commit -m "refactor(cw): 拆分槽位综合状态读取"
```

---

### Task 3: Return Slots Stage Projection Through Daemon/RPC

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_cw_slots.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: Write failing tests for response projection**

Add/adjust a `read_cw_slots` test:

```python
def test_read_cw_slots_projects_stage_and_status_into_slots_payload(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)

    def reader():
        return slots_module.CwSlotsReadResult(
            front=[{"name": "希儿"}],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
        )

    slots_module.read_cw_slots(session, reader=reader)

    payload = session.scene_state["cw"]["slots"]
    assert payload["stage"] == "preparation"
    assert payload["stage_stale"] is False
    assert payload["stage_status"] == {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2", "role_count": {"front": 1, "back": 0, "hand": 0, "field": 1, "total": 1}}
    assert payload["stage_status_stale"] is False
    assert session.scene_state["cw"]["stage"]["value"] == "preparation"
    assert session.last_stage == {"scene": "cw", "value": "preparation"}
```

Add directed merge coverage:

```python
def test_read_cw_slots_directed_read_still_projects_status_from_merged_slots(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)["slots"] = {
        "front": [{"name": "旧希儿"}, None],
        "back": [{"name": "佩拉"}],
        "hand": [{"name": "停云"}],
        "stale": False,
    }

    def reader():
        return slots_module.CwSlotsReadResult(
            front=[{"name": "新希儿"}, None],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"},
        )

    slots_module.read_cw_slots(session, reader=reader, targets=["front:0"])

    assert session.scene_state["cw"]["slots"]["back"][0] == {"name": "佩拉"}
    assert session.scene_state["cw"]["slots"]["hand"][0] == {"name": "停云"}
    assert session.scene_state["cw"]["slots"]["stage_status"]["role_count"] == {"front": 1, "back": 1, "hand": 1, "field": 2, "total": 3}
```

Add ambiguous invalidation coverage:

```python
def test_read_cw_slots_invalidates_stage_when_stage_ambiguous(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "level": 3}}
    session.last_stage = {"scene": "cw", "value": "shop"}

    def reader():
        raise TrailError("STAGE_AMBIGUOUS", "当前资源无法区分阶段: preparation, shop")

    with pytest.raises(TrailError) as exc_info:
        slots_module.read_cw_slots(session, reader=reader)

    assert exc_info.value.code == "STAGE_AMBIGUOUS"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["error"]["code"] == "STAGE_AMBIGUOUS"
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 3}
    assert session.last_stage is None
```

Add daemon protocol test near other CW command-service tests:

```python
def test_command_service_handles_cw_slots_read_with_stage_projection(tmp_path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw import slots as slots_module

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root: {})
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, targets=None: lambda: slots_module.CwSlotsReadResult(front=[{"name": "希儿"}], back=[], hand=[], stage="preparation", stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"}))

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(request_id="req-slots-read", protocol_version=PROTOCOL_VERSION, workspace_root=str(tmp_path), session_id=session.session_id, verbose=False, method="cw.slots.read", payload={"session_id": session.session_id})

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["stage"] == "preparation"
    assert payload["data"]["stage_status"]["level"] == 3
    assert payload["data"]["stage_status_stale"] is False
```

Add RPC contract coverage in `tests/test_cw_rpc_contracts.py` for the same response shape. Use the existing RPC helper style in that file, but assert the `cw.slots.read` result includes `data.stage`, `data.stage_status`, and `data.stage_status_stale` when the reader actually returns those facts.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_cw_slots.py::test_read_cw_slots_projects_stage_and_status_into_slots_payload tests/test_cw_slots.py::test_read_cw_slots_directed_read_still_projects_status_from_merged_slots tests/test_cw_slots.py::test_read_cw_slots_invalidates_stage_when_stage_ambiguous tests/test_daemon_protocol.py::test_command_service_handles_cw_slots_read_with_stage_projection tests/test_cw_rpc_contracts.py::test_cw_slots_read_contract_includes_stage_projection -q
```

Expected: fail because projection keys are missing.

- [ ] **Step 3: Implement projection**

In `read_cw_slots`, wrap `result = reader()` so `TrailError("STAGE_AMBIGUOUS", ...)` calls `stage._invalidate_cw_stage(session, code=exc.code, message=str(exc))`, clears `session.last_stage`, and re-raises. When `result.stage_status is not None`, compute `status_with_role_count`, call `stage._replace_stage_fields(session, value=result.stage, stale=False, status=status_with_role_count)` and set `session.last_stage = {"scene": "cw", "value": result.stage}` if `result.stage is not None`; otherwise call `_replace_stage_fields(session, status=status_with_role_count)` without changing `last_stage`.

Then add projection keys to `cw_state["slots"]`:

```python
cw_state["slots"]["stage"] = result.stage
cw_stage = ensure_cw_state(session).get("stage") if isinstance(ensure_cw_state(session).get("stage"), dict) else {}
cw_state["slots"]["stage_stale"] = False if result.stage is not None else bool(cw_stage.get("stale", True))
cw_state["slots"]["stage_status"] = deepcopy(status_with_role_count)
cw_state["slots"]["stage_status_stale"] = bool(status_with_role_count.get("stale", True))
```

If the stage detector returns `None`, keep `stage` as `None`, set `stage_stale` according to session stage stale state, and still project status OCR.

- [ ] **Step 4: Run tests to verify GREEN**

Run the same command from Step 2. Expected: pass.

- [ ] **Step 5: Run protocol regressions**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_cw_slots.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
rtk git add trail/scenes/cw/slots.py trail/daemon/cw_service.py tests/test_cw_slots.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py
rtk git commit -m "feat(cw): 在槽位读取中返回综合状态"
```

---

### Task 4: Sectionize Shop Renderers

**Files:**
- Modify: `trail/output/rendering.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: Write failing shop renderer tests**

Update existing shop stage projection tests and affected CLI/RPC stdout contract tests before running RED. In `tests/test_cw_rpc_contracts.py`, update `test_cw_shop_scan_renders_fresh_stage_status_projection`, `test_cw_shop_buy_exp_maps_to_canonical_command`, `test_cw_shop_status_renders_stale_stage_status_without_stale_values`, and `test_cw_shop_status_renders_missing_stage_status_as_stale` so their expected lines match the new `# 商店信息` / `# 综合信息` behavior or the no-heading single-section behavior. Add specific renderer checks:

```python
def test_render_output_sections_shop_scan_items_and_status():
    payload = {
        "ok": True,
        "data": {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
            "stage": "shop",
            "stage_stale": False,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
            "stage_status_stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=1",
        "# 商店信息",
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "# 综合信息",
        "info stage=shop stale=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
    ]
```

Add explicit status and buy-exp coverage:

```python
def test_render_output_sections_shop_status_only_when_multiple_fact_groups():
    payload = {"ok": True, "data": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": True, "stale": False}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    lines = render_output("cw.shop.status", payload).splitlines()

    assert lines == ["ok cw.shop.status count=1", "item idx=1 slot=1 name=银狼 cost=20"]
    assert not any(line.startswith("# ") for line in lines)


def test_render_output_sections_shop_status_when_stage_projection_exists():
    payload = {"ok": True, "data": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": True, "stale": False, "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"}, "stage_status_stale": False}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=1",
        "# 商店信息",
        "item idx=1 slot=1 name=银狼 cost=20",
        "# 综合信息",
        "info stage_level=3 stage_exp=0/8 stage_team_size=2/2 stage_status_stale=0",
    ]


def test_render_output_shop_status_only_stage_stale_has_no_section_heading():
    payload = {"ok": True, "data": {"items": [], "stage_status_stale": True}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=0",
        "info stage_status_stale=1",
    ]


def test_render_output_sections_shop_buy_exp_and_preserves_null_team_size():
    payload = {"ok": True, "data": {"opened": True, "stale": False, "items": [], "coins": 36, "level": 4, "exp": "0/8", "reserve_full": False, "team_size": None}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    assert render_output("cw.shop.buy_exp", payload).splitlines() == [
        "ok cw.shop.buy_exp opened=1 stale=0 count=0",
        "# 商店信息",
        "info coins=36 reserve_full=0",
        "# 综合信息",
        "info level=4 exp=0/8 team_size=null",
    ]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py::test_render_output_sections_shop_scan_items_and_status tests/test_output_rendering.py::test_render_output_sections_shop_status_only_when_multiple_fact_groups tests/test_output_rendering.py::test_render_output_sections_shop_status_when_stage_projection_exists tests/test_output_rendering.py::test_render_output_shop_status_only_stage_stale_has_no_section_heading tests/test_output_rendering.py::test_render_output_sections_shop_buy_exp_and_preserves_null_team_size tests/test_cw_rpc_contracts.py::test_cw_shop_scan_renders_fresh_stage_status_projection tests/test_cw_rpc_contracts.py::test_cw_shop_buy_exp_maps_to_canonical_command tests/test_cw_rpc_contracts.py::test_cw_shop_status_renders_stale_stage_status_without_stale_values tests/test_cw_rpc_contracts.py::test_cw_shop_status_renders_missing_stage_status_as_stale -q
```

Expected: fail because headings are missing or order differs.

- [ ] **Step 3: Implement shop section helpers**

Add `_append_cw_shop_section(lines, data, *, force_section: bool = False)` that emits `# 商店信息` only when both shop facts and another section will be printed, or when `force_section=True`. For single-body `cw.shop.status` with only items, keep the existing no-heading output.

Split `_append_cw_shop_snapshot_info` into explicit helpers: `_append_cw_shop_fact_lines(lines, data)` for `coins/reserve_full`, `_append_cw_shop_buy_exp_status_lines(lines, data)` for `level/exp/team_size`, and `_append_cw_status_section(lines, data)` for `stage_status` projection. For `cw.shop.buy_exp`, render `team_size=null` explicitly when the key exists with `None`; do not rely on `_append_fact_line` to preserve `None`.

- [ ] **Step 4: Run tests to verify GREEN**

Run the focused renderer and RPC stdout tests together:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py::test_render_output_sections_shop_scan_items_and_status tests/test_output_rendering.py::test_render_output_sections_shop_status_only_when_multiple_fact_groups tests/test_output_rendering.py::test_render_output_sections_shop_status_when_stage_projection_exists tests/test_output_rendering.py::test_render_output_shop_status_only_stage_stale_has_no_section_heading tests/test_output_rendering.py::test_render_output_sections_shop_buy_exp_and_preserves_null_team_size tests/test_cw_rpc_contracts.py::test_cw_shop_scan_renders_fresh_stage_status_projection tests/test_cw_rpc_contracts.py::test_cw_shop_buy_exp_maps_to_canonical_command tests/test_cw_rpc_contracts.py::test_cw_shop_status_renders_stale_stage_status_without_stale_values tests/test_cw_rpc_contracts.py::test_cw_shop_status_renders_missing_stage_status_as_stale -q
```

Expected: pass.

- [ ] **Step 5: Run output renderer regressions**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
rtk git add trail/output/rendering.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py
rtk git commit -m "feat(output): 板块化 CW 商店输出"
```

---

### Task 5: Sectionize Portal Renderer

**Files:**
- Modify: `trail/output/rendering.py`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: Write failing portal renderer test**

Update `test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff` to use this complete expected output:

```python
assert lines == [
    "ok cw.portal.select idx=1 投资环境=击破概念股",
    "shot path=.trail/shots/portal-prep.png",
    "info read_image_first=1",
    "# 综合信息",
    "info stage=preparation stale=0",
    "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
    "# 攻略提示",
    "info skill_info=运营思路 text=先收集事实",
    "# 角色信息",
    "slot pos=front:0 name=希儿 star=1 traits=巡猎",
    "slot pos=hand:0 name=停云",
    "# 羁绊信息",
    'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
    "# 商店信息",
    "item idx=1 slot=1 name=银狼 cost=20",
    "info coins=40 reserve_full=0",
    'warn code=W msg="warn text"',
    "ref path=p sim=0.9",
    "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
]
assert lines[-1].startswith("info handoff_skill=trail-cw-prep ")
assert not any(" opened=" in line or " stale=" in line for line in lines if not line.startswith("info stage="))
```

Ensure the payload has `slots.stage="preparation"`, `slots.stage_stale=False`, and `shop.stage_status={"level": 3, "exp": "0/8", "team_size": "1/2", "stale": False}` so `_merge_cw_status_projection` must merge stage from slots with fresh status from shop.

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py::test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff -q
```

Expected: fail because portal headings are missing.

- [ ] **Step 3: Implement portal section rendering**

In `_render_cw_portal_select`, extract nested status projection before sections:

```python
status_source = _merge_cw_status_projection(_as_dict(data.get("slots")), _as_dict(data.get("shop")), data)
_append_cw_status_section(lines, status_source)
_append_skill_info_section(lines, data)
_append_cw_slot_section(lines, slots)
_append_cw_trait_section(lines, slots)
_append_cw_shop_section(lines, shop)
```

`_merge_cw_status_projection` should only return the stable projection keys for the renderer; it should not render anything.
Projection priority: prefer a non-stale shop projection over a slots projection because shop is collected later in `cw.portal.select`; if shop status is stale or missing, use slots projection. If slots has `stage/stage_stale` and shop only has `stage_status/stage_status_stale`, merge those pieces.

- [ ] **Step 4: Run tests to verify GREEN**

Run the command from Step 2. Expected: pass.

- [ ] **Step 5: Run output renderer regressions**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
rtk git add trail/output/rendering.py tests/test_output_rendering.py
rtk git commit -m "feat(output): 板块化 CW 入口输出"
```

---

### Task 6: Update Protocol Docs and Active Skills

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-hsr/references/scene-entry-index.md`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_output_debug.py`
- Test: `tests/test_skill_structure.py`

- [ ] **Step 1: Write failing doc assertions**

Add assertions that `AGENTS.md` documents:

```python
assert "`# 标题` 行" in agents
assert "标题行不承载 must-keep 事实" in agents
assert "标题行不得插入 `shot path=...` 与 `info read_image_first=1` 之间" in agents
assert "首批固定标题" in agents
```

Update README example assertions to include `# 综合信息`, `# 角色信息`, `# 羁绊信息`, `# 商店信息`.

Update `tests/test_skill_structure.py` to assert `trail-cw-entry`, `trail-cw-portal`, and `trail-cw-prep` tell agents to skip `# ` headings as facts and consume facts under sections after reading screenshots; assert scene entry index still routes `cw.portal.select -> trail-cw-prep` without treating headings as facts.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py::test_readme_mentions_text_output_protocol tests/test_output_debug.py::test_project_agents_declares_renderer_contracts tests/test_skill_structure.py::test_active_cw_skills_document_portal_select_auto_collect_prep_facts -q
```

Expected: fail because docs do not mention section headings.

- [ ] **Step 3: Update docs**

In `AGENTS.md`, update output protocol:

- Default body lines may include `# 标题` section headings.
- Headings are not prefixes and do not carry facts.
- Fixed first-batch headings: `# 综合信息`, `# 攻略提示`, `# 角色信息`, `# 羁绊信息`, `# 商店信息`.
- Headings cannot appear between screenshot and `read_image_first`, after handoff, or in failure output.

In `README.md`, update examples for `cw.slots.read`, `cw.shop.scan`, and `cw.portal.select`.

In `skills/trail-cw-entry/SKILL.md`, `skills/trail-cw-prep/SKILL.md`, `skills/trail-cw-portal/SKILL.md`, and `skills/trail-hsr/references/scene-entry-index.md`, add guidance: read screenshot first, treat `# ` lines as section titles only, then consume facts under `# 综合信息`, `# 攻略提示`, `# 角色信息`, `# 羁绊信息`, `# 商店信息`.

- [ ] **Step 4: Run tests to verify GREEN**

Run the command from Step 2. Expected: pass.

- [ ] **Step 5: Run doc/skill regressions**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_output_rendering.py tests/test_output_debug.py tests/test_skill_structure.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
rtk git add AGENTS.md README.md skills/trail-cw-entry/SKILL.md skills/trail-cw-prep/SKILL.md skills/trail-cw-portal/SKILL.md skills/trail-hsr/references/scene-entry-index.md tests/test_output_rendering.py tests/test_output_debug.py tests/test_skill_structure.py
rtk git commit -m "docs(output): 说明默认文本板块标题"
```

---

### Task 7: Final Verification

**Files:**
- Verify only; no planned code edits.

- [ ] **Step 1: Run focused full suite**

Run:

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null; $tempPath=(Resolve-Path ".pytest-tmp").Path; $env:TMP=$tempPath; $env:TEMP=$tempPath; python -m pytest tests/test_cw_slots.py tests/test_cw_stage.py tests/test_cw_shop.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -q
```

Expected: all pass.

- [ ] **Step 2: Inspect status**

Run:

```powershell
rtk git status --short
```

Expected: only unrelated pre-existing untracked docs may remain.

- [ ] **Step 3: Final code review**

Dispatch a final read-only review subagent with full spec and plan paths. Required focus: protocol compatibility, section heading ordering, status reader split, `buy_exp team_size=null`, portal handoff last, docs/tests sync.

- [ ] **Step 4: Fix review findings if any**

If final review returns `CHANGES_REQUIRED`, create targeted fix tasks and rerun the relevant tests. Do not finish with open Critical or Important findings.
