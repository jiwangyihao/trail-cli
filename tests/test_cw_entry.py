from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trail.runtime.model import Box
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.entry import enter_cw
from trail.session.store import SessionStore


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> Box:
    return Box(left=left, top=top, width=width, height=height, source=_asset(alias))


def _install_template_runtime(runtime, *, locate_results: dict[str, object] | None = None, wait_results: dict[str, object] | None = None) -> None:
    locate_results = locate_results or {}
    wait_results = wait_results or {}

    def locate(template: str, **kwargs):
        del kwargs
        runtime.locate_calls.append(template)
        return locate_results.get(template)

    def wait_img(template: str, timeout: int = 10, interval: float = 0.5):
        del timeout, interval
        runtime.wait_calls.append(template)
        return wait_results.get(template)

    runtime.locate = locate
    runtime.wait_img = wait_img


def test_enter_cw_records_entry_snapshot_and_invalidates_stage(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"steps": [1], "stale": False},
        "stage": {"stale": False, "value": "shop"},
    }

    refreshed = enter_cw(session, mode="continue", difficulty="highest", battle_mode="overclock")

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.scene_state["cw"]["slots"] == {"stale": True, "hand": ["希儿"]}
    assert refreshed.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert refreshed.scene_state["cw"]["sell_plan"] == {"stale": True}


