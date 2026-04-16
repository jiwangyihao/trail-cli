from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_build_cw_stage_detector_falls_back_to_settle_keywords_from_ocr():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []
            self.ocr_calls = 0

        def locate(self, template: str, **kwargs):
            self.templates.append(template)
            return None

        def ocr(self, **kwargs):
            self.ocr_calls += 1
            return [
                {"text": "挑战成功"},
                {"text": "1-1奖励"},
                {"text": "继续挑战"},
            ]

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "settle"
    assert runtime.ocr_calls == 1


def test_build_cw_stage_detector_reads_tuple_ocr_results_for_settle_keywords():
    class RuntimeSpy:
        def locate(self, template: str, **kwargs):
            return None

        def ocr(self, **kwargs):
            return [
                ([[837.0, 199.0], [1081.0, 199.0]], "挑战成功", 0.98),
                ([[911.0, 880.0], [1011.0, 880.0]], "继续挑战", 0.99),
            ]

    detector = stage_scene.build_cw_stage_detector(RuntimeSpy())

    assert detector() == "settle"


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


def test_wait_cw_stage_sleeps_between_detection_attempts(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    stages = iter([None, "settle"])
    sleeps: list[float] = []

    monkeypatch.setattr(stage_scene, "sleep", lambda seconds: sleeps.append(seconds))

    refreshed = stage_scene.wait_cw_stage(session, detector=lambda: next(stages), timeout=1)

    assert refreshed.scene_state["cw"]["stage"] == {"value": "settle", "stale": False}
    assert sleeps == [0.5]


def test_wait_cw_stage_raises_timeout_when_stage_missing(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ticks = iter([0.0, 0.2, 1.2])

    monkeypatch.setattr(stage_scene, "monotonic", lambda: next(ticks))

    with pytest.raises(TrailError) as exc_info:
        stage_scene.wait_cw_stage(session, detector=lambda: None, timeout=1)

    assert exc_info.value.code == "STAGE_TIMEOUT"
    assert str(exc_info.value) == "等待货币战争阶段超时"


def _build_cw_harness(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, cw_service, command_service


def _set_stage(session, stage: dict):
    session.scene_state.setdefault("cw", {})["stage"] = dict(stage)
    value = stage.get("value")
    if isinstance(value, str) and not stage.get("stale"):
        session.last_stage = {"scene": "cw", "value": value}
    return session


def test_cw_stage_detect_service_persists_stage_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["value"] == "preparation"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"


def test_cw_stage_wait_service_persists_waited_stage_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_stage",
        lambda session, detector, timeout: _set_stage(session, {"value": "settle", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.wait",
        payload={"session_id": session.session_id, "timeout": 120},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["value"] == "settle"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"] == {"value": "settle", "stale": False}


def test_cw_stage_detect_service_preserves_last_stage_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "event", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert result["value"] == "event"
    assert persisted.scene_state["cw"]["stage"]["value"] == "event"
    assert persisted.last_stage == {"scene": "cw", "value": "event"}
