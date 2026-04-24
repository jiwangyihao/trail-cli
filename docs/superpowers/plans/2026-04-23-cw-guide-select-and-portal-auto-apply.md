# CW Guide Select And Portal Auto Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `cw.guide.apply` 拆成“显式选择当前攻略”和“在 `cw.portal.select` 后自动应用当前攻略”两段链路，同时把 `cw.guide.current` / 相关读命令收口到 session 当前已选攻略语义。

**Architecture:** 先在 `trail.scenes.cw.guide` 与 `trail.daemon.cw_service` 收口“当前已选攻略”的严格 session helper，去掉普通 `guide.fetch.cw` artifact 对 current-guide 读路径的隐式恢复；再把 `guide.fetch.cw --select` 接到 `trail.daemon.command_service` 的 mutation-aware 分支，只写 session 不做 UI。最后把 `cw.guide.apply` 改成消费当前已选攻略的手动兜底入口，并让 `cw.portal.select` 在 portal 点击后自动执行同一条 UI apply helper，README / skills / AGENTS / help / tests 全面同步。实施前先在仓库旁创建独立 git worktree，并在该 worktree 内按任务顺序执行。

**Tech Stack:** Python 3.12, Typer, daemon RPC, `trail.scenes.cw.*`, `trail.daemon.*`, `trail.output.rendering`, pytest, `uv run pytest`, git worktree

---

## File Structure

### Create
- `docs/superpowers/plans/2026-04-23-cw-guide-select-and-portal-auto-apply.md`
  - 当前实现计划文档。

### Modify
- `trail/scenes/cw/guide.py`
  - 抽出“写当前已选攻略但不 reset 运行态”的 helper，以及“UI apply 成功后失效运行态”的 helper。
- `trail/daemon/cw_service.py`
  - 用 strict session-only helper 取代 current-guide artifact fallback；改写 `cw.guide.current`、`cw.guide.apply`、`cw.portal.select`、`cw.shop.status`，并确认 `cw.strategy.detect|refresh` 的 loaded-guide 语义继续只看 session 当前 guide。
- `trail/daemon/command_service.py`
  - 为 `guide.fetch.cw --select` 增加 mutation-aware 分支；保持普通 `guide.fetch.cw` 只读路径不变。
- `trail/commands/guide.py`
  - 给 `guide fetch cw` 增加 `--select` / `--session` 参数矩阵，并把 `session_id` 放到 daemon 顶层请求。
- `trail/commands/cw.py`
  - 去掉 `cw guide apply` 的 `--guide` / `--lineup-id` 参数，更新 `cw guide` help 文案。
- `README.md`
  - 更新推荐命令链、`cw.guide.current|apply` 语义、`guide.fetch.cw` 的 preview/select 边界。
- `AGENTS.md`
  - 把 `cw.guide.current|apply` 的术语从“已应用攻略”调整为“当前已选攻略”，并保留输出字段冻结约束。
- `skills/trail-cw-guide/SKILL.md`
  - 明确“先 `guide.fetch.cw --select`，再走 `cw.portal.select` 自动 apply”的职责边界。
- `skills/trail-cw-guide/references/command-surface.md`
  - 同步命令面：`guide.fetch.cw --select`、`cw.guide.current`、`cw.guide.apply` 的新语义。
- `skills/trail-cw-guide/references/confirmation-checklist.md`
  - 把攻略确认 checklist 的“apply”时机移动到 portal select 之后。
- `skills/trail-cw-entry/SKILL.md`
  - 明确开局入口 skill 在 portal select 成功后会自动兑现当前已选攻略。

### Test
- `tests/test_cw_guide.py`
  - 锁定 selected guide helper、strict current-guide 读取、无 implicit artifact recovery 的行为。
- `tests/test_guide_rpc_contracts.py`
  - 锁定 `guide.fetch.cw --select` CLI / daemon 契约与 YAML shape 不扩张。
- `tests/test_cw_rpc_contracts.py`
  - 锁定 `cw.guide.apply` 新签名与 `cw.guide.current|apply` 输出语义。
- `tests/test_cw_portal.py`
  - 锁定 `cw.portal.select` 无 guide 时 fail-before-click、有 guide 时 auto apply 的行为。
- `tests/test_daemon_protocol.py`
  - 锁定 `guide.fetch.cw --select` request tracking，以及 `cw.portal.select` post-click failure 的 recover 语义。
