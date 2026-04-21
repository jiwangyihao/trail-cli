# Currency Wars Batch Place/Sell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `trail cw slots place-one` / `trail cw hand sell-one` 替换为批量 `place` / `sell` 命令，并保持 slots 状态持久化、stdout 协议、文档与测试同步正确。

**Architecture:** 前门改造分成四层：CLI 改名并把批量参数整理成 RPC payload；scene 层把单步上场/卖牌包装成保序批处理并在部分执行失败时只写最小可信状态；daemon mutation 复用现有 `completed` 已知失败语义，在 session 已保存后再返回 failure envelope；renderer、README、skill 与契约测试统一切到新命令名。实现坚持最小改动，不新增 request-status 状态种类，不推导新的 slots 快照，不引入通用批处理框架。

**Tech Stack:** Python 3.12, Typer, Trail daemon mutation pipeline, pytest, Markdown docs

**Git Note:** 当前会话未经用户明确要求，不创建 commit。

---

### Task 1: CLI Front Door Rename And Payload Shaping

**Files:**
- Modify: `trail/commands/cw.py:1-9,173-189`
- Test: `tests/test_cw_rpc_contracts.py:345-590`

- [ ] **Step 1: 写出 CLI/RPC 契约失败测试**

在 `tests/test_cw_rpc_contracts.py` 里先把旧 `place-one` / `sell-one` 的 happy-path 断言改造成新命令，同时新增旧命令删除与本地输入失败断言。直接写成下面这组测试：

```python
def test_cw_slots_place_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.slots.place": build_success_response(
                request_id="req-cw-slots-place",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
                screenshot=".trail/shots/req-cw-slots-place.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "slots",
            "place",
            "--session",
            SESSION_ID,
            "--action",
            "hand:0,front:0",
            "--action",
            "hand:1,back:2",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.slots.place front=1 back=1 hand=0 stale=1",
        screenshot=".trail/shots/req-cw-slots-place.png",
    )
    _assert_single_call(
        client,
        method="cw.slots.place",
        payload={
            "actions": [
                {"source": "hand:0", "target": "front:0"},
                {"source": "hand:1", "target": "back:2"},
            ]
        },
        tmp_path=tmp_path,
    )


def test_cw_hand_sell_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.hand.sell": build_success_response(
                request_id="req-cw-hand-sell",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [None], "stale": True},
                screenshot=".trail/shots/req-cw-hand-sell.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "0", "--slot", "2"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.hand.sell front=1 back=1 hand=1 stale=1",
        screenshot=".trail/shots/req-cw-hand-sell.png",
    )
    _assert_single_call(client, method="cw.hand.sell", payload={"slots": [0, 2]}, tmp_path=tmp_path)


def test_cw_slots_place_one_is_removed(cli_runner):
    result = cli_runner.invoke(app, ["cw", "slots", "place-one", "--help"])
    assert result.exit_code != 0
    assert "No such command 'place-one'" in result.stdout


def test_cw_slots_place_requires_at_least_one_action(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})
    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID])
    assert result.exit_code == 0
    assert "fail cw.slots.place" in result.stdout
    assert client.calls == []


def test_cw_slots_place_rejects_malformed_action_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})
    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID, "--action", "hand:0-front:0"])
    assert result.exit_code == 0
    assert "fail cw.slots.place" in result.stdout
    assert client.calls == []


def test_cw_hand_sell_one_is_removed(cli_runner):
    result = cli_runner.invoke(app, ["cw", "hand", "sell-one", "--help"])
    assert result.exit_code != 0
    assert "No such command 'sell-one'" in result.stdout


def test_cw_hand_sell_requires_at_least_one_slot(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})
    result = cli_runner.invoke(app, ["cw", "hand", "sell", "--session", SESSION_ID])
    assert result.exit_code == 0
    assert "fail cw.hand.sell" in result.stdout
    assert client.calls == []
```

- [ ] **Step 2: 运行这些契约测试，确认现在确实失败**

Run:

```bash
python -m pytest tests/test_cw_rpc_contracts.py -q
```

