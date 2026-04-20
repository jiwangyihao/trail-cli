# CW Portal Detect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `trail cw portal detect --session <id>`，让用户在已经手动进入投资环境页时，可以重新识别三张卡、补挂 guide 摘要，并把 fresh portal snapshot 写回 session，随后直接执行 `trail cw portal select`。

**Architecture:** 继续沿用现有 `scene -> daemon -> CLI/renderer` 分层。scene 层只负责稳定 `invest` 页面上的 raw snapshot 采样与 session 基础回写，daemon 层负责 capture 路由、guide enrich 和最终响应组装，CLI 与 renderer 只做薄壳与文本协议输出。实现时优先复用现有 `cw.portal.refresh` / `cw.start` 的卡片与 guide 组装逻辑，避免字段形状漂移。

**Tech Stack:** Python 3.12, Typer, daemon RPC, `trail.scenes.cw.*`, `trail.daemon.*`, `trail.output.rendering`, pytest

---

## File Structure

### Create
- `docs/superpowers/specs/2026-04-20-cw-portal-detect-design.md`
  - 已完成；作为 implementation plan 的唯一 spec 输入。
- `docs/superpowers/plans/2026-04-20-cw-portal-detect.md`
  - 当前计划文档。

### Modify
- `trail/scenes/cw/portal.py`
  - 抽取“稳定 invest 页面 -> raw snapshot”的共享 helper，并新增 `detect_cw_portal`。
- `trail/daemon/cw_service.py`
  - 新增 `cw.portal.detect` handler，并复用现有 guide enrich 逻辑。
- `trail/daemon/command_service.py`
  - 为 `cw.portal.detect` 增加 capture 路由，不把它放进 mutation 路径。
- `trail/commands/cw.py`
  - 增加 `trail cw portal detect --session <id>` 与 help 文案。
- `trail/output/rendering.py`
  - 把 `cw.portal.detect` 接到 `_render_cw_portal_cards`，并锁住 YAML reject。
- `tests/test_cw_portal.py`
  - scene/helper 级 detect、refresh 回归、restart truth 边界测试。
- `tests/test_daemon_protocol.py`
  - detect capture 路由、guide failure 退化成功、非 mutation 语义测试。
- `tests/test_cw_rpc_contracts.py`
  - CLI wrapper / canonical command / stdout contract 测试。
- `tests/test_output_rendering.py`
  - detect renderer golden、YAML reject、failure 文本约束、README/skill 文案断言。
- `tests/test_atomic_commands.py`
  - `cw` / `cw portal` / `cw portal detect` help 边界测试。
- `README.md`
  - 更新 CW 命令边界与恢复场景。
- `skills/trail-cw/SKILL.md`
  - 更新标准流程与 detect-vs-refresh 恢复用法。

## Task 1: scene 层抽出 raw snapshot helper 并冻结 detect 语义

**Files:**
- Modify: `trail/scenes/cw/portal.py`
- Modify: `tests/test_cw_portal.py`

- [ ] **Step 1: 先写失败测试，锁定 detect/helper 的 scene 语义**

```python
def test_detect_cw_portal_updates_snapshot_and_entry_from_invest_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime(
        ocr_result=[{"text": "beta"}],
        locate_results={
            _asset("portal.collection"): Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection")),
        },
    )
    cards = [{**_portal_cards()[0], "new": 1}, *_portal_cards()[1:]]
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards, raising=False)

    snapshot = portal_module.detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert snapshot == {
        "cards": cards,
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
        "stale": False,
    }
    assert session.scene_state["cw"]["portal"] == snapshot
    assert session.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
    }


def test_detect_cw_portal_preserves_existing_entry_truth(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [], "mode": "new", "difficulty": "lowest", "battle_mode": "overclock", "stale": True},
    }
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    snapshot = portal_module.detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert snapshot["mode"] == "continue"
    assert snapshot["difficulty"] == "current"
    assert snapshot["battle_mode"] == "standard"


def test_detect_cw_portal_rejects_non_invest_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "home"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        portal_module.detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert exc_info.value.code == "CW_PORTAL_PAGE_INVALID"
```

- [ ] **Step 2: 跑红灯，确认 `detect_cw_portal` 与共享 helper 还不存在**

Run:
`uv run pytest tests/test_cw_portal.py -k "detect_cw_portal or refresh_cw_portal" -q --basetemp .trail/pytest-temp-cw-portal-detect-scene -p no:cacheprovider`

Expected:
- 新增的 `detect_cw_portal` 用例报 `AttributeError` 或断言失败。
- 现有 `refresh_cw_portal` 用例仍保持现状。