- `tests/test_output_rendering.py`
  - 锁定 `cw.guide.current|apply` summary family 不变、`guide.fetch.cw --select` 文本/YAML 不扩张。
- `tests/test_atomic_commands.py`
  - 锁定 `cw guide` / `guide fetch cw` help 文案更新与 legacy 参数移除。
- `tests/test_output_debug.py`
  - 锁定 `AGENTS.md` 内关于 `cw.guide.current|apply` 的文档约束。
- `tests/test_cw_strategy.py`
  - 锁定 strategy detect/refresh 在无 selected guide 时继续表现为 `guide_loaded=0`，且不依赖 plain fetch artifact recovery。
- `tests/test_skill_structure.py`
  - 锁定 `trail-cw-guide`、`trail-cw-entry` 的 active 文档叙述与入口边界。

## Task 1: 收口 session 当前攻略 helper，并移除 implicit artifact recovery

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_cw_strategy.py`

- [ ] **Step 1: 先写失败测试，锁定“select 只写当前攻略，不 reset 运行态”与 strict current-guide 读取语义**

```python
def test_select_cw_guide_updates_guide_without_resetting_runtime_state(tmp_path: Path):
    from trail.scenes.cw.guide import select_cw_guide
    from trail.scenes.cw.models import ensure_cw_state
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry().for_workspace(str(tmp_path))
    session = registry.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    ensure_cw_state(session).update(
        {
            "portal": {"cards": [{"card_idx": 1}], "stale": False},
            "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
            "slots": {"stale": False, "hand": ["银狼"]},
            "sell_plan": {"candidates": [0]},
            "stage": {"stale": False, "name": "shop"},
        }
    )

    select_cw_guide(
        session,
        guide_data={
            "artifact_id": "art-selected",
            "lineup_id": "abc",
            "share_code": "##demo##",
            "on_field": {"希儿": 9},
            "off_field": {"佩拉": 3},
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 9,
        },
    )

    cw_state = ensure_cw_state(session)
    assert cw_state["guide"]["artifact"] == "art-selected"
    assert cw_state["constraints"] == {"min_coins": 40, "min_level": 7, "mid_level": 9, "priority": {}, "positioning": {}}
    assert cw_state["portal"] == {"cards": [{"card_idx": 1}], "stale": False}
    assert cw_state["shop"] == {"opened": True, "stale": False, "items": [{"name": "希儿"}]}
    assert cw_state["slots"] == {"stale": False, "hand": ["银狼"]}
    assert cw_state["sell_plan"] == {"candidates": [0]}
    assert cw_state["stage"] == {"stale": False, "name": "shop"}


def test_cw_guide_current_requires_selected_guide_in_session(tmp_path: Path):
    registry, service, session, cw_service, _ = _build_cw_harness(tmp_path)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert exc_info.value.code == "CW_GUIDE_SELECTION_REQUIRED"


def test_cw_shop_status_does_not_recover_guide_summary_from_plain_fetch_artifact(tmp_path: Path):
    registry, service, session, cw_service, _ = _build_cw_harness(tmp_path)
    ArtifactStore(tmp_path / ".trail" / "artifacts").create(
        scene="cw",
        kind="guide",
        payload={**fake_guide(), "lineup_id": "preview-only", "share_code": "##preview##", "recovery_origin": "guide.fetch.cw"},
    )

    payload = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert "guide_summary" not in payload


def test_detect_cw_strategy_keeps_guide_loaded_zero_when_session_guide_missing(tmp_path: Path):
    import trail.scenes.cw.strategy as strategy_module
    from trail.artifacts.store import ArtifactStore
    from trail.daemon.session_service import SessionServiceRegistry
    from tests.test_cw_strategy import StrategyRuntime

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    ArtifactStore(tmp_path / ".trail" / "artifacts").create(
        scene="cw",
        kind="guide",
        payload={
            "lineup_id": "preview-only",
            "share_code": "##preview##",
            "on_field": {"希儿": 9},
            "off_field": {"佩拉": 3},
            "first_fight_augments": ["快攻"],
            "second_fight_augments": ["回蓝"],
            "recovery_origin": "guide.fetch.cw",
        },
    )
    runtime = StrategyRuntime(
        ocr_results=[
            [
                {"text": "快攻", "center_x": 320, "center_y": 420, "left": 260, "top": 400, "width": 120, "height": 32},
                {"text": "回蓝", "center_x": 960, "center_y": 420, "left": 900, "top": 400, "width": 120, "height": 32},
                {"text": "保守", "center_x": 1600, "center_y": 420, "left": 1540, "top": 400, "width": 120, "height": 32},
            ]
        ]
    )

    snapshot = strategy_module.detect_cw_strategy(session, runtime=runtime, strategy_list=[{"name": "快攻"}, {"name": "回蓝"}, {"name": "保守"}])

    assert [card["guide_loaded"] for card in snapshot["cards"]] == [0, 0, 0]