Expected:

- `cw slots place` / `cw hand sell` 相关测试失败
- 失败原因应包含旧命令名仍然存在、payload 仍是单值、或本地输入失败尚未实现

- [ ] **Step 3: 在 CLI 层实现新命令与最小本地校验**

修改 `trail/commands/cw.py`，保留 `_print_cw()` 调用路径，但新增一个本地 failure envelope helper 和一个 `--action` 解析 helper，然后把旧命令替换为新命令。最小实现直接照这个结构写：

```python
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture


def _cw_input_invalid_response(message: str) -> dict:
    return with_auto_capture(
        None,
        lambda: (_ for _ in ()).throw(TrailError("CW_OPTION_INVALID", message)),
    )


def _parse_place_actions(values: list[str] | None) -> list[dict[str, str]]:
    raw_values = list(values or [])
    if not raw_values:
        raise TrailError("CW_OPTION_INVALID", "cw slots place requires at least one --action")
    actions: list[dict[str, str]] = []
    for raw in raw_values:
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise TrailError("CW_OPTION_INVALID", f"cw slots place action invalid: {raw}")
        actions.append({"source": parts[0], "target": parts[1]})
    return actions


@slots_app.command("place")
def cw_slots_place(
    session: str = typer.Option(..., "--session"),
    action: list[str] | None = typer.Option(None, "--action"),
) -> None:
    try:
        actions = _parse_place_actions(action)
    except TrailError as error:
        print_output("cw.slots.place", _cw_input_invalid_response(str(error)))
        return
    _print_cw("cw.slots.place", session_id=session, payload={"actions": actions})


@hand_app.command("sell")
def cw_hand_sell(
    session: str = typer.Option(..., "--session"),
    slot: list[int] | None = typer.Option(None, "--slot"),
) -> None:
    slots = list(slot or [])
    if not slots:
        print_output("cw.hand.sell", _cw_input_invalid_response("cw hand sell requires at least one --slot"))
        return
    _print_cw("cw.hand.sell", session_id=session, payload={"slots": slots})
```

同时删除旧的 `@slots_app.command("place-one")` / `@hand_app.command("sell-one")` 入口。

- [ ] **Step 4: 重跑 CLI/RPC 契约测试，确认 front door 收口正确**

Run:

```bash
python -m pytest tests/test_cw_rpc_contracts.py -q
```

Expected:

- 新 `place` / `sell` happy path 通过
- 旧命令删除断言通过
- 本地输入失败测试通过且 fake daemon 未收到调用

### Task 2: Scene Batch Semantics And Minimal State Writes

**Files:**
- Modify: `trail/scenes/cw/slots.py:13-16,85-87,281-328`
- Test: `tests/test_cw_slots.py:389-506,726-831`

- [ ] **Step 1: 先写 scene 层失败测试，锁住保序、遇错即停和 stale 写回**

在 `tests/test_cw_slots.py` 里新增/替换单元测试，至少先写这三条：

