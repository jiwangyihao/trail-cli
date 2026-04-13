from __future__ import annotations

import json

import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw import stage as stage_scene
from trail.session.store import SessionStore


def test_build_cw_stage_detector_maps_resource_aliases_to_stage_values():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []

        def locate(self, template: str, **kwargs):
            self.templates.append(template)
            if str(template).endswith("fortune_teller.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "fortune"
    assert any(str(template).endswith("fortune_teller.png") for template in runtime.templates)


def test_build_cw_stage_detector_maps_fold_to_shop():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []

        def locate(self, template: str, **kwargs):
            self.templates.append(template)
            if str(template).endswith("fold.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "shop"


def test_build_cw_stage_detector_maps_replenish_template_to_replenish():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []

        def locate(self, template: str, **kwargs):
            self.templates.append(template)
            if str(template).endswith("replenish_stage.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "replenish"


def test_detect_cw_stage_refreshes_stage_snapshot(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    refreshed = stage_scene.detect_cw_stage(session, detector=lambda: "shop")

    assert refreshed.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert refreshed.last_stage == {"scene": "cw", "value": "shop"}


def test_wait_cw_stage_retries_until_stage_is_detected(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    stages = iter([None, None, "settle"])

    refreshed = stage_scene.wait_cw_stage(session, detector=lambda: next(stages), timeout=1)

    assert refreshed.scene_state["cw"]["stage"] == {"value": "settle", "stale": False}
    assert refreshed.last_stage == {"scene": "cw", "value": "settle"}


def test_wait_cw_stage_raises_timeout_when_stage_missing(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ticks = iter([0.0, 0.2, 1.2])

    monkeypatch.setattr(stage_scene, "monotonic", lambda: next(ticks))

    with pytest.raises(TrailError) as exc_info:
        stage_scene.wait_cw_stage(session, detector=lambda: None, timeout=1)

    assert exc_info.value.code == "STAGE_TIMEOUT"
    assert str(exc_info.value) == "等待货币战争阶段超时"


def test_cw_stage_detect_cli_refreshes_stage_snapshot(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    monkeypatch.setattr(cw_cmd, "stage_detector_factory", lambda runtime: (lambda: "event"), raising=False)

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"value": "event", "stale": False}
    assert payload["screenshot"]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["stage"] == payload["data"]
    assert session.last_stage == {"scene": "cw", "value": "event"}


def test_cw_stage_detect_cli_refreshes_replenish_stage(cli_runner, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    store = SessionStore(tmp_path / ".trail" / "sessions")

    class RuntimeSpy:
        def __init__(self, screenshot_path):
            self._shot = screenshot_path

        def capture_after_action(self, optional: bool = False):
            return self._shot

        def locate(self, template: str, **kwargs):
            if str(template).endswith("replenish_stage.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    monkeypatch.setattr(
        cw_cmd,
        "runtime_factory",
        lambda **kwargs: RuntimeSpy(tmp_path / "after.png"),
        raising=False,
    )

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"value": "replenish", "stale": False}
    assert payload["screenshot"]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"value": "replenish", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "replenish"}


def test_cw_stage_wait_cli_returns_timeout_error(cli_runner, fake_runtime, fake_session, monkeypatch):
    import trail.commands.cw as cw_cmd

    ticks = iter([0.0, 0.2, 1.2])
    monkeypatch.setattr(stage_scene, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cw_cmd, "stage_detector_factory", lambda runtime: (lambda: None), raising=False)

    result = cli_runner.invoke(app, ["cw", "stage", "wait", "--session", fake_session, "--timeout", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "STAGE_TIMEOUT",
        "message": "等待货币战争阶段超时",
    }
    assert payload["screenshot"]


@pytest.mark.parametrize(
    ("argv"),
    [
        ["detect"],
        ["wait", "--timeout", "1"],
    ],
)
def test_cw_stage_cli_uses_run_session_command_failure_persistence(cli_runner, fake_runtime, fake_session, monkeypatch, argv):
    import trail.commands.cw as cw_cmd

    captured: dict = {}
    monkeypatch.setattr(cw_cmd, "stage_detector_factory", lambda runtime: (lambda: "event"), raising=False)

    def fake_run_session_command(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "data": {"value": "event", "stale": False},
            "error": None,
            "screenshot": "after.png",
            "timing": {},
        }

    monkeypatch.setattr(cw_cmd, "run_session_command", fake_run_session_command, raising=False)

    result = cli_runner.invoke(app, ["cw", "stage", *argv, "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert callable(captured["failure_persistence"])