```

- [ ] **Step 2: 运行这组用例，确认当前实现还会 reset 运行态并继续 artifact recovery**

Run:
`uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py -k "select_cw_guide or guide_current_requires_selected_guide or does_not_recover_guide_summary or keeps_guide_loaded_zero" -q --basetemp .trail/pytest-temp-cw-guide-select-state-red -p no:cacheprovider`

Expected:
- FAIL，至少会出现 `ImportError: cannot import name 'select_cw_guide'`、`cw.guide.current` 仍从 artifact 恢复、或 `guide_summary` 仍被补回。

- [ ] **Step 3: 写最小实现，新增 no-reset selected-guide helper 与 runtime invalidation helper，并让 current-guide 读取改成 strict session-only**

```python
def select_cw_guide(session: SessionModel, *, guide_data: dict) -> SessionModel:
    # 只更新当前攻略与约束，不清空 portal/shop/slots/stage 等运行态。
    refreshed = apply_cw_guide(session, guide_data=guide_data, reset_dependent_state=False)
    return refreshed


def invalidate_cw_guide_runtime_state(session: SessionModel) -> SessionModel:
    defaults = CwSceneState().model_dump()
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = defaults["slots"]
    cw_state["sell_plan"] = defaults["sell_plan"]
    cw_state["shop"] = defaults["shop"]
    cw_state["stage"] = defaults["stage"]
    return session


def _require_selected_guide(session) -> dict:
    guide_state = session.scene_state.get("cw", {}).get("guide")
    if not isinstance(guide_state, dict):
        raise TrailError(
            "CW_GUIDE_SELECTION_REQUIRED",
            "cw current guide is empty; run guide.fetch.cw --select first",
        )
    share_code = guide_state.get("share_code")
    if not isinstance(share_code, str) or not share_code:
        raise TrailError("CW_GUIDE_STATE_INVALID", "current cw guide missing share_code")
    return guide_state


def _guide_summary_or_none(session) -> dict | None:
    guide_state = session.scene_state.get("cw", {}).get("guide")
    return guide_state if isinstance(guide_state, dict) else None


def _current_guide(session, *, artifact_store: ArtifactStore):
    return _require_selected_guide(session)


def _shop_status(session, *, artifact_store: ArtifactStore) -> dict:
    payload = shop_cw_status(session)
    guide_state = _guide_summary_or_none(session)
    if guide_state is None:
        payload.pop("guide_summary", None)
    return payload
```

- [ ] **Step 4: 补一个失败测试，锁定 apply 成功后才 reset 运行态，而不是 select 时 reset**

```python
def test_invalidate_cw_guide_runtime_state_resets_runtime_dependent_slices_only(tmp_path: Path):
    from trail.scenes.cw.guide import invalidate_cw_guide_runtime_state
    from trail.scenes.cw.models import ensure_cw_state
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry().for_workspace(str(tmp_path))
    session = registry.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    ensure_cw_state(session).update(
        {
            "guide": {"lineup_id": "abc", "share_code": "##demo##"},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9, "priority": {}, "positioning": {}},
            "slots": {"stale": False, "hand": ["银狼"]},
            "sell_plan": {"candidates": [0]},
            "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
            "stage": {"stale": False, "name": "shop"},
        }
    )

    invalidate_cw_guide_runtime_state(session)

    cw_state = ensure_cw_state(session)
    assert cw_state["guide"] == {"lineup_id": "abc", "share_code": "##demo##"}
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["slots"]["stale"] is True
    assert cw_state["shop"]["stale"] is True
    assert cw_state["stage"]["stale"] is True
