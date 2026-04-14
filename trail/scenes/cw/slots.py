from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.runtime.resources import resolve_scene_asset
from trail.session.models import SessionModel

SlotsSnapshotReader = Callable[[], tuple[list[Any], list[Any], list[Any]]]
SlotMover = Callable[[str, str], object]
HandSeller = Callable[[int], object]
CrystalCollector = Callable[[], object]

CW_WIDTH = 1920
CW_HEIGHT = 1080


def _point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
    return int(CW_WIDTH * x_ratio), int(CW_HEIGHT * y_ratio)


def _region(from_x: float, from_y: float, to_x: float, to_y: float) -> dict[str, int]:
    return {
        "from_x": int(CW_WIDTH * from_x),
        "from_y": int(CW_HEIGHT * from_y),
        "to_x": int(CW_WIDTH * to_x),
        "to_y": int(CW_HEIGHT * to_y),
    }


FRONT_SLOT_POINTS = [
    _point(0.386, 0.365),
    _point(0.464, 0.365),
    _point(0.536, 0.365),
    _point(0.611, 0.365),
]
BACK_SLOT_POINTS = [
    _point(0.3056, 0.620),
    _point(0.3806, 0.620),
    _point(0.4525, 0.620),
    _point(0.5264, 0.620),
    _point(0.6004, 0.620),
    _point(0.6738, 0.620),
]
HAND_SLOT_POINTS = [
    _point(0.229, 0.844),
    _point(0.297, 0.844),
    _point(0.358, 0.844),
    _point(0.426, 0.844),
    _point(0.488, 0.844),
    _point(0.556, 0.844),
    _point(0.618, 0.844),
    _point(0.684, 0.844),
    _point(0.749, 0.844),
]
SLOT_POINTS_BY_AREA = {
    "front": FRONT_SLOT_POINTS,
    "back": BACK_SLOT_POINTS,
    "hand": HAND_SLOT_POINTS,
}

INFO_DISMISS_POINT = _point(0.5, 0.5)
HAND_EXPAND_DISMISS_POINT = _point(0.35, 0.20)
SELL_SLOT_POINT = _point(0.05, 0.86)
SLOT_NAME_REGION = _region(0.775, 0.175, 0.880, 0.2315)
CANNOT_BE_FIELDED_REGION = _region(0.25, 0.25, 0.75, 0.75)
CRYSTAL_DRAG_PATHS = [
    (*_point(0.68, 0.18), *_point(0.82, 0.18)),
    (*_point(0.68, 0.25), *_point(0.82, 0.25)),
    (*_point(0.68, 0.30), *_point(0.83, 0.30)),
    (*_point(0.68, 0.35), *_point(0.84, 0.35)),
    (*_point(0.68, 0.40), *_point(0.83, 0.40)),
]

OPEN_TEMPLATE_ALIAS = "slots.open"
CANNOT_BE_FIELDED_ALIAS = "slots.cannot_be_fielded"
HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS = 5


def _clear_sell_plan(cw_state: dict) -> None:
    cw_state["sell_plan"] = {}


def _snapshot_has_any_name(*areas: list[Any]) -> bool:
    return any(value is not None and str(value).strip() for area in areas for value in area)


def _template(scene_alias: str) -> str:
    return str(resolve_scene_asset("cw", scene_alias))


def _box_center(box: Any) -> tuple[int, int]:
    center = getattr(box, "center", None)
    if isinstance(center, tuple) and len(center) == 2:
        return int(center[0]), int(center[1])

    if isinstance(box, dict):
        return int(box["left"]) + int(box["width"]) // 2, int(box["top"]) + int(box["height"]) // 2

    raise TrailError("SLOTS_UI_INVALID", "slot ui match result missing box coordinates")


def _read_ocr_piece(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], str | int | float):
            return str(item[1])
        for value in item:
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, str | int | float):
        return str(item)
    return ""


def _read_slot_name(runtime, *, point: tuple[int, int]) -> str | None:
    runtime.click_point(*point)
    try:
        pieces = runtime.ocr(**SLOT_NAME_REGION) or []
    finally:
        runtime.click_point(*INFO_DISMISS_POINT)
    name = "".join(_read_ocr_piece(piece).strip() for piece in pieces).strip()
    return name or None


def _collapse_expanded_hand_card(runtime) -> None:
    template = _template(OPEN_TEMPLATE_ALIAS)
    for _ in range(HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS):
        box = runtime.locate(template)
        if box is None:
            return
        runtime.click_point(*_box_center(box))
        runtime.click_point(*HAND_EXPAND_DISMISS_POINT)
    raise TrailError("SLOTS_OPEN_STUCK", "手牌展开卡片未关闭，请确认当前在编队界面")


def _slot_points(area: str) -> list[tuple[int, int]]:
    points = SLOT_POINTS_BY_AREA.get(area)
    if points is None:
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {area}")
    return points


def _empty_slots_snapshot() -> tuple[list[Any], list[Any], list[Any]]:
    return [None] * len(FRONT_SLOT_POINTS), [None] * len(BACK_SLOT_POINTS), [None] * len(HAND_SLOT_POINTS)


def _parse_slot_targets(targets: list[str] | None = None) -> dict[str, set[int]] | None:
    if not targets:
        return None

    parsed = {"front": set(), "back": set(), "hand": set()}
    for target in targets:
        area, index = _parse_slot_reference(target, allowed_areas={"front", "back", "hand"})
        parsed[area].add(index)
    return parsed