- [ ] **Step 3: 最小实现共享 helper 与 `detect_cw_portal`**

```python
def _snapshot_cw_portal_from_invest_page(session, *, runtime, portal_list: object) -> dict[str, object]:
    _require_portal_page(runtime, session=session, expected_page="invest")
    cards = summarize_portal_cards(
        runtime.ocr(),
        portal_list,
        collection_matches=detect_portal_collection_matches(runtime),
    )
    truth = _portal_entry_truth(session)
    snapshot = {
        "cards": cards,
        "mode": truth.get("mode"),
        "difficulty": truth.get("difficulty"),
        "battle_mode": truth.get("battle_mode"),
        "stale": False,
    }
    cw_state = ensure_cw_state(session)
    cw_state["portal"] = snapshot
    cw_state["entry"] = {
        "page": "invest",
        "mode": snapshot.get("mode"),
        "difficulty": snapshot.get("difficulty"),
        "battle_mode": snapshot.get("battle_mode"),
    }
    return snapshot


def detect_cw_portal(session, *, runtime, portal_list: object) -> dict[str, object]:
    return _snapshot_cw_portal_from_invest_page(session, runtime=runtime, portal_list=portal_list)


def refresh_cw_portal(session, *, runtime, portal_list: object) -> dict[str, object]:
    _require_portal_page(runtime, session=session, expected_page="invest")
    # 现有 click + settle + wait 逻辑保持原样
    sleep(PORTAL_SETTLE_INTERVAL)
    _wait_for_portal_page(
        runtime,
        session=session,
        expected_page="invest",
        error_code="CW_PORTAL_REFRESH_UNAVAILABLE",
        error_message="cw portal.refresh did not settle back to invest",
    )
    return _snapshot_cw_portal_from_invest_page(session, runtime=runtime, portal_list=portal_list)
```

- [ ] **Step 4: 跑 scene 测试，确认 detect 与 refresh wait 语义都通过**

Run:
`uv run pytest tests/test_cw_portal.py -k "detect_cw_portal or refresh_cw_portal" -q --basetemp .trail/pytest-temp-cw-portal-detect-scene-green -p no:cacheprovider`

Expected:
- `detect_cw_portal_*` 全绿。
- `refresh_cw_portal_waits_*` 旧测试继续全绿。

- [ ] **Step 5: 补一条 restart truth 边界测试，防止 detect 错误放宽 restart 前提**

```python
def test_detect_snapshot_does_not_make_restart_valid_without_entry_truth(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    portal_module.detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert session.scene_state["cw"]["portal"]["stale"] is False
    assert session.scene_state["cw"]["entry"]["mode"] is None
```

Run:
`uv run pytest tests/test_cw_portal.py -k "detect_snapshot_does_not_make_restart_valid_without_entry_truth" -q --basetemp .trail/pytest-temp-cw-portal-detect-truth -p no:cacheprovider`

Expected:
- 该测试通过，并把“detect 不伪造 truth”冻结在 scene 层。

- [ ] **Step 6: 再补一条 detect -> select 顺序测试，证明 detect 产物可被下游直接消费**

```python
def test_detect_snapshot_can_be_consumed_by_select(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    portal_module.detect_cw_portal(session, runtime=runtime, portal_list=[])
    selected = portal_module.select_cw_portal(session, card_idx=2, runtime=runtime)

    assert selected == _portal_cards()[1]
    assert session.scene_state["cw"]["portal"]["stale"] is True
    assert session.scene_state["cw"]["entry"]["page"] == "in_game"
```

Run:
`uv run pytest tests/test_cw_portal.py -k "detect_snapshot_can_be_consumed_by_select" -q --basetemp .trail/pytest-temp-cw-portal-detect-select -p no:cacheprovider`

Expected:
- 该测试通过，冻结 detect 写回的 snapshot shape 足以被 `select` 直接消费。

## Task 2: daemon 层接入 `cw.portal.detect`，并锁住 capture / enrich 语义

**Files:**
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_portal.py`

- [ ] **Step 1: 先写失败测试，锁定 detect 的 capture 路由与 guide fallback**

```python
def _build_cw_portal_detect_harness(tmp_path: Path, monkeypatch, *, detect_impl, guide_fetcher):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-portal-detect.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda timeout=10: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_portal", detect_impl, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_list", guide_fetcher)

    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-detect",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.detect",
        payload={"session_id": session.session_id},
    )
    return service, session, runtime, command_service, request