def test_enter_cw_runs_new_mode_ui_flow_from_start_related_pages(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.last_stage = {"scene": "cw", "value": "shop"}

    start_box = _box("entry.start", left=10, top=20)
    enter_box = _box("entry.new", left=80, top=90)
    highest_box = _box("entry.difficulty.highest", left=140, top=160)
    start_game_box = _box("entry.start_game", left=220, top=260)
    next_step_box = _box("stage.settle", left=320, top=360)
    blank_box = _box("stage.boss_preview", left=360, top=400)
    invest_box = _box("entry.invest_environment", left=420, top=460)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("entry.start"): start_box,
            _asset("stage.preparation"): None,
            _asset("stage.settle"): next_step_box,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.new"): enter_box,
            _asset("entry.difficulty.highest"): highest_box,
            _asset("entry.start_game"): start_game_box,
            _asset("stage.settle"): next_step_box,
            _asset("stage.boss_preview"): blank_box,
            _asset("entry.invest_environment"): invest_box,
        },
    )

    refreshed = enter_cw(session, mode="new", difficulty="highest", battle_mode="overclock", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert runtime.clicks == [
        start_box.center,
        (300, 450),
        enter_box.center,
        highest_box.center,
        start_game_box.center,
        next_step_box.center,
        blank_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.new"),
        _asset("entry.difficulty.highest"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


def test_enter_cw_runs_world_to_currency_wars_entry_chain_before_continue_flow(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=12, top=24)
    cosmic_box = _box("entry.cosmic_strife", left=36, top=48)
    start_box = _box("entry.start", left=84, top=96)
    continue_box = _box("entry.continue", left=120, top=132)
    blank_box = _box("stage.boss_preview", left=168, top=180)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.keys: list[tuple[str, int, float]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            self.keys.append((key, presses, interval))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("stage.preparation"): None,
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("stage.settle"): None,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
            _asset("entry.continue"): continue_box,
            _asset("stage.boss_preview"): blank_box,
        },
    )

    refreshed = enter_cw(session, mode="continue", difficulty="current", battle_mode="standard", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert runtime.keys == [("f4", 1, 0.2)]
    assert runtime.clicks == [
        cosmic_box.center,
        (464, 324),
        (1494, 884),
        start_box.center,
        (300, 250),
        continue_box.center,
        blank_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.menu"),
        _asset("entry.cosmic_strife"),
        _asset("entry.start"),
        _asset("entry.continue"),
        _asset("stage.boss_preview"),
    ]


def test_enter_cw_does_not_treat_generic_settle_template_as_top_level_entry_recovery(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=20, top=32)
    cosmic_box = _box("entry.cosmic_strife", left=44, top=56)
    start_box = _box("entry.start", left=92, top=104)
    continue_box = _box("entry.continue", left=128, top=140)
    blank_box = _box("stage.boss_preview", left=176, top=188)
    next_step_box = _box("stage.settle", left=224, top=236)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.keys: list[tuple[str, int, float]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            self.keys.append((key, presses, interval))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("stage.preparation"): None,
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("stage.settle"): next_step_box,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
            _asset("entry.continue"): continue_box,
            _asset("stage.boss_preview"): blank_box,
        },
    )

    refreshed = enter_cw(session, mode="continue", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.keys == [("f4", 1, 0.2)]
    assert next_step_box.center not in runtime.clicks
    assert runtime.clicks == [
        cosmic_box.center,
        (464, 324),
        (1494, 884),
        start_box.center,
        (300, 250),
        continue_box.center,
        blank_box.center,
    ]


def _build_cw_harness(tmp_path: Path, *, runtime=None):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace() if runtime is None else runtime
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, cw_service, command_service


def _run_cw_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    return command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(workspace_root),
            session_id=session.session_id,
            verbose=False,
            method=method,
            payload={"session_id": session.session_id, **payload},
        )
    )


def _set_entry(session, entry: dict):
    session.scene_state.setdefault("cw", {})["entry"] = dict(entry)
    session.scene_state["cw"]["stage"] = {"stale": True}
    return session


def test_cw_enter_mutation_flows_through_command_service_journal(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, mode, difficulty, battle_mode, runtime: _set_entry(
            session,
            {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode},
        ),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-1",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-1")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["mode"] == "continue"
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["entry"]["battle_mode"] == "standard"
    assert persisted.scene_state["cw"]["stage"] == {"stale": True}


def test_cw_enter_mutation_rejects_tainted_session_until_reconciled(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    service.begin_mutation(session_id=session.session_id, request_id="req-cw-tainted", command_name="cw.guide.apply")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-cw-tainted",
        command_name="cw.guide.apply",
        final_state="persisted_but_response_unknown",
        envelope={
            "ok": False,
            "data": {},
            "screenshot": ".trail/shots/req-cw-tainted.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"detail": "mutation result unknown"},
            "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        },
    )
    enter_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, mode, difficulty, battle_mode, runtime: enter_calls.append((mode, difficulty, battle_mode))
        or _set_entry(
            session,
            {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode},
        ),
    )

    blocked = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-blocked",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )

    assert service.request_status("req-cw-tainted")["tainted"] is True
    assert blocked["ok"] is False
    assert blocked["error"] == {
        "code": "SESSION_RECONCILE_REQUIRED",
        "message": "session is tainted; reconcile before mutating cw commands",
    }
    assert enter_calls == []

    service.reconcile_session(session.session_id)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-reconciled",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )

    assert envelope["ok"] is True
    assert enter_calls == [("continue", "current", "standard")]
    assert service.request_status("req-cw-enter-reconciled")["final_state"] == "completed"


def test_cw_enter_duplicate_terminal_replay_precedes_tainted_gate(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    enter_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, mode, difficulty, battle_mode, runtime: enter_calls.append((mode, difficulty, battle_mode))
        or _set_entry(
            session,
            {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode},
        ),
    )
    risky_envelope = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-enter-tainted.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"detail": "mutation result unknown"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }
    service.begin_mutation(session_id=session.session_id, request_id="req-cw-enter-tainted", command_name="cw.enter")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-cw-enter-tainted",
        command_name="cw.enter",
        final_state="applied_but_not_persisted",
        envelope=risky_envelope,
    )

    replay = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-tainted",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )
    blocked = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-blocked",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )

    assert replay["request_id"] == "req-cw-enter-tainted"
    assert replay["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert replay["screenshot"] == ".trail/shots/req-cw-enter-tainted.png"
    assert blocked["ok"] is False
    assert blocked["error"] == {
        "code": "SESSION_RECONCILE_REQUIRED",
        "message": "session is tainted; reconcile before mutating cw commands",
    }
    assert enter_calls == []


def test_cw_enter_marks_applied_but_not_persisted_when_ui_side_effect_fails_late(tmp_path: Path, monkeypatch):
    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)

    def late_failure(session, mode, difficulty, battle_mode, runtime):
        del session, mode, difficulty, battle_mode
        runtime.click_point(640, 360)
        raise RuntimeError("cw enter late failure")

    monkeypatch.setattr("trail.daemon.cw_service.enter_cw", late_failure)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-late-fail",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )
    status = service.request_status("req-cw-enter-late-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [(640, 360)]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert "cw enter late failure" in envelope["debug"]["detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state.get("cw", {}).get("entry") is None