```python
def test_place_cw_slots_runs_actions_in_order(tmp_path):
    session = build_fake_cw_session(tmp_path)
    calls: list[tuple[str, str]] = []

    def placer(source: str, target: str) -> None:
        calls.append((source, target))

    refreshed = place_cw_slots(
        session,
        actions=[
            {"source": "hand:2", "target": "back:0"},
            {"source": "hand:0", "target": "front:0"},
        ],
        placer=placer,
    )

    assert calls == [("hand:2", "back:0"), ("hand:0", "front:0")]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_place_cw_slots_stops_after_first_runtime_failure_and_keeps_stale(tmp_path):
    session = build_fake_cw_session(tmp_path)
    calls: list[tuple[str, str]] = []

    def placer(source: str, target: str) -> None:
        calls.append((source, target))
        if len(calls) == 2:
            raise TrailError("SLOTS_CANNOT_BE_FIELDED", f"target slot cannot field character: {target}")

    with pytest.raises(TrailError) as exc_info:
        place_cw_slots(
            session,
            actions=[
                {"source": "hand:2", "target": "back:0"},
                {"source": "hand:0", "target": "front:0"},
                {"source": "hand:1", "target": "back:2"},
            ],
            placer=placer,
        )

    assert exc_info.value.code == "SLOTS_CANNOT_BE_FIELDED"
    assert calls == [("hand:2", "back:0"), ("hand:0", "front:0")]
    assert exc_info.value.known_failure_after_save is True
    assert session.scene_state["cw"]["slots"]["stale"] is True
    assert session.scene_state["cw"]["sell_plan"] == {}


def test_sell_cw_hand_slots_requires_non_empty_list(tmp_path):
    session = build_fake_cw_session(tmp_path)
    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=[])
    assert exc_info.value.code == "CW_OPTION_INVALID"


def test_sell_cw_hand_slots_runs_slots_in_order(tmp_path):
    session = build_fake_cw_session(tmp_path)
    calls: list[int] = []

    def seller(slot: int) -> None:
        calls.append(slot)

    refreshed = sell_cw_hand_slots(session, slots=[2, 0], seller=seller)

    assert calls == [2, 0]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_sell_cw_hand_slots_stops_after_first_runtime_failure(tmp_path):
    session = build_fake_cw_session(tmp_path)
    calls: list[int] = []

    def seller(slot: int) -> None:
        calls.append(slot)
        if len(calls) == 2:
            raise TrailError("UNEXPECTED_ERROR", f"sell failed at slot: {slot}")

    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=[2, 0, 1], seller=seller)

    assert exc_info.value.code == "UNEXPECTED_ERROR"
    assert calls == [2, 0]
    assert exc_info.value.known_failure_after_save is True
    assert session.scene_state["cw"]["slots"]["stale"] is True
    assert session.scene_state["cw"]["sell_plan"] == {}
```

- [ ] **Step 2: 运行 scene 单测，确认它们先红**

Run:

```bash
python -m pytest tests/test_cw_slots.py -q
```

Expected:

- 新批量 helper 不存在或行为不符导致失败
- 旧单步测试仍会引用 `place_one_cw_slot` / `sell_one_cw_hand`

- [ ] **Step 3: 在 `trail/scenes/cw/slots.py` 里实现批量 helper，保留最小状态写回**

不要引入通用批处理框架；直接在 `slots.py` 里补最小 helper。照下面这个结构实现：

```python
def _mark_slots_stale(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


def _normalize_place_actions(actions: list[dict[str, str]]) -> list[tuple[str, str]]:
    if not actions:
        raise TrailError("CW_OPTION_INVALID", "cw slots place requires at least one --action")
    normalized: list[tuple[str, str]] = []
    for action in actions:
        source = str(action["source"])
        target = str(action["target"])
        _parse_slot_reference(source)
        _parse_slot_reference(target)
        normalized.append((source, target))
    return normalized


def _normalize_sell_slots(slots: list[int]) -> list[int]:
    if not slots:
        raise TrailError("CW_OPTION_INVALID", "cw hand sell requires at least one --slot")
    normalized: list[int] = []
    for slot in slots:
        if slot < 0 or slot >= len(HAND_SLOT_POINTS):
            raise TrailError("SLOTS_POSITION_INVALID", f"invalid hand slot: {slot}")
        normalized.append(slot)
    return normalized


def place_cw_slots(session: SessionModel, *, actions: list[dict[str, str]], placer: SlotMover | None = None) -> SessionModel:
    normalized = _normalize_place_actions(actions)
    for source, target in normalized:
        try:
            if placer is not None:
                placer(source, target)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)


def sell_cw_hand_slots(session: SessionModel, *, slots: list[int], seller: HandSeller | None = None) -> SessionModel:
    normalized = _normalize_sell_slots(slots)
    for slot in normalized:
        try:
            if seller is not None:
                seller(slot)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)
```

然后把旧的 `place_one_cw_slot()` / `sell_one_cw_hand()` 单测替换成新 helper 单测；`swap_cw_slots()` 保持不动。