```

- [ ] **Step 5: 跑 `tests/test_cw_guide.py` 的目标子集，确认 selected-guide 基线通过**

Run:
`uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py -k "select_cw_guide or invalidate_cw_guide_runtime_state or guide_current_requires_selected_guide or does_not_recover_guide_summary or keeps_guide_loaded_zero" -q --basetemp .trail/pytest-temp-cw-guide-select-state-green -p no:cacheprovider`

Expected:
- PASS，以上 selected-guide 语义相关用例全部通过。

- [ ] **Step 6: 提交这一层基础状态语义**

```bash
git add trail/scenes/cw/guide.py trail/daemon/cw_service.py tests/test_cw_guide.py tests/test_cw_strategy.py
git commit -m "refactor(cw): 收口当前攻略 session 语义"
```

## Task 2: 接入 `guide.fetch.cw --select` 的 CLI 参数矩阵与 mutation-aware daemon 分支

**Files:**
- Modify: `trail/commands/guide.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/scenes/cw/guide.py`
- Test: `tests/test_guide_rpc_contracts.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，锁定 CLI 参数矩阵、top-level `session_id` 透传，以及 `--select` 不扩张 success data/YAML**

```python
def test_guide_fetch_select_requires_session_and_rejects_session_without_select(cli_runner):
    missing_session = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--select"])
    stray_session = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--session", "sess-1"])

    assert missing_session.exit_code == 0
    assert missing_session.stdout.splitlines()[0] == "fail guide.fetch.cw code=GUIDE_INPUT_INVALID"
    assert stray_session.exit_code == 0
    assert stray_session.stdout.splitlines()[0] == "fail guide.fetch.cw code=GUIDE_INPUT_INVALID"


def test_guide_fetch_select_passes_top_level_session_and_keeps_yaml_shape(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "guide.fetch.cw": build_success_response(
                request_id="req-guide-fetch-select",
                data={"lineup_id": "abc", "share_code": "##demo##", "title": "7群攻2银河学者"},
            )
        }
    )

    text_result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--select", "--session", "sess-1"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "fetch", "cw", "abc", "--select", "--session", "sess-1"])

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert client.calls == [
        {"method": "guide.fetch.cw", "payload": {"url": "abc", "select": True}, "workspace_root": str(tmp_path), "session_id": "sess-1", "verbose": False},
        {"method": "guide.fetch.cw", "payload": {"url": "abc", "select": True}, "workspace_root": str(tmp_path), "session_id": "sess-1", "verbose": False},
    ]
    assert "selected:" not in yaml_result.stdout
    assert "session_id:" not in yaml_result.stdout


def test_command_service_handles_guide_fetch_cw_select_and_persists_selected_guide(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    selected: list[dict[str, object]] = []

    def fake_fetch_payload(url: str):
        return {"lineup_id": url, "share_code": "##demo##"}

    def fake_select_cw_guide(session, *, guide_data: dict):
        selected.append(guide_data)
        return session

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_payload", fake_fetch_payload)
    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide", lambda url, *, fetcher: fetcher(url))
    monkeypatch.setattr("trail.scenes.cw.guide.select_cw_guide", fake_select_cw_guide)

    session_services = SessionServiceRegistry()
    session = session_services.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=session_services)
    response = command_service.handle(
        DaemonRequest(
            request_id="req-guide.fetch.cw-select",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
        )
    )

    assert response["data"] == {"lineup_id": "abc", "share_code": "##demo##"}
    assert selected[0]["lineup_id"] == "abc"


def test_command_service_routes_guide_fetch_select_through_run_mutation(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService, success

    routed: list[dict[str, object]] = []
    service = CommandService(runtime_service=SimpleNamespace())

    monkeypatch.setattr(
        service,
        "_run_mutation",
        lambda request, command_name, handler, **kwargs: routed.append(
            {
                "method": request.method,
                "command_name": command_name,
                "session_id": request.session_id,
                "enforce_cw_tainted": kwargs.get("enforce_cw_tainted"),
                "tainted_session_id": kwargs.get("tainted_session_id"),
            }
        )
        or success({"lineup_id": "abc", "share_code": "##demo##"}),
    )

    response = service.handle(
        DaemonRequest(
            request_id="req-guide.fetch.cw-select-route",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id="sess-1",
            verbose=False,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
        )
    )

    assert response["ok"] is True
    assert routed == [
        {
            "method": "guide.fetch.cw",
            "command_name": "guide.fetch.cw",
            "session_id": "sess-1",
            "enforce_cw_tainted": True,
            "tainted_session_id": "sess-1",
        }
    ]


def test_command_service_rejects_guide_fetch_select_without_top_level_session_id(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    response = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry()).handle(
        DaemonRequest(
            request_id="req-guide.fetch.cw-select-missing-session",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_command_service_rejects_guide_fetch_select_when_session_is_tainted(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    session_services = SessionServiceRegistry()
    workspace_service = session_services.for_workspace(str(tmp_path))
    session = workspace_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = workspace_service.load_session(session.session_id)
    loaded.scene_state.setdefault("daemon", {})["tainted"] = True
    workspace_service.save_session(loaded)

    response = CommandService(runtime_service=SimpleNamespace(), session_service=session_services).handle(
        DaemonRequest(
            request_id="req-guide.fetch.cw-select-tainted",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "SESSION_RECONCILE_REQUIRED"
```

