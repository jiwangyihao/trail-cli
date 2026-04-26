from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

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
    from trail.runtime.batch_ocr import BatchOcrTarget, pack_batch_ocr_targets

    batch_targets = [
        BatchOcrTarget(target["key"], Image.new("RGB", target["size"]))
        for target in targets
    ]
    _, packed = pack_batch_ocr_targets(batch_targets)
    pieces = []
    for target, item in zip(targets, packed, strict=True):
        text = target.get("text")
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


def test_slots_read_enriches_slot_traits_and_summarizes_field_traits(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "traits": [
            {"id": 2001, "name": "巡猎"},
            {"id": 2002, "name": "量子"},
            {"id": 2003, "name": "辅助"},
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

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == {"name": "希儿", "star": 4, "traits": ["巡猎", "量子"]}
    assert refreshed.scene_state["cw"]["slots"]["back"][0] == {"name": "佩拉", "star": 2, "traits": ["量子"]}
    assert refreshed.scene_state["cw"]["slots"]["hand"][0] == {"name": "布洛妮娅", "star": 3, "traits": ["巡猎", "辅助"]}
    assert refreshed.scene_state["cw"]["slots"]["trait_summary"] == [
        {"trait": "量子", "tiers": [1, 2], "owned_roles": 2, "active_tier": 2, "total_tiers": 2, "ratio": 1.0},
        {"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5},
    ]


def test_summarize_field_trait_status_sorts_by_activation_ratio_and_limits_top_ten():
    slots_module = load_cw_slots_module()
    summarize = getattr(slots_module, "_summarize_field_trait_status", None)
    assert summarize is not None

    front = [{"name": f"角色{index}", "traits": [f"羁绊{index}"]} for index in range(12)]
    back = []
    guide_config = {
        "traits": [{"id": index, "name": f"羁绊{index}"} for index in range(12)],
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