- [ ] **Step 4: 重跑 scene 单测，确认批量语义成立**

Run:

```bash
python -m pytest tests/test_cw_slots.py -q
```

Expected:

- 新的保序、stop-on-first-error、stale 写回测试通过
- 与 slots journal 相关的旧命令名断言仍失败，留给后续 daemon 任务处理

### Task 3: Daemon Mutation Completion Semantics And Control-Plane Tests

**Files:**
- Modify: `trail/daemon/cw_service.py:187-223,311-361`
- Modify: `trail/daemon/command_service.py:16-31`
- Test: `tests/test_cw_slots.py:726-831`
- Test: `tests/test_daemon_protocol.py:1154-1230`
- Test: `tests/test_daemon_session_service.py:162-240`

- [ ] **Step 1: 先写 daemon/control-plane 失败测试，锁住 `completed + tainted=0` 语义**

先补三类测试：command-service journal 矩阵迁移到新命令名、known-failure protocol 测试、session-service completed failure 不 taint 测试。直接加下面这种断言：

```python
def test_command_service_handles_cw_slots_place_known_failure_as_completed(tmp_path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)

    def fail_after_partial_execution(session, actions, placer):
        session.scene_state.setdefault("cw", {})["slots"] = {"front": ["希儿"], "back": [], "hand": [], "stale": True}
        session.scene_state["cw"]["sell_plan"] = {}
        error = TrailError("SLOTS_CANNOT_BE_FIELDED", "target slot cannot field character: front:0")
        error.known_failure_after_save = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.place_cw_slots", fail_after_partial_execution)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-slots-place-known-fail",
        method="cw.slots.place",
        payload={"actions": [{"source": "hand:0", "target": "front:0"}]},
    )

    status = service.request_status("req-cw-slots-place-known-fail")
    assert envelope["ok"] is False
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
    assert service.load_session(session.session_id).scene_state["cw"]["slots"]["stale"] is True


def test_finish_mutation_completed_failure_does_not_taint_session(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-known-fail", command_name="cw.slots.place")

    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-known-fail",
        command_name="cw.slots.place",
        final_state="completed",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-known-fail.png",
            error={"code": "SLOTS_CANNOT_BE_FIELDED", "message": "target slot cannot field character: front:0"},
        ),
    )

    status = service.request_status("req-known-fail")
    assert status["final_state"] == "completed"
    assert status["tainted"] is False


def test_begin_mutation_allows_following_cw_command_after_completed_failure(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-known-fail", command_name="cw.slots.place")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-known-fail",
        command_name="cw.slots.place",
        final_state="completed",
        envelope=_envelope(ok=False, error={"code": "SLOTS_CANNOT_BE_FIELDED", "message": "target slot cannot field character: front:0"}),
    )

    accepted = service.begin_mutation(
        session_id=session.session_id,
        request_id="req-next",
        command_name="cw.hand.sell",
        enforce_cw_tainted=True,
    )

    assert accepted["status"] == "accepted"
```

在同一轮里再补两条明确的 fallback 测试，不要只靠“旧测试不回归”带过：

1. monkeypatch known-failure 路径里的 `session_service.save_session` 抛 `OSError("save failed")`，断言 `request_status.final_state == "applied_but_not_persisted"` 且 `tainted is True`。
2. monkeypatch `trail.daemon.cw_service.with_auto_capture` 在 known-failure 路径上抛 `RuntimeError("capture failed")`，断言 `request_status.final_state == "persisted_but_response_unknown"` 且 `tainted is True`。

同时把 `tests/test_cw_slots.py` 的 journal 参数矩阵从 `cw.slots.place_one` / `cw.hand.sell_one` 改成 `cw.slots.place` / `cw.hand.sell`，payload 改成 `actions=[...]` / `slots=[...]`。

- [ ] **Step 2: 运行 daemon 相关测试，确认它们先红**

Run:

```bash
python -m pytest tests/test_cw_slots.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py -q
```

Expected:

- journal 矩阵因为方法名和 payload 还没改而失败
- known-failure `completed + tainted=0` 测试失败