- [ ] **Step 2: 跑 `guide.fetch.cw --select` 相关契约测试，确认现在还没有 select 分支与参数校验**

Run:
`uv run pytest tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py -k "guide_fetch_select or handles_guide_fetch_cw_select or routes_guide_fetch_select_through_run_mutation or missing_top_level_session_id or session_is_tainted" -q --basetemp .trail/pytest-temp-guide-fetch-select-red -p no:cacheprovider`

Expected:
- FAIL，至少会出现 Typer 不认识 `--select` / `--session`、或 `CommandService` 仍只走普通 fetch 路径、不写 session。

- [ ] **Step 3: 写最小实现，接上 CLI 参数矩阵与 mutation-aware `guide.fetch.cw --select` 分支**

```python
@guide_app.command("fetch")
def guide_fetch(
    scene: str,
    url: str,
    select: bool = typer.Option(False, "--select", help="把这份攻略写成当前已选攻略；只在 cw 场景可用。"),
    session: str | None = typer.Option(None, "--session", help="只在 --select 时必填；用于写入当前 session 的已选攻略。"),
) -> None:
    if not _require_cw_scene(scene):
        print_output(f"guide.fetch.{scene}", _unsupported_scene_response(scene))
        return
    if select and not session:
        print_output(f"guide.fetch.{scene}", _guide_input_invalid_response("guide fetch cw --select requires --session"))
        return
    if session and not select:
        print_output(f"guide.fetch.{scene}", _guide_input_invalid_response("guide fetch cw --session requires --select"))
        return
    payload = {"url": url, "select": True} if select else {"url": url}
    print_output(
        f"guide.fetch.{scene}",
        call_daemon(f"guide.fetch.{scene}", payload, session_id=session if select else None),
    )


def _handle_guide_fetch_select(self, request, session_service):
    if not isinstance(request.session_id, str) or not request.session_id:
        raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw --select requires session_id")
    from trail.scenes.cw import guide as cw_guide

    guide_payload = to_jsonable(cw_guide.fetch_cw_guide(request.payload["url"], fetcher=cw_guide.fetch_cw_guide_payload))
    artifact = ArtifactStore(Path(request.workspace_root) / ".trail" / "artifacts").create(scene="cw", kind="guide", payload={**guide_payload, "recovery_origin": "guide.fetch.cw"})
    session = session_service.load_session(request.session_id)
    cw_guide.select_cw_guide(session, guide_data={**guide_payload, "artifact_id": artifact.artifact_id})
    session_service.save_session(session)
    return success(guide_payload)


if request.method.startswith("guide.fetch."):
    if request.payload.get("select"):
        return self._run_mutation(
            request,
            request.method,
            lambda service: self._handle_guide_fetch_select(request, service),
            handler_persisted_state=True,
            response_builder=lambda envelope: envelope,
            enforce_cw_tainted=bool(request.session_id),
            tainted_session_id=request.session_id if isinstance(request.session_id, str) else None,
        )
    return self._handle_guide_fetch(request)
```

- [ ] **Step 4: 跑 `tests/test_guide_rpc_contracts.py`，确认 select 分支与 YAML 契约通过**

Run:
`uv run pytest tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py -k "guide_fetch" -q --basetemp .trail/pytest-temp-guide-fetch-select-green -p no:cacheprovider`

Expected:
- PASS，普通 `guide.fetch.cw` 仍然无需 session，`--select` 路径成功写 session 且文本/YAML 形状不扩张。

- [ ] **Step 5: 提交命令与 daemon 选择路径**

```bash
git add trail/commands/guide.py trail/daemon/command_service.py trail/scenes/cw/guide.py tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py
git commit -m "feat(guide): 支持 fetch select 记录当前攻略"
```

