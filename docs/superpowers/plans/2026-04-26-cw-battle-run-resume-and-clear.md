# CW Battle Run Resume And Clear Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: First use superpowers:using-git-worktrees to create a project-local worktree, then use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cw.battle.run` 默认采用 `90s` 短预算并可靠支持 `in_progress + 重跑` 的续跑语义，同时新增内部续跑提示位、`clear-in-progress` 命令、本轮 battle 阶段号持久化，以及面向 Agent 的明确默认文本提示。

**Architecture:** 在 `battle.py` 内把“单次调用观察到的 battle-flow 证据”和“上一条 `in_battle=1` 续跑提示位”分开处理：前者继续驱动 battle 状态机，后者只在开场无信号帧时提供容错。CLI / daemon / renderer 层同步收口到新的 `90s` 默认预算、`cw.battle.clear_in_progress` 命令、`info next_action=...` 提示，以及 README / 场景说明中的续跑语义。

**Tech Stack:** Python 3、Typer CLI、Trail daemon RPC、pytest、README / AGENTS / skills 文档断言测试。

---

> 当前用户尚未要求创建 git commit。执行本计划时，每个 task 的 checkpoint 先以“目标测试通过 + diff 自检”代替 commit；若用户后续明确要求提交，再按 Conventional Commits 创建提交。

## 文件结构

### 修改文件

- `trail/commands/cw.py`
  - 把 `cw battle run` 的默认 `--timeout` 从 `570` 收口到 `90`。
  - 新增 `trail cw battle clear-in-progress --session <id>`。
- `trail/daemon/command_timeouts.py`
  - 作为 battle.run 默认执行预算与 transport/read timeout 的单一事实来源。
- `trail/daemon/client.py`
  - 复用 `command_timeouts.py` 的 battle.run timeout 归一与 response-timeout resolver。
- `trail/daemon/cw_service.py`
  - 新增 `cw.battle.clear_in_progress` handler。
  - battle.run service 层 timeout 归一同步改成 `90s` 默认。
- `trail/daemon/command_service.py`
  - 若 `clear-in-progress` 需要绕开 `last_result` 覆写，需在这里显式处理。
- `trail/scenes/cw/battle.py`
  - 增加 battle-run 内部续跑提示位的读写。
  - `in_battle=1` 的续跑开场若撞进无 UI / 无文字帧，允许等待而不是立刻 `CW_BATTLE_STATE_UNKNOWN`。
  - battle.run 结束时持久化 `last_battle_round`（例如 `1-4`）。
- `trail/output/rendering.py`
  - `cw.battle.run` 的所有 `status=in_progress` success 结果新增 `info next_action=cw.battle.run why=battle_flow_not_finished`。
  - 新增 `cw.battle.clear_in_progress` 的 renderer。
- `README.md`
  - battle.run 默认 timeout 改为 `90s`。
  - 追加“默认依赖 `in_progress + 重跑 battle.run` 续跑语义”的说明。
  - 明确结算页也属于 battle flow，应继续运行 `battle.run`。
- `AGENTS.md`
  - 冻结 `cw.battle.clear_in_progress` 的 renderer/字段顺序与 README/测试同步要求。
- `docs/cw-stage-reference/README.md`
  - 若存在 battle / settle 场景说明，补充“结算页继续 battle.run”的场景说明。
- `skills/trail-cw-entry/SKILL.md`
  - 明确 `battle flow = 战斗中 + 结算未收口`，以及 `in_progress` 时继续 `cw.battle.run`。
- `skills/trail-hsr/references/simple-command-surface.md`
  - 同步 battle.run 默认 `90s`、`clear-in-progress` 与 `in_progress + 重跑` 语义。
- `tests/test_cw_battle_run.py`
  - battle-run 续跑提示位、无 UI 动画续跑、`挑战结束` 结算变体、`last_battle_round` 持久化等核心行为测试。
- `tests/test_daemon_protocol.py`
  - `90s` 默认 timeout 与 `clear-in-progress` 的 command service 契约。
- `tests/test_cw_rpc_contracts.py`
  - `cw battle run` 新默认 timeout `90`、`cw battle clear-in-progress` 的 CLI/RPC 契约。
