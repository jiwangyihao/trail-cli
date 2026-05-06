from __future__ import annotations

import importlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box
from trail.scenes.cw.models import ensure_cw_state
from trail.session.store import SessionStore


SESSION_ID = "s" * 32


def load_cw_slots_module():
    try:
        return importlib.import_module("trail.scenes.cw.slots")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.slots: {exc}")


def fake_reader():
    return ["希儿"], ["佩拉"], ["银狼", None, "阮·梅"]


def _read_cw_slots_variable_cost(session, snapshot, **kwargs):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None
    return read_cw_slots(session, reader=lambda: snapshot, **kwargs)


def _plan_cw_hand_sell_variable_cost(session):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None
    return plan_cw_hand_sell(session)


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


def _complete_guide(**overrides: object) -> dict[str, object]:
    guide: dict[str, object] = {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "guide-demo",
        "title": "测试攻略",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": "前期按测试运营",
        "role_stages": [],
        "first_fight_augments": [],
        "second_fight_augments": [],
        "order_basic": [],
        "order_compose": [],
    }
    guide.update(overrides)
    return guide


def seed_fresh_stage_status(session):
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        "status": {
            "stale": False,
            "level": 7,
            "role_count": {"front": 1, "back": 1, "hand": 2, "field": 2, "total": 4},
        },
    }


def _stage_module():
    return importlib.import_module("trail.scenes.cw.stage")


def _assert_slots_read_result(result):
    assert type(result).__name__ == "CwSlotsReadResult"
    return result


def _stage_status_capture_calls():
    stage_scene = _stage_module()
    return [
        {**stage_scene.CW_STATUS_LEVEL_REGION, "normalize": False},
        {**stage_scene.CW_STATUS_EXP_REGION, "normalize": False},
        {**stage_scene.CW_STATUS_TEAM_SIZE_REGION, "normalize": False},
    ]


def _packed_ocr_pieces(targets: list[dict[str, object]]):
    targets_key = tuple((target["key"], target["size"], target.get("text")) for target in targets)
    return deepcopy(_packed_ocr_pieces_cached(targets_key))


@lru_cache(maxsize=None)
def _packed_ocr_pieces_cached(targets_key):
    from trail.runtime.batch_ocr import BatchOcrTarget, pack_batch_ocr_targets

    batch_targets = [
        BatchOcrTarget(key, Image.new("RGB", size))
        for key, size, _text in targets_key
    ]
    _, packed = pack_batch_ocr_targets(batch_targets)
    pieces = []
    for (_key, _size, text), item in zip(targets_key, packed, strict=True):
        if not text:
            continue
        rect = item.content_rect
        pieces.append(
            {
                "text": text,
                "box": {
                    "left": rect["left"] + 2,
                    "top": rect["top"] + 2,
                    "width": max(1, min(60, rect["width"] - 4)),
                    "height": max(1, min(20, rect["height"] - 4)),
                },
            }
        )
    return pieces


def _consume_pending_batch_targets(runtime):
    offset = getattr(runtime, "_batch_target_offset", 0)
    pending = runtime.batch_targets[offset:]
    runtime._batch_target_offset = len(runtime.batch_targets)
    return pending


def _record_batch_capture(slots_module, runtime, kwargs, image, *, slot_text: str | None = None):
    stage_scene = _stage_module()
    key = None
    text = None
    if kwargs == {**stage_scene.CW_STATUS_LEVEL_REGION, "normalize": False}:
        key = ("stage_status", "level")
        text = "LV.7"
    elif kwargs == {**stage_scene.CW_STATUS_EXP_REGION, "normalize": False}:
        key = ("stage_status", "exp")
        text = "4/52"
    elif kwargs == {**stage_scene.CW_STATUS_TEAM_SIZE_REGION, "normalize": False}:
        key = ("stage_status", "team_size")
        text = "3/3"
    elif kwargs == {**slots_module.SLOT_NAME_REGION, "normalize": False}:
        current_slot = getattr(runtime, "current_slot", None)
        assert current_slot is not None
        key = ("slot", *current_slot)
        text = slot_text

    if key is not None:
        runtime.batch_targets.append({"key": key, "size": image.size, "text": text})


