from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.daemon.command_service import (
    CommandService,
    PersistedButResponseUnknown,
    SideEffectAppliedButStateNotPersisted,
)
from trail.daemon.session_service import SessionService, SessionServiceRegistry


def _request(
    tmp_path: Path,
    *,
    method: str,
    payload: dict,
    request_id: str,
    session_id: str | None,
    verbose: bool = False,
):
    return SimpleNamespace(
        method=method,
        payload=payload,
        request_id=request_id,
        session_id=session_id,
        workspace_root=str(tmp_path),
        verbose=verbose,
    )


def _envelope(
    *,
    ok: bool = True,
    data: dict | None = None,
    screenshot: str | None = None,
    error: dict | None = None,
):
    return {
        "ok": ok,
        "data": {} if data is None else data,
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": error,
    }


class StubRuntime:
    def __init__(self, screenshot_path: Path):
        self._screenshot_path = screenshot_path
        self.clicks: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []
        self.keys: list[tuple[str, int]] = []

    def capture_after_action(self, optional: bool = False):
        del optional
        return str(self._screenshot_path)

    def collect_warnings(self):
        return []

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return []

    def consume_debug_trace(self):
        return []

    def click_point(self, x: int, y: int):
        self.clicks.append((x, y))

    def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
        self.drags.append((from_x, from_y, to_x, to_y))

    def press_key(self, key: str, presses: int = 1):
        self.keys.append((key, presses))


class StubRuntimeService:
    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        return self._runtime


def test_request_status_returns_machine_readable_fields(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-2", command_name="cw.stage.detect")

    status = service.request_status("req-2")

    assert status["request_id"] == "req-2"
    assert status["method"] == "cw.stage.detect"
    assert status["workspace_root"] == str(tmp_path)
    assert status["session_id"] == session.session_id
    assert status["final_state"] is None
    assert status["last_visible_stage"] == "accepted"
    assert status["tainted"] is False
    assert status["started_at"]
    assert status["updated_at"]


def test_reconcile_session_clears_tainted_state(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("daemon", {})["tainted"] = True
    service.save_session(loaded)

    result = service.reconcile_session(session.session_id)

    assert result == {"session_id": session.session_id, "tainted": False}
    assert service.load_session(session.session_id).scene_state["daemon"]["tainted"] is False


def test_duplicate_request_id_returns_duplicate_terminal(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-3", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-3",
        command_name="input.click",
        final_state="completed",
        envelope=_envelope(data={"clicked": [10, 20]}, screenshot=".trail/shots/req-3.png"),
    )

    result = service.begin_mutation(session_id=session.session_id, request_id="req-3", command_name="input.click")

    assert result["status"] == "duplicate_terminal"
    assert result["record"]["last_envelope"]["data"]["clicked"] == [10, 20]


def test_duplicate_request_id_returns_duplicate_in_progress(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-3b", command_name="input.click")

    result = service.begin_mutation(session_id=session.session_id, request_id="req-3b", command_name="input.click")

    assert result["status"] == "duplicate_in_progress"


def test_duplicate_request_id_is_workspace_global(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    first = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    second = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2})
    service.begin_mutation(session_id=first.session_id, request_id="req-3c", command_name="cw.enter")

    result = service.begin_mutation(session_id=second.session_id, request_id="req-3c", command_name="cw.shop.buy-slot")

    assert result["status"] == "request_id_conflict"


def test_finish_mutation_marks_tainted_for_unknown_result(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-4", command_name="cw.stage.detect")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-4",
        command_name="cw.stage.detect",
        final_state="persisted_but_response_unknown",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-4.png",
            error={"code": "DAEMON_UNAVAILABLE", "message": "lost response"},
        ),
    )

    loaded = service.load_session(session.session_id)
    assert loaded.scene_state["daemon"]["tainted"] is True
    assert loaded.last_result == {
        "command": "cw.stage.detect",
        "ok": False,
        "data": {},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "lost response"},
    }


