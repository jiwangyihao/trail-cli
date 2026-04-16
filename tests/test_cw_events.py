from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.scenes.cw.models import ensure_cw_state
from trail.session.store import SessionStore


def result_text(result) -> str:
    return result.stdout + getattr(result, "stderr", "")


def load_cw_events_module():
    try:
        return importlib.import_module("trail.scenes.cw.events")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.events: {exc}")


@pytest.mark.parametrize(
    ("method_name", "expected_options"),
    [
        ("read_cw_replenish", [1, 2, 3]),
        ("read_cw_invest", [1, 2, 3]),
        ("read_cw_encounter", [1, 2]),
        ("read_cw_fortune", [1, 2]),
    ],
)
def test_event_reads_return_expected_options(tmp_path, method_name, expected_options):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)

    assert method(session) == {"options": expected_options}


@pytest.mark.parametrize(
    ("method_name", "option"),
    [
        ("choose_cw_replenish", 1),
        ("choose_cw_invest", 2),
        ("choose_cw_encounter", 1),
        ("choose_cw_fortune", 2),
    ],
)
def test_event_chooses_call_chooser_and_invalidate_stage_snapshot(tmp_path, method_name, option):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}
    chosen: list[int] = []

    refreshed = method(session, option=option, chooser=lambda selected: chosen.append(selected))

    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert chosen == [option]


def test_event_handle_returns_type_and_action(tmp_path):
    events_module = load_cw_events_module()
    handle_cw_event = getattr(events_module, "handle_cw_event", None)
    assert handle_cw_event is not None

    from tests.conftest import build_fake_cw_session, fake_event_handler

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "event", "stale": False}
    session.last_stage = {"scene": "cw", "value": "event"}
    result = handle_cw_event(session, handler=fake_event_handler)

    assert result == {"event_type": "special", "handled_action": "confirm"}
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


@pytest.mark.parametrize(
    ("method_name", "callback_name"),
    [
        ("confirm_cw_boss_preview", "confirmer"),
        ("settle_cw_next", "continuer"),
        ("start_cw_battle", "starter"),
        ("continue_cw_battle", "continuer"),
    ],
)
def test_boss_preview_settle_and_battle_actions_invalidate_stage_snapshot(tmp_path, method_name, callback_name):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "battle", "stale": False}
    session.last_stage = {"scene": "cw", "value": "battle"}
    calls: list[str] = []

    refreshed = method(session, **{callback_name: lambda: calls.append("called")})

    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert calls == ["called"]


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


def _set_stage(session, stage: dict):
    session.scene_state.setdefault("cw", {})["stage"] = dict(stage)
    session.last_stage = None
    return session


def _set_event_result(session, result: dict):
    session.scene_state.setdefault("cw", {})["stage"] = {"stale": True}
    session.last_stage = None
    return result


@pytest.mark.parametrize(
    ("method", "patch_name", "expected_options"),
    [
        ("cw.replenish.read", "read_cw_replenish", [1, 2, 3]),
        ("cw.invest.read", "read_cw_invest", [1, 2, 3]),
        ("cw.encounter.read", "read_cw_encounter", [1, 2]),
        ("cw.fortune.read", "read_cw_fortune", [1, 2]),
    ],
)
def test_cw_event_read_services_return_options(tmp_path: Path, monkeypatch, method: str, patch_name: str, expected_options: list[int]):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr(f"trail.daemon.cw_service.{patch_name}", lambda session: {"options": expected_options})

    result = cw_service.handle(
        method=method,
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result == {"options": expected_options}


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_data"),
    [
        (
            "cw.event.handle",
            "req-event-handle-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.event_handler_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.handle_cw_event",
                    lambda session, handler: _set_event_result(session, {"event_type": "special", "handled_action": "confirm"}),
                ),
            ),
            {"event_type": "special", "handled_action": "confirm"},
        ),
        (
            "cw.replenish.choose",
            "req-replenish-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.replenish_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_replenish", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.invest.choose",
            "req-invest-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.invest_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_invest", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.encounter.choose",
            "req-encounter-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.encounter_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_encounter", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.fortune.choose",
            "req-fortune-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.fortune_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_fortune", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.boss_preview.confirm",
            "req-boss-confirm-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.boss_preview_confirmer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.confirm_cw_boss_preview", lambda session, confirmer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.battle.start",
            "req-battle-start-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_starter_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.start_cw_battle", lambda session, starter: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.battle.continue",
            "req-battle-continue-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.continue_cw_battle", lambda session, continuer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.settle.next",
            "req-settle-next-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.settle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.settle_cw_next", lambda session, continuer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
    ],
)
def test_cw_event_mutations_flow_through_command_service_journal(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    payload: dict,
    setup_patches,
    expected_data: dict,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = service.request_status(request_id)
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == expected_data
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["stage"] == ({"stale": True} if method != "cw.event.handle" else {"stale": True})