- `tests/test_output_rendering.py`
  - `in_progress` 新提示行与 `cw.battle.clear_in_progress` renderer goldens。
- `tests/test_atomic_commands.py`
  - `state.dump` / YAML 中 battle-run 相关持久化字段（包括 `last_battle_round`）的可见性与保留语义。

### 不修改文件

- `trail/scenes/cw/events.py`
  - 当前 `出战` OCR fallback 和 team-count dialog 热修已足够，不作为本次重点。
- battle in-progress 专门 skill 本体
  - 本轮只更新 README / 场景说明，不直接新建 skill 本体。

## Task 1: 默认 timeout 收口到 90 秒

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/command_timeouts.py`
- Modify: `trail/daemon/client.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写 battle.run 默认 timeout 变化的失败用例**

在 `tests/test_cw_rpc_contracts.py` 和 `tests/test_daemon_protocol.py` 追加新的默认值断言：

```python
def test_cw_battle_run_forwards_new_default_timeout(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"cw.battle.run": build_success_response(request_id="req", data={})})

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 90}, tmp_path=tmp_path)


def test_resolve_response_timeout_defaults_for_battle_run():
    assert resolve_response_timeout("cw.battle.run", {}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 90}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 45}) == 75.0
```

- [ ] **Step 2: 运行 timeout 红灯**

Run: `uv run pytest --basetemp .pytest-tmp-task1-red tests/test_cw_rpc_contracts.py::test_cw_battle_run_forwards_new_default_timeout tests/test_daemon_protocol.py::test_resolve_response_timeout_defaults_for_battle_run -v`

Expected: FAIL，当前默认值仍是 `570` / `600`。

- [ ] **Step 3: 修改 CLI / client / service 默认值**

最小改动如下：

```python
# trail/daemon/command_timeouts.py
DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS = 90


def normalize_cw_battle_run_timeout(raw_timeout: Any) -> int:
    if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool) and raw_timeout > 0:
        return raw_timeout
    return DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS
```

```python
# trail/daemon/client.py
def resolve_response_timeout(method: str, payload: dict[str, Any] | None) -> float:
    return resolve_command_response_timeout(method, payload)
```

```python
# trail/commands/cw.py
DEFAULT_CW_BATTLE_RUN_TIMEOUT = DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS
```

```python
# trail/daemon/cw_service.py
"cw.battle.run": lambda: run_cw_battle(
    session,
    runtime=runtime(),
    timeout=normalize_cw_battle_run_timeout(payload.get("timeout")),
),
```

- [ ] **Step 4: 回跑 timeout 绿灯**

Run: `uv run pytest --basetemp .pytest-tmp-task1-green tests/test_cw_rpc_contracts.py::test_cw_battle_run_forwards_new_default_timeout tests/test_daemon_protocol.py::test_resolve_response_timeout_defaults_for_battle_run -v`

Expected: PASS。

## Task 2: 内部续跑提示位与 `clear-in-progress` 命令

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/scenes/cw/battle.py`
- Modify: `tests/test_cw_battle_run.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写 battle-run 内部续跑提示位的失败用例**

在 `tests/test_cw_battle_run.py` 增加 battle_resume 生命周期红灯：

```python
def test_run_cw_battle_tolerates_unknown_opening_when_resume_hint_is_set(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["unknown", "battle_progress"], sleep_advances_from=("unknown",))

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result["status"] == "in_progress"
    assert result["in_battle"] is True


def test_run_cw_battle_sets_resume_hint_after_in_battle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["battle_progress"])

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["in_battle"] is True
    assert ensure_cw_state(session)["battle_resume"] == {"in_battle_hint": True}


def test_run_cw_battle_does_not_set_resume_hint_for_settle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "unknown"], sleep_advances_from=("unknown",))

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "settle"
    assert result["in_battle"] is False
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_clears_resume_hint_after_completed_result(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["battle_progress", "stable_stage"])

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: "shop")
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result["status"] == "completed"
    assert ensure_cw_state(session).get("battle_resume") == {}
```