def _merge_area_snapshot(previous: Any, current: list[Any], *, size: int, targets: set[int] | None) -> list[Any]:
    if targets is None:
        return list(current)

    merged = list(previous) if isinstance(previous, list) else [None] * size
    if len(merged) < size:
        merged.extend([None] * (size - len(merged)))
    else:
        merged = merged[:size]

    for index in targets:
        merged[index] = current[index]
    return merged


def _parse_slot_reference(value: str, *, allowed_areas: set[str] | None = None) -> tuple[str, int]:
    area, separator, raw_index = value.partition(":")
    if separator != ":" or not raw_index.isdecimal():
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {value}")
    if allowed_areas is not None and area not in allowed_areas:
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {value}")

    index = int(raw_index)
    points = _slot_points(area)
    if index < 0 or index >= len(points):
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {value}")
    return area, index


def _ensure_fieldable_target(runtime, *, target: str) -> None:
    box = runtime.locate(_template(CANNOT_BE_FIELDED_ALIAS), **CANNOT_BE_FIELDED_REGION)
    if box is None:
        return
    runtime.click_point(*INFO_DISMISS_POINT)
    raise TrailError("SLOTS_CANNOT_BE_FIELDED", f"target slot cannot field character: {target}")


def build_cw_slots_reader(runtime, targets: list[str] | None = None) -> SlotsSnapshotReader:
    parsed_targets = _parse_slot_targets(targets)

    def reader() -> tuple[list[str | None], list[str | None], list[str | None]]:
        _collapse_expanded_hand_card(runtime)
        front, back, hand = _empty_slots_snapshot()
        targets_by_area = parsed_targets or {
            "front": set(range(len(FRONT_SLOT_POINTS))),
            "back": set(range(len(BACK_SLOT_POINTS))),
            "hand": set(range(len(HAND_SLOT_POINTS))),
        }

        for index in sorted(targets_by_area["front"]):
            front[index] = _read_slot_name(runtime, point=FRONT_SLOT_POINTS[index])
        for index in sorted(targets_by_area["back"]):
            back[index] = _read_slot_name(runtime, point=BACK_SLOT_POINTS[index])
        for index in sorted(targets_by_area["hand"]):
            hand[index] = _read_slot_name(runtime, point=HAND_SLOT_POINTS[index])
        return front, back, hand

    return reader


def build_cw_slot_swapper(runtime) -> SlotMover:
    def swapper(source: str, target: str) -> None:
        source_area, source_index = _parse_slot_reference(source)
        target_area, target_index = _parse_slot_reference(target)
        runtime.drag_to(*_slot_points(source_area)[source_index], *_slot_points(target_area)[target_index])
        if target_area != "hand":
            _ensure_fieldable_target(runtime, target=target)

    return swapper


def build_cw_hand_seller(runtime) -> HandSeller:
    def seller(slot: int) -> None:
        if slot < 0 or slot >= len(HAND_SLOT_POINTS):
            raise TrailError("SLOTS_POSITION_INVALID", f"invalid hand slot: {slot}")
        runtime.drag_to(*HAND_SLOT_POINTS[slot], *SELL_SLOT_POINT)

    return seller


def build_cw_crystal_collector(runtime) -> CrystalCollector:
    def collector() -> None:
        for drag in CRYSTAL_DRAG_PATHS:
            runtime.drag_to(*drag)

    return collector


def read_cw_slots(session: SessionModel, *, reader: SlotsSnapshotReader, targets: list[str] | None = None) -> SessionModel:
    front, back, hand = reader()
    cw_state = ensure_cw_state(session)
    previous = cw_state.get("slots", {})
    parsed_targets = _parse_slot_targets(targets)
    merged_front = _merge_area_snapshot(previous.get("front"), front, size=len(FRONT_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["front"])
    merged_back = _merge_area_snapshot(previous.get("back"), back, size=len(BACK_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["back"])
    merged_hand = _merge_area_snapshot(previous.get("hand"), hand, size=len(HAND_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["hand"])
    if not _snapshot_has_any_name(merged_front, merged_back, merged_hand):
        raise TrailError("SLOTS_READ_EMPTY", "未读取到任何货币战争槽位角色，请确认当前在编队界面")
    cw_state["slots"] = {
        "front": deepcopy(merged_front),
        "back": deepcopy(merged_back),
        "hand": deepcopy(merged_hand),
        "stale": False,
    }
    _clear_sell_plan(cw_state)
    return session


def swap_cw_slots(session: SessionModel, *, source: str, target: str, swapper: SlotMover | None = None) -> SessionModel:
    if swapper is not None:
        swapper(source, target)
    del source, target
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


def place_one_cw_slot(session: SessionModel, *, source: str, target: str, placer: SlotMover | None = None) -> SessionModel:
    if placer is not None:
        placer(source, target)
    del source, target
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


def collect_cw_crystals(session: SessionModel, *, collector: CrystalCollector | None = None) -> SessionModel:
    if collector is not None:
        collector()
    cw_state = ensure_cw_state(session)
    metrics = cw_state.setdefault("metrics", {})
    metrics["last_crystal_collection"] = "done"
    return session


def plan_cw_hand_sell(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    slots = cw_state.get("slots", {})
    if slots.get("stale", True):
        raise TrailError("SLOTS_STALE", "槽位快照已失效，请先执行 trail cw slots read")
    hand = slots.get("hand", [])
    candidates = [index for index, value in enumerate(hand) if value is not None]
    cw_state["sell_plan"] = {"candidates": candidates}
    return deepcopy(cw_state["sell_plan"])


def sell_one_cw_hand(session: SessionModel, *, slot: int, seller: HandSeller | None = None) -> SessionModel:
    if seller is not None:
        seller(slot)
    del slot
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session
