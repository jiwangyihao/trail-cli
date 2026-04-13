from __future__ import annotations

import importlib
import json

import pytest

from trail.cli import app
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


@pytest.mark.parametrize(
    ("argv", "expected_options"),
    [
        (["cw", "replenish", "read"], [1, 2, 3]),
        (["cw", "invest", "read"], [1, 2, 3]),
        (["cw", "encounter", "read"], [1, 2]),
        (["cw", "fortune", "read"], [1, 2]),
    ],
)
def test_cw_event_read_cli_returns_options(cli_runner, fake_runtime, fake_session, argv, expected_options, tmp_path):
    result = cli_runner.invoke(app, [*argv, "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"options": expected_options}
    assert payload["screenshot"]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.last_result == {
        "command": ".".join(argv),
        "ok": True,
        "data": {"options": expected_options},
        "error": None,
    }


@pytest.mark.parametrize(
    ("factory_name", "argv", "option"),
    [
        ("replenish_chooser_factory", ["cw", "replenish", "choose"], 1),
        ("invest_chooser_factory", ["cw", "invest", "choose"], 2),
        ("encounter_chooser_factory", ["cw", "encounter", "choose"], 1),
        ("fortune_chooser_factory", ["cw", "fortune", "choose"], 2),
    ],
)
def test_cw_event_choose_cli_invalidates_stage_snapshot(
    cli_runner,
    fake_runtime,
    fake_session,
    argv,
    factory_name,
    option,
    tmp_path,
    monkeypatch,
):
    import trail.commands.cw as cw_cmd

    chosen: list[int] = []
    monkeypatch.setattr(cw_cmd, factory_name, lambda runtime: (lambda selected: chosen.append(selected)), raising=False)

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}
    store.save(session)

    result = cli_runner.invoke(app, [*argv, "--session", fake_session, "--option", str(option)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert payload["screenshot"]
    assert chosen == [option]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


@pytest.mark.parametrize(
    ("argv", "option", "expected_clicks"),
    [
        (["cw", "replenish", "choose"], 1, [(0.2, 0.52), (0.88, 0.91)]),
        (["cw", "invest", "choose"], 2, [(0.5, 0.3), (0.77, 0.521)]),
        (["cw", "encounter", "choose"], 1, [(0.35, 0.5), (0.5, 0.84)]),
        (["cw", "fortune", "choose"], 2, [(0.8, 0.3), (0.77, 0.521)]),
    ],
)
def test_cw_event_choose_cli_uses_default_runtime_actions_and_clears_last_stage(
    cli_runner,
    fake_runtime,
    fake_session,
    argv,
    option,
    expected_clicks,
    tmp_path,
):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}
    store.save(session)

    result = cli_runner.invoke(app, [*argv, "--session", fake_session, "--option", str(option)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert fake_runtime.clicks == expected_clicks

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_cw_event_handle_cli_returns_type_and_action(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    monkeypatch.setattr(cw_cmd, "event_handler_factory", lambda runtime: (lambda: ("special", "confirm")), raising=False)

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"event_type": "special", "handled_action": "confirm"}
    assert payload["screenshot"]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.last_result == {
        "command": "cw.event.handle",
        "ok": True,
        "data": {"event_type": "special", "handled_action": "confirm"},
        "error": None,
    }


@pytest.mark.parametrize(
    ("factory_name", "argv"),
    [
        ("boss_preview_confirmer_factory", ["cw", "boss-preview", "confirm"]),
        ("settle_continuer_factory", ["cw", "settle", "next"]),
        ("battle_starter_factory", ["cw", "battle", "start"]),
        ("battle_continuer_factory", ["cw", "battle", "continue"]),
    ],
)
def test_cw_boss_preview_settle_and_battle_cli_invalidates_stage_snapshot(
    cli_runner,
    fake_runtime,
    fake_session,
    argv,
    factory_name,
    tmp_path,
    monkeypatch,
):
    import trail.commands.cw as cw_cmd

    calls: list[str] = []
    monkeypatch.setattr(cw_cmd, factory_name, lambda runtime: (lambda: calls.append("called")), raising=False)

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["stage"] = {"value": "settle", "stale": False}
    session.last_stage = {"scene": "cw", "value": "settle"}
    store.save(session)

    result = cli_runner.invoke(app, [*argv, "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert payload["screenshot"]
    assert calls == ["called"]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_cw_event_handle_cli_uses_default_handler_and_clears_last_stage(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["stage"] = {"value": "event", "stale": False}
    session.last_stage = {"scene": "cw", "value": "event"}
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"event_type": "special", "handled_action": "confirm"}
    assert fake_runtime.clicks == [(0.5, 0.25), (0.77, 0.521)]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


@pytest.mark.parametrize(
    ("argv", "expected_clicks"),
    [
        (["cw", "boss-preview", "confirm"], [(0.5, 0.7)]),
        (["cw", "settle", "next"], [(0.5, 0.82)]),
        (["cw", "battle", "start"], [(0.5, 0.824)]),
        (["cw", "battle", "continue"], [(0.5, 0.824)]),
    ],
)
def test_cw_boss_preview_settle_and_battle_cli_uses_default_runtime_actions_and_clears_last_stage(
    cli_runner,
    fake_runtime,
    fake_session,
    argv,
    expected_clicks,
    tmp_path,
):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["stage"] = {"value": "settle", "stale": False}
    session.last_stage = {"scene": "cw", "value": "settle"}
    store.save(session)

    result = cli_runner.invoke(app, [*argv, "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert fake_runtime.clicks == expected_clicks

    session = store.load(fake_session)
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


@pytest.mark.parametrize(
    ("argv"),
    [
        ["cw", "replenish", "read"],
        ["cw", "invest", "read"],
        ["cw", "encounter", "read"],
        ["cw", "fortune", "read"],
        ["cw", "boss-preview", "confirm"],
        ["cw", "battle", "start"],
        ["cw", "battle", "continue"],
        ["cw", "settle", "next"],
        ["cw", "event", "handle"],
    ],
)
def test_cw_event_cli_requires_session_for_read_and_handle_commands(cli_runner, argv):
    result = cli_runner.invoke(app, argv)
    output = result_text(result)

    assert result.exit_code != 0
    assert "Missing option" in output
    assert "--session" in output


@pytest.mark.parametrize(
    ("argv"),
    [
        ["cw", "replenish", "choose", "--option", "1"],
        ["cw", "invest", "choose", "--option", "2"],
        ["cw", "encounter", "choose", "--option", "1"],
        ["cw", "fortune", "choose", "--option", "2"],
    ],
)
def test_cw_event_choose_cli_requires_session_when_option_is_present(cli_runner, argv):
    result = cli_runner.invoke(app, argv)
    output = result_text(result)

    assert result.exit_code != 0
    assert "Missing option" in output
    assert "--session" in output