- [ ] **Step 2: 先写 `clear-in-progress` 不覆盖 battle 摘要的失败用例**

在 `tests/test_daemon_protocol.py` 增加真实 `CommandService + DaemonRequest` 契约：

```python
def test_command_service_clear_in_progress_preserves_last_battle_summary(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.last_result = {
        "command": "cw.battle.run",
        "ok": True,
        "data": {"status": "in_progress", "in_battle": True, "round": "1-4"},
        "error": None,
    }
    session.last_screenshot = ".trail/shots/req-battle.png"
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}

    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None
    session_service.save_session(session)

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: None)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    payload = command_service.handle(
        DaemonRequest(
            request_id="req-clear",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.battle.clear_in_progress",
            payload={},
        )
    )

    assert payload["ok"] is True
    assert payload["data"] == {"cleared": True}
    refreshed = session_service.load_session(session.session_id)
    assert refreshed.last_result == session.last_result
    assert refreshed.last_screenshot == session.last_screenshot
    assert ensure_cw_state(refreshed).get("stage") == {"stale": True}
    assert refreshed.last_stage is None
    assert ensure_cw_state(refreshed).get("battle_resume") == {}
```

- [ ] **Step 3: 运行红灯**

Run: `uv run pytest --basetemp .pytest-tmp-task2-red tests/test_cw_battle_run.py -k "resume_hint" tests/test_daemon_protocol.py::test_command_service_clear_in_progress_preserves_last_battle_summary -v`

Expected: FAIL。

- [ ] **Step 4: 写最小实现**

在 `battle.py` 引入窄提示位 helper：

```python
def _battle_resume_state(session: SessionModel) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    resume = cw_state.get("battle_resume")
    if isinstance(resume, dict):
        return resume
    resume = {}
    cw_state["battle_resume"] = resume
    return resume


def _seed_resume_in_battle(session: SessionModel) -> bool:
    resume = _battle_resume_state(session)
    return bool(resume.get("in_battle_hint"))


def _store_resume_in_battle(session: SessionModel, *, enabled: bool) -> None:
    resume = _battle_resume_state(session)
    if enabled:
        resume["in_battle_hint"] = True
    else:
        resume.pop("in_battle_hint", None)
```

然后在 `run_cw_battle()` 开场用 `_seed_resume_in_battle(session)` 容忍首次 `unknown`，并在结束时：

```python
_store_resume_in_battle(session, enabled=result.get("status") == "in_progress" and result.get("in_battle") is True)
```

`clear-in-progress` 命令最小路由：

```python
# trail/commands/cw.py
@battle_app.command("clear-in-progress")
def cw_battle_clear_in_progress(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.battle.clear_in_progress", session_id=session, payload={})
```

```python
# trail/daemon/cw_service.py
"cw.battle.clear_in_progress": lambda: _clear_cw_battle_resume_hint(session),


def _clear_cw_battle_resume_hint(session) -> dict:
    resume = ensure_cw_state(session).get("battle_resume")
    had_hint = isinstance(resume, dict) and bool(resume.get("in_battle_hint"))
    if isinstance(resume, dict):
        resume.pop("in_battle_hint", None)
    return {"cleared": had_hint}
```

`cw.battle.clear_in_progress` 的实现路径必须在 `command_service.py` 中锁死：不要加入 `CW_MUTATING_METHODS`，而是走“修改 session 后保存并直接返回 success envelope”的非 mutation 路径，避免 `finish_mutation()` 覆写 `last_result/last_screenshot`。

- [ ] **Step 5: 回跑绿灯**

Run: `uv run pytest --basetemp .pytest-tmp-task2-green tests/test_cw_battle_run.py -k "resume_hint" tests/test_daemon_protocol.py::test_command_service_clear_in_progress_preserves_last_battle_summary -v`

Expected: PASS。

## Task 3: battle 阶段号持久化

**Files:**
- Modify: `trail/scenes/cw/battle.py`
- Modify: `tests/test_cw_battle_run.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写 battle 阶段号持久化失败用例**

```python
def test_run_cw_battle_persists_last_battle_round(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "stable_stage"])

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: "shop")
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

    assert result["round"] == "1-1"
    assert ensure_cw_state(session)["metrics"]["last_battle_round"] == "1-1"