def test_run_mutation_records_stage_progression(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-5",
        session_id=session.session_id,
    )
    events: list[str] = []

    def wrap(name: str):
        original = getattr(service, name)

        def recorder(*args, **kwargs):
            events.append(name)
            return original(*args, **kwargs)

        return recorder

    for name in (
        "begin_mutation",
        "mark_executing",
        "mark_side_effect_applied",
        "mark_state_persisted",
        "finish_mutation",
    ):
        monkeypatch.setattr(service, name, wrap(name))

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: _envelope(data={"clicked": [10, 20]}, screenshot=".trail/shots/req-5.png"),
    )

    assert result["ok"] is True
    assert events == [
        "begin_mutation",
        "mark_executing",
        "mark_side_effect_applied",
        "mark_state_persisted",
        "finish_mutation",
    ]
    assert service.request_status("req-5")["final_state"] == "completed"
    assert service.request_status("req-5")["last_visible_stage"] == "responded"


@pytest.mark.parametrize(
    ("error_type", "final_state"),
    [
        (SideEffectAppliedButStateNotPersisted, "applied_but_not_persisted"),
        (PersistedButResponseUnknown, "persisted_but_response_unknown"),
    ],
)
def test_run_mutation_marks_unknown_result_terminal_states(tmp_path: Path, error_type, final_state: str):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id=f"{final_state}-request",
        session_id=session.session_id,
    )
    envelope = _envelope(
        ok=False,
        screenshot=f".trail/shots/{final_state}.png",
        error={"code": "DAEMON_UNAVAILABLE", "message": final_state},
    )

    with pytest.raises(error_type):
        command_service._run_mutation(
            request,
            "input.click",
            lambda svc: (_ for _ in ()).throw(error_type(envelope=envelope)),
        )

    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)
    assert status["final_state"] == final_state
    assert loaded.scene_state["daemon"]["tainted"] is True
    assert loaded.last_screenshot == envelope["screenshot"]


def test_run_mutation_marks_failed_before_side_effect(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-6b",
        session_id=session.session_id,
    )

    with pytest.raises(RuntimeError):
        command_service._run_mutation(
            request,
            "input.click",
            lambda svc: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    loaded = service.load_session(session.session_id)
    assert service.request_status("req-6b")["final_state"] == "failed_before_side_effect"
    assert loaded.last_result == {
        "command": "input.click",
        "ok": False,
        "data": {},
        "error": {"code": "RuntimeError", "message": "boom"},
    }
    assert loaded.last_screenshot is None


def test_request_status_survives_service_restart(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-7", command_name="cw.shop.buy-slot")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-7",
        command_name="cw.shop.buy-slot",
        final_state="completed",
        envelope=_envelope(data={"slot": 2}, screenshot=".trail/shots/req-7.png"),
    )

    reloaded = SessionService(workspace_root=tmp_path)

    assert reloaded.request_status("req-7")["final_state"] == "completed"


@pytest.mark.parametrize(
    ("method", "payload", "expected"),
    [
        ("input.click", {"x": 10, "y": 20}, {"clicked": [10, 20]}),
        (
            "input.drag",
            {"from_x": 10, "from_y": 20, "to_x": 30, "to_y": 40},
            {"dragged": [10, 20, 30, 40]},
        ),
        ("input.key", {"key": "f", "presses": 2}, {"key": "f", "presses": 2}),
    ],
)
def test_handle_routes_input_mutations_through_journal_without_session(
    tmp_path: Path,
    method: str,
    payload: dict,
    expected: dict,
):
    runtime = StubRuntime(tmp_path / f"{method.replace('.', '-')}.png")
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=StubRuntimeService(runtime),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method=method,
        payload=payload,
        request_id=f"{method}-request",
        session_id=None,
    )

    response = command_service.handle(request)

    status = registry.for_workspace(str(tmp_path)).request_status(request.request_id)
    assert response["ok"] is True
    assert response["data"] == expected
    assert status["final_state"] == "completed"
    assert status["session_id"] is None
