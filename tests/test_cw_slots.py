from __future__ import annotations

import importlib
import json
from copy import deepcopy

import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.store import SessionStore


def load_cw_slots_module():
    try:
        return importlib.import_module("trail.scenes.cw.slots")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.slots: {exc}")


def fake_reader():
    return ["希儿"], ["佩拉"], ["银狼", None, "阮·梅"]


def build_fake_cw_session(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    state = ensure_cw_state(session)
    state["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    return session


def test_slots_read_refreshes_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    front = ["希儿"]
    back = ["佩拉"]
    hand = ["银狼", None, "阮·梅"]
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    refreshed = read_cw_slots(session, reader=lambda: (front, back, hand))

    front.append("卡芙卡")
    back.clear()
    hand[0] = "停云"

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_slots_swap_invalidates_existing_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    swap_cw_slots = getattr(slots_module, "swap_cw_slots", None)
    assert swap_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    refreshed = swap_cw_slots(session, source="hand:0", target="front:0")

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }


def test_slots_place_one_invalidates_existing_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    place_one_cw_slot = getattr(slots_module, "place_one_cw_slot", None)
    assert place_one_cw_slot is not None

    session = build_fake_cw_session(tmp_path)
    refreshed = place_one_cw_slot(session, source="hand:2", target="back:0")

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }


def test_collect_cw_crystals_records_metric(tmp_path):
    slots_module = load_cw_slots_module()
    collect_cw_crystals = getattr(slots_module, "collect_cw_crystals", None)
    assert collect_cw_crystals is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = collect_cw_crystals(session)

    assert refreshed.scene_state["cw"]["metrics"]["last_crystal_collection"] == "done"


def test_sell_plan_returns_candidates_and_refreshes_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    result = plan_cw_hand_sell(session)

    assert result == {"candidates": [0, 2]}
    assert session.scene_state["cw"]["sell_plan"] == {"candidates": [0, 2]}
    assert session.scene_state["cw"]["slots"] == before_slots

    result["candidates"].append(9)
    assert session.scene_state["cw"]["sell_plan"] == {"candidates": [0, 2]}


def test_sell_plan_rejects_stale_slots_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = True
    session.scene_state["cw"]["sell_plan"] = {}

    with pytest.raises(TrailError) as exc_info:
        plan_cw_hand_sell(session)

    assert exc_info.value.code == "SLOTS_STALE"
    assert session.scene_state["cw"]["sell_plan"] == {}


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("swap_cw_slots", {"source": "hand:0", "target": "front:0"}),
        ("place_one_cw_slot", {"source": "hand:2", "target": "back:0"}),
        ("sell_one_cw_hand", {"slot": 2}),
    ],
)
def test_slots_mutations_clear_sell_plan(tmp_path, method_name, kwargs):
    slots_module = load_cw_slots_module()
    method = getattr(slots_module, method_name, None)
    assert method is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}

    refreshed = method(session, **kwargs)

    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_sell_one_marks_slots_snapshot_stale(tmp_path):
    slots_module = load_cw_slots_module()
    sell_one_cw_hand = getattr(slots_module, "sell_one_cw_hand", None)
    assert sell_one_cw_hand is not None

    session = build_fake_cw_session(tmp_path)
    refreshed = sell_one_cw_hand(session, slot=2)

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }


def test_cw_slots_read_cli_refreshes_snapshot(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    monkeypatch.setattr(cw_cmd, "slots_reader", fake_reader, raising=False)

    result = cli_runner.invoke(app, ["cw", "slots", "read", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]


def test_cw_slots_swap_cli_marks_snapshot_stale(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "slots", "swap", "--session", fake_session, "--source", "hand:0", "--target", "front:0"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }

    session = store.load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]


def test_cw_slots_place_one_cli_marks_snapshot_stale(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "slots", "place-one", "--session", fake_session, "--source", "hand:2", "--target", "back:0"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }

    session = store.load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]


def test_cw_crystals_collect_cli_records_metric(cli_runner, fake_runtime, fake_session, tmp_path):
    result = cli_runner.invoke(app, ["cw", "crystals", "collect", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"last_crystal_collection": "done"}

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["metrics"] == payload["data"]


def test_cw_hand_sell_plan_cli_returns_candidates_and_persists_snapshot(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "hand", "sell-plan", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"candidates": [0, 2]}

    session = store.load(fake_session)
    assert session.scene_state["cw"]["sell_plan"] == payload["data"]
    assert session.scene_state["cw"]["slots"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }


def test_cw_hand_sell_plan_cli_rejects_stale_slots_snapshot(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }
    ensure_cw_state(session)["sell_plan"] = {}
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "hand", "sell-plan", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SLOTS_STALE"

    session = store.load(fake_session)
    assert session.scene_state["cw"]["sell_plan"] == {}


def test_cw_hand_sell_plan_then_slots_swap_cli_clears_sell_plan(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    store.save(session)

    sell_plan_result = cli_runner.invoke(app, ["cw", "hand", "sell-plan", "--session", fake_session])
    assert sell_plan_result.exit_code == 0

    swap_result = cli_runner.invoke(app, ["cw", "slots", "swap", "--session", fake_session, "--source", "hand:0", "--target", "front:0"])
    assert swap_result.exit_code == 0

    session = store.load(fake_session)
    assert session.scene_state["cw"]["sell_plan"] == {}


def test_cw_hand_sell_one_cli_marks_slots_stale(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": False,
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "hand", "sell-one", "--session", fake_session, "--slot", "2"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "front": ["希儿"],
        "back": ["佩拉"],
        "hand": ["银狼", None, "阮·梅"],
        "stale": True,
    }

    session = store.load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]
