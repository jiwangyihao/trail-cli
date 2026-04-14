from __future__ import annotations

import importlib
import json
from copy import deepcopy

import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.runtime.model import Box
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


def test_build_cw_slots_reader_reads_runtime_slot_snapshots_and_closes_overlay():
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.locate_calls = 0
            self.ocr_calls: list[dict] = []
            self._ocr_results = iter(
                [
                    [([0, 0], "希儿", 0.99)],
                    [],
                    [],
                    [],
                    [([0, 0], "佩拉", 0.99)],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [([0, 0], "银狼", 0.99)],
                    [],
                    [([0, 0], "阮·梅", 0.99)],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                ]
            )

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            if self.locate_calls == 1:
                return Box(left=10, top=20, width=30, height=40)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            return next(self._ocr_results)

    runtime = RuntimeSpy()

    front, back, hand = build_cw_slots_reader(runtime)()

    assert front == ["希儿", None, None, None]
    assert back == ["佩拉", None, None, None, None, None]
    assert hand == ["银狼", None, "阮·梅", None, None, None, None, None, None]
    assert runtime.clicks[0] == (25, 40)
    assert runtime.clicks[1] == slots_module.HAND_EXPAND_DISMISS_POINT
    assert len(runtime.ocr_calls) == 19


def test_build_cw_slots_reader_reads_only_requested_slots():
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.locate_calls = 0
            self.ocr_calls: list[dict] = []
            self._ocr_results = iter(
                [
                    [([0, 0], "希儿", 0.99)],
                    [([0, 0], "阮·梅", 0.99)],
                ]
            )

        def locate(self, template: str, **kwargs):
            del template, kwargs
            self.locate_calls += 1
            if self.locate_calls == 1:
                return Box(left=10, top=20, width=30, height=40)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            return next(self._ocr_results)

    runtime = RuntimeSpy()

    front, back, hand = build_cw_slots_reader(runtime, targets=["front:0", "hand:2"])()

    assert front == ["希儿", None, None, None]
    assert back == [None, None, None, None, None, None]
    assert hand == [None, None, "阮·梅", None, None, None, None, None, None]
    assert len(runtime.ocr_calls) == 2


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


def test_collapse_expanded_hand_card_rejects_stuck_open_overlay():
    slots_module = load_cw_slots_module()
    collapse = getattr(slots_module, "_collapse_expanded_hand_card", None)
    assert collapse is not None

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


def test_build_cw_hand_seller_and_crystal_collector_use_runtime_drags():
    slots_module = load_cw_slots_module()
    build_cw_hand_seller = getattr(slots_module, "build_cw_hand_seller", None)
    build_cw_crystal_collector = getattr(slots_module, "build_cw_crystal_collector", None)
    assert build_cw_hand_seller is not None
    assert build_cw_crystal_collector is not None

    class RuntimeSpy:
        def __init__(self):
            self.drags: list[tuple[int, int, int, int]] = []

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
            self.drags.append((from_x, from_y, to_x, to_y))

    runtime = RuntimeSpy()

    build_cw_hand_seller(runtime)(slot=2)
    build_cw_crystal_collector(runtime)()

    assert runtime.drags[0] == (*slots_module.HAND_SLOT_POINTS[2], *slots_module.SELL_SLOT_POINT)
    assert runtime.drags[1:] == slots_module.CRYSTAL_DRAG_PATHS


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
    locate_results = iter([Box(left=10, top=20, width=30, height=40), None])
    ocr_results = iter(
        [
            [([0, 0], "希儿", 0.99)],
            [],
            [],
            [],
            [([0, 0], "佩拉", 0.99)],
            [],
            [],
            [],
            [],
            [],
            [([0, 0], "银狼", 0.99)],
            [],
            [([0, 0], "阮·梅", 0.99)],
            [],
            [],
            [],
            [],
            [],
            [],
        ]
    )

    monkeypatch.setattr(fake_runtime, "locate", lambda template, **kwargs: next(locate_results), raising=False)
    monkeypatch.setattr(fake_runtime, "ocr", lambda **kwargs: next(ocr_results), raising=False)

    result = cli_runner.invoke(app, ["cw", "slots", "read", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]


def test_cw_slots_read_cli_accepts_targeted_slots_and_preserves_existing_snapshot(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    ensure_cw_state(session)["slots"] = {
        "front": ["希儿", None, None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, None, None, None, None, None, None, None],
        "stale": False,
    }
    store.save(session)

    locate_results = iter([Box(left=10, top=20, width=30, height=40), None])
    ocr_results = iter(
        [
            [([0, 0], "布洛妮娅", 0.99)],
            [([0, 0], "阮·梅", 0.99)],
        ]
    )

    monkeypatch.setattr(fake_runtime, "locate", lambda template, **kwargs: next(locate_results), raising=False)
    monkeypatch.setattr(fake_runtime, "ocr", lambda **kwargs: next(ocr_results), raising=False)

    result = cli_runner.invoke(
        app,
        ["cw", "slots", "read", "--session", fake_session, "--slot", "front:1", "--slot", "hand:2"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {
        "front": ["希儿", "布洛妮娅", None, None],
        "back": ["佩拉", None, None, None, None, None],
        "hand": ["银狼", None, "阮·梅", None, None, None, None, None, None],
        "stale": False,
    }


def test_cw_slots_read_cli_rejects_empty_snapshot_without_polluting_session(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    monkeypatch.setattr(fake_runtime, "locate", lambda template, **kwargs: None, raising=False)
    monkeypatch.setattr(fake_runtime, "ocr", lambda **kwargs: [], raising=False)

    result = cli_runner.invoke(app, ["cw", "slots", "read", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SLOTS_READ_EMPTY"

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert "cw" not in session.scene_state


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
    assert fake_runtime.drags == [
        (439, 911, 741, 394),
    ]

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
    assert fake_runtime.drags == [
        (687, 911, 586, 669),
    ]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]


def test_cw_crystals_collect_cli_records_metric(cli_runner, fake_runtime, fake_session, tmp_path):
    result = cli_runner.invoke(app, ["cw", "crystals", "collect", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"last_crystal_collection": "done"}
    assert fake_runtime.drags == [
        (1305, 194, 1574, 194),
        (1305, 270, 1574, 270),
        (1305, 324, 1593, 324),
        (1305, 378, 1612, 378),
        (1305, 432, 1593, 432),
    ]

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
    assert fake_runtime.drags == [
        (687, 911, 96, 928),
    ]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["slots"] == payload["data"]