def test_command_service_handles_cw_portal_detect_and_updates_snapshot(tmp_path: Path, monkeypatch):
    snapshot = {"cards": _portal_cards(), "mode": None, "difficulty": None, "battle_mode": None, "stale": False}
    service, session, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: session.scene_state.setdefault("cw", {}).__setitem__("portal", snapshot) or snapshot,
        guide_fetcher=lambda **kwargs: {"portals": [], "count": 0, "more": False},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-detect.png"
    assert runtime.capture_requests == [(False, "req-cw-portal-detect")]
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == snapshot


def test_command_service_cw_portal_detect_guide_lookup_failure_returns_raw_cards(tmp_path: Path, monkeypatch):
    snapshot = {"cards": _portal_cards(), "mode": None, "difficulty": None, "battle_mode": None, "stale": False}
    service, session, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: session.scene_state.setdefault("cw", {}).__setitem__("portal", snapshot) or snapshot,
        guide_fetcher=lambda **kwargs: (_ for _ in ()).throw(TrailError("GUIDE_FETCH_FAILED", "boom")),
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["cards"] == _portal_cards()
    assert "guides" not in payload["data"]["cards"][0]
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == snapshot
```

- [ ] **Step 2: 跑红灯，确认 detect 还未注册到 daemon 路由**

Run:
`uv run pytest tests/test_daemon_protocol.py -k "cw_portal_detect" -q --basetemp .trail/pytest-temp-cw-portal-detect-daemon-red -p no:cacheprovider`

Expected:
- 请求 `cw.portal.detect` 报 `unsupported method`、缺少 handler、或走错 `_run_cw()` 路径；不应因为 monkeypatch 目标缺符号而先被测试脚手架卡住。

- [ ] **Step 3: 最小实现 `CW_CAPTURE_METHODS` 与 `cw.portal.detect` handler**

```python
CW_CAPTURE_METHODS = {
    "cw.slots.read",
    "cw.portal.detect",
}


if request.method.startswith("cw."):
    service = self._session_service(request)
    if request.method in CW_CAPTURE_METHODS:
        return self._run_cw_with_capture(request, service=service)
    if request.method in CW_MUTATING_METHODS:
        session_id = request.session_id or request.payload.get("session_id")
        return self._run_mutation(
            request,
            request.method,
            lambda session_service: self._run_cw_mutation(request, service=session_service),
            handler_persisted_state=True,
            response_builder=lambda payload: payload,
            enforce_cw_tainted=bool(isinstance(session_id, str) and session_id),
            tainted_session_id=session_id if isinstance(session_id, str) and session_id else None,
        )
    return success(self._run_cw(request, service=service), request_id=request.request_id)
```

```python
handlers = {
    "cw.portal.select": lambda: select_cw_portal(
        session,
        card_idx=payload["card_idx"],
        runtime=runtime(),
    ),
    "cw.portal.detect": lambda: _attach_guides_to_portal_snapshot(
        session,
        detect_cw_portal(
            session,
            runtime=runtime(),
            portal_list=fetch_cw_guide_config().get("portal_list", []),
        ),
    ),
}
```

- [ ] **Step 4: 补一条非 mutation 边界测试，确认 detect 不会进入 mutation unknown**

```python
def test_command_service_cw_portal_detect_failure_returns_capture_envelope(tmp_path: Path, monkeypatch):
    service, session, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: (_ for _ in ()).throw(TrailError("CW_PORTAL_PAGE_INVALID", "cw portal action only supports invest, current page: home")),
        guide_fetcher=lambda **kwargs: {"portals": [], "count": 0, "more": False},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["request_id"] == "req-cw-portal-detect"
    assert payload["error"] == {
        "code": "CW_PORTAL_PAGE_INVALID",
        "message": "cw portal action only supports invest, current page: home",
    }
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-detect.png"
    assert runtime.capture_requests == [(True, "req-cw-portal-detect")]
