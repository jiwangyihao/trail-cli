from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from difflib import SequenceMatcher
from time import sleep
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.ocr_config import OcrRequestConfig
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
SLOT_NAME_REGION = _region(0.78, 0.175, 0.880, 0.2315)
CANNOT_BE_FIELDED_REGION = _region(0.25, 0.25, 0.75, 0.75)
CRYSTAL_DRAG_DURATION_SECONDS = 0.2
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
SLOT_PANEL_SETTLE_SECONDS = 0.2
INITIAL_UI_DISMISS_SETTLE_SECONDS = 1.0
SLOT_NAME_STRIP_GAP = 24


def _clear_sell_plan(cw_state: dict) -> None:
    cw_state["sell_plan"] = {}


def _mark_slots_stale(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


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


def _capture_slot_name_panel_image(runtime, *, point: tuple[int, int]):
    runtime.click_point(*point)
    sleep(SLOT_PANEL_SETTLE_SECONDS * 2)
    try:
        return runtime.capture_image(**SLOT_NAME_REGION, normalize=False)
    finally:
        runtime.click_point(*INFO_DISMISS_POINT)
        sleep(SLOT_PANEL_SETTLE_SECONDS)


def _compose_slot_name_strip_image(captures: list[dict[str, Any]]) -> tuple[Image.Image, list[dict[str, Any]]]:
    if not captures:
        return Image.new("RGB", (1, 1), color="white"), []

    converted = [capture["image"].convert("RGB") for capture in captures]
    width = max(image.width for image in converted)
    total_height = sum(image.height for image in converted) + SLOT_NAME_STRIP_GAP * (len(converted) - 1)
    strip = Image.new("RGB", (width, total_height), color="white")
    layouts: list[dict[str, Any]] = []
    cursor_y = 0
    for capture, image in zip(captures, converted, strict=False):
        strip.paste(image, (0, cursor_y))
        layouts.append({
            "area": capture["area"],
            "index": capture["index"],
            "top": cursor_y,
            "bottom": cursor_y + image.height,
        })
        cursor_y += image.height + SLOT_NAME_STRIP_GAP
    return strip, layouts


def _read_piece_box(piece: Any) -> tuple[int, int, int, int] | None:
    box = None
    if isinstance(piece, dict):
        box = piece.get("box") or piece.get("points")
        if box is None and {"left", "top", "width", "height"}.issubset(piece):
            box = piece
    elif isinstance(piece, (list, tuple)) and piece:
        box = piece[0]

    if box is None:
        return None
    if isinstance(box, dict) and {"left", "top", "width", "height"}.issubset(box):
        return int(box["left"]), int(box["top"]), int(box["width"]), int(box["height"])
    if all(hasattr(box, attr) for attr in ("left", "top", "width", "height")):
        return int(box.left), int(box.top), int(box.width), int(box.height)
    if isinstance(box, (list, tuple)) and box and all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in box):
        xs = [int(point[0]) for point in box]
        ys = [int(point[1]) for point in box]
        left = min(xs)
        top = min(ys)
        return left, top, max(xs) - left, max(ys) - top
    return None


def _find_slot_name_layout(layouts: list[dict[str, Any]], *, center_y: float) -> dict[str, Any] | None:
    for index, layout in enumerate(layouts):
        if layout["top"] <= center_y < layout["bottom"]:
            return layout
        if index == len(layouts) - 1 and center_y == layout["bottom"]:
            return layout
    return None


def _map_ocr_pieces_to_slot_names(
    layouts: list[dict[str, Any]],
    pieces: list[Any],
    *,
    dropped: list[dict[str, Any]] | None = None,
) -> dict[tuple[str, int], str | None]:
    grouped: dict[tuple[str, int], list[tuple[int, int | None, int | None, str, bool]]] = {
        (layout["area"], layout["index"]): [] for layout in layouts
    }
    single_target = (layouts[0]["area"], layouts[0]["index"]) if len(layouts) == 1 else None
    for order, piece in enumerate(pieces):
        text = _read_ocr_piece(piece).strip()
        if not text:
            continue
        bounds = _read_piece_box(piece)
        if bounds is None:
            if single_target is not None:
                grouped[single_target].append((order, None, None, text, False))
            elif dropped is not None:
                dropped.append({"reason": "missing_box", "order": order, "text": text})
            continue
        left, top, width, height = bounds
        center_y = top + (height / 2)
        layout = _find_slot_name_layout(layouts, center_y=center_y)
        if layout is None:
            if dropped is not None:
                dropped.append({"reason": "center_y_outside_layout", "order": order, "text": text, "center_y": center_y})
            continue
        grouped[(layout["area"], layout["index"])].append((order, top, left, text, True))

    names: dict[tuple[str, int], str | None] = {}
    for layout in layouts:
        key = (layout["area"], layout["index"])
        entries = grouped[key]
        boxed_entries = [item for item in entries if item[4]]
        geometryless_entries = [item for item in entries if not item[4]]
        boxed_entries.sort(key=lambda item: (item[1], item[2], item[0]))
        geometryless_entries.sort(key=lambda item: item[0])
        ordered_entries = boxed_entries + geometryless_entries if boxed_entries else geometryless_entries
        name = "".join(text for _, _, _, text, _ in ordered_entries).strip()
        names[key] = name or None
    return names


