from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_build_cw_slots_reader_reads_runtime_slot_snapshots_and_closes_overlay(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

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


def test_build_cw_slots_reader_reads_only_requested_slots(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

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


def test_build_cw_slots_reader_waits_for_slot_panel_settle_between_interactions(monkeypatch):
    slots_module = load_cw_slots_module()
    build_cw_slots_reader = getattr(slots_module, "build_cw_slots_reader", None)
    assert build_cw_slots_reader is not None

    class RuntimeSpy:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.state = "idle"
            self.current_name: str | None = None
            self.names = {
                slots_module.FRONT_SLOT_POINTS[0]: "希儿",
                slots_module.HAND_SLOT_POINTS[0]: "银狼",
            }

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
            self.state = "panel-opening"

        def ocr(self, **kwargs):
            del kwargs
            if self.state == "panel-open" and self.current_name is not None:
                return [([0, 0], self.current_name, 0.99)]
            return []

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

    front, back, hand = build_cw_slots_reader(runtime, targets=["front:0", "hand:0"])()

    assert front == ["希儿", None, None, None]
    assert back == [None, None, None, None, None, None]
    assert hand == ["银狼", None, None, None, None, None, None, None, None]


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


def test_cw_slots_read_service_persists_incremental_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
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
            "cw.slots.place_one",
            "req-slots-place-1",
            {"source": "hand:0", "target": "front:0"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.slot_placer_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.place_one_cw_slot",
                    lambda session, source, target, placer: _set_slots(session, {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": False}),
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
            "cw.hand.sell_one",
            "req-hand-sell-one-1",
            {"slot": 0},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.hand_seller_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.sell_one_cw_hand",
                    lambda session, slot, seller: _set_slots(session, {"front": [], "back": [], "hand": [None], "stale": True}),
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
                lambda session: (_set_sell_plan(session, {"candidates": [0, 2]}), {"candidates": [0, 2]})[1],
            ),
            "candidates",
            [0, 2],
            ("sell_plan", "candidates"),
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