@pytest.mark.parametrize(
    ("args", "command", "payload"),
    [
        (
            ["cw", "slots", "read", "--session", SESSION_ID, "--slot", "front:1", "--slot", "back:2"],
            "cw.slots.read",
            {"slot": ["front:0", "back:1"]},
        ),
        (
            ["cw", "slots", "swap", "--session", SESSION_ID, "--source", "hand:1", "--target", "front:1"],
            "cw.slots.swap",
            {"source": "hand:0", "target": "front:0"},
        ),
        (
            ["cw", "slots", "place", "--session", SESSION_ID, "--action", "hand:1,front:1"],
            "cw.slots.place",
            {"actions": [{"source": "hand:0", "target": "front:0"}]},
        ),
        (
            ["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "1", "--slot", "3"],
            "cw.hand.sell",
            {"slots": [0, 2]},
        ),
    ],
)
def test_cw_slot_cli_converts_agent_visible_slots_to_internal_zero_based(
    cli_runner,
    monkeypatch,
    args,
    command,
    payload,
):
    from trail.cli import app

    captured = {}
    monkeypatch.setattr(
        "trail.commands.cw._print_cw",
        lambda command, *, session_id, payload=None: captured.update(command=command, session_id=session_id, payload=payload),
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert captured == {"command": command, "session_id": SESSION_ID, "payload": payload}


@pytest.mark.parametrize(
    ("args", "command", "leaked_refs"),
    [
        (["cw", "slots", "read", "--session", SESSION_ID, "--slot", "front:0"], "cw.slots.read", ["front:0"]),
        (
            ["cw", "slots", "swap", "--session", SESSION_ID, "--source", "hand:0", "--target", "front:1"],
            "cw.slots.swap",
            ["hand:0"],
        ),
        (["cw", "slots", "place", "--session", SESSION_ID, "--action", "hand:0,front:1"], "cw.slots.place", ["hand:0"]),
        (["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "0"], "cw.hand.sell", ["hand:0"]),
    ],
)
def test_cw_slot_cli_rejects_zero_agent_visible_slot_without_rpc(cli_runner, monkeypatch, args, command, leaked_refs):
    from trail.cli import app

    calls = []
    monkeypatch.setattr(
        "trail.commands.cw._print_cw",
        lambda command, *, session_id, payload=None: calls.append((command, session_id, payload)),
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0] == f"fail {command} code=CW_OPTION_INVALID"
    for leaked_ref in leaked_refs:
        assert leaked_ref not in result.stdout
    assert calls == []


@pytest.mark.parametrize("action", ["hand:0-front:0", "hand:1-front:1"])
def test_cw_slots_place_malformed_action_omits_slot_refs_without_rpc(cli_runner, monkeypatch, action):
    from trail.cli import app

    calls = []
    monkeypatch.setattr(
        "trail.commands.cw._print_cw",
        lambda command, *, session_id, payload=None: calls.append((command, session_id, payload)),
    )

    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID, "--action", action])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.place code=CW_OPTION_INVALID",
        'why msg="cw slots place action invalid"',
    ]
    assert "hand:" not in result.stdout
    assert "front:" not in result.stdout
    assert "hand:2-front:2" not in result.stdout
    assert calls == []


def test_cw_hand_sell_rejects_non_numeric_slot_with_protocol_failure(cli_runner, monkeypatch):
    from trail.cli import app

    calls = []
    monkeypatch.setattr(
        "trail.commands.cw._print_cw",
        lambda command, *, session_id, payload=None: calls.append((command, session_id, payload)),
    )

    result = cli_runner.invoke(app, ["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "abc"])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0] == "fail cw.hand.sell code=CW_OPTION_INVALID"
    assert "Usage:" not in result.stdout
    assert calls == []


def test_slots_module_does_not_expose_legacy_strip_ocr_helpers():
    slots_module = load_cw_slots_module()

    for helper_name in (
        "_compose_slot_name_strip_image",
        "_map_ocr_pieces_to_slot_names",
        "_read_batch_slot_names",
        "_read_piece_box",
        "_find_slot_name_layout",
        "_record_slot_name_piece_drops",
    ):
        assert not hasattr(slots_module, helper_name)


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


def test_canonical_cw_role_slots_deduplicates_front_back_hand_order():
    slots = load_cw_slots_module()
    snapshot = {
        "front": [{"name": "希儿"}],
        "back": [{"name": "佩拉"}, {"name": "希儿"}],
        "hand": ["希儿", "银狼", {"name": "银狼"}],
        "stale": False,
    }

    roles = slots.canonical_cw_role_slots(snapshot)

    assert [(role["pos"], role["name"]) for role in roles] == [
        ("front:1", "希儿"),
        ("back:1", "佩拉"),
        ("hand:2", "银狼"),
    ]


def test_read_cw_slots_records_lv999_variable_cost_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    assert result.response_snapshot["front"][0]["cost"] == 4
    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 1
    assert state["choice_available"] is False


def test_read_cw_slots_marks_fielded_lv999_two_star_choice_available(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 2
    assert state["choice_available"] is True
    assert state["choice_pending_after_fielding"] is False


def test_read_cw_slots_marks_hand_lv999_two_star_pending_only(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([], [], [{"name": "银狼LV.999", "cost": 4, "star": 2}])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 2
    assert state["choice_available"] is False
    assert state["choice_pending_after_fielding"] is True


def test_read_cw_slots_marks_mixed_field_one_star_and_hand_two_star_pending(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = (
        [{"name": "银狼LV.999", "cost": 4, "star": 1}],
        [],
        [{"name": "银狼LV.999", "cost": 4, "star": 2}],
    )

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 2
    assert state["choice_available"] is False
    assert state["choice_pending_after_fielding"] is True


def test_read_cw_slots_fills_missing_lv999_cost_from_known_current_phase(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    snapshot = ([{"name": "银狼LV.999", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1
    assert session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 4


def test_read_cw_slots_does_not_backfill_lv999_cost_when_variable_cost_roles_stale(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles_stale"] = True
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1}}
    snapshot = ([{"name": "银狼LV.999", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["uncertain"] is True
    assert role["stale"] is True
    assert "cost" not in role
    assert result.response_snapshot["warnings"][0]["code"] == "CW_SLOTS_LV999_COST_UNKNOWN"
    variable_roles = session.scene_state["cw"]["variable_cost_roles"]
    assert isinstance(variable_roles, dict)
    variable_role = variable_roles["银狼LV.999"]
    assert isinstance(variable_role, dict)
    assert session.scene_state["cw"]["variable_cost_roles_stale"] is True
    assert variable_role["cost"] == 4


def test_read_cw_slots_clears_variable_cost_roles_stale_after_reliable_lv999_observation(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles_stale"] = True
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1}}
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 1}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    assert session.scene_state["cw"]["variable_cost_roles_stale"] is False


def test_read_cw_slots_keeps_variable_cost_roles_stale_without_reliable_lv999_observation(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles_stale"] = True
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1}}
    snapshot = ([{"name": "希儿"}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    assert session.scene_state["cw"]["variable_cost_roles_stale"] is True


def test_read_cw_slots_targeted_read_does_not_refresh_lv999_from_unread_previous_snapshot(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles_stale"] = True
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1}}
    cw_state["slots"] = {
        "front": [{"name": "银狼LV.999", "cost": 4, "star": 1}, None, None, None],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }
    snapshot = ([None, {"name": "希儿"}, None, None], [None] * 6, [None] * 9)

    result = _read_cw_slots_variable_cost(session, snapshot, targets=["front:1"])

    assert session.scene_state["cw"]["variable_cost_roles_stale"] is True
    response_lv999 = result.response_snapshot["front"][0]
    assert response_lv999["name"] == "银狼LV.999"
    assert response_lv999["uncertain"] is True
    assert response_lv999["stale"] is True
    assert "cost" not in response_lv999
    assert "star" not in response_lv999
    persisted_lv999 = session.scene_state["cw"]["slots"]["front"][0]
    assert persisted_lv999["name"] == "银狼LV.999"
    assert persisted_lv999["uncertain"] is True
    assert persisted_lv999["stale"] is True
    assert "cost" not in persisted_lv999
    assert "star" not in persisted_lv999


def test_read_cw_slots_marks_lv999_missing_cost_uncertain_without_reliable_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["uncertain"] is True
    assert role["stale"] is True
    assert "cost" not in role
    assert "银狼LV.999" not in session.scene_state["cw"].get("variable_cost_roles", {})


def test_read_cw_slots_fills_invalid_lv999_cost_from_known_current_phase(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    snapshot = ([{"name": "银狼LV.999", "cost": 2, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1
    assert session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 4


def test_read_cw_slots_marks_invalid_lv999_cost_uncertain_without_reliable_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": "bad", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["uncertain"] is True
    assert role["stale"] is True
    assert "cost" not in role
    assert "银狼LV.999" not in session.scene_state["cw"].get("variable_cost_roles", {})


def test_read_cw_slots_allows_new_cost_phase_choice_after_previous_cost_confirmed(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {
            "cost": 4,
            "star": 1,
            "choice_available": False,
            "last_confirmed_choice": "cost_up",
            "last_confirmed_choice_cost": 3,
            "confirmed_choices_by_cost": {3: "cost_up"},
        }
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is True
    assert state["choice_confirmed"] is False


def test_read_cw_slots_does_not_reopen_same_cost_confirmed_choice(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {
            "cost": 4,
            "star": 2,
            "choice_available": False,
            "last_confirmed_choice": "equipment",
            "last_confirmed_choice_cost": 4,
            "confirmed_choices_by_cost": {4: "equipment"},
        }
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is False
    assert state["choice_confirmed"] is True


def test_read_cw_slots_normalizes_persisted_choice_cost_keys(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {
            "cost": 4,
            "star": 2,
            "choice_available": False,
            "last_confirmed_choice": "equipment",
            "last_confirmed_choice_cost": 4,
            "confirmed_choices_by_cost": {"4": "equipment"},
        }
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is False
    assert state["choice_confirmed"] is True
    assert state["confirmed_choices_by_cost"] == {4: "equipment"}


def test_read_cw_slots_keeps_lv999_name_when_role_id_conflicts_with_plain_silver_wolf(tmp_path):
    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "roles": [
            {"id": "1006", "name": "银狼", "trait_ids": []},
            {"id": "15062", "name": "银狼LV.999", "trait_ids": []},
        ],
        "traits": [],
    }
    snapshot = ([{"name": "银狼LV.999", "role_id": "1006", "cost": 4, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot, guide_config=guide_config)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1


def test_read_cw_slots_preserves_equipments_for_new_canonical_only(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {
        "front": [{"name": "希儿", "equipments": ["高周波电锯"]}],
        "back": [None],
        "hand": [{"name": "希儿", "equipments": ["错误副本装备"]}, {"name": "佩拉", "equipments": ["战场手册"]}],
        "stale": False,
    }

    def reader():
        return [None], [{"name": "希儿"}], [{"name": "希儿"}, {"name": "佩拉"}]

    result = slots.read_cw_slots(session, reader=reader, guide_config=None)

    assert result.response_snapshot["back"][0]["equipments"] == ["高周波电锯"]
    assert "equipments" not in result.response_snapshot["hand"][0]
    assert result.response_snapshot["hand"][1]["equipments"] == ["战场手册"]
    assert cw_state["slots"]["back"][0]["equipments"] == ["高周波电锯"]
    assert "equipments" not in cw_state["slots"]["hand"][0]


def test_read_cw_slots_persists_stage_status_without_overwriting_stage_value(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False}
    result = slots.CwSlotsReadResult(
        front=[{"name": "希儿", "star": 4}, None, None, None],
        back=[None, None, None, None, None, None],
        hand=[None, None, None, None, None, None, None, None, None],
        stage_status={"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    )

    refreshed = slots.read_cw_slots(session, reader=lambda: result)

    assert refreshed.scene_state["cw"]["stage"]["value"] == "shop"
    assert refreshed.scene_state["cw"]["stage"]["stale"] is False
    assert refreshed.scene_state["cw"]["stage"]["status"]["level"] == 7
    assert refreshed.scene_state["cw"]["stage"]["status"]["exp"] == "4/52"
    assert refreshed.scene_state["cw"]["stage"]["status"]["team_size"] == "3/3"
    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"] == {
        "front": 1,
        "back": 0,
        "hand": 0,
        "field": 1,
        "total": 1,
    }


def test_slots_read_response_snapshot_carries_precaptured_screenshot(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    screenshot = tmp_path / ".trail" / "shots" / "slots.png"
    result = slots.CwSlotsReadResult(
        front=[{"name": "希儿"}, None, None, None],
        back=[None] * 6,
        hand=[None] * 9,
        screenshot=str(screenshot),
    )

    refreshed = slots.read_cw_slots(session, reader=lambda: result)

    assert refreshed.response_snapshot["_screenshot"].endswith("slots.png")
    assert "_screenshot" not in refreshed.scene_state["cw"]["slots"]


def test_slots_read_unknown_full_snapshot_fails_without_marking_empty(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    previous_slots = deepcopy(session.scene_state["cw"]["slots"])
    unknown = {"match_kind": "unknown", "raw_name": "", "score": 0.12}

    with pytest.raises(TrailError) as exc_info:
        slots.read_cw_slots(
            session,
            reader=lambda: ([unknown, None, None, None], [None] * 6, [None] * 9),
        )

    assert exc_info.value.code == "SLOTS_RECOGNITION_UNCERTAIN"
    assert session.scene_state["cw"]["slots"] == previous_slots


def test_slots_read_unknown_target_preserves_fresh_previous(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": [
            {
                "name": "希儿",
                "role_id": "1102",
                "raw_name": "旧希儿",
                "match_score": 0.88,
                "score": 0.88,
                "match_kind": "fuzzy",
                "candidates": [{"name": "希儿", "score": 0.88}],
                "empty_score": 0.02,
                "fee_color": "gold",
                "star_boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}],
                "confidence_reason": "legacy",
            },
            None,
            None,
            None,
        ],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }
    unknown = {"match_kind": "unknown", "raw_name": "", "score": 0.12}

    refreshed = slots.read_cw_slots(
        session,
        reader=lambda: ([unknown, None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    preserved = {"name": "希儿", "role_id": "1102"}
    assert refreshed.response_snapshot["front"][0] == preserved
    assert refreshed.scene_state["cw"]["slots"]["front"][0] == preserved
    assert refreshed.response_snapshot["warnings"][0]["code"] == "SLOTS_RECOGNITION_UNCERTAIN"
    assert refreshed.response_snapshot["warnings"][0]["position"] == {"kind": "slot", "area": "front", "index": 0}
    assert refreshed.response_snapshot["warnings"][0]["preserved_previous"] == 1


def test_slots_read_unknown_target_without_previous_value_fails(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": [None, None, None, None],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }
    previous_slots = deepcopy(session.scene_state["cw"]["slots"])
    unknown = {"match_kind": "unknown", "raw_name": "", "score": 0.12}

    with pytest.raises(TrailError) as exc_info:
        slots.read_cw_slots(
            session,
            reader=lambda: ([unknown, None, None, None], [None] * 6, [None] * 9),
            targets=["front:0"],
        )

    assert exc_info.value.code == "SLOTS_RECOGNITION_UNCERTAIN"
    assert session.scene_state["cw"]["slots"] == previous_slots


def test_slots_read_unknown_target_with_stale_previous_fails(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = True
    previous_slots = deepcopy(session.scene_state["cw"]["slots"])
    unknown = {"match_kind": "unknown", "raw_name": "", "score": 0.12}

    with pytest.raises(TrailError) as exc_info:
        slots.read_cw_slots(
            session,
            reader=lambda: ([unknown, None, None, None], [None] * 6, [None] * 9),
            targets=["front:0"],
        )

    assert exc_info.value.code == "SLOTS_RECOGNITION_UNCERTAIN"
    assert session.scene_state["cw"]["slots"] == previous_slots


def test_read_cw_slots_projects_stage_and_status_into_slots_payload(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)

    def reader():
        return slots_module.CwSlotsReadResult(
            front=[{"name": "希儿"}],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
        )

    slots_module.read_cw_slots(session, reader=reader)

    payload = session.scene_state["cw"]["slots"]
    assert payload["stage"] == "preparation"
    assert payload["stage_stale"] is False
    assert payload["stage_status"] == {
        "stale": False,
        "level": 3,
        "exp": "0/8",
        "team_size": "1/2",
        "role_count": {"front": 1, "back": 0, "hand": 0, "field": 1, "total": 1},
    }
    assert payload["stage_status_stale"] is False
    assert session.scene_state["cw"]["stage"]["value"] == "preparation"
    assert session.last_stage == {"scene": "cw", "value": "preparation"}


def test_read_cw_slots_projects_unknown_stage_status_into_slots_payload(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}

    def reader():
        return slots_module.CwSlotsReadResult(
            front=[{"name": "希儿"}],
            back=[],
            hand=[],
            stage=None,
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
        )

    slots_module.read_cw_slots(session, reader=reader)

    payload = session.scene_state["cw"]["slots"]
    assert payload["stage"] is None
    assert payload["stage_stale"] is False
    assert payload["stage_status"] == {
        "stale": False,
        "level": 3,
        "exp": "0/8",
        "team_size": "1/2",
        "role_count": {"front": 1, "back": 0, "hand": 0, "field": 1, "total": 1},
    }
    assert payload["stage_status_stale"] is False
    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["stale"] is False
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_read_cw_slots_directed_read_still_projects_status_from_merged_slots(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)["slots"] = {
        "front": [{"name": "旧希儿"}, None],
        "back": [{"name": "佩拉"}],
        "hand": [{"name": "停云"}],
        "stale": False,
    }

    def reader():
        return slots_module.CwSlotsReadResult(
            front=[{"name": "新希儿"}, None],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"},
        )

    slots_module.read_cw_slots(session, reader=reader, targets=["front:0"])

    assert session.scene_state["cw"]["slots"]["back"][0] == {"name": "佩拉"}
    assert session.scene_state["cw"]["slots"]["hand"][0] == {"name": "停云"}
    assert session.scene_state["cw"]["slots"]["stage_status"]["role_count"] == {
        "front": 1,
        "back": 1,
        "hand": 1,
        "field": 2,
        "total": 3,
    }


def test_read_cw_slots_invalidates_stage_when_stage_ambiguous(tmp_path):
    slots_module = load_cw_slots_module()
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "level": 3}}
    session.last_stage = {"scene": "cw", "value": "shop"}

    def reader():
        raise TrailError("STAGE_AMBIGUOUS", "当前资源无法区分阶段: preparation, shop")

    with pytest.raises(TrailError) as exc_info:
        slots_module.read_cw_slots(session, reader=reader)

    assert exc_info.value.code == "STAGE_AMBIGUOUS"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["error"]["code"] == "STAGE_AMBIGUOUS"
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 3}
    assert session.last_stage is None


def test_build_cw_slot_roles_reader_does_not_capture_status_regions(monkeypatch):
    slots_module = load_cw_slots_module()
    captures: list[dict] = []
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(
            by_key={target.key: SimpleNamespace(text="希儿") for target in targets},
        ),
    )
    monkeypatch.setattr(slots_module, "_read_slot_star_counts", lambda captures: {})
    monkeypatch.setattr(slots_module, "_capture_slot_panel_images", lambda runtime, point: {"name_image": f"img-{point}"})

    class Runtime:
        def capture_image(self, **kwargs):
            captures.append(kwargs)
            return "status-image"

        def click_point(self, *args):
            pass

    reader = slots_module.build_cw_slot_roles_reader(Runtime(), targets=["front:0"])
    reader()

    assert captures == []


def test_build_cw_status_reader_reads_stage_and_status(monkeypatch):
    slots_module = load_cw_slots_module()
    seen: list[str] = []
    captured_regions: list[dict] = []

    def fake_batch(runtime, targets, trace_prefix):
        assert [target.key for target in targets] == [
            ("stage_status", "level"),
            ("stage_status", "exp"),
            ("stage_status", "team_size"),
        ]
        return SimpleNamespace(
            by_key={
                ("stage_status", "level"): SimpleNamespace(pieces=["LV.3"]),
                ("stage_status", "exp"): SimpleNamespace(pieces=["0/8"]),
                ("stage_status", "team_size"): SimpleNamespace(pieces=["2/2"]),
            },
        )

    monkeypatch.setattr(slots_module, "run_batch_ocr", fake_batch)
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: seen.append("stage") or "preparation")

    class Runtime:
        def capture_image(self, **kwargs):
            captured_regions.append(kwargs)
            return kwargs

    result = slots_module.build_cw_status_reader(Runtime())()

    assert seen == ["stage"]
    assert captured_regions == [
        {**slots_module.stage.CW_STATUS_LEVEL_REGION, "normalize": False},
        {**slots_module.stage.CW_STATUS_EXP_REGION, "normalize": False},
        {**slots_module.stage.CW_STATUS_TEAM_SIZE_REGION, "normalize": False},
    ]
    assert result.stage == "preparation"
    assert result.stage_status == {"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"}


def test_build_cw_status_reader_keeps_status_when_stage_unknown(monkeypatch):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: None)
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(
            by_key={
                ("stage_status", "level"): SimpleNamespace(pieces=["LV.3"]),
                ("stage_status", "exp"): SimpleNamespace(pieces=["0/8"]),
                ("stage_status", "team_size"): SimpleNamespace(pieces=["2/2"]),
            },
        ),
    )

    class Runtime:
        def capture_image(self, **kwargs):
            return kwargs

    result = slots_module.build_cw_status_reader(Runtime())()

    assert result.stage is None
    assert result.stage_status["level"] == 3


def test_build_cw_slot_icon_reader_skips_screenshot_save_without_request_id(monkeypatch, tmp_path):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: "preparation")
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(
            by_key={
                ("stage_status", "level"): SimpleNamespace(pieces=["LV.3"]),
                ("stage_status", "exp"): SimpleNamespace(pieces=["0/8"]),
                ("stage_status", "team_size"): SimpleNamespace(pieces=["1/2"]),
            }
        ),
    )

    class Runtime:
        def __init__(self):
            self.capture_calls: list[dict[str, object]] = []
            self.save_calls: list[str | None] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

        def capture_image(self, **kwargs):
            self.capture_calls.append(dict(kwargs))
            if kwargs == {"normalize": True}:
                return Image.new("RGBA", (1920, 1080), "black")
            return Image.new("RGBA", (64, 32), "black")

        def save_capture_image_to_workspace(self, image, request_id=None):
            del image
            self.save_calls.append(request_id)
            return tmp_path / ".trail" / "shots" / "unexpected.jpg"

    class Recognizer:
        def recognize_crop(self, crop, area):
            del crop, area
            return SimpleNamespace(
                name=None,
                role_id=None,
                candidates=[],
                score=None,
                empty=True,
                star_count=0,
                star_boxes=[],
                fee_color="",
                match_kind="empty",
                confidence_reason="empty_template_match",
                rarity=None,
                cost=None,
                diagnostics={"empty_score": 0.99},
            )

    runtime = Runtime()
    reader = slots_module.build_cw_slot_icon_reader(
        runtime,
        Recognizer(),
        targets=["front:0"],
        request_id=None,
        dismiss_initial_overlay=False,
    )

    result = reader()

    assert result.screenshot is None
    assert runtime.save_calls == []
    assert runtime.capture_calls.count({"normalize": True}) == 1


def test_slots_read_icon_low_confidence_keeps_response_diagnostics_and_warning(monkeypatch, tmp_path):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)
    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: "preparation")
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(
            by_key={
                ("stage_status", "level"): SimpleNamespace(pieces=["LV.3"]),
                ("stage_status", "exp"): SimpleNamespace(pieces=["0/8"]),
                ("stage_status", "team_size"): SimpleNamespace(pieces=["1/2"]),
            }
        ),
    )

    class Runtime:
        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

        def capture_image(self, **kwargs):
            if kwargs == {"normalize": True}:
                return Image.new("RGBA", (1920, 1080), "black")
            return Image.new("RGBA", (64, 32), "black")

        def save_capture_image_to_workspace(self, image, request_id=None):
            del image
            return tmp_path / ".trail" / "shots" / f"{request_id}.jpg"

    class Recognizer:
        def recognize_crop(self, crop, area):
            del crop, area
            return SimpleNamespace(
                name="希儿",
                role_id="1102",
                candidates=[],
                score=0.61,
                empty=False,
                star_count=2,
                star_boxes=[],
                fee_color="gold",
                match_kind="low_confidence",
                confidence_reason="low_score",
                rarity="5",
                cost="5",
                diagnostics={"empty_score": 0.03, "candidates": [{"name": "希儿", "role_id": "1102", "score": 0.61}]},
            )

    session = build_fake_cw_session(tmp_path)
    result = slots_module.build_cw_slot_icon_reader(
        Runtime(),
        Recognizer(),
        targets=["front:0"],
        request_id="req-low-confidence",
        dismiss_initial_overlay=False,
    )()

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: result,
        targets=["front:0"],
        guide_config={"traits": [], "roles": [{"id": "1102", "name": "希儿"}]},
    )

    response_slot = refreshed.response_snapshot["front"][0]
    assert response_slot["name"] == "希儿"
    assert response_slot["role_id"] == "1102"
    assert response_slot["match_kind"] == "low_confidence"
    assert response_slot["score"] == 0.61
    assert response_slot["raw_name"] == "希儿"
    assert refreshed.response_snapshot["warnings"][0]["code"] == "CW_ROLE_MATCH_LOW_CONFIDENCE"
    assert refreshed.response_snapshot["warnings"][0]["position"] == {"kind": "slot", "area": "front", "index": 0}
    stored = refreshed.scene_state["cw"]["slots"]["front"][0]
    for key in ("raw_name", "match_kind", "score", "candidates", "empty_score", "fee_color", "star_boxes", "confidence_reason"):
        assert key not in stored


def test_slots_read_icon_high_confidence_strips_response_diagnostics(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    value = {
        "name": "希儿",
        "role_id": "1102",
        "star": 2,
        "score": 0.94,
        "match_kind": "icon",
        "raw_name": "希儿",
        "candidates": [{"name": "希儿", "role_id": "1102", "score": 0.94}],
        "empty_score": 0.02,
        "fee_color": "gold",
        "star_boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}],
        "confidence_reason": "score_gap",
    }

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: ([value, None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
        guide_config={"traits": [], "roles": [{"id": "1102", "name": "希儿"}]},
    )

    response_slot = refreshed.response_snapshot["front"][0]
    stored_slot = refreshed.scene_state["cw"]["slots"]["front"][0]
    assert response_slot == {"name": "希儿", "role_id": "1102", "star": 2}
    assert stored_slot == {"name": "希儿", "role_id": "1102", "star": 2}
    assert "warnings" not in refreshed.response_snapshot
    for key in ("raw_name", "match_kind", "score", "candidates", "empty_score", "fee_color", "star_boxes", "confidence_reason"):
        assert key not in response_slot
        assert key not in stored_slot


def test_build_cw_slots_reader_batches_stage_status_before_slot_clicks(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    events: list[str] = []
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: events.append(f"sleep({seconds})"), raising=False)
    monkeypatch.setattr(slots_module, "_count_slot_stars_in_image", lambda image: 4, raising=False)

    class RuntimeSpy:
        def __init__(self):
            self.batch_targets: list[dict[str, object]] = []
            self.current_slot: tuple[str, int] | None = None
            self.ocr_image_calls: list[dict[str, object]] = []
            self.slot_names = {("front", 0): "希儿", ("back", 0): "佩拉", ("hand", 0): "银狼"}

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            if point == slots_module.INFO_DISMISS_POINT:
                events.append("click(INFO_DISMISS_POINT)")
                return
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    events.append(f"click({area}:{points.index(point)})")
                    return
            events.append(f"click({x},{y})")

        def capture_image(self, **kwargs):
            stage_calls = _stage_status_capture_calls()
            if kwargs in stage_calls:
                event_names = ["CW_STATUS_LEVEL_REGION", "CW_STATUS_EXP_REGION", "CW_STATUS_TEAM_SIZE_REGION"]
                events.append(f"capture_image({event_names[stage_calls.index(kwargs)]})")
            image = Image.new("RGB", (201, 61), color="white")
            slot_text = self.slot_names.get(self.current_slot)
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=slot_text)
            return image

        def ocr_image(self, image, *, ocr=None):
            pending = _consume_pending_batch_targets(self)
            self.ocr_image_calls.append({"size": image.size, "ocr": ocr, "keys": [target["key"] for target in pending]})
            return _packed_ocr_pieces(pending)

    runtime = RuntimeSpy()

    result = build_cw_slots_reader(runtime, targets=["front:0", "back:0", "hand:0"])()

    result = _assert_slots_read_result(result)
    assert result.stage_status == {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}
    assert result.front == [{"name": "希儿", "star": 4}, None, None, None]
    assert result.back == [{"name": "佩拉", "star": 4}, None, None, None, None, None]
    assert result.hand == [{"name": "银狼", "star": 4}, None, None, None, None, None, None, None, None]
    assert [call["keys"] for call in runtime.ocr_image_calls] == [
        [("stage_status", "level"), ("stage_status", "exp"), ("stage_status", "team_size")],
        [("slot", "front", 0), ("slot", "back", 0), ("slot", "hand", 0)],
    ]
    assert events[:2] == ["click(INFO_DISMISS_POINT)", "sleep(1.0)"]
    first_slot_click = events.index("click(front:0)")
    assert events.index("capture_image(CW_STATUS_LEVEL_REGION)") < first_slot_click
    assert events.index("capture_image(CW_STATUS_EXP_REGION)") < first_slot_click
    assert events.index("capture_image(CW_STATUS_TEAM_SIZE_REGION)") < first_slot_click


def test_slots_read_rejects_empty_snapshot_and_preserves_previous_state(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    previous = deepcopy(session.scene_state["cw"])

    with pytest.raises(TrailError) as exc_info:
        read_cw_slots(
            session,
            reader=lambda: ([None, None, None, None], [None, None, None, None, None, None], [None] * 9),
        )

    assert exc_info.value.code == "SLOTS_READ_EMPTY"
    assert session.scene_state["cw"] == previous


def test_build_cw_slots_reader_batches_status_and_target_captures_separately(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)
    monkeypatch.setattr(slots_module, "_count_slot_stars_in_image", lambda image: 4, raising=False)

    strip_height = 61

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_calls: list[dict[str, int | bool]] = []
            self.batch_targets: list[dict[str, object]] = []
            self.current_slot: tuple[str, int] | None = None
            self.ocr_image_calls: list[dict[str, object]] = []
            self.slot_names = {("front", 0): "希儿", ("back", 0): "佩拉", ("hand", 0): "银狼"}

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            point = (x, y)
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    break

        def capture_image(self, **kwargs):
            self.capture_calls.append(kwargs)
            image = Image.new("RGB", (201, strip_height), color="white")
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=self.slot_names.get(self.current_slot))
            return image

        def ocr_image(self, image, *, ocr=None):
            pending = _consume_pending_batch_targets(self)
            self.ocr_image_calls.append({"size": image.size, "ocr": ocr, "keys": [target["key"] for target in pending]})
            return _packed_ocr_pieces(pending)

    runtime = RuntimeSpy()

    result = _assert_slots_read_result(build_cw_slots_reader(runtime, targets=["front:0", "back:0", "hand:0"])())

    assert result.front == [{"name": "希儿", "star": 4}, None, None, None]
    assert result.back == [{"name": "佩拉", "star": 4}, None, None, None, None, None]
    assert result.hand == [{"name": "银狼", "star": 4}, None, None, None, None, None, None, None, None]
    assert runtime.capture_calls == _stage_status_capture_calls() + [
        {**slots_module.SLOT_NAME_REGION, "normalize": False},
        {**slots_module.SLOT_STAR_REGION, "normalize": False},
        {**slots_module.SLOT_NAME_REGION, "normalize": False},
        {**slots_module.SLOT_STAR_REGION, "normalize": False},
        {**slots_module.SLOT_NAME_REGION, "normalize": False},
        {**slots_module.SLOT_STAR_REGION, "normalize": False},
    ]
    assert [call["keys"] for call in runtime.ocr_image_calls] == [
        [("stage_status", "level"), ("stage_status", "exp"), ("stage_status", "team_size")],
        [("slot", "front", 0), ("slot", "back", 0), ("slot", "hand", 0)],
    ]
    assert all(call["ocr"].ocr_mode == "high" for call in runtime.ocr_image_calls)
    assert all(call["ocr"].retry_high == "never" for call in runtime.ocr_image_calls)


def test_build_cw_slots_reader_ignores_geometryless_piece_when_multiple_targets(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    strip_height = 61

    class RuntimeSpy:
        def __init__(self):
            self.batch_targets: list[dict[str, object]] = []
            self.current_slot: tuple[str, int] | None = None
            self.slot_names = {("hand", 0): "银狼"}

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    break

        def capture_image(self, **kwargs):
            image = Image.new("RGB", (201, strip_height), color="white")
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=self.slot_names.get(self.current_slot))
            return image

        def ocr_image(self, image, *, ocr=None):
            del ocr
            return [{"text": "噪声"}] + _packed_ocr_pieces(_consume_pending_batch_targets(self))

    runtime = RuntimeSpy()

    result = _assert_slots_read_result(build_cw_slots_reader(runtime, targets=["front:0", "hand:0"])())

    assert result.front == [None, None, None, None]
    assert result.back == [None, None, None, None, None, None]
    assert result.hand == [{"name": "银狼", "star": None}, None, None, None, None, None, None, None, None]


def test_build_cw_slots_reader_reads_runtime_slot_snapshots_and_closes_overlay(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    strip_height = 61

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.locate_calls = 0
            self.capture_calls: list[dict[str, int | bool]] = []
            self.batch_targets: list[dict[str, object]] = []
            self.current_slot: tuple[str, int] | None = None
            self.ocr_image_calls: list[dict[str, object]] = []
            self.slot_names = {
                ("front", 0): "希儿",
                ("back", 0): "佩拉",
                ("hand", 0): "银狼",
                ("hand", 2): "阮·梅",
            }

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            if self.locate_calls == 1:
                return Box(left=10, top=20, width=30, height=40)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            point = (x, y)
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    break

        def capture_image(self, **kwargs):
            self.capture_calls.append(kwargs)
            image = Image.new("RGB", (201, strip_height), color="white")
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=self.slot_names.get(self.current_slot))
            return image

        def ocr_image(self, image, *, ocr=None):
            pending = _consume_pending_batch_targets(self)
            self.ocr_image_calls.append({"size": image.size, "ocr": ocr, "keys": [target["key"] for target in pending]})
            return _packed_ocr_pieces(pending)

    runtime = RuntimeSpy()

    result = _assert_slots_read_result(build_cw_slots_reader(runtime)())

    assert result.front == [{"name": "希儿", "star": None}, None, None, None]
    assert result.back == [{"name": "佩拉", "star": None}, None, None, None, None, None]
    assert result.hand == [{"name": "银狼", "star": None}, None, {"name": "阮·梅", "star": None}, None, None, None, None, None, None]
    assert runtime.clicks[0] == (25, 40)
    assert runtime.clicks[1] == slots_module.HAND_EXPAND_DISMISS_POINT
    assert runtime.capture_calls == _stage_status_capture_calls() + [
        item
        for _ in range(19)
        for item in (
            {**slots_module.SLOT_NAME_REGION, "normalize": False},
            {**slots_module.SLOT_STAR_REGION, "normalize": False},
        )
    ]
    assert len(runtime.ocr_image_calls) == 2


def test_build_cw_slots_reader_reads_only_requested_slots(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    strip_height = 61

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.locate_calls = 0
            self.capture_calls: list[dict[str, int | bool]] = []
            self.batch_targets: list[dict[str, object]] = []
            self.current_slot: tuple[str, int] | None = None
            self.ocr_image_calls: list[dict[str, object]] = []
            self.slot_names = {("front", 0): "希儿", ("hand", 2): "阮·梅"}

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            if self.locate_calls == 1:
                return Box(left=10, top=20, width=30, height=40)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            point = (x, y)
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    break

        def capture_image(self, **kwargs):
            self.capture_calls.append(kwargs)
            image = Image.new("RGB", (201, strip_height), color="white")
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=self.slot_names.get(self.current_slot))
            return image

        def ocr_image(self, image, *, ocr=None):
            pending = _consume_pending_batch_targets(self)
            self.ocr_image_calls.append({"size": image.size, "ocr": ocr, "keys": [target["key"] for target in pending]})
            return _packed_ocr_pieces(pending)

    runtime = RuntimeSpy()

    result = _assert_slots_read_result(build_cw_slots_reader(runtime, targets=["front:0", "hand:2"])())

    assert result.front == [{"name": "希儿", "star": None}, None, None, None]
    assert result.back == [None, None, None, None, None, None]
    assert result.hand == [None, None, {"name": "阮·梅", "star": None}, None, None, None, None, None, None]
    assert runtime.capture_calls == _stage_status_capture_calls() + [
        {**slots_module.SLOT_NAME_REGION, "normalize": False},
        {**slots_module.SLOT_STAR_REGION, "normalize": False},
        {**slots_module.SLOT_NAME_REGION, "normalize": False},
        {**slots_module.SLOT_STAR_REGION, "normalize": False},
    ]
    assert [call["keys"] for call in runtime.ocr_image_calls] == [
        [("stage_status", "level"), ("stage_status", "exp"), ("stage_status", "team_size")],
        [("slot", "front", 0), ("slot", "hand", 2)],
    ]


def test_build_cw_slots_reader_dismisses_center_before_first_slot_capture(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    sleeps: list[float] = []
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: sleeps.append(seconds), raising=False)

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def capture_image(self, **kwargs):
            del kwargs
            return Image.new("RGB", (201, 61), color="white")

        def ocr_image(self, image, *, ocr=None):
            del image, ocr
            return []

    runtime = RuntimeSpy()

    build_cw_slots_reader(runtime, targets=["front:0"])()

    assert runtime.clicks[:3] == [
        slots_module.INFO_DISMISS_POINT,
        slots_module.FRONT_SLOT_POINTS[0],
        slots_module.INFO_DISMISS_POINT,
    ]
    assert sleeps[0] == 1.0


def test_build_cw_slots_reader_can_skip_initial_overlay_dismiss(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    events: list[str] = []

    class RuntimeSpy:
        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            if point == slots_module.INFO_DISMISS_POINT:
                events.append("click(INFO_DISMISS_POINT)")
            else:
                events.append(f"click({point})")

        def capture_image(self, **kwargs):
            del kwargs
            return object()

    batch_by_key = {
        ("stage_status", "level"): SimpleNamespace(text="1"),
        ("stage_status", "exp"): SimpleNamespace(text="0/2"),
        ("stage_status", "team_size"): SimpleNamespace(text="1/2"),
        ("slot", "front", 0): SimpleNamespace(text="希儿"),
    }

    monkeypatch.setattr(slots_module, "sleep", lambda seconds: events.append(f"sleep({seconds})"), raising=False)
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(by_key=batch_by_key),
        raising=False,
    )
    monkeypatch.setattr(slots_module, "_read_slot_star_counts", lambda captures: {("front", 0): 1}, raising=False)

    reader = build_cw_slots_reader(RuntimeSpy(), targets=["front:0"], dismiss_initial_overlay=False)

    result = _assert_slots_read_result(reader())

    assert result.front[0] == {"name": "希儿", "star": 1}
    assert events[0] == f"click({slots_module.FRONT_SLOT_POINTS[0]})"
    assert "sleep(1.0)" not in events[:1]
    assert events.count("click(INFO_DISMISS_POINT)") == 1


def test_capture_slot_name_panel_image_waits_longer_before_capture_and_keeps_dismiss_settle(monkeypatch):
    slots_module = load_cw_slots_module()
    capture_slot_name_panel_image = getattr(slots_module, "_capture_slot_name_panel_image", None)
    assert capture_slot_name_panel_image is not None
    sleeps: list[float] = []

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def capture_image(self, **kwargs):
            del kwargs
            return Image.new("RGB", (201, 61), color="white")

    runtime = RuntimeSpy()
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: sleeps.append(seconds), raising=False)

    capture_slot_name_panel_image(runtime, point=slots_module.FRONT_SLOT_POINTS[0])

    assert runtime.clicks == [slots_module.FRONT_SLOT_POINTS[0], slots_module.INFO_DISMISS_POINT]
    assert sleeps == [slots_module.SLOT_PANEL_SETTLE_SECONDS * 2, slots_module.SLOT_PANEL_SETTLE_SECONDS]


def test_build_cw_slots_reader_waits_for_slot_panel_settle_between_interactions(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.state = "idle"
            self.current_name: str | None = None
            self.current_slot: tuple[str, int] | None = None
            self.batch_targets: list[dict[str, object]] = []
            self.names = {
                slots_module.FRONT_SLOT_POINTS[0]: "希儿",
                slots_module.HAND_SLOT_POINTS[0]: "银狼",
            }
            self.slot_names = {("front", 0): "希儿", ("hand", 0): "银狼"}
            self.capture_index = 0

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            self.clicks.append(point)
            if point == slots_module.INFO_DISMISS_POINT:
                if self.state == "panel-open":
                    self.state = "panel-closing"
                return
            if self.state != "idle":
                return
            self.current_name = self.names.get(point)
            for area, points in slots_module.SLOT_POINTS_BY_AREA.items():
                if point in points:
                    self.current_slot = (area, points.index(point))
                    break
            self.state = "panel-opening"

        def capture_image(self, **kwargs):
            if kwargs not in _stage_status_capture_calls():
                assert self.state == "panel-open"
                assert self.current_name is not None
            self.capture_index += 1
            image = Image.new("RGB", (201, 61), color="white")
            _record_batch_capture(slots_module, self, kwargs, image, slot_text=self.slot_names.get(self.current_slot))
            return image

        def ocr_image(self, image, *, ocr=None):
            del image, ocr
            return _packed_ocr_pieces(_consume_pending_batch_targets(self))

        def settle(self, seconds: float):
            assert seconds > 0
            if self.state == "panel-opening":
                self.state = "panel-open"
                return
            if self.state == "panel-closing":
                self.state = "idle"
                self.current_name = None

    runtime = RuntimeSpy()
    monkeypatch.setattr(slots_module, "sleep", runtime.settle, raising=False)

    result = _assert_slots_read_result(build_cw_slots_reader(runtime, targets=["front:0", "hand:0"])())

    assert result.front == [{"name": "希儿", "star": None}, None, None, None]
    assert result.back == [None, None, None, None, None, None]
    assert result.hand == [{"name": "银狼", "star": None}, None, None, None, None, None, None, None, None]


def test_slots_read_partial_refresh_preserves_existing_unknown_positions(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿", "布洛妮娅", None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }


def test_slots_read_partial_refresh_from_stale_base_keeps_snapshot_stale(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": True,
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿", "布洛妮娅", None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": True,
    }


def test_slots_read_partial_refresh_from_fresh_base_keeps_snapshot_fresh(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿", "布洛妮娅", None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }


def test_slots_read_partial_refresh_from_no_base_keeps_snapshot_stale(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": [None, "布洛妮娅", None, None],
        "back": [None, None, None, None, None, None],
        "hand": [None, None, None, None, None, None, None, None, None],
        "stale": True,
    }


def test_slots_read_partial_refresh_from_stale_base_empty_target_raises_slots_read_empty(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = True
    previous = deepcopy(session.scene_state["cw"])

    with pytest.raises(TrailError) as exc_info:
        read_cw_slots(
            session,
            reader=lambda: ([None] * 4, [None] * 6, [None] * 9),
            targets=["front:1"],
        )

    assert exc_info.value.code == "SLOTS_READ_EMPTY"
    assert session.scene_state["cw"] == previous


def test_slots_read_partial_refresh_from_fresh_base_empty_target_preserves_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None] * 4, [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"] == {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }


def test_slots_read_uses_role_stages_as_authoritative_name_candidates(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {
                "stage": "Final",
                "front_roles": [{"name": "布洛妮娅", "star": 3, "first_equipments": [{"name": "不要作为候选"}]}],
                "back_roles": [{"name": "椒丘", "star": 2}],
            }
        ]
    }

    authoritative, _ = slots_module._session_slot_name_candidates(session.scene_state["cw"])
    assert "布洛妮娅" in authoritative
    assert "椒丘" in authoritative
    assert "不要作为候选" not in authoritative
    session.scene_state["cw"]["slots"]["front"] = ["布洛妮娅", None, None, None]
    session.scene_state["cw"]["slots"]["stale"] = False

    refreshed = read_cw_slots(
        session,
        reader=lambda: (["布罗妮娅", None, None, None], ["椒丘", None, None, None, None, None], [None] * 9),
        guide_config=None,
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布洛妮娅"
    assert refreshed.scene_state["cw"]["slots"]["back"][0] == "椒丘"


def test_slots_read_guide_summary_treats_incomplete_legacy_guide_as_not_loaded(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "artifact": "legacy-artifact",
        "share_code": "##demo##",
        "on_field": {"布洛妮娅": 1},
        "off_field": {},
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: (["布罗妮娅", None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布罗妮娅"


def test_slots_read_preserves_star_metadata_when_normalizing_name(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = _complete_guide(
        role_stages=[{"stage": "Final", "front_roles": [{"name": "布洛妮娅"}], "back_roles": []}],
    )
    session.scene_state["cw"]["slots"]["front"] = [{"name": "布洛妮娅", "star": 2}, None, None, None]
    session.scene_state["cw"]["slots"]["stale"] = False

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([{"name": "布罗妮娅", "star": 3}, None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == {"name": "布洛妮娅", "star": 3}


def test_slots_read_uses_full_config_catalog_without_selected_guide(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"].pop("guide", None)
    guide_config = {
        "traits": [{"id": "1007", "name": "仙舟", "layers": [{"layer": 3}]}],
        "roles": [{"id": "1502", "name": "爻光", "trait_ids": ["1007"]}],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([{"name": "交光", "star": 1}, None, None, None], [None] * 6, [None] * 9),
        guide_config=guide_config,
    )

    stored = refreshed.scene_state["cw"]["slots"]["front"][0]
    assert stored == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}
    assert "raw_name" not in stored
    assert "match_score" not in stored
    assert "match_kind" not in stored


def test_slots_read_preserves_variable_cost_role_identity_from_icon_result(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "traits": [
            {"id": "1002", "name": "星核猎手", "layers": [{"layer": 1}, {"layer": 2}]},
            {"id": "2009", "name": "量子同频", "layers": [{"layer": 1}, {"layer": 2}]},
            {"id": "2012", "name": "欢愉", "layers": [{"layer": 1}, {"layer": 2}]},
            {"id": "3006", "name": "头号玩家", "layers": [{"layer": 1}, {"layer": 2}]},
        ],
        "roles": [
            {"id": "1006", "name": "银狼", "trait_ids": ["1002", "2009"]},
            {"id": "15061", "name": "银狼LV.999", "trait_ids": ["1002", "2012", "3006"]},
            {"id": "15062", "name": "银狼LV.999", "trait_ids": ["1002", "2012", "3006"]},
            {"id": "15063", "name": "银狼LV.999", "trait_ids": ["1002", "2012", "3006"]},
        ],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: (
            [
                {
                    "name": "银狼LV.999",
                    "role_id": "15063",
                    "rarity": "5",
                    "cost": "5",
                    "star": 1,
                },
                {"name": "银狼", "role_id": "1006", "rarity": "4", "star": 1},
                None,
                None,
            ],
            [None] * 6,
            [None] * 9,
        ),
        guide_config=guide_config,
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == {
        "name": "银狼LV.999",
        "role_id": "15063",
        "star": 1,
        "rarity": "5",
        "cost": 5,
        "traits": ["星核猎手", "欢愉", "头号玩家"],
    }
    assert refreshed.scene_state["cw"]["slots"]["front"][1] == {
        "name": "银狼",
        "role_id": "1006",
        "star": 1,
        "rarity": "4",
        "traits": ["星核猎手", "量子同频"],
    }


def test_slots_read_preserves_unmatched_role_id_identity(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    refreshed = read_cw_slots(
        session,
        reader=lambda: (
            [
                {
                    "name": "银狼LV.999",
                    "role_id": "15063",
                    "rarity": "5",
                    "cost": "5",
                    "star": 1,
                },
                None,
                None,
                None,
            ],
            [None] * 6,
            [None] * 9,
        ),
        guide_config={
            "traits": [
                {"id": "1002", "name": "星核猎手", "layers": [{"layer": 1}, {"layer": 2}]},
                {"id": "2009", "name": "量子同频", "layers": [{"layer": 1}, {"layer": 2}]},
            ],
            "roles": [{"id": "1006", "name": "银狼", "trait_ids": ["1002", "2009"]}],
        },
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == {
        "name": "银狼LV.999",
        "role_id": "15063",
        "star": 1,
        "rarity": "5",
        "cost": 5,
    }


def test_slots_read_keeps_match_diagnostics_response_only(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "traits": [{"id": "1007", "name": "仙舟", "layers": [{"layer": 3}]}],
        "roles": [{"id": "1502", "name": "爻光", "trait_ids": ["1007"]}],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([{"name": "交光", "star": 1}, None, None, None], [None] * 6, [None] * 9),
        guide_config=guide_config,
    )

    response_slot = refreshed.response_snapshot["front"][0]
    assert response_slot == {
        "name": "爻光",
        "role_id": "1502",
        "star": 1,
        "traits": ["仙舟"],
        "raw_name": "交光",
        "match_score": 0.5,
        "match_kind": "low_confidence",
    }
    assert refreshed.response_snapshot["warnings"][0]["code"] == "CW_ROLE_MATCH_LOW_CONFIDENCE"
    assert refreshed.response_snapshot["warnings"][0]["position"] == {"kind": "slot", "area": "front", "index": 0}
    stored = refreshed.scene_state["cw"]["slots"]["front"][0]
    assert stored == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}
    assert "warnings" not in refreshed.scene_state["cw"]["slots"]


def _assert_no_slot_match_diagnostics(value):
    if isinstance(value, dict):
        assert "raw_name" not in value
        assert "match_score" not in value
        assert "score" not in value
        assert "match_kind" not in value
        for child in value.values():
            _assert_no_slot_match_diagnostics(child)
        return
    if isinstance(value, list):
        for child in value:
            _assert_no_slot_match_diagnostics(child)


def test_slots_read_strips_stale_match_diagnostics_from_preserved_targeted_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": [
            {
                "name": "爻光",
                "role_id": "1502",
                "star": 1,
                "traits": ["仙舟"],
                "raw_name": "交光",
                "match_score": 0.5,
                "score": 0.5,
                "match_kind": "low_confidence",
            },
            None,
            None,
            None,
        ],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, {"name": "布洛妮娅", "star": 1}, None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
        guide_config={"traits": [], "roles": []},
    )

    stored_slots = refreshed.scene_state["cw"]["slots"]
    _assert_no_slot_match_diagnostics(stored_slots)
    _assert_no_slot_match_diagnostics(refreshed.response_snapshot)
    assert stored_slots["front"][0] == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}
    assert refreshed.response_snapshot["front"][0] == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}


def test_slots_read_does_not_catalog_rename_unrefreshed_preserved_slots(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": ["交光", None, None, None],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }
    guide_config = {
        "traits": [
            {"id": "1007", "name": "仙舟", "layers": [{"layer": 1}, {"layer": 3}]},
            {"id": "2001", "name": "风", "layers": [{"layer": 1}, {"layer": 2}]},
        ],
        "roles": [
            {"id": "1502", "name": "爻光", "trait_ids": ["1007"]},
            {"id": "1003", "name": "布洛妮娅", "trait_ids": ["2001"]},
        ],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None, {"name": "布洛妮娅", "star": 1}, None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
        guide_config=guide_config,
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "交光"
    assert refreshed.response_snapshot["front"][0] == "交光"
    assert refreshed.scene_state["cw"]["slots"]["front"][1] == {"name": "布洛妮娅", "role_id": "1003", "star": 1, "traits": ["风"]}
    assert "warnings" not in refreshed.response_snapshot
    _assert_no_slot_match_diagnostics(refreshed.scene_state["cw"]["slots"])


def test_slots_read_enriches_slot_traits_and_summarizes_field_traits(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "traits": [
            {"id": 2001, "name": "巡猎", "layers": [{"layer": 1}, {"layer": 2}]},
            {"id": 2002, "name": "量子", "layers": [{"layer": 1}, {"layer": 2}]},
            {"id": 2003, "name": "辅助", "layers": [{"layer": 1}, {"layer": 2}]},
        ],
        "roles": [
            {"id": 1001, "name": "希儿", "trait_ids": [2001, 2002]},
            {"id": 1002, "name": "佩拉", "trait_ids": [2002]},
            {"id": 1003, "name": "布洛妮娅", "trait_ids": [2001, 2003]},
        ],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: (
            [{"name": "希儿", "star": 4}, None, None, None],
            [{"name": "佩拉", "star": 2}, None, None, None, None, None],
            [{"name": "布洛妮娅", "star": 3}, None, None, None, None, None, None, None, None],
        ),
        guide_config=guide_config,
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == {"name": "希儿", "role_id": "1001", "star": 4, "traits": ["巡猎", "量子"]}
    assert refreshed.scene_state["cw"]["slots"]["back"][0] == {"name": "佩拉", "role_id": "1002", "star": 2, "traits": ["量子"]}
    assert refreshed.scene_state["cw"]["slots"]["hand"][0] == {"name": "布洛妮娅", "role_id": "1003", "star": 3, "traits": ["巡猎", "辅助"]}
    assert refreshed.scene_state["cw"]["slots"]["trait_summary"] == [
        {"trait": "量子", "tiers": [1, 2], "owned_roles": 2, "active_tier": 2, "total_tiers": 2, "ratio": 1.0},
        {"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5},
    ]


def test_slots_read_trait_summary_uses_enriched_layers_without_role_count_fallback(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "traits": [
            {"id": "1007", "name": "仙舟", "layers": [{"layer": 3}, {"layer": 5}]},
            {"id": "1004", "name": "公司"},
        ],
        "roles": [
            {"id": "1502", "name": "爻光", "trait_ids": ["1007"]},
            {"id": "1304", "name": "砂金", "trait_ids": ["1004"]},
        ],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: (
            [{"name": "爻光", "star": 1}, {"name": "砂金", "star": 1}, None, None],
            [None] * 6,
            [None] * 9,
        ),
        guide_config=guide_config,
    )

    assert refreshed.scene_state["cw"]["slots"]["trait_summary"] == [
        {"trait": "仙舟", "tiers": [3, 5], "owned_roles": 1, "active_tier": 0, "total_tiers": 2, "ratio": 0.0}
    ]


def test_slots_read_stage_role_count_uses_merged_snapshot_for_partial_reads(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }
    result = slots_module.CwSlotsReadResult(
        front=[None, {"name": "布洛妮娅", "star": 1}, None, None],
        back=[None] * 6,
        hand=[None] * 9,
        stage_status={"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    )

    refreshed = read_cw_slots(session, reader=lambda: result, targets=["front:1"])

    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"] == {
        "front": 2,
        "back": 1,
        "hand": 2,
        "field": 3,
        "total": 5,
    }


def test_slots_read_stage_role_count_ignores_stale_previous_snapshot_on_partial_reads(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": ["希儿", "黑塔", None, None],
        "back": ["佩拉", "停云", None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": True,
    }
    result = slots_module.CwSlotsReadResult(
        front=[None, {"name": "布洛妮娅", "star": 1}, None, None],
        back=[None] * 6,
        hand=[None] * 9,
        stage_status={"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    )

    refreshed = read_cw_slots(session, reader=lambda: result, targets=["front:1"])

    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"] == {
        "front": 1,
        "back": 0,
        "hand": 0,
        "field": 1,
        "total": 1,
    }


def test_slots_read_trait_summary_ignores_stale_previous_field_roles_on_partial_reads(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, None, None, None, None, None, None, None],
        "stale": True,
    }
    guide_config = {
        "traits": [
            {"id": "1007", "name": "仙舟", "layers": [{"layer": 1}, {"layer": 3}]},
            {"id": "2002", "name": "量子", "layers": [{"layer": 1}, {"layer": 2}]},
        ],
        "roles": [
            {"id": "1502", "name": "爻光", "trait_ids": ["1007"]},
            {"id": "1001", "name": "希儿", "trait_ids": ["2002"]},
            {"id": "1002", "name": "佩拉", "trait_ids": ["2002"]},
            {"id": "1004", "name": "银狼", "trait_ids": ["2002"]},
        ],
    }
    result = slots_module.CwSlotsReadResult(
        front=[None, {"name": "爻光", "star": 1}, None, None],
        back=[None] * 6,
        hand=[None] * 9,
        stage_status={"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    )

    refreshed = read_cw_slots(session, reader=lambda: result, targets=["front:1"], guide_config=guide_config)

    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"] == {
        "front": 1,
        "back": 0,
        "hand": 0,
        "field": 1,
        "total": 1,
    }
    assert refreshed.scene_state["cw"]["slots"]["trait_summary"] == [
        {"trait": "仙舟", "tiers": [1, 3], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.33}
    ]
    assert refreshed.response_snapshot["trait_summary"] == refreshed.scene_state["cw"]["slots"]["trait_summary"]


def test_summarize_field_trait_status_sorts_by_activation_ratio_and_limits_top_ten():
    slots_module = load_cw_slots_module()
    summarize = getattr(slots_module, "_summarize_field_trait_status", None)
    assert summarize is not None

    front = [{"name": f"角色{index}", "traits": [f"羁绊{index}"]} for index in range(12)]
    back = []
    guide_config = {
        "traits": [
            {"id": index, "name": f"羁绊{index}", "layers": [{"layer": role_idx + 1} for role_idx in range(index + 1)]}
            for index in range(12)
        ],
        "roles": [
            {"id": f"r{index}-{role_idx}", "name": f"角色{index}" if role_idx == 0 else f"羁绊{index}候补{role_idx}", "trait_ids": [index]}
            for index in range(12)
            for role_idx in range(index + 1)
        ],
    }

    summary = summarize(front, back, guide_config=guide_config)

    assert len(summary) == 10
    assert [item["trait"] for item in summary[:3]] == ["羁绊0", "羁绊1", "羁绊2"]
    assert summary[0]["ratio"] == 1.0
    assert summary[-1]["trait"] == "羁绊9"


def test_slots_read_does_not_use_stale_slot_names_as_normalization_candidates(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Final", "front_roles": [{"name": "布罗妮娅"}], "back_roles": []}
        ],
    }
    session.scene_state["cw"]["slots"]["front"] = ["布洛妮娅", None, None, None]
    session.scene_state["cw"]["slots"]["stale"] = True

    refreshed = read_cw_slots(
        session,
        reader=lambda: (["布妮娅", None, None, None], [None] * 6, [None] * 9),
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布罗妮娅"


def test_slots_read_ignores_nested_portal_names_as_authoritative_candidates(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [],
    }
    session.scene_state["cw"]["portal"] = {
        "cards": [
            {
                "guides": [
                    {
                        "final_role_cards": [
                            {"name": "爻光"},
                        ]
                    }
                ]
            }
        ]
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None] * 4, [None] * 6, [None, None, None, None, None, None, "交光", None, None]),
        targets=["hand:6"],
    )

    assert refreshed.scene_state["cw"]["slots"]["hand"][6] == "交光"


def test_slots_read_prefers_role_stage_candidates_over_previous_fresh_slot_noise(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Final", "front_roles": [], "back_roles": [{"name": "爻光"}]}
        ],
    }
    session.scene_state["cw"]["portal"] = {
        "cards": [
            {
                "guides": [
                    {
                        "final_role_cards": [
                            {"name": "爻光"},
                        ]
                    }
                ]
            }
        ]
    }
    session.scene_state["cw"]["slots"]["hand"] = ["银狼", None, "阮·梅", None, None, None, "目交光", None, None]
    session.scene_state["cw"]["slots"]["stale"] = False

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([None] * 4, [None] * 6, [None, None, None, None, None, None, "交光", None, None]),
        targets=["hand:6"],
    )

    assert refreshed.scene_state["cw"]["slots"]["hand"][6] == "爻光"


def test_slots_read_keeps_ambiguous_name_when_best_match_is_not_unique(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {
                "stage": "Final",
                "front_roles": [{"name": "布洛妮娅"}],
                "back_roles": [{"name": "布罗妮娅"}],
            }
        ],
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: (["布妮娅", None, None, None], [None] * 6, [None] * 9),
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布妮娅"


def test_collapse_expanded_hand_card_rejects_stuck_open_overlay(monkeypatch):
    slots_module = load_cw_slots_module()
    collapse = getattr(slots_module, "_collapse_expanded_hand_card", None)
    assert collapse is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    class RuntimeSpy:
        def __init__(self):
            self.locate_calls = 0
            self.clicks: list[tuple[int, int]] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            return Box(left=10, top=20, width=30, height=40)

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = RuntimeSpy()

    with pytest.raises(TrailError) as exc_info:
        collapse(runtime)

    assert exc_info.value.code == "SLOTS_OPEN_STUCK"
    assert runtime.locate_calls == slots_module.HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS
    assert runtime.clicks == [(25, 40), slots_module.HAND_EXPAND_DISMISS_POINT] * slots_module.HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS


def test_collapse_expanded_hand_card_waits_for_dismiss_settle(monkeypatch):
    slots_module = load_cw_slots_module()
    collapse = getattr(slots_module, "_collapse_expanded_hand_card", None)
    assert collapse is not None

    class RuntimeSpy:
        def __init__(self):
            self.locate_calls = 0
            self.clicks: list[tuple[int, int]] = []
            self.state = "open"

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            if self.state != "closed":
                return Box(left=10, top=20, width=30, height=40)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == slots_module.HAND_EXPAND_DISMISS_POINT and self.state == "open":
                self.state = "closing"

        def settle(self, seconds: float):
            assert seconds > 0
            if self.state == "closing":
                self.state = "closed"

    runtime = RuntimeSpy()
    monkeypatch.setattr(slots_module, "sleep", runtime.settle, raising=False)

    collapse(runtime)

    assert runtime.locate_calls == 2
    assert runtime.clicks == [(25, 40), slots_module.HAND_EXPAND_DISMISS_POINT]


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


def test_build_cw_slot_swapper_drags_between_slot_points_and_rejects_cannot_be_fielded():
    slots_module = load_cw_slots_module()
    build_cw_slot_swapper = getattr(slots_module, "build_cw_slot_swapper", None)
    assert build_cw_slot_swapper is not None

    class RuntimeSpy:
        def __init__(self, *, blocked: bool):
            self.blocked = blocked
            self.drags: list[tuple[int, int, int, int]] = []
            self.clicks: list[tuple[int, int]] = []

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
            self.drags.append((from_x, from_y, to_x, to_y))

        def locate(self, template: str, **kwargs):
            del template, kwargs
            if self.blocked:
                return Box(left=0, top=0, width=10, height=10)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = RuntimeSpy(blocked=False)
    build_cw_slot_swapper(runtime)(source="hand:0", target="front:1")

    assert runtime.drags == [
        (*slots_module.HAND_SLOT_POINTS[0], *slots_module.FRONT_SLOT_POINTS[1]),
    ]

    blocked_runtime = RuntimeSpy(blocked=True)
    with pytest.raises(TrailError) as exc_info:
        build_cw_slot_swapper(blocked_runtime)(source="hand:0", target="front:1")

    assert exc_info.value.code == "SLOTS_CANNOT_BE_FIELDED"
    assert blocked_runtime.clicks == [slots_module.INFO_DISMISS_POINT]


def test_cannot_be_fielded_error_uses_agent_visible_slot_reference():
    slots_module = load_cw_slots_module()

    class RuntimeSpy:
        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
            del from_x, from_y, to_x, to_y

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return Box(left=0, top=0, width=10, height=10)

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

    with pytest.raises(TrailError) as exc_info:
        slots_module.build_cw_slot_swapper(RuntimeSpy())(source="hand:0", target="front:0")

    assert exc_info.value.code == "SLOTS_CANNOT_BE_FIELDED"
    assert str(exc_info.value) == "target slot cannot field character: front:1"
    assert "front:0" not in str(exc_info.value)


def test_place_cw_slots_runs_actions_in_order(tmp_path):
    slots_module = load_cw_slots_module()
    place_cw_slots = getattr(slots_module, "place_cw_slots", None)
    assert place_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    calls: list[tuple[str, str]] = []

    def placer(source: str, target: str) -> None:
        calls.append((source, target))

    refreshed = place_cw_slots(
        session,
        actions=[
            {"source": "hand:2", "target": "back:0"},
            {"source": "hand:0", "target": "front:0"},
        ],
        placer=placer,
    )

    assert calls == [("hand:2", "back:0"), ("hand:0", "front:0")]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
    assert refreshed.scene_state["cw"]["slots"]["front"] == before_slots["front"]
    assert refreshed.scene_state["cw"]["slots"]["back"] == before_slots["back"]
    assert refreshed.scene_state["cw"]["slots"]["hand"] == before_slots["hand"]
    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_place_cw_slots_stops_after_first_runtime_failure_and_keeps_stale(tmp_path):
    slots_module = load_cw_slots_module()
    place_cw_slots = getattr(slots_module, "place_cw_slots", None)
    assert place_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    calls: list[tuple[str, str]] = []

    def placer(source: str, target: str) -> None:
        calls.append((source, target))
        if len(calls) == 2:
            raise TrailError("SLOTS_CANNOT_BE_FIELDED", f"target slot cannot field character: {target}")

    with pytest.raises(TrailError) as exc_info:
        place_cw_slots(
            session,
            actions=[
                {"source": "hand:2", "target": "back:0"},
                {"source": "hand:0", "target": "front:0"},
                {"source": "hand:1", "target": "back:2"},
            ],
            placer=placer,
        )

    assert exc_info.value.code == "SLOTS_CANNOT_BE_FIELDED"
    assert calls == [("hand:2", "back:0"), ("hand:0", "front:0")]
    assert exc_info.value.known_failure_after_save is True
    assert session.scene_state["cw"]["slots"]["stale"] is True
    assert session.scene_state["cw"]["slots"]["front"] == before_slots["front"]
    assert session.scene_state["cw"]["slots"]["back"] == before_slots["back"]
    assert session.scene_state["cw"]["slots"]["hand"] == before_slots["hand"]
    assert session.scene_state["cw"]["sell_plan"] == {}


def test_place_cw_slots_requires_non_empty_actions_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    place_cw_slots = getattr(slots_module, "place_cw_slots", None)
    assert place_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        place_cw_slots(session, actions=[])

    assert exc_info.value.code == "CW_OPTION_INVALID"
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_place_cw_slots_rejects_invalid_position_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    place_cw_slots = getattr(slots_module, "place_cw_slots", None)
    assert place_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        place_cw_slots(session, actions=[{"source": "hand:2", "target": "front:99"}])

    assert exc_info.value.code == "SLOTS_POSITION_INVALID"
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_place_cw_slots_rejects_malformed_action_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    place_cw_slots = getattr(slots_module, "place_cw_slots", None)
    assert place_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        place_cw_slots(session, actions=[{}])

    assert exc_info.value.code == "CW_OPTION_INVALID"
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_collect_cw_crystals_records_metric(tmp_path):
    slots_module = load_cw_slots_module()
    collect_cw_crystals = getattr(slots_module, "collect_cw_crystals", None)
    assert collect_cw_crystals is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = collect_cw_crystals(session)

    assert refreshed.scene_state["cw"]["metrics"]["last_crystal_collection"] == "done"


def test_build_cw_hand_seller_and_crystal_collector_use_runtime_drags():
    slots_module = load_cw_slots_module()
    build_cw_hand_seller = getattr(slots_module, "build_cw_hand_seller", None)
    build_cw_crystal_collector = getattr(slots_module, "build_cw_crystal_collector", None)
    assert build_cw_hand_seller is not None
    assert build_cw_crystal_collector is not None

    class RuntimeSpy:
        def __init__(self):
            self.drags: list[tuple[int, int, int, int, float | None]] = []

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration: float | None = None):
            self.drags.append((from_x, from_y, to_x, to_y, duration))

    runtime = RuntimeSpy()

    build_cw_hand_seller(runtime)(slot=2)
    build_cw_crystal_collector(runtime)()

    assert runtime.drags[0] == (*slots_module.HAND_SLOT_POINTS[2], *slots_module.SELL_SLOT_POINT, None)
    assert runtime.drags[1:] == [(*drag, 0.2) for drag in slots_module.CRYSTAL_DRAG_PATHS]


def test_sell_plan_returns_reference_items_ordered_by_priority(tmp_path):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [
            {"name": "阮·梅", "star": 1},
            {"name": "黑塔", "star": 1},
            {"name": "停云", "star": 2},
            {"name": "银狼", "star": 3},
        ],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "5/5", "stale": False}

    result = plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert [item["name"] for item in result["items"]] == ["阮·梅", "黑塔", "停云", "银狼"]
    assert [item["category"] for item in result["items"]] == ["非攻略", "前期", "中期", "后期超买"]
    assert [item["recommendation"] for item in result["items"]] == ["推荐", "推荐", "推荐", "可以"]
    assert result["todos"] == []
    assert session.scene_state["cw"]["sell_plan"] == result

    result["items"].append({"slot": 9, "name": "噪声"})
    assert [item["name"] for item in session.scene_state["cw"]["sell_plan"]["items"]] == ["阮·梅", "黑塔", "停云", "银狼"]


@pytest.mark.parametrize(
    ("stage_value", "boss_preview", "expected"),
    [
        ("1-2", False, {"非攻略": "可以", "前期": "不推荐", "中期": "不推荐", "后期超买": "不推荐"}),
        ("2-1", False, {"非攻略": "推荐", "前期": "可以", "中期": "不推荐", "后期超买": "不推荐"}),
        ("2-5", True, {"非攻略": "推荐", "前期": "推荐", "中期": "不推荐", "后期超买": "不推荐"}),
        ("3-1", False, {"非攻略": "推荐", "前期": "推荐", "中期": "可以", "后期超买": "不推荐"}),
        ("3-5", True, {"非攻略": "推荐", "前期": "推荐", "中期": "推荐", "后期超买": "可以"}),
    ],
)
def test_sell_plan_recommendation_table_by_stage(tmp_path, stage_value, boss_preview, expected):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [
            {"name": "阮·梅", "star": 1},
            {"name": "黑塔", "star": 1},
            {"name": "停云", "star": 2},
            {"name": "银狼", "star": 3},
        ],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": stage_value, "boss_preview": boss_preview, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "5/5", "stale": False}

    plan = load_cw_slots_module().plan_cw_hand_sell(session)

    by_category = {item["category"]: item["recommendation"] for item in plan["items"]}
    for category, recommendation in expected.items():
        assert by_category[category] == recommendation


def test_sell_plan_protects_final_role_when_missing_from_field(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "银狼", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    item = load_cw_slots_module().plan_cw_hand_sell(session)["items"][0]

    assert item["category"] == "后期"
    assert item["protected"] is True
    assert item["recommendation"] == "不推荐"
    assert item["target_star"] == 3
    assert item["current_star"] is None


def test_sell_plan_protects_upgraded_lv999_one_star(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["slots"] = {
        "front": [{"name": "银狼LV.999", "cost": 4, "star": 1}],
        "back": [],
        "hand": [{"name": "银狼LV.999", "cost": 4, "star": 1}],
        "stale": False,
    }
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    cw_state["guide"] = {
        "role_stages": [
            {"stage": "Final", "front_roles": [{"name": "银狼LV.999", "cost": 4, "star": 1}], "back_roles": []}
        ]
    }

    result = _plan_cw_hand_sell_variable_cost(session)

    lv999_items = [item for item in result["items"] if item["name"] == "银狼LV.999"]
    assert lv999_items
    assert all(item["protected"] is True for item in lv999_items)


def test_sell_plan_protects_final_role_when_field_star_below_target(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼", "star": 2}], "back": [], "hand": [{"name": "银狼", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    item = load_cw_slots_module().plan_cw_hand_sell(session)["items"][0]

    assert item["category"] == "后期"
    assert item["protected"] is True
    assert item["recommendation"] == "不推荐"
    assert item["current_star"] == 2


def test_sell_plan_missing_star_marks_todo_and_protects_final_role(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼"}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼"}], "back": [], "hand": [{"name": "银狼"}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "star" in result["todos"]
    assert result["items"][0]["protected"] is True
    assert result["items"][0]["recommendation"] == "不推荐"


def test_sell_plan_marks_star_todo_when_final_current_star_missing_on_field(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼"}], "back": [], "hand": [{"name": "银狼", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)
    item = result["items"][0]

    assert item["protected"] is True
    assert item["recommendation"] == "不推荐"
    assert item["target_star"] == 3
    assert item["current_star"] is None
    assert "star" in result["todos"]


def test_sell_plan_missing_stage_or_team_size_marks_todos_and_avoids_authoritative_candidates(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"stale": True}
    session.scene_state["cw"]["shop"] = {"stale": True}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert "stage" in result["todos"]
    assert "team_size" in result["todos"]


@pytest.mark.parametrize(
    "stage_state",
    [
        {"value": "2-1", "boss_preview": False},
        {"value": "2-1", "boss_preview": False, "stale": 1},
        {"value": "2-1", "boss_preview": False, "stale": "true"},
    ],
)
def test_sell_plan_non_false_stage_stale_marks_stage_todo(tmp_path, stage_state):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = stage_state
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "stage" in result["todos"]


@pytest.mark.parametrize(
    "shop_state",
    [
        {"team_size": "1/1"},
        {"team_size": "1/1", "stale": 1},
        {"team_size": "1/1", "stale": "true"},
    ],
)
def test_sell_plan_non_false_shop_stale_marks_team_size_todo(tmp_path, shop_state):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "2-1", "boss_preview": False, "stale": False}
    session.scene_state["cw"]["shop"] = shop_state

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "team_size" in result["todos"]


def test_sell_plan_missing_stage_state_marks_stage_todo(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"].pop("stage", None)
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert "stage" in result["todos"]


def test_sell_plan_missing_team_size_value_marks_team_size_todo(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "2-1", "boss_preview": False, "stale": False}
    session.scene_state["cw"]["shop"] = {"stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert "team_size" in result["todos"]


def test_sell_plan_marks_all_items_not_recommended_when_under_team_size(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼", "star": 3}], "back": [], "hand": [{"name": "阮·梅", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "3/3", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert [item["name"] for item in result["items"]] == ["阮·梅"]
    assert all(item["recommendation"] == "不推荐" for item in result["items"])


@pytest.mark.parametrize("team_size", ["1/3", 3, "3"])
def test_sell_plan_team_size_ratio_uses_right_side(tmp_path, team_size):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [{"name": "阮·梅", "star": 1}],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": team_size, "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert [item["name"] for item in result["items"]] == ["阮·梅"]
    assert all(item["recommendation"] == "不推荐" for item in result["items"])


@pytest.mark.parametrize("team_size", [0, -1, True, "0", "-1", "abc/7", "1/0", "-1/3", "1/-3", "abc"])
def test_sell_plan_invalid_team_size_marks_team_size_todo(tmp_path, team_size):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [{"name": "阮·梅", "star": 1}],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": team_size, "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "team_size" in result["todos"]


@pytest.mark.parametrize("star", [0, -1, 99, True, "0", "-1", "99", "abc"])
def test_sell_plan_invalid_final_star_marks_todo_and_protects_role(tmp_path, star):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": star}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼", "star": star}], "back": [], "hand": [{"name": "银狼", "star": star}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "star" in result["todos"]
    assert result["items"][0]["protected"] is True


def test_sell_plan_marks_missing_final_when_last_stage_is_used_as_final(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "停云", "star": 2}],
        "back": [],
        "hand": [{"name": "停云", "star": 2}],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "missing_final" in result["todos"]
    assert result["items"][0]["category"] == "后期超买"


@pytest.mark.parametrize(
    "role_stages",
    [
        [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}],
        [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ],
    ],
)
def test_sell_plan_marks_stage_granularity_when_stage_roles_are_too_coarse(tmp_path, role_stages):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": role_stages}
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [{"name": "银狼", "star": 3}],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "stage_granularity" in result["todos"]


def test_sell_plan_marks_stage_granularity_when_role_stages_are_empty(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": []}
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "阮·梅", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "2-1", "boss_preview": False, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "stage_granularity" in result["todos"]


def test_sell_plan_missing_boss_preview_uses_non_boss_recommendation(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [{"name": "黑塔", "star": 1}, {"name": "停云", "star": 2}, {"name": "银狼", "star": 3}],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "2-5", "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "4/4", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "boss_preview" in result["todos"]
    assert {item["category"]: item["recommendation"] for item in result["items"]}["前期"] == "可以"


@pytest.mark.parametrize("stage_value", ["shop", "battle", "boss_preview"])
def test_sell_plan_unparseable_stage_value_marks_stage_todo(tmp_path, stage_value):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": stage_value, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "stage" in result["todos"]


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


def test_sell_plan_rejects_non_false_stale_slots_snapshot(tmp_path):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = ""
    session.scene_state["cw"]["sell_plan"] = {}

    with pytest.raises(TrailError) as exc_info:
        plan_cw_hand_sell(session)

    assert exc_info.value.code == "SLOTS_STALE"
    assert session.scene_state["cw"]["sell_plan"] == {}


@pytest.mark.parametrize("slots_value", [None, [], "stale"])
def test_sell_plan_rejects_malformed_slots_snapshot_as_stale(tmp_path, slots_value):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = slots_value
    session.scene_state["cw"]["sell_plan"] = {}

    with pytest.raises(TrailError) as exc_info:
        plan_cw_hand_sell(session)

    assert exc_info.value.code == "SLOTS_STALE"
    assert session.scene_state["cw"]["sell_plan"] == {}


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("swap_cw_slots", {"source": "hand:0", "target": "front:0"}),
        (
            "place_cw_slots",
            {
                "actions": [{"source": "hand:2", "target": "back:0"}],
            },
        ),
        ("sell_cw_hand_slots", {"slots": [2]}),
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


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("swap_cw_slots", {"source": "hand:0", "target": "front:0"}),
        ("place_cw_slots", {"actions": [{"source": "hand:2", "target": "back:0"}]}),
        ("place_one_cw_slot", {"source": "hand:0", "target": "front:0"}),
        ("sell_cw_hand_slots", {"slots": [2]}),
        ("sell_one_cw_hand", {"slot": 2}),
    ],
)
def test_slots_mutations_mark_stage_status_stale_without_losing_values(tmp_path, method_name, kwargs):
    slots_module = load_cw_slots_module()
    method = getattr(slots_module, method_name, None)
    assert method is not None

    session = build_fake_cw_session(tmp_path)
    seed_fresh_stage_status(session)

    refreshed = method(session, **kwargs)

    assert refreshed.scene_state["cw"]["stage"]["value"] == "shop"
    assert refreshed.scene_state["cw"]["stage"]["status"] == {
        "stale": True,
        "level": 7,
        "role_count": {"front": 1, "back": 1, "hand": 2, "field": 2, "total": 4},
    }


@pytest.mark.parametrize(
    ("method_name", "kwargs", "runtime_kwarg"),
    [
        ("swap_cw_slots", {"source": "hand:0", "target": "front:0"}, "swapper"),
        ("place_one_cw_slot", {"source": "hand:0", "target": "front:0"}, "placer"),
        ("sell_one_cw_hand", {"slot": 2}, "seller"),
    ],
)
def test_single_slot_mutation_failures_mark_slots_and_stage_status_stale(tmp_path, method_name, kwargs, runtime_kwarg):
    slots_module = load_cw_slots_module()
    method = getattr(slots_module, method_name, None)
    assert method is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = False
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        "status": {"stale": False, "level": 7, "role_count": {"total": 3}},
    }

    def fail(*args):
        del args
        raise TrailError("UNEXPECTED_ERROR", "single slot mutation failed")

    with pytest.raises(TrailError) as exc_info:
        method(session, **kwargs, **{runtime_kwarg: fail})

    assert exc_info.value.code == "UNEXPECTED_ERROR"
    assert exc_info.value.known_failure_after_save is True
    assert session.scene_state["cw"]["slots"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {
        "stale": True,
        "level": 7,
        "role_count": {"total": 3},
    }


def test_sell_cw_hand_slots_requires_non_empty_list_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    sell_cw_hand_slots = getattr(slots_module, "sell_cw_hand_slots", None)
    assert sell_cw_hand_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=[])

    assert exc_info.value.code == "CW_OPTION_INVALID"
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_sell_cw_hand_slots_rejects_non_integer_slot_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    sell_cw_hand_slots = getattr(slots_module, "sell_cw_hand_slots", None)
    assert sell_cw_hand_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=["0"])

    assert exc_info.value.code == "CW_OPTION_INVALID"
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_sell_cw_hand_slots_rejects_out_of_range_slot_without_mutating_session(tmp_path):
    slots_module = load_cw_slots_module()
    sell_cw_hand_slots = getattr(slots_module, "sell_cw_hand_slots", None)
    assert sell_cw_hand_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["sell_plan"] = {"candidates": [0, 2]}
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    before_sell_plan = deepcopy(session.scene_state["cw"]["sell_plan"])

    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=[99])

    assert exc_info.value.code == "SLOTS_POSITION_INVALID"
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert session.scene_state["cw"]["slots"] == before_slots
    assert session.scene_state["cw"]["sell_plan"] == before_sell_plan


def test_sell_cw_hand_slots_runs_slots_in_order(tmp_path):
    slots_module = load_cw_slots_module()
    sell_cw_hand_slots = getattr(slots_module, "sell_cw_hand_slots", None)
    assert sell_cw_hand_slots is not None

    session = build_fake_cw_session(tmp_path)
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    calls: list[int] = []

    def seller(slot: int) -> None:
        calls.append(slot)

    refreshed = sell_cw_hand_slots(session, slots=[2, 0], seller=seller)

    assert calls == [2, 0]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
    assert refreshed.scene_state["cw"]["slots"]["front"] == before_slots["front"]
    assert refreshed.scene_state["cw"]["slots"]["back"] == before_slots["back"]
    assert refreshed.scene_state["cw"]["slots"]["hand"] == before_slots["hand"]
    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_sell_cw_hand_slots_stops_after_first_runtime_failure(tmp_path):
    slots_module = load_cw_slots_module()
    sell_cw_hand_slots = getattr(slots_module, "sell_cw_hand_slots", None)
    assert sell_cw_hand_slots is not None

    session = build_fake_cw_session(tmp_path)
    before_slots = deepcopy(session.scene_state["cw"]["slots"])
    calls: list[int] = []

    def seller(slot: int) -> None:
        calls.append(slot)
        if len(calls) == 2:
            raise TrailError("UNEXPECTED_ERROR", f"sell failed at slot: {slot}")

    with pytest.raises(TrailError) as exc_info:
        sell_cw_hand_slots(session, slots=[2, 0, 1], seller=seller)

    assert exc_info.value.code == "UNEXPECTED_ERROR"
    assert calls == [2, 0]
    assert exc_info.value.known_failure_after_save is True
    assert session.scene_state["cw"]["slots"]["stale"] is True
    assert session.scene_state["cw"]["slots"]["front"] == before_slots["front"]
    assert session.scene_state["cw"]["slots"]["back"] == before_slots["back"]
    assert session.scene_state["cw"]["slots"]["hand"] == before_slots["hand"]
    assert session.scene_state["cw"]["sell_plan"] == {}


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


def _set_slots(session, slots: dict):
    session.scene_state.setdefault("cw", {})["slots"] = deepcopy(slots)
    return session


def _set_metrics(session, metrics: dict):
    session.scene_state.setdefault("cw", {})["metrics"] = dict(metrics)
    return session


def _set_sell_plan(session, plan: dict):
    session.scene_state.setdefault("cw", {})["sell_plan"] = dict(plan)
    return session


def _fake_reference_sell_plan():
    return {
        "reference_only": True,
        "candidates": [],
        "items": [
            {
                "slot": 0,
                "name": "阮·梅",
                "star": 1,
                "target_star": None,
                "current_star": None,
                "category": "非攻略",
                "recommendation": "推荐",
                "priority": 10,
                "protected": False,
                "reason": "非攻略角色",
            }
        ],
        "todos": [],
    }


def test_cw_slots_read_service_persists_incremental_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: (["希儿", None, None, None], [None] * 6, [None] * 9),
    )

    result = cw_service.handle(
        method="cw.slots.read",
        payload={"session_id": session.session_id, "slot": ["front:0"]},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["front"][0] == "希儿"
    assert service.load_session(session.session_id).scene_state["cw"]["slots"]["front"][0] == "希儿"


def test_cw_slots_read_service_preserves_existing_snapshot_shape(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
    )

    result = cw_service.handle(
        method="cw.slots.read",
        payload={"session_id": session.session_id, "slot": ["front:1"]},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["front"][1] == "布洛妮娅"
    assert service.load_session(session.session_id).scene_state["cw"]["slots"]["front"][1] == "布洛妮娅"


def test_cw_slots_read_service_requests_enriched_guide_config(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda **kwargs: calls.append(dict(kwargs)) or {"traits": [], "roles": []},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: (["希儿", None, None, None], [None] * 6, [None] * 9),
    )

    cw_service.handle(
        method="cw.slots.read",
        payload={"session_id": session.session_id, "slot": ["front:0"]},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert calls == [{"workspace_root": str(tmp_path), "enrich_traits": True}]


def test_cw_slots_read_command_service_promotes_catalog_warnings_without_persisting_them(tmp_path: Path, monkeypatch):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    runtime = Runtime()
    cw_service.runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda **kwargs: {
            "traits": [{"id": "1007", "name": "仙舟", "layers": [{"layer": 3}]}],
            "roles": [{"id": "1502", "name": "爻光", "trait_ids": ["1007"]}],
        },
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: ([{"name": "交光", "star": 1}, None, None, None], [None] * 6, [None] * 9),
    )

    envelope = command_service.handle(
        DaemonRequest(
            request_id="req-cw-slots-read-catalog-warning",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id, "slot": ["front:0"]},
        )
    )

    assert envelope["ok"] is True
    assert envelope["data"]["front"][0]["raw_name"] == "交光"
    assert envelope["data"]["front"][0]["match_kind"] == "low_confidence"
    assert "warnings" not in envelope["data"]
    assert envelope["warnings"][0]["code"] == "CW_ROLE_MATCH_LOW_CONFIDENCE"
    persisted = service.load_session(session.session_id).scene_state["cw"]["slots"]
    assert persisted["front"][0] == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}
    assert "warnings" not in persisted


def test_cw_slots_read_command_service_captures_screenshot(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda **kwargs: {"traits": [], "roles": []},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: (["希儿", None, None, None], [None] * 6, [None] * 9),
    )

    request_id = "req-cw-slots-read-capture"
    envelope = command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id, "slot": ["front:0"]},
        )
    )

    assert envelope["ok"] is True
    assert envelope["request_id"] == request_id
    assert envelope["screenshot"] == f".trail/shots/{request_id}.png"
    with pytest.raises(TrailError) as exc_info:
        service.request_status(request_id)
    assert exc_info.value.code == "REQUEST_NOT_FOUND"


def test_cw_slots_read_command_service_preserves_read_failure_semantics(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda **kwargs: {"traits": [], "roles": []},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: ([None] * 4, [None] * 6, [None] * 9),
    )

    request_id = "req-cw-slots-read-empty"
    envelope = command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id},
        )
    )

    assert envelope["ok"] is False
    assert envelope["request_id"] == request_id
    assert envelope["error"] == {
        "code": "SLOTS_READ_EMPTY",
        "message": "未读取到任何货币战争槽位角色，请确认当前在编队界面",
    }
    assert envelope["screenshot"] == f".trail/shots/{request_id}.png"


def test_cw_slots_read_command_service_preserves_unexpected_exception_semantics(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "unexpected.png"

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        command_service.handle(
            DaemonRequest(
                request_id="req-cw-slots-read-unexpected",
                protocol_version=PROTOCOL_VERSION,
                workspace_root=str(tmp_path),
                session_id=session.session_id,
                verbose=False,
                method="cw.slots.read",
                payload={"session_id": session.session_id},
            )
        )


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "assertion_key", "assertion_value", "state_path"),
    [
        (
            "cw.slots.swap",
            "req-slots-swap-1",
            {"source": "hand:0", "target": "front:0"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.slot_swapper_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.swap_cw_slots",
                    lambda session, source, target, swapper: _set_slots(session, {"front": ["希儿"], "back": [], "hand": [], "stale": False}),
                ),
            ),
            "front",
            ["希儿"],
            ("slots", "front"),
        ),
        (
            "cw.slots.place",
            "req-slots-place-1",
            {"actions": [{"source": "hand:0", "target": "front:0"}]},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.slot_placer_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.place_cw_slots",
                    lambda session, actions, placer: _set_slots(session, {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": False}),
                ),
            ),
            "back",
            ["佩拉"],
            ("slots", "back"),
        ),
        (
            "cw.crystals.collect",
            "req-crystals-collect-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.collect_cw_crystals",
                    lambda session, collector: _set_metrics(session, {"last_crystal_collection": "done"}),
                ),
            ),
            "last_crystal_collection",
            "done",
            ("metrics", "last_crystal_collection"),
        ),
        (
            "cw.hand.sell",
            "req-hand-sell-1",
            {"slots": [0]},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.hand_seller_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.sell_cw_hand_slots",
                    lambda session, slots, seller: _set_slots(session, {"front": [], "back": [], "hand": [None], "stale": True}),
                ),
            ),
            "stale",
            True,
            ("slots", "stale"),
        ),
        (
            "cw.hand.sell_plan",
            "req-hand-sell-plan-1",
            {},
            lambda monkeypatch: monkeypatch.setattr(
                "trail.daemon.cw_service.plan_cw_hand_sell",
                lambda session: (_set_sell_plan(session, _fake_reference_sell_plan()), _fake_reference_sell_plan())[1],
            ),
            "reference_only",
            True,
            ("sell_plan", "reference_only"),
        ),
    ],
)
def test_cw_slots_and_hand_mutations_flow_through_command_service_journal(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    payload: dict,
    setup_patches,
    assertion_key: str,
    assertion_value,
    state_path: tuple[str, str],
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
    persisted = service.load_session(session.session_id).scene_state["cw"]

    assert envelope["ok"] is True
    assert envelope["data"][assertion_key] == assertion_value
    assert status["final_state"] == "completed"
    assert persisted[state_path[0]][state_path[1]] == assertion_value
    if method == "cw.hand.sell_plan":
        assert envelope["data"]["items"] == _fake_reference_sell_plan()["items"]
        assert persisted["sell_plan"]["items"] == _fake_reference_sell_plan()["items"]