## Task 3: 改写 `cw.guide.apply` 与 `cw.portal.select`，统一消费当前已选攻略

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/scenes/cw/guide.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_cw_portal.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_cw_guide.py`

- [ ] **Step 1: 先写失败测试，锁定 `cw.guide.apply` 新签名、portal select fail-before-click 与 auto apply 行为**

```python
def test_cw_guide_apply_cli_requires_only_session(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {"cw.guide.apply": build_success_response(request_id="req-cw-guide-apply", data={"lineup_id": "abc", "share_code": "##demo##"})}
    )

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", "sess-1"])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.guide.apply", payload={}, tmp_path=tmp_path)


def test_cw_guide_apply_direct_rpc_rejects_legacy_lineup_payload(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    session_services = SessionServiceRegistry()
    session = session_services.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **_: object()))
    response = CommandService(
        runtime_service=SimpleNamespace(get_runtime=lambda **_: object()),
        session_service=session_services,
        cw_service=cw_service,
    ).handle(
        DaemonRequest(
            request_id="req-cw-guide-apply-legacy",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.guide.apply",
            payload={"session_id": session.session_id, "lineup_id": "abc"},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "CW_GUIDE_APPLY_ARGS_NOT_SUPPORTED"


def test_cw_portal_select_requires_selected_guide_before_click(tmp_path: Path):
    from trail.daemon.cw_service import CwService

    registry, service, session, _, _ = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "mode": "new", "difficulty": "normal", "battle_mode": "standard", "stale": False},
        "guide": None,
    }
    service.save_session(loaded)

    with pytest.raises(TrailError) as exc_info:
        CwService(runtime_service=SimpleNamespace(get_runtime=lambda **_: SimpleNamespace(click_point=lambda *args: None))).handle(
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert exc_info.value.code == "CW_GUIDE_SELECTION_REQUIRED"


def test_cw_portal_select_auto_applies_selected_guide(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    applied: list[str] = []
    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, *, card_idx, runtime: {"card_idx": card_idx, "portal_title": "商店"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, *, share_code: applied.append(share_code))

    registry, service, session, _, _ = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": {"lineup_id": "abc", "share_code": "##demo##"},
        "portal": {
            "cards": [
                {"card_idx": 1, "portal_title": "事件"},
                {"card_idx": 2, "portal_title": "商店"},
            ],
            "mode": "new",
            "difficulty": "normal",
            "battle_mode": "standard",
            "stale": False,
        },
    }
    service.save_session(loaded)

    selected = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **_: object())).handle(
        method="cw.portal.select",
        payload={"session_id": session.session_id, "card_idx": 2},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert selected["card_idx"] == 2
    assert applied == ["##demo##"]


def test_cw_portal_select_post_click_failure_uses_recoverable_mutation_envelope(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, *, card_idx, runtime: {"card_idx": card_idx, "portal_title": "商店"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, *, share_code: (_ for _ in ()).throw(RuntimeError("apply failed")))
    session_services = SessionServiceRegistry()
    service = session_services.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": {"lineup_id": "abc", "share_code": "##demo##"},
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "mode": "new", "difficulty": "normal", "battle_mode": "standard", "stale": False},
    }
    service.save_session(loaded)

    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **_: object()))
    command_service = CommandService(
        runtime_service=SimpleNamespace(get_runtime=lambda **_: object()),
        session_service=session_services,
        cw_service=cw_service,
    )
    response = command_service.handle(
        DaemonRequest(
            request_id="req-portal-select-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert response["request_id"]
    status = command_service.handle(
        DaemonRequest(
            request_id="req-daemon-request-status",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="daemon.request_status",
            payload={"request_id": response["request_id"]},
        )
    )
    assert status["ok"] is True
    assert status["data"]["tainted"] is True
    assert status["data"]["final_state"] == "applied_but_not_persisted"
    assert status["data"]["last_visible_stage"] in {"side_effect_applied", "state_persisted"}
    blocked = command_service.handle(
        DaemonRequest(
            request_id="req-cw-guide-apply-blocked-after-taint",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
        )
    )
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "SESSION_RECONCILE_REQUIRED"
```

- [ ] **Step 2: 运行 portal/apply 契约测试，确认当前 CLI 还要求 `--guide`，portal select 也还不会 auto apply**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_cw_portal.py tests/test_daemon_protocol.py -k "guide_apply or portal_select or legacy_lineup_payload" -q --basetemp .trail/pytest-temp-cw-guide-apply-portal-red -p no:cacheprovider`

Expected:
- FAIL，至少会出现 `cw guide apply` 仍要求 `--guide` / `--lineup-id`，或 `cw.portal.select` 缺 guide 时已发生点击、副作用，或根本没有 auto apply。

- [ ] **Step 3: 写最小实现，删掉 `cw.guide.apply` 的 legacy 入口，并让 `cw.portal.select` / `cw.guide.apply` 共享 selected-guide apply helper**

```python
@cw_guide_app.command("apply")
def cw_guide_apply(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.guide.apply", session_id=session)


CW_GUIDE_HELP = "查看或手动应用当前对局已选攻略。先用 guide.fetch.cw --select 记录当前攻略；portal select 成功后会自动应用。当前攻略摘要会输出 攻略ID、攻略标题、攻略码、版本、攻略标签 与 攻略快照ID。"


def _validate_cw_guide_apply_payload(payload: dict) -> None:
    if any(key in payload for key in ("lineup_id", "guide")):
        raise TrailError(
            "CW_GUIDE_APPLY_ARGS_NOT_SUPPORTED",
            "cw guide.apply no longer accepts lineup_id/guide; use guide.fetch.cw --select",
        )


def _apply_selected_guide_via_ui(session, *, runtime) -> dict:
    guide = _require_selected_guide(session)
    apply_cw_guide_via_ui(runtime, share_code=guide["share_code"])
    invalidate_cw_guide_runtime_state(session)
    return guide


def _apply_selected_guide_after_portal_select(session, *, runtime, card_idx: int) -> dict:
    guide = _require_selected_guide(session)
    selected = select_cw_portal(session, card_idx=card_idx, runtime=runtime)
    try:
        apply_cw_guide_via_ui(runtime, share_code=guide["share_code"])
        invalidate_cw_guide_runtime_state(session)
    except Exception as error:
        raise CwSideEffectAppliedError("cw.portal.select guide apply side effect already ran") from error
    return selected


handlers = {
    "cw.portal.select": lambda: _apply_selected_guide_after_portal_select(
        session,
        runtime=runtime(),
        card_idx=payload["card_idx"],
    ),
    "cw.guide.apply": lambda: (_validate_cw_guide_apply_payload(payload), _apply_selected_guide_via_ui(session, runtime=runtime()))[-1],
    "cw.guide.current": lambda: _require_selected_guide(session),
}
```

- [ ] **Step 4: 跑 `cw.guide.apply` / `cw.portal.select` 相关测试，确认 strict selected-guide apply 链路通过**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_cw_portal.py tests/test_daemon_protocol.py tests/test_cw_guide.py -k "guide_apply or portal_select or guide_current or legacy_lineup_payload" -q --basetemp .trail/pytest-temp-cw-guide-apply-portal-green -p no:cacheprovider`

Expected:
- PASS，`cw.guide.apply` 只吃 session 当前 guide，`cw.portal.select` 无 guide 时 fail-before-click，有 guide 时 auto apply，并在 post-click failure 里走 recoverable mutation 语义。

- [ ] **Step 5: 提交 apply / portal 共享链路**

```bash
git add trail/commands/cw.py trail/daemon/cw_service.py trail/scenes/cw/guide.py tests/test_cw_rpc_contracts.py tests/test_cw_portal.py tests/test_daemon_protocol.py tests/test_cw_guide.py
git commit -m "feat(cw): 在 portal select 后自动应用已选攻略"
```

## Task 4: 同步输出语义、help、README、skills 与 AGENTS 文档

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-guide/references/command-surface.md`
- Modify: `skills/trail-cw-guide/references/confirmation-checklist.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_output_debug.py`
- Test: `tests/test_skill_structure.py`

- [ ] **Step 1: 先写失败测试，锁定“当前已选攻略”术语、help 文案与 skill 边界**

```python
def test_cw_guide_help_describes_selected_guide_flow(cli_runner):
    result = cli_runner.invoke(app, ["cw", "guide", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "当前已选攻略" in normalized
    assert "guide.fetch.cw --select" in normalized
    assert "--lineup-id" not in normalized
    assert "--guide" not in normalized


def test_guide_fetch_cw_help_distinguishes_preview_and_select(cli_runner):
    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "--select" in result.output
    assert "只在 --select 时必填" in normalized
    assert "当前已选攻略" in normalized or "查看完整攻略" in normalized


def test_cw_portal_select_post_click_failure_renders_request_and_recover_lines(cli_runner, fake_daemon_client, tmp_path):
    fake_daemon_client(
        {
            "cw.portal.select": {
                "request_id": "req-portal-select-fail",
                "ok": False,
                "data": {"tainted": True},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"last_known_stage": "side_effect_applied"},
                "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
            }
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", "sess-1", "--card-idx", "1"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.portal.select code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-portal-select-fail",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-portal-select-fail",
    ]


def test_agents_documents_current_selected_guide_semantics():
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "current/apply 只看当前已选攻略摘要" in agents
    assert "已应用攻略摘要" not in agents


def test_trail_cw_guide_skill_mentions_select_then_portal_apply():
    skill_text = (PROJECT_ROOT / "skills" / "trail-cw-guide" / "SKILL.md").read_text(encoding="utf-8")

    assert "guide.fetch.cw --select" in skill_text
    assert "cw.portal.select" in skill_text
    assert "自动应用" in skill_text
```

- [ ] **Step 2: 运行文档/帮助测试，确认仓库文案仍停留在“当前已应用攻略”旧语义**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_atomic_commands.py tests/test_output_debug.py tests/test_skill_structure.py -k "guide_help or guide_fetch_cw_help or selected_guide or trail_cw_guide or post_click_failure_renders_request_and_recover" -q --basetemp .trail/pytest-temp-cw-guide-docs-red -p no:cacheprovider`

Expected:
- FAIL，至少会出现 help 仍暴露 `--guide` / `--lineup-id`、README/AGENTS/skills 仍写“已应用攻略”。

- [ ] **Step 3: 更新 README、AGENTS 与 skills 文案，统一为“先 select，再 portal auto apply，current/apply 看的是当前已选攻略”**

```markdown
- `guide.fetch.cw` 默认只负责查看完整攻略；只有 `guide.fetch.cw --select --session <id>` 才会把该攻略记为当前已选攻略。
- `cw.portal.select` 在选择投资环境后会自动应用当前已选攻略；如果还没 select，会直接失败并提示先执行 `guide.fetch.cw --select`。
- `cw.guide.current|apply` 显示的是当前已选攻略摘要；`cw.guide.apply` 只是手动兜底，不再接受 lineup id。
```

- [ ] **Step 4: 跑文档/帮助/输出测试，确认术语、help 与 skill 边界全部更新到位**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_output_debug.py tests/test_skill_structure.py -q --basetemp .trail/pytest-temp-cw-guide-docs-green -p no:cacheprovider`

Expected:
- PASS，`cw.guide.current|apply` 仍用同一 summary family，但仓库文案、help 与 skill 文档全部改成“当前已选攻略”语义。

- [ ] **Step 5: 提交文档与 help 同步**

```bash
git add README.md AGENTS.md skills/trail-cw-guide/SKILL.md skills/trail-cw-guide/references/command-surface.md skills/trail-cw-guide/references/confirmation-checklist.md skills/trail-cw-entry/SKILL.md tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_output_debug.py tests/test_skill_structure.py
git commit -m "docs(cw): 同步攻略选择与自动应用链路"
```

## Task 5: 运行受影响回归并确认交付面完整

**Files:**
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_guide_rpc_contracts.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_cw_portal.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_output_debug.py`
- Test: `tests/test_skill_structure.py`

- [ ] **Step 1: 跑完整的受影响测试矩阵，确认 selected-guide / portal auto apply / 文档同步全部通过**

Run:
`uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_cw_portal.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_output_debug.py tests/test_skill_structure.py -q --basetemp .trail/pytest-temp-cw-guide-full -p no:cacheprovider`

Expected:
- PASS，且不再出现 `guide.fetch.cw` implicit recovery、`cw.guide.apply --guide`、或 `cw.portal.select` 未自动 apply 的旧断言。

- [ ] **Step 2: 手动 spot check 三条关键命令的最终行为**

```text
trail guide fetch cw abc --select --session <id>
trail cw guide current --session <id>
trail cw portal select --session <id> --card-idx 2
```

Expected:
- 第一条：返回与普通 `guide.fetch.cw` 相同的完整攻略文本，但 session 内当前攻略已被写入。
- 第二条：返回 `cw.guide.current` 摘要，语义是“当前已选攻略”。
- 第三条：success 首行仍是 `ok cw.portal.select idx=2 投资环境=...`，内部自动完成 guide UI apply；若 apply 后失败，则走已有 recover 链。

- [ ] **Step 3: 整理最终提交说明，准备进入 subagent-driven-development 的后续批量执行/PR 流程**

```bash
git status --short
git log --oneline -3
```

Expected:
- 只有本计划覆盖的代码、测试、README、skills、AGENTS 改动。
- 最近提交消息仍保持 Conventional Commits + 简体中文 subject。
