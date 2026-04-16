from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
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
    def __init__(
        self,
        screenshot_path: Path,
        *,
        click_error: Exception | None = None,
        drag_error: Exception | None = None,
        key_error: Exception | None = None,
    ):
        self._screenshot_path = screenshot_path
        self.clicks: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []
        self.keys: list[tuple[str, int]] = []
        self.warnings: list[dict] = []
        self.references: list[dict] = []
        self.trace: list[dict] = []
        self.click_error = click_error
        self.drag_error = drag_error
        self.key_error = key_error

    def capture_after_action(self, optional: bool = False):
        del optional
        return str(self._screenshot_path)

    def collect_warnings(self):
        warnings = list(self.warnings)
        self.warnings.clear()
        return warnings

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return list(self.references)

    def consume_debug_trace(self):
        trace = list(self.trace)
        self.trace.clear()
        return trace

    def click_point(self, x: int, y: int):
        if self.click_error is not None:
            raise self.click_error
        self.clicks.append((x, y))

    def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
        if self.drag_error is not None:
            raise self.drag_error
        self.drags.append((from_x, from_y, to_x, to_y))

    def press_key(self, key: str, presses: int = 1):
        if self.key_error is not None:
            raise self.key_error
        self.keys.append((key, presses))


class StubRuntimeService:
    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        return self._runtime


class FailingRuntimeService:
    def __init__(self, error: Exception):
        self._error = error

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        raise self._error


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


def test_request_status_reflects_reconcile_clearing_taint(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-reconcile-status", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-reconcile-status",
        command_name="input.click",
        final_state="persisted_but_response_unknown",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-reconcile-status.png",
            error={"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        ),
    )

    assert service.request_status("req-reconcile-status")["tainted"] is True

    service.reconcile_session(session.session_id)

    assert service.request_status("req-reconcile-status")["tainted"] is False


def test_request_status_uses_record_taint_when_risky_record_saved_but_session_not_updated(tmp_path: Path, monkeypatch):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-record-taint", command_name="input.click")
    original_save_session = service.save_session

    def fail_save_session(model):
        raise OSError("disk full")

    monkeypatch.setattr(service, "save_session", fail_save_session)

    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-record-taint",
        command_name="input.click",
        final_state="persisted_but_response_unknown",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-record-taint.png",
            error={"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        ),
    )

    monkeypatch.setattr(service, "save_session", original_save_session)

    assert service.load_session(session.session_id).scene_state.get("daemon", {}).get("tainted", False) is False
    assert service.request_status("req-record-taint")["tainted"] is True

    service.reconcile_session(session.session_id)

    assert service.request_status("req-record-taint")["tainted"] is False


def test_request_status_reflects_reconcile_clearing_taint(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-reconcile-status", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-reconcile-status",
        command_name="input.click",
        final_state="persisted_but_response_unknown",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-reconcile-status.png",
            error={"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        ),
    )

    assert service.request_status("req-reconcile-status")["tainted"] is True

    service.reconcile_session(session.session_id)

    assert service.request_status("req-reconcile-status")["tainted"] is False


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


def test_duplicate_terminal_returns_original_failure_envelope(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-dup-fail", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-dup-fail",
        command_name="input.click",
        final_state="failed_before_side_effect",
        envelope=_envelope(
            ok=False,
            screenshot=".trail/shots/req-dup-fail.png",
            error={"code": "WINDOW_NOT_FOUND", "message": "window not found"},
        ),
    )
    command_service = CommandService(
        runtime_service=StubRuntimeService(StubRuntime(tmp_path / "dup-terminal.png")),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-dup-fail",
        session_id=session.session_id,
    )

    response = command_service.handle(request)

    assert response["request_id"] == "req-dup-fail"
    assert response["ok"] is False
    assert response["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": "window not found",
    }
    assert response["screenshot"] == ".trail/shots/req-dup-fail.png"


def test_duplicate_terminal_without_last_envelope_returns_invalid_record_failure(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=StubRuntimeService(StubRuntime(tmp_path / "dup-terminal-invalid.png")),
        session_service=registry,
    )
    service = registry.for_workspace(str(tmp_path))
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-dup-invalid",
        session_id=None,
    )
    monkeypatch.setattr(
        service,
        "begin_mutation",
        lambda **kwargs: {
            "status": "duplicate_terminal",
            "record": {
                "request_id": "req-dup-invalid",
                "method": "input.click",
                "session_id": None,
                "final_state": "failed_before_side_effect",
            },
        },
    )

    response = command_service.handle(request)

    assert response["ok"] is False
    assert response["error"] == {
        "code": "REQUEST_TERMINAL_RECORD_INVALID",
        "message": "terminal request record missing envelope",
    }


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


def test_completed_request_id_reuse_across_scope_conflicts(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    first = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    second = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2})
    service.begin_mutation(session_id=first.session_id, request_id="req-3d", command_name="input.click")
    service.finish_mutation(
        session_id=first.session_id,
        request_id="req-3d",
        command_name="input.click",
        final_state="completed",
        envelope=_envelope(data={"clicked": [10, 20]}, screenshot=".trail/shots/req-3d.png"),
    )

    result = service.begin_mutation(session_id=second.session_id, request_id="req-3d", command_name="input.drag")

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

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: (_ for _ in ()).throw(error_type(envelope=envelope)),
    )

    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)
    assert result["error"] == envelope["error"]
    assert result["screenshot"] == envelope["screenshot"]
    assert status["final_state"] == final_state
    assert loaded.scene_state["daemon"]["tainted"] is True
    assert loaded.last_screenshot == envelope["screenshot"]