def test_run_cw_battle_persists_last_battle_round_for_settle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "unknown"], sleep_advances_from=("unknown",))

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "settle"
    assert ensure_cw_state(session)["metrics"]["last_battle_round"] == "1-1"
    assert ensure_cw_state(session)["stage"] == {"stale": True}
    assert session.last_stage is None
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest --basetemp .pytest-tmp-task3-red tests/test_cw_battle_run.py -k "last_battle_round" -v`

Expected: FAIL。

- [ ] **Step 3: 在 `battle.py` 最小持久化 `last_battle_round`**

```python
def _persist_battle_round(session: SessionModel, summary: dict[str, object]) -> None:
    battle_round = summary.get("round")
    if isinstance(battle_round, str) and battle_round:
        ensure_cw_state(session).setdefault("metrics", {})["last_battle_round"] = battle_round
```

并在所有 battle.run 结束路径（completed / in_progress / failure 前已拿到 summary 时）调用这个 helper。

同时补 `state.dump` / YAML 可见性：

```python
def test_state_dump_yaml_includes_last_battle_round(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({
        "state.dump": build_success_response(
            request_id="req-state-dump-round",
            data={
                "session_id": "session-1",
                "scene_state": {"cw": {"metrics": {"last_battle_round": "1-1"}}},
            },
        )
    })
    result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", "session-1"])

    assert result.exit_code == 0
    assert "last_battle_round: 1-1" in result.stdout
```

- [ ] **Step 4: 回跑绿灯**

Run: `uv run pytest --basetemp .pytest-tmp-task3-green tests/test_cw_battle_run.py -k "last_battle_round" tests/test_atomic_commands.py::test_state_dump_yaml_includes_last_battle_round -v`

Expected: PASS。

## Task 4: `in_progress` 文本提示与 `clear-in-progress` renderer

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写 renderer 与 CLI 文本失败用例**

```python
def test_render_output_cw_battle_run_in_progress_adds_next_action_hint():
    payload = {
        "ok": True,
        "data": {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": 90},
        "screenshot": ".trail/shots/req.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req.png",
        "info read_image_first=1",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_run_settle_in_progress_adds_next_action_hint():
    payload = {
        "ok": True,
        "data": {"status": "in_progress", "stage": "settle", "stale": True, "in_battle": False, "timeout_seconds": 90},
        "screenshot": ".trail/shots/req-settle.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress stage=settle stale=1 in_battle=0",
        "shot path=.trail/shots/req-settle.png",
        "info read_image_first=1",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_clear_in_progress_summary():
    payload = {"ok": True, "data": {"cleared": False}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}
    assert render_output("cw.battle.clear_in_progress", payload).splitlines() == [
        "ok cw.battle.clear_in_progress cleared=0"
    ]


def test_cw_battle_clear_in_progress_rejects_yaml_output(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"cw.battle.clear_in_progress": build_success_response(request_id="req-clear", data={"cleared": False})})
    result = cli_runner.invoke(app, ["--format", "yaml", "cw", "battle", "clear-in-progress", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.battle.clear_in_progress code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.battle.clear_in_progress"',
    ]
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest --basetemp .pytest-tmp-task4-red tests/test_output_rendering.py -k "clear_in_progress or next_action=cw.battle.run" tests/test_cw_rpc_contracts.py -k "cw_battle_clear_in_progress or cw_battle_run_renders_timeout_summary" -v`

Expected: FAIL。

- [ ] **Step 3: 最小实现 renderer 和 CLI 断言**

```python
def _render_cw_battle_run(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    _append_fact_line(lines, "info", ("timeout_seconds", data.get("timeout_seconds")))
    if data.get("status") == "in_progress":
        _append_fact_line(lines, "info", ("next_action", command), ("why", "battle_flow_not_finished"))


def _render_cw_battle_clear_in_progress(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(command, payload, ("cleared", bool(data.get("cleared")) if "cleared" in data else None))
```

并在 `TEXT_RENDERERS` 中注册 `cw.battle.clear_in_progress`。

同时补 CLI / RPC 契约：

```python
def test_cw_battle_clear_in_progress_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"cw.battle.clear_in_progress": build_success_response(request_id="req-clear", data={"cleared": False})})
    result = cli_runner.invoke(app, ["cw", "battle", "clear-in-progress", "--session", SESSION_ID])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.clear_in_progress", payload={}, tmp_path=tmp_path)


def test_cw_battle_clear_in_progress_renders_summary(cli_runner, fake_daemon_client, tmp_path):
    fake_daemon_client({"cw.battle.clear_in_progress": build_success_response(request_id="req-clear", data={"cleared": True})})
    result = cli_runner.invoke(app, ["cw", "battle", "clear-in-progress", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.battle.clear_in_progress cleared=1"]
```

- [ ] **Step 4: 回跑绿灯**

Run: `uv run pytest --basetemp .pytest-tmp-task4-green tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -k "cw_battle_run or cw_battle_clear_in_progress" -v`

Expected: PASS。

## Task 5: README / 场景说明同步

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/cw-stage-reference/README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-hsr/references/simple-command-surface.md`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写文档断言失败用例**

```python
def test_readme_documents_battle_run_short_timeout_and_resume_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    stage_reference = (PROJECT_ROOT / "docs" / "cw-stage-reference" / "README.md").read_text(encoding="utf-8")
    entry_skill = (PROJECT_ROOT / "skills" / "trail-cw-entry" / "SKILL.md").read_text(encoding="utf-8")
    simple_surface = (PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "simple-command-surface.md").read_text(encoding="utf-8")

    assert "默认 timeout 现在是 `90s`" in readme
    assert "结算页也属于 battle flow" in readme
    assert "info next_action=cw.battle.run why=battle_flow_not_finished" in readme
    assert "trail cw battle clear-in-progress --session <id>" in readme
    assert "cw.battle.clear_in_progress" in agents
    assert "不加入 YAML allowlist" in agents
    assert "结算页也属于 battle flow" in stage_reference
    assert "仍在 battle flow 中就继续运行 `trail cw battle run --session <id>`" in entry_skill
    assert "battle in-progress" in simple_surface
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest --basetemp .pytest-tmp-task5-red tests/test_output_rendering.py -k "battle_run_short_timeout_and_resume_contract" -v`

Expected: FAIL。

- [ ] **Step 3: 更新 README / AGENTS / 场景说明**

文档至少要补：

```md
- `trail cw battle run` 默认 timeout 改为 `90s`
- `status=in_progress` 时，先看截图；若仍在 battle flow 中，继续运行 `trail cw battle run --session <id>`
- battle flow 明确包含结算页与结算翻页
- `trail cw battle clear-in-progress --session <id>` 只清 battle.run 的内部续跑提示位
- battle in-progress 这次只补场景/命令说明，不实现 skill 本体
```

- [ ] **Step 4: 回跑绿灯**

Run: `uv run pytest --basetemp .pytest-tmp-task5-green tests/test_output_rendering.py -k "battle_run_short_timeout_and_resume_contract or help_boundaries" -v`

Expected: PASS。

## 最终验证

- [ ] **Step 1: 跑 battle.run 相关聚焦回归**

Run: `uv run pytest --basetemp .pytest-tmp-final-targeted tests/test_cw_battle_run.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py -k "cw_battle_run or clear_in_progress or battle_flow_not_finished or last_battle_round or resolve_response_timeout" -v`

Expected: PASS。

- [ ] **Step 2: 跑一轮更宽的 CW 文档与命令回归**

Run: `uv run pytest --basetemp .pytest-tmp-final-regression tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py -k "cw_battle or cw_settle or cw_stage or help_boundaries or state_dump" -v && uv run pytest --basetemp .pytest-tmp-final-render-full tests/test_output_rendering.py -v`

Expected: PASS。

- [ ] **Step 3: 自检差异边界**

Run: `rtk git status --short`

Expected: 只看到 battle.run 续跑、`clear-in-progress`、文档同步和对应测试变更。 