- [ ] **Step 3: 在 daemon 层复用现有 completed 终态，新增“保存后再封装 failure envelope”的分支**

先在 `trail/daemon/cw_service.py` 里把 handler 映射切到新方法名，再在 `handle_mutation()` 里拦截 `known_failure_after_save`。最小改动直接照下面结构做：

```python
try:
    result = handlers[method]()
except TrailError as error:
    if getattr(error, "known_failure_after_save", False):
        try:
            session_service.save_session(session)
        except Exception as save_error:
            raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(save_error)) from save_error

        extra_delay_seconds = PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS if method == "cw.portal.select" else 0.0
        capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id, extra_delay_seconds=extra_delay_seconds)
        try:
            return with_auto_capture(capture_runtime, lambda: (_ for _ in ()).throw(error), verbose=verbose)
        except Exception as capture_error:
            raise PersistedButResponseUnknown(_unknown_result_envelope(capture_error)) from capture_error
    raise
except CwSideEffectAppliedError as error:
    raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(error)) from error
except Exception as error:
    if getattr(error, "completed_after_side_effect", False):
        raise
    if tracker.side_effect_applied:
        raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(error)) from error
    raise
```

这段代码需要补齐导入：

```python
from trail.daemon.command_service import PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted
```

然后做三件同步改动：

```python
# trail/daemon/command_service.py
CW_MUTATING_METHODS = {
    ...,
    "cw.slots.place",
    ...,
    "cw.hand.sell",
    ...,
}

# trail/daemon/cw_service.py handlers
"cw.slots.place": lambda: place_cw_slots(
    session,
    actions=payload["actions"],
    placer=slot_placer_factory(runtime()),
).scene_state["cw"]["slots"],
"cw.hand.sell": lambda: sell_cw_hand_slots(
    session,
    slots=payload["slots"],
    seller=hand_seller_factory(runtime()),
).scene_state["cw"]["slots"],
```

关键点：不要改 `request_status` 的状态集合；只让 `_run_mutation()` 在收到 `ok=False` 但 handler 已经保存过状态的 envelope 时照常 `finish_mutation(final_state="completed")`。

- [ ] **Step 4: 重跑 daemon/control-plane 测试，确认 request-status 语义正确**

Run:

```bash
python -m pytest tests/test_cw_slots.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py -q
```

Expected:

- `cw.slots.place` / `cw.hand.sell` journal 测试通过
- 已知业务 failure 场景为 `final_state=completed`、`tainted=0`
- 后续 `begin_mutation(..., enforce_cw_tainted=True)` 不会把 completed failure 错拦成 `SESSION_RECONCILE_REQUIRED`
- known-failure 分支里的 `save_session` 失败仍是 `applied_but_not_persisted`，save 后 capture/metadata 失败仍是 `persisted_but_response_unknown`
- `save_session` / response unknown 相关旧测试不回归

### Task 4: Renderer, README, Skill, And End-To-End Verification

**Files:**
- Modify: `trail/output/rendering.py:441-553,1087-1092`
- Modify: `README.md:37-57,123-182`
- Modify: `skills/trail-cw-slots/SKILL.md:14-40`
- Test: `tests/test_output_rendering.py:1555-1569,2059-2159`

- [ ] **Step 1: 先写 renderer 与文档失败测试**

先在 `tests/test_output_rendering.py` 里补直接断言，而不是只靠 CLI 契约间接覆盖。先写成这样：