```

说明：renderer/CLI 层再补“有 `request id`、无 `tainted=1`、无 `recover`”的文本 golden；daemon 层这里锁住的是“detect 走非 mutation capture envelope，并使用 optional screenshot，而不是 mutation journal”。

- [ ] **Step 5: 运行 daemon 侧测试，确认 capture、guide fallback、restart 前提都通过**

Run:
`uv run pytest tests/test_daemon_protocol.py tests/test_cw_portal.py -k "portal_detect or restart" -q --basetemp .trail/pytest-temp-cw-portal-detect-daemon-green -p no:cacheprovider`

Expected:
- 新增 detect 用例全绿。
- 既有 `cw.portal.refresh` / `cw.portal.restart` 相关用例不回归。

## Task 3: 接入 CLI wrapper 与 renderer，冻结 stdout 协议

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，锁定 CLI / renderer / YAML reject**

```python
def test_cw_portal_detect_renders_portal_cards_family(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.portal.detect": build_success_response(
                request_id="req-cw-portal-detect",
                screenshot=".trail/shots/req-cw-portal-detect.png",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "guides": [{"lineup_id": "alpha-guide", "title": "Alpha攻略", "carry_roles": ["希儿"], "support_hard": True, "has_change_equip": False, "has_expert": True, "like": 123, "favour": 45}],
                        }
                    ],
                    "mode": None,
                    "difficulty": None,
                    "battle_mode": None,
                    "stale": False,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "detect", "--session", SESSION_ID])

    assert result.stdout.splitlines() == [
        "ok cw.portal.detect cards=1",
        "shot path=.trail/shots/req-cw-portal-detect.png",
        'opt idx=1 title="Alpha Portal" score=0.99',
        'opt idx=1 desc="Alpha Desc"',
        'guide idx=1 gid=1 id=alpha-guide title=Alpha攻略 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45',
    ]
    _assert_single_call(client, method="cw.portal.detect", payload={}, tmp_path=tmp_path)


def test_render_output_renders_cw_portal_detect_family():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "guides": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                }
            ],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": False,
        },
        "screenshot": ".trail/shots/req-cw-portal-detect.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.detect", payload).splitlines()[0] == "ok cw.portal.detect cards=1"


def test_render_output_cw_portal_detect_rejects_yaml_output():
    payload = {
        "ok": True,
        "data": {
            "cards": [{"card_idx": 1, "portal_title": "Alpha Portal", "portal_description": "Alpha Desc", "score": 0.99}],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": False,
        },
        "screenshot": ".trail/shots/req-cw-portal-detect.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.detect", payload, output_format="yaml").splitlines() == [
        "fail cw.portal.detect code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-portal-detect.png",
        'why msg="yaml not supported for cw.portal.detect"',
    ]
```

- [ ] **Step 2: 跑红灯，确认 CLI 还没有 `portal detect`，renderer 也未注册**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -k "portal_detect" -q --basetemp .trail/pytest-temp-cw-portal-detect-cli-red -p no:cacheprovider`

Expected:
- `No such command 'detect'` 或 `render_output()` 回退到 generic success。

- [ ] **Step 3: 最小实现 CLI 与 renderer 映射**

```python
CW_PORTAL_HELP = "投资环境页上的选择/识别/刷新/重开动作。"


@portal_app.command("detect")
def cw_portal_detect(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.portal.detect", session_id=session)
```

```python
TEXT_RENDERERS["cw.portal.detect"] = _render_cw_portal_cards
```

- [ ] **Step 4: 补一条 failure 文本测试，锁住“有 request id、无 tainted、无 recover”**

```python
def test_render_output_renders_cw_portal_detect_failure_without_recover():
    payload = {
        "request_id": "req-cw-portal-detect-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-portal-detect-fail.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "CW_PORTAL_PAGE_INVALID", "message": "cw portal action only supports invest, current page: home"},
    }

    assert render_output("cw.portal.detect", payload).splitlines() == [
        "fail cw.portal.detect code=CW_PORTAL_PAGE_INVALID",
        "request id=req-cw-portal-detect-fail",
        "shot path=.trail/shots/req-cw-portal-detect-fail.png",
        'why msg="cw portal action only supports invest, current page: home"',
    ]
```

- [ ] **Step 5: 运行 CLI / renderer 契约测试**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -k "portal_detect" -q --basetemp .trail/pytest-temp-cw-portal-detect-cli-green -p no:cacheprovider`

Expected:
- `cw.portal.detect` wrapper、stdout golden、YAML reject、failure text 断言全部通过。

## Task 4: 更新 help、README 与 skill，锁住恢复场景文案

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，锁定 help 和文档锚点**

```python
def test_cw_help_exposes_detect_in_portal_group(cli_runner):
    result = cli_runner.invoke(app, ["cw", "--help"])
    block = _extract_help_command_block(result.output, "portal")

    assert "投资环境页" in block
    assert "识别" in block
    assert "刷新" in block
    assert "重开" in block


def test_cw_portal_group_help_mentions_detect(cli_runner):
    result = cli_runner.invoke(app, ["cw", "portal", "--help"])
    normalized = _normalize_help(result.output)

    assert "投资环境页" in normalized
    assert "识别" in normalized
    assert "刷新" in normalized
    assert "重开" in normalized


def test_cw_portal_detect_help_describes_snapshot_only_contract(cli_runner):
    result = cli_runner.invoke(app, ["cw", "portal", "detect", "--help"])

    assert "当前已在投资环境页时重新识别并保存 portal snapshot" in result.output
    assert "推进流程" not in result.output


