from pathlib import Path

import pytest

from trail.commands.helpers import run_session_command
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.output.envelope import command_failure, command_success
from trail.session.store import SessionStore


def test_failure_envelope_contains_error_code_and_screenshot(tmp_path):
    result = command_failure(
        code="WINDOW_NOT_FOUND",
        message="window missing",
        screenshot=tmp_path / "fail.png",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "WINDOW_NOT_FOUND"
    assert result["screenshot"].endswith("fail.png")


def test_command_success_deep_copies_data(tmp_path):
    payload = {"items": ["银狼"]}

    result = command_success(data=payload, screenshot=tmp_path / "ok.png")
    payload["items"].append("卡芙卡")

    assert result["data"] == {"items": ["银狼"]}


class FakeRuntime:
    def __init__(self, screenshot_path: Path):
        self._shot = screenshot_path
        self.calls: list[bool] = []

    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        return self._shot


class OptionalCaptureFailsRuntime(FakeRuntime):
    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        if optional:
            raise RuntimeError("capture failed")
        return self._shot


class RequiredCaptureFailsRuntime(FakeRuntime):
    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        if not optional:
            raise RuntimeError("capture failed")
        return self._shot


def test_with_auto_capture_wraps_trail_error_as_failure(tmp_path):
    runtime = FakeRuntime(tmp_path / "failed.png")

    result = with_auto_capture(
        runtime,
        lambda: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", "window missing")),
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": "window missing",
    }
    assert result["screenshot"].endswith("failed.png")
    assert runtime.calls == [True]


def test_run_session_command_persists_last_result_and_last_screenshot(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after.png"),
        command_name="session.inspect",
        action=lambda loaded: {"session_id": loaded.session_id},
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is True
    assert loaded.last_result["command"] == "session.inspect"
    assert loaded.last_screenshot.endswith("after.png")


def test_run_session_command_persists_failure_result_and_last_screenshot(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after-fail.png"),
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", loaded.session_id)),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {"code": "WINDOW_NOT_FOUND", "message": session.session_id},
    }
    assert loaded.last_screenshot.endswith("after-fail.png")


def test_run_session_command_does_not_persist_partial_state_on_failure(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state = {"cw": {"coins": 5}}
    session.last_stage = {"scene": "cw", "value": "shop"}
    store.save(session)

    def fail_after_mutation(loaded):
        loaded.scene_state["cw"]["coins"] = 99
        loaded.last_stage = {"scene": "cw", "value": "battle"}
        raise TrailError("WINDOW_NOT_FOUND", loaded.session_id)

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after-fail.png"),
        command_name="session.inspect",
        action=fail_after_mutation,
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert loaded.scene_state == {"cw": {"coins": 5}}
    assert loaded.last_stage == {"scene": "cw", "value": "shop"}


def test_run_session_command_wraps_unexpected_exception_and_persists_failure(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = FakeRuntime(tmp_path / "after-crash.png")

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(RuntimeError(f"boom:{loaded.session_id}")),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert result["error"] == {
        "code": "UNEXPECTED_ERROR",
        "message": f"RuntimeError: boom:{session.session_id}",
    }
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {
            "code": "UNEXPECTED_ERROR",
            "message": f"RuntimeError: boom:{session.session_id}",
        },
    }
    assert loaded.last_screenshot.endswith("after-crash.png")
    assert runtime.calls == [True]


def test_run_session_command_keeps_success_when_required_capture_fails(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = RequiredCaptureFailsRuntime(tmp_path / "after-success.png")

    def succeed_and_mutate(loaded):
        loaded.scene_state["cw"] = {"coins": 12}
        return {"session_id": loaded.session_id}

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=succeed_and_mutate,
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is True
    assert result["error"] is None
    assert result["screenshot"] is None
    assert loaded.scene_state == {"cw": {"coins": 12}}
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": True,
        "data": {"session_id": session.session_id},
        "error": None,
    }
    assert loaded.last_screenshot is None
    assert runtime.calls == [False]


def test_run_session_command_persists_failure_when_optional_capture_also_fails(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = OptionalCaptureFailsRuntime(tmp_path / "after-fail.png")

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", loaded.session_id)),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert result["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": session.session_id,
    }
    assert result["screenshot"] is None
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {"code": "WINDOW_NOT_FOUND", "message": session.session_id},
    }
    assert loaded.last_screenshot is None
    assert runtime.calls == [True]