def test_run_mutation_keeps_terminal_state_when_side_effect_unknown_marker_fails(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-side-effect-marker-fail",
        session_id=session.session_id,
    )
    envelope = _envelope(
        ok=False,
        screenshot=".trail/shots/req-side-effect-marker-fail.png",
        error={"code": "DAEMON_UNAVAILABLE", "message": "applied_but_not_persisted"},
    )

    def fail_mark_side_effect_applied(**kwargs):
        raise OSError("side effect stage marker failed")

    monkeypatch.setattr(service, "mark_side_effect_applied", fail_mark_side_effect_applied)

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: (_ for _ in ()).throw(SideEffectAppliedButStateNotPersisted(envelope=envelope)),
    )

    status = service.request_status(request.request_id)
    assert result["error"] == envelope["error"]
    assert result["screenshot"] == envelope["screenshot"]
    assert "side effect stage marker failed" in result["debug"]["stage_detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["last_visible_stage"] == "responded"
    assert status["tainted"] is True


def test_run_mutation_keeps_terminal_state_when_persisted_unknown_marker_fails(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-persisted-unknown-marker-fail",
        session_id=session.session_id,
    )
    envelope = _envelope(
        ok=False,
        screenshot=".trail/shots/req-persisted-unknown-marker-fail.png",
        error={"code": "DAEMON_UNAVAILABLE", "message": "persisted_but_response_unknown"},
    )

    def fail_mark_state_persisted(**kwargs):
        raise OSError("state persisted marker failed")

    monkeypatch.setattr(service, "mark_state_persisted", fail_mark_state_persisted)

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: (_ for _ in ()).throw(PersistedButResponseUnknown(envelope=envelope)),
    )

    status = service.request_status(request.request_id)
    assert result["error"] == envelope["error"]
    assert result["screenshot"] == envelope["screenshot"]
    assert "state persisted marker failed" in result["debug"]["stage_detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["last_visible_stage"] == "responded"
    assert status["tainted"] is True


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

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    loaded = service.load_session(session.session_id)
    assert result["error"] == {"code": "RuntimeError", "message": "boom"}
    assert service.request_status("req-6b")["final_state"] == "failed_before_side_effect"
    assert loaded.last_result == {
        "command": "input.click",
        "ok": False,
        "data": {},
        "error": {"code": "RuntimeError", "message": "boom"},
    }
    assert loaded.last_screenshot is None


def test_handle_marks_terminal_failure_when_mark_executing_fails(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    command_service = CommandService(
        runtime_service=StubRuntimeService(StubRuntime(tmp_path / "mark-executing-fail.png")),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-mark-executing-fail",
        session_id=None,
    )

    def fail_mark_executing(**kwargs):
        raise OSError("executing marker failed")

    monkeypatch.setattr(service, "mark_executing", fail_mark_executing)

    response = command_service.handle(request)

    status = registry.for_workspace(str(tmp_path)).request_status("req-mark-executing-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "OSError",
        "message": "executing marker failed",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert status["last_visible_stage"] == "responded"


def test_run_mutation_marks_post_handler_stage_failure_as_applied_but_not_persisted(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-post-side-effect-fail",
        session_id=session.session_id,
    )

    def fail_mark_side_effect_applied(**kwargs):
        raise OSError("journal write failed")

    monkeypatch.setattr(service, "mark_side_effect_applied", fail_mark_side_effect_applied)

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: _envelope(data={"clicked": [10, 20]}, screenshot=".trail/shots/req-post-side-effect-fail.png"),
    )

    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)
    assert result["ok"] is False
    assert result["screenshot"] == ".trail/shots/req-post-side-effect-fail.png"
    assert result["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert result["debug"]["last_known_stage"] == "handler_completed"
    assert "OSError" in result["debug"]["detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert loaded.scene_state["daemon"]["tainted"] is True


def test_run_mutation_marks_finish_failure_as_persisted_but_response_unknown(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-finish-fail",
        session_id=session.session_id,
    )
    original_finish_mutation = service.finish_mutation

    def fail_completed_finish_mutation(**kwargs):
        if kwargs["final_state"] == "completed":
            raise OSError("flush failed")
        return original_finish_mutation(**kwargs)

    monkeypatch.setattr(service, "finish_mutation", fail_completed_finish_mutation)

    result = command_service._run_mutation(
        request,
        "input.click",
        lambda svc: _envelope(data={"clicked": [10, 20]}, screenshot=".trail/shots/req-finish-fail.png"),
    )

    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)
    assert result["ok"] is False
    assert result["screenshot"] == ".trail/shots/req-finish-fail.png"
    assert result["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert result["debug"]["last_known_stage"] == "state_persisted"
    assert "OSError" in result["debug"]["detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert loaded.scene_state["daemon"]["tainted"] is True


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


@pytest.mark.parametrize("final_state", ["applied_but_not_persisted", "persisted_but_response_unknown"])
def test_request_status_stays_conservative_when_unknown_result_session_is_unreadable(
    tmp_path: Path,
    monkeypatch,
    final_state: str,
):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id=f"req-{final_state}", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id=f"req-{final_state}",
        command_name="input.click",
        final_state=final_state,
        envelope=_envelope(
            ok=False,
            screenshot=f".trail/shots/{final_state}.png",
            error={"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        ),
    )
    monkeypatch.setattr(service, "load_session", lambda session_id: (_ for _ in ()).throw(FileNotFoundError(session_id)))

    status = service.request_status(f"req-{final_state}")

    assert status["final_state"] == final_state
    assert status["tainted"] is True


def test_request_status_waits_for_record_write_to_finish(tmp_path: Path, monkeypatch):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-race", command_name="input.click")
    original_save_record = service._save_record
    started = threading.Event()
    release = threading.Event()

    def blocking_save_record(record):
        if record["request_id"] == "req-race" and record.get("last_visible_stage") == "executing":
            started.set()
            release.wait(timeout=2)
        return original_save_record(record)

    monkeypatch.setattr(service, "_save_record", blocking_save_record)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(
            service.mark_executing,
            request_id="req-race",
            session_id=session.session_id,
            command_name="input.click",
        )
        assert started.wait(timeout=2) is True
        reader = executor.submit(service.request_status, "req-race")
        time.sleep(0.1)
        assert reader.done() is False
        release.set()
        writer.result()
        status = reader.result()

    assert status["last_visible_stage"] == "executing"


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


def test_handle_returns_captured_failure_envelope_for_input_mutation_error(tmp_path: Path):
    runtime = StubRuntime(
        tmp_path / "input-fail.png",
        click_error=TrailError("INPUT_BACKEND_MISSING", "input backend missing"),
    )
    runtime.warnings = [{"code": "WINDOW_NOT_FOREGROUND", "message": "窗口未前台"}]
    runtime.references = [{"path": "trail/ref.png", "similarity": 0.97}]
    runtime.trace = [{"step": "click"}]
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=StubRuntimeService(runtime),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-handle-fail",
        session_id=None,
        verbose=True,
    )

    response = command_service.handle(request)

    assert response["request_id"] == "req-handle-fail"
    assert response["ok"] is False
    assert response["error"] == {
        "code": "INPUT_BACKEND_MISSING",
        "message": "input backend missing",
    }
    assert response["screenshot"] == str(tmp_path / "input-fail.png")
    assert response["warnings"] == [{"code": "WINDOW_NOT_FOREGROUND", "message": "窗口未前台"}]
    assert response["references"] == [{"path": "trail/ref.png", "similarity": 0.97}]
    assert response["debug"] == {"trace": [{"step": "click"}]}
    assert registry.for_workspace(str(tmp_path)).request_status("req-handle-fail")["final_state"] == "failed_before_side_effect"


def test_handle_records_runtime_preflight_failure_in_journal(tmp_path: Path):
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=FailingRuntimeService(TrailError("WINDOW_NOT_FOUND", "window not found")),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-runtime-prefight-fail",
        session_id=None,
    )

    response = command_service.handle(request)

    status = registry.for_workspace(str(tmp_path)).request_status("req-runtime-prefight-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": "window not found",
    }
    assert status["final_state"] == "failed_before_side_effect"


def test_session_service_registry_is_thread_safe(tmp_path: Path, monkeypatch):
    created: list[object] = []

    class SlowSessionService:
        def __init__(self, *, workspace_root: Path):
            self.workspace_root = Path(workspace_root)
            created.append(self)
            time.sleep(0.05)

    monkeypatch.setattr("trail.daemon.session_service.SessionService", SlowSessionService)
    registry = SessionServiceRegistry()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(registry.for_workspace, str(tmp_path)) for _ in range(8)]

    services = [future.result() for future in futures]

    assert len(created) == 1
    assert len({id(service) for service in services}) == 1


def test_session_service_registry_reuses_equivalent_workspace_paths(tmp_path: Path):
    registry = SessionServiceRegistry()
    first = registry.for_workspace(str(tmp_path))
    equivalent = os.path.join(str(tmp_path), os.pardir, tmp_path.name)
    second = registry.for_workspace(equivalent)

    assert second is first


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive path semantics are Windows-specific")
def test_session_service_registry_reuses_workspace_paths_with_different_case(tmp_path: Path):
    registry = SessionServiceRegistry()
    first = registry.for_workspace(str(tmp_path))
    second = registry.for_workspace(str(tmp_path).upper())

    assert second is first


def test_handle_returns_unknown_result_when_recovery_finish_mutation_fails(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = StubRuntime(tmp_path / "recovery-finish-fail.png")
    command_service = CommandService(
        runtime_service=StubRuntimeService(runtime),
        session_service=registry,
    )
    request = _request(
        tmp_path,
        method="input.click",
        payload={"x": 10, "y": 20},
        request_id="req-recovery-finish-fail",
        session_id=session.session_id,
    )
    original_finish_mutation = service.finish_mutation

    def fail_mark_state_persisted(**kwargs):
        raise OSError("persist marker failed")

    def fail_recovery_finish_mutation(**kwargs):
        if kwargs["final_state"] != "completed":
            raise OSError("recovery finish failed")
        return original_finish_mutation(**kwargs)

    monkeypatch.setattr(service, "mark_state_persisted", fail_mark_state_persisted)
    monkeypatch.setattr(service, "finish_mutation", fail_recovery_finish_mutation)

    response = command_service.handle(request)

    status = registry.for_workspace(str(tmp_path)).request_status("req-recovery-finish-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["screenshot"] == str(tmp_path / "recovery-finish-fail.png")
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert "persist marker failed" in response["debug"]["detail"]
    assert "recovery finish failed" in response["debug"]["recovery_detail"]
    assert status["last_visible_stage"] == "responded"
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