def test_readme_and_cw_skill_document_detect_recovery_boundary():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw_skill = (PROJECT_ROOT / "skills" / "trail-cw" / "SKILL.md").read_text(encoding="utf-8")

    assert "已在投资环境页但 session 没有 fresh portal snapshot 时，使用 `trail cw portal detect --session <id>`" in readme
    assert "`trail cw portal detect` 只重建当前三张卡识别结果，不点击、不刷新、不重开" in cw_skill
```

- [ ] **Step 2: 跑红灯，确认现有 help 和 README 还没有 detect 边界**

Run:
`uv run pytest tests/test_atomic_commands.py tests/test_output_rendering.py -q --basetemp .trail/pytest-temp-cw-portal-detect-docs-red -p no:cacheprovider`

Expected:
- `portal` 分组帮助仍只有“选择/刷新/重开”。
- README / skill 还缺 detect 恢复场景。

- [ ] **Step 3: 最小更新帮助文本与文档**

```python
CW_APP_HELP = "货币战争固定流程命令：enter 到首页，start 从首页进入投资环境页；portal 负责投资环境页上的识别、选择、刷新与重开；其余分组处理局内阶段与资源。"
CW_PORTAL_HELP = "投资环境页上的识别/选择/刷新/重开动作。"


@portal_app.command("detect")
def cw_portal_detect(session: str = typer.Option(..., "--session")) -> None:
    """当前已在投资环境页时重新识别并保存 portal snapshot。"""
    _print_cw("cw.portal.detect", session_id=session)
```

```md
- `trail cw portal detect --session <id>`：当你已经手动进入投资环境页，但当前 session 没有 fresh portal snapshot 时，重新识别并缓存当前三张卡。
- `trail cw portal.refresh --session <id>`：点击刷新后生成新的三张卡；会消耗刷新动作，不等于 detect。
```

```md
- 若用户已在投资环境页，但 `cw start` 中途失败或 session 丢失 portal 快照，优先运行 `trail cw portal detect --session <id>`，不要重复执行 `trail cw start`。
- `trail cw portal detect` 只重建当前三张卡识别结果，不点击、不刷新、不重开。
```

- [ ] **Step 4: 跑文档与 help 断言，确认 detect-vs-refresh 恢复场景已固定**

Run:
`uv run pytest tests/test_atomic_commands.py tests/test_output_rendering.py -q --basetemp .trail/pytest-temp-cw-portal-detect-docs-green -p no:cacheprovider`

Expected:
- `cw` / `cw portal` / `cw portal detect` help 文案通过。
- README / `skills/trail-cw/SKILL.md` 的新锚点断言通过。

## Task 5: 做一轮特性级验证并准备执行切换

**Files:**
- Modify: `tests/test_cw_portal.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 运行整组特性测试，确认 scene / daemon / CLI / docs 一起通过**

Run:
`uv run pytest tests/test_cw_portal.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py -q --basetemp .trail/pytest-temp-cw-portal-detect-full -p no:cacheprovider`

Expected:
- 新增 detect 用例全绿。
- 现有 `cw.portal.refresh` / `cw.portal.restart` / `cw.start` 相关回归不红。

- [ ] **Step 2: 目测核对文档与协议不变量**

Checklist:
- `ok cw.portal.detect cards=<n>` 是默认成功首行。
- 有截图时必出 `shot path=.trail/shots/req-cw-portal-detect.png`。
- detect failure 可有 `request id=<id>`，但无 `tainted=1`、无 `recover`。
- detect 生成的 snapshot 已被 `select` 顺序测试证明可直接消费。
- `trail cw portal detect --help` 没有“推进流程”“进入投资环境页”之类越界表述。
- README 与 skill 都明确了 detect-vs-refresh 的区别。

- [ ] **Step 3: 保持工作树不提交，等待用户选择执行方式**

Run:
`rtk git status --short`

Expected:
- 只显示本特性涉及的工作树修改。
- 不创建 commit，除非用户后续明确要求。

## Plan Self-Review

- spec coverage：已覆盖 capture 路由、stable invest helper、guide enrich 分层、detect 后 select、restart truth 边界、YAML reject、failure 无 taint/recover、help/README/skill 文案更新。
- placeholder scan：计划中未保留 `TODO` / `TBD` / “类似 Task N” 之类占位表述。
- type consistency：统一使用 `cw.portal.detect`、`detect_cw_portal`、`_snapshot_cw_portal_from_invest_page`、`cards/mode/difficulty/battle_mode/stale`、`cw.entry.page="invest"` 这一组命名与字段。