def _record_slot_name_piece_drops(runtime, dropped: list[dict[str, Any]]) -> None:
    record_trace = getattr(runtime, "_record_trace", None)
    if not callable(record_trace):
        return
    for payload in dropped:
        record_trace("cw_slots_batch_ocr_drop", **payload)


def _read_batch_slot_names(runtime, captures: list[dict[str, Any]]) -> dict[tuple[str, int], str | None]:
    strip, layouts = _compose_slot_name_strip_image(captures)
    pieces = runtime.ocr_image(
        strip,
        ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"),
    ) or []
    dropped: list[dict[str, Any]] = []
    names = _map_ocr_pieces_to_slot_names(layouts, pieces, dropped=dropped)
    _record_slot_name_piece_drops(runtime, dropped)
    return names


def _collapse_expanded_hand_card(runtime) -> None:
    template = _template(OPEN_TEMPLATE_ALIAS)
    for _ in range(HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS):
        box = runtime.locate(template)
        if box is None:
            return
        runtime.click_point(*_box_center(box))
        runtime.click_point(*HAND_EXPAND_DISMISS_POINT)
        sleep(SLOT_PANEL_SETTLE_SECONDS)
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


def _score_slot_name_candidates(text: str, *, candidates: list[str]) -> list[tuple[str, float]]:
    scored = [(candidate, SequenceMatcher(a=text, b=candidate).ratio()) for candidate in candidates]
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _collect_nested_slot_name_candidates(value: Any, *, candidates: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "name":
                text = str(item or "").strip()
                if text:
                    candidates.append(text)
            _collect_nested_slot_name_candidates(item, candidates=candidates)
        return
    if isinstance(value, list):
        for item in value:
            _collect_nested_slot_name_candidates(item, candidates=candidates)


def _dedupe_slot_name_candidates(candidates: list[str]) -> list[str]:
    return list(dict.fromkeys(candidates))


def _session_slot_name_candidates(cw_state: dict[str, Any]) -> tuple[list[str], list[str]]:
    authoritative_candidates: list[str] = []
    guide = cw_state.get("guide") if isinstance(cw_state.get("guide"), dict) else {}
    for group in (guide.get("on_field", {}), guide.get("off_field", {})):
        if isinstance(group, dict):
            for name in group:
                text = str(name).strip()
                if text:
                    authoritative_candidates.append(text)
    _collect_nested_slot_name_candidates(guide, candidates=authoritative_candidates)

    portal = cw_state.get("portal") if isinstance(cw_state.get("portal"), dict) else {}
    _collect_nested_slot_name_candidates(portal, candidates=authoritative_candidates)

    slot_candidates: list[str] = []
    slots = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    if slots.get("stale") is False:
        for area in ("front", "back", "hand"):
            for value in slots.get(area, []) or []:
                text = str(value or "").strip()
                if text:
                    slot_candidates.append(text)
    return _dedupe_slot_name_candidates(authoritative_candidates), _dedupe_slot_name_candidates(slot_candidates)


def _is_short_slot_name_variant(left: str, right: str) -> bool:
    if not left or not right or left == right or len(left) != len(right) or len(left) > 4:
        return False
    same_positions = sum(1 for left_char, right_char in zip(left, right, strict=False) if left_char == right_char)
    return same_positions >= len(left) - 1


def _normalize_slot_name_against_candidates(text: str, *, candidates: list[str]) -> str | None:
    if text in candidates:
        return text

    scored = _score_slot_name_candidates(text, candidates=candidates)
    if not scored:
        return None
    best_candidate, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    if best_score >= 0.72 and (best_score - second_score) >= 0.12:
        return best_candidate

    short_variants = [candidate for candidate, _ in scored if _is_short_slot_name_variant(text, candidate)]
    if len(short_variants) == 1:
        return short_variants[0]
    return None


def _normalize_slot_name(raw: str | None, *, authoritative_candidates: list[str], slot_candidates: list[str]) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = _normalize_slot_name_against_candidates(text, candidates=authoritative_candidates)
    if normalized is not None:
        return normalized
    normalized = _normalize_slot_name_against_candidates(text, candidates=slot_candidates)
    if normalized is not None:
        return normalized
    return text


def _next_slots_stale(previous: dict[str, Any], *, parsed_targets: dict[str, set[int]] | None) -> bool:
    if parsed_targets is None:
        return False
    return previous.get("stale", True) is not False


def _slots_read_empty_snapshot(
    previous: dict[str, Any],
    *,
    parsed_targets: dict[str, set[int]] | None,
    front: list[Any],
    back: list[Any],
    hand: list[Any],
    merged_front: list[Any],
    merged_back: list[Any],
    merged_hand: list[Any],
) -> bool:
    if parsed_targets is None:
        return not _snapshot_has_any_name(front, back, hand)
    if previous.get("stale", True) is False:
        return not _snapshot_has_any_name(merged_front, merged_back, merged_hand)
    return not _snapshot_has_any_name(front, back, hand)


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

        captures: list[dict[str, Any]] = []
        runtime.click_point(*INFO_DISMISS_POINT)
        sleep(INITIAL_UI_DISMISS_SETTLE_SECONDS)
        for area in ("front", "back", "hand"):
            points = SLOT_POINTS_BY_AREA[area]
            for index in sorted(targets_by_area[area]):
                captures.append({
                    "area": area,
                    "index": index,
                    "image": _capture_slot_name_panel_image(runtime, point=points[index]),
                })

        names_by_slot = _read_batch_slot_names(runtime, captures)
        for (area, index), value in names_by_slot.items():
            if area == "front":
                front[index] = value
            elif area == "back":
                back[index] = value
            else:
                hand[index] = value
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
            runtime.drag_to(*drag, duration=CRYSTAL_DRAG_DURATION_SECONDS)

    return collector


def read_cw_slots(session: SessionModel, *, reader: SlotsSnapshotReader, targets: list[str] | None = None) -> SessionModel:
    front, back, hand = reader()
    cw_state = ensure_cw_state(session)
    previous = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    parsed_targets = _parse_slot_targets(targets)
    authoritative_candidates, slot_candidates = _session_slot_name_candidates(cw_state)
    front = [_normalize_slot_name(value, authoritative_candidates=authoritative_candidates, slot_candidates=slot_candidates) for value in front]
    back = [_normalize_slot_name(value, authoritative_candidates=authoritative_candidates, slot_candidates=slot_candidates) for value in back]
    hand = [_normalize_slot_name(value, authoritative_candidates=authoritative_candidates, slot_candidates=slot_candidates) for value in hand]
    merged_front = _merge_area_snapshot(previous.get("front"), front, size=len(FRONT_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["front"])
    merged_back = _merge_area_snapshot(previous.get("back"), back, size=len(BACK_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["back"])
    merged_hand = _merge_area_snapshot(previous.get("hand"), hand, size=len(HAND_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["hand"])
    if _slots_read_empty_snapshot(
        previous,
        parsed_targets=parsed_targets,
        front=front,
        back=back,
        hand=hand,
        merged_front=merged_front,
        merged_back=merged_back,
        merged_hand=merged_hand,
    ):
        raise TrailError("SLOTS_READ_EMPTY", "未读取到任何货币战争槽位角色，请确认当前在编队界面")
    cw_state["slots"] = {
        "front": deepcopy(merged_front),
        "back": deepcopy(merged_back),
        "hand": deepcopy(merged_hand),
        "stale": _next_slots_stale(previous, parsed_targets=parsed_targets),
    }
    _clear_sell_plan(cw_state)
    return session


def _normalize_place_actions(actions: list[dict[str, str]]) -> list[tuple[str, str]]:
    if not actions:
        raise TrailError("CW_OPTION_INVALID", "cw slots place requires at least one --action")

    normalized: list[tuple[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            raise TrailError("CW_OPTION_INVALID", f"cw slots place action invalid: {action!r}")
        source = action.get("source")
        target = action.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise TrailError("CW_OPTION_INVALID", f"cw slots place action invalid: {action!r}")
        _parse_slot_reference(source)
        _parse_slot_reference(target)
        normalized.append((source, target))
    return normalized


def _normalize_sell_slots(slots: list[int]) -> list[int]:
    if not slots:
        raise TrailError("CW_OPTION_INVALID", "cw hand sell requires at least one --slot")

    normalized: list[int] = []
    for slot in slots:
        if not isinstance(slot, int) or isinstance(slot, bool):
            raise TrailError("CW_OPTION_INVALID", f"cw hand sell slot invalid: {slot!r}")
        if slot < 0 or slot >= len(HAND_SLOT_POINTS):
            raise TrailError("SLOTS_POSITION_INVALID", f"invalid hand slot: {slot}")
        normalized.append(slot)
    return normalized


def swap_cw_slots(session: SessionModel, *, source: str, target: str, swapper: SlotMover | None = None) -> SessionModel:
    if swapper is not None:
        swapper(source, target)
    del source, target
    return _mark_slots_stale(session)


def place_cw_slots(session: SessionModel, *, actions: list[dict[str, str]], placer: SlotMover | None = None) -> SessionModel:
    normalized = _normalize_place_actions(actions)
    for source, target in normalized:
        try:
            if placer is not None:
                placer(source, target)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)


def place_one_cw_slot(session: SessionModel, *, source: str, target: str, placer: SlotMover | None = None) -> SessionModel:
    if placer is not None:
        placer(source, target)
    del source, target
    return _mark_slots_stale(session)


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


def sell_cw_hand_slots(session: SessionModel, *, slots: list[int], seller: HandSeller | None = None) -> SessionModel:
    normalized = _normalize_sell_slots(slots)
    for slot in normalized:
        try:
            if seller is not None:
                seller(slot)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)


def sell_one_cw_hand(session: SessionModel, *, slot: int, seller: HandSeller | None = None) -> SessionModel:
    if seller is not None:
        seller(slot)
    del slot
    return _mark_slots_stale(session)