```python
def test_render_output_renders_cw_slots_place_success_text():
    payload = {
        "ok": True,
        "data": {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
        "screenshot": ".trail/shots/req-place.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.place", payload).splitlines() == [
        "ok cw.slots.place front=1 back=1 hand=0 stale=1",
        "shot path=.trail/shots/req-place.png",
    ]


def test_render_output_renders_cw_slots_place_known_failure_without_recover():
    payload = {
        "request_id": "req-place-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-place-fail.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "SLOTS_CANNOT_BE_FIELDED", "message": "target slot cannot field character: front:0"},
    }

    assert render_output("cw.slots.place", payload).splitlines() == [
        "fail cw.slots.place code=SLOTS_CANNOT_BE_FIELDED",
        "request id=req-place-fail",
        "shot path=.trail/shots/req-place-fail.png",
        'why msg="target slot cannot field character: front:0"',
    ]


def test_render_output_renders_cw_hand_sell_success_text():
    payload = {
        "ok": True,
        "data": {"front": ["希儿"], "back": ["佩拉"], "hand": [None], "stale": True},
        "screenshot": ".trail/shots/req-sell.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell", payload).splitlines() == [
        "ok cw.hand.sell front=1 back=1 hand=1 stale=1",
        "shot path=.trail/shots/req-sell.png",
    ]


def test_render_output_renders_cw_hand_sell_known_failure_without_recover():
    payload = {
        "request_id": "req-sell-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-sell-fail.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "UNEXPECTED_ERROR", "message": "sell failed at slot: 0"},
    }

    assert render_output("cw.hand.sell", payload).splitlines() == [
        "fail cw.hand.sell code=UNEXPECTED_ERROR",
        "request id=req-sell-fail",
        "shot path=.trail/shots/req-sell-fail.png",
        'why msg="sell failed at slot: 0"',
    ]


def test_readme_documents_batch_slots_place_and_sell_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "trail cw slots place --session <id> --action hand:0,front:0" in readme
    assert "trail cw hand sell --session <id> --slot 0 --slot 2" in readme
    assert "中途失败后应重新执行 `trail cw slots read`" in readme


def test_trail_cw_slots_skill_documents_batch_place_and_sell() -> None:
    skill_text = (PROJECT_ROOT / "skills" / "trail-cw-slots" / "SKILL.md").read_text(encoding="utf-8")
    assert "trail cw slots place" in skill_text
    assert "trail cw hand sell" in skill_text
    assert "place-one" not in skill_text
    assert "sell-one" not in skill_text
```

- [ ] **Step 2: 运行 renderer/doc 测试，确认它们先红**

Run:

```bash
python -m pytest tests/test_output_rendering.py -q
```

Expected:

- renderer 注册仍挂旧命令导致失败
- README / skill 还没更新导致字符串断言失败

- [ ] **Step 3: 改 renderer 映射并同步 README / skill 文档**

在 `trail/output/rendering.py` 里只改 command map，不新增 renderer 家族；在 README 货币战争流程段新增一小段批量示例；在 `skills/trail-cw-slots/SKILL.md` 把职责、标准流程、执行规则里的旧命令全面替换。具体改动按下面骨架完成：

```python
# trail/output/rendering.py
RENDERERS = {
    ...,
    "cw.slots.read": _render_cw_slots_read,
    "cw.slots.swap": _render_cw_slots,
    "cw.slots.place": _render_cw_slots,
    ...,
    "cw.hand.sell": _render_cw_slots,
    "cw.hand.sell_plan": _render_cw_sell_plan,
}
```

README 新增一段紧凑示例文本：

```text
- 批量上场：`trail cw slots place --session <id> --action hand:0,front:0 --action hand:1,back:2`
- 批量卖牌：`trail cw hand sell --session <id> --slot 0 --slot 2`
- 这两类命令都严格保序、遇错即停；只要中途失败且前面动作可能已生效，就应重新执行 `trail cw slots read`
```

`skills/trail-cw-slots/SKILL.md` 则把流程段改成：

```text
4. 如需从手牌批量上场：`trail cw slots place --session <id> --action <src,dst> ...`
5. 如需卖牌，先看建议：`trail cw hand sell-plan --session <id>`
6. 真正出售时显式执行：`trail cw hand sell --session <id> --slot <n> --slot <m>`
```

- [ ] **Step 4: 运行最终目标测试集，确认这轮改动闭环**

Run:

```bash
python -m pytest tests/test_cw_slots.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py -q
```

Expected:

- 以上 5 个文件全部通过
- 新命令名、known-failure `completed/tainted=0` 语义、README/skill 文档同步全部被验证
