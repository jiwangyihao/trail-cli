from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
import re
from typing import Any

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.entry import _detect_current_enter_page
from trail.scenes.cw.guide import SHARE_CODE_PATTERN
from trail.scenes.cw.models import ensure_cw_state

STRATEGY_SCREEN_WIDTH = 1920
STRATEGY_LANE_COUNT = 3
STRATEGY_CARD_CENTER_Y = 320
STRATEGY_REFRESH_Y = 800
STRATEGY_CONFIRM_POINT = (1480, 560)
STRATEGY_PAGE_MARKERS = ("请选择投资策略",)
STRATEGY_ROW_MERGE_Y_DELTA = 20
STRATEGY_CARD_MIN_CENTER_Y = 120
STRATEGY_CARD_MAX_CENTER_Y = 900
VALID_STRATEGY_CARD_INDEXES = frozenset({1, 2, 3})
STRATEGY_NOISE_PATTERN = re.compile(r"[，。！？：；、“”‘’（）《》〈〉【】『』「」—…·,.;:!?\'\"()\[\]{}<>/\\|@#$%^&*_+=~-]+")
STRATEGY_WHITESPACE_PATTERN = re.compile(r"\s+")
STRATEGY_REFRESH_PATTERN = re.compile(r"刷新次数\s*([0-9]+)")
STRATEGY_MATCH_THRESHOLD = 0.6


def summarize_strategy_cards(
    ocr_pieces: object,
    strategy_list: object,
    *,
    guide_state: object | None = None,
) -> list[dict[str, object]]:
    catalog = _normalize_strategy_catalog(strategy_list)
    raw_cards = _normalize_strategy_card_inputs(ocr_pieces)
    if not raw_cards:
        return []
    raw_cards = _order_strategy_candidates(raw_cards)

    cards: list[dict[str, object]] = []
    for index, item in enumerate(raw_cards, start=1):
        card_idx = int(item.get("card_idx") or index)
        if card_idx not in VALID_STRATEGY_CARD_INDEXES:
            continue
        matched = _match_strategy_catalog(
            str(item.get("strategy_title") or ""),
            str(item.get("strategy_description") or ""),
            catalog,
        )
        if item.get("_match_required") and not isinstance(matched, Mapping):
            continue
        strategy_title = str(matched.get("title") if isinstance(matched, Mapping) else item.get("strategy_title") or "")
        strategy_description = str(
            matched.get("description") if isinstance(matched, Mapping) and matched.get("description") else item.get("strategy_description") or ""
        )
        guide_match, guide_loaded = _guide_match_for_strategy(strategy_title, guide_state)
        cards.append(
            {
                "card_idx": card_idx,
                "strategy_title": strategy_title,
                "strategy_description": strategy_description,
                "refresh_count": _coerce_refresh_count(item.get("refresh_count")),
                "guide_match": guide_match,
                "guide_loaded": guide_loaded,
            }
        )
    return cards


def detect_cw_strategy(session, *, runtime, strategy_list: object) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)

    cw_state = ensure_cw_state(session)
    cards = summarize_strategy_cards(runtime.ocr(), strategy_list, guide_state=cw_state.get("guide"))
    snapshot = {
        "cards": cards,
        "stale": False,
    }
    cw_state["strategy"] = snapshot
    return snapshot


def select_cw_strategy(session, *, card_idx: int, runtime) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)
    _validate_card_idx(card_idx, command_name="cw strategy.select")

    cw_state = ensure_cw_state(session)
    snapshot = _require_strategy_snapshot(session, command_name="cw strategy.select")
    cards = snapshot["cards"]
    selected_card = next(
        (
            card
            for card in cards
            if isinstance(card, Mapping) and int(card.get("card_idx") or 0) == card_idx
        ),
        None,
    )
    if not isinstance(selected_card, Mapping):
        raise TrailError("CW_STRATEGY_SNAPSHOT_REQUIRED", "cw strategy.select requires fresh strategy snapshot")

    runtime.click_point(*_strategy_card_point(card_idx))
    runtime.click_point(*STRATEGY_CONFIRM_POINT)

    selected = dict(selected_card)
    cw_state["strategy"] = {**snapshot, "stale": True}
    _invalidate_strategy_stage(session)
    return selected


def refresh_cw_strategy(session, *, card_idx: int, runtime, strategy_list: object) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)
    _validate_card_idx(card_idx, command_name="cw strategy.refresh")

    runtime.click_point(*_resolve_strategy_refresh_click_point(runtime, card_idx))

    snapshot = detect_cw_strategy(session, runtime=runtime, strategy_list=strategy_list)
    _invalidate_strategy_stage(session)
    return snapshot


def _detect_strategy_page_state(runtime, *, session=None) -> dict[str, object]:
    current = _detect_current_enter_page(runtime, session=session)
    if current.get("page") != "in_game" or current.get("stage") != "invest":
        return current
    if _read_strategy_page_heading(current):
        return current
    try:
        heading = _read_strategy_page_heading_from_ocr(runtime.ocr())
    except Exception:
        heading = ""
    if not heading:
        return current
    return {**current, "heading": heading}


def _require_strategy_page(runtime, *, session) -> dict[str, object]:
    current = _detect_strategy_page_state(runtime, session=session)
    if _is_strategy_page(current):
        return current

    message = (
        "cw strategy action only supports in_game/invest, "
        f"current page: {current.get('page')}, stage: {current.get('stage')}"
    )
    error = TrailError("CW_STRATEGY_PAGE_INVALID", message)
    error.data = {"page": current.get("page"), "stage": current.get("stage")}
    raise error


def _is_strategy_page(current: Mapping[str, object]) -> bool:
    if current.get("page") != "in_game" or current.get("stage") != "invest":
        return False
    heading = _read_strategy_page_heading(current)
    return any(marker in heading for marker in STRATEGY_PAGE_MARKERS)


def _read_strategy_page_heading(current: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ("title", "heading"):
        value = current.get(key)
        if isinstance(value, str) and value.strip():
            parts.append("".join(value.split()))
    return " ".join(parts)


def _read_strategy_page_heading_from_ocr(ocr_pieces: object) -> str:
    pieces = _normalize_strategy_ocr_pieces(ocr_pieces)
    if pieces:
        return _build_strategy_lane_text(pieces)
    if not isinstance(ocr_pieces, (list, tuple)):
        return ""
    parts: list[str] = []
    for piece in ocr_pieces:
        if isinstance(piece, str) and piece.strip():
            parts.append(piece.strip())
    return " ".join(parts)


def _invalidate_strategy_stage(session) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None


def _require_strategy_snapshot(session, *, command_name: str) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    strategy = cw_state.get("strategy") if isinstance(cw_state.get("strategy"), Mapping) else None
    cards = strategy.get("cards") if isinstance(strategy, Mapping) else None
    if isinstance(cards, list) and cards and strategy.get("stale") is not True:
        return {
            "cards": [dict(card) if isinstance(card, Mapping) else card for card in cards],
            "stale": bool(strategy.get("stale")),
        }
    if isinstance(strategy, Mapping) and strategy.get("stale") is True and isinstance(cards, list) and cards:
        raise TrailError("CW_STRATEGY_SNAPSHOT_REQUIRED", f"{command_name} requires fresh strategy snapshot")
    raise TrailError("CW_STRATEGY_SNAPSHOT_REQUIRED", f"{command_name} requires cached strategy snapshot")


def _validate_card_idx(card_idx: int, *, command_name: str) -> None:
    if card_idx in VALID_STRATEGY_CARD_INDEXES:
        return
    raise TrailError("CW_STRATEGY_CARD_IDX_INVALID", f"{command_name} only supports card_idx 1|2|3, got: {card_idx}")


def _strategy_card_point(card_idx: int) -> tuple[int, int]:
    lane_width = STRATEGY_SCREEN_WIDTH // STRATEGY_LANE_COUNT
    return (lane_width * (card_idx - 1) + lane_width // 2, STRATEGY_CARD_CENTER_Y)


def _strategy_refresh_point(card_idx: int) -> tuple[int, int]:
    lane_width = STRATEGY_SCREEN_WIDTH // STRATEGY_LANE_COUNT
    return (lane_width * (card_idx - 1) + lane_width // 2, STRATEGY_REFRESH_Y)


def _resolve_strategy_refresh_click_point(runtime, card_idx: int) -> tuple[int, int]:
    refresh_box = _locate_strategy_refresh_icon(runtime, card_idx)
    if refresh_box is not None:
        return _box_center(refresh_box)
    refresh_label = _locate_strategy_refresh_label_center(runtime, card_idx)
    if refresh_label is not None:
        return refresh_label
    return _strategy_refresh_point(card_idx)


def _locate_strategy_refresh_icon(runtime, card_idx: int):
    lane_width = 1 / STRATEGY_LANE_COUNT
    from_x = lane_width * (card_idx - 1)
    to_x = lane_width * card_idx
    try:
        return runtime.locate(
            _asset("strategy.refresh"),
            from_x=from_x,
            from_y=0.72,
            to_x=to_x,
            to_y=0.92,
        )
    except Exception:
        return None


def _locate_strategy_refresh_label_center(runtime, card_idx: int) -> tuple[int, int] | None:
    pieces = _normalize_strategy_ocr_pieces(runtime.ocr())
    for piece in pieces:
        text = str(piece.get("text") or "")
        if STRATEGY_REFRESH_PATTERN.search(text) is None:
            continue
        if _resolve_strategy_lane_index(float(piece["center_x"])) != card_idx - 1:
            continue
        return round(float(piece["center_x"])), round(float(piece["center_y"]))
    return None


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box_center(box: Any) -> tuple[int, int]:
    if hasattr(box, "center"):
        center = getattr(box, "center")
        if isinstance(center, tuple) and len(center) == 2:
            return int(center[0]), int(center[1])
    if isinstance(box, Mapping):
        left = _coerce_float(box.get("left"))
        top = _coerce_float(box.get("top"))
        width = _coerce_float(box.get("width"))
        height = _coerce_float(box.get("height"))
        if None not in {left, top, width, height}:
            return round(left + width / 2.0), round(top + height / 2.0)
    raise TypeError(f"unsupported box: {box!r}")


def _coerce_refresh_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_strategy_catalog(strategy_list: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(strategy_list, (list, tuple)):
        return normalized
    for item in strategy_list:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("strategy_title") or item.get("title") or item.get("name") or item.get("text") or "").strip()
        if not title:
            continue
        description = str(item.get("strategy_description") or item.get("description") or item.get("desc") or "").strip()
        normalized.append(
            {
                "strategy_id": str(item.get("strategy_id") or item.get("id") or title),
                "title": title,
                "description": description,
                "normalized_title": _normalize_strategy_text(title),
                "normalized_combined": _normalize_strategy_text(" ".join(part for part in (title, description) if part)),
            }
        )
    return normalized


def _normalize_strategy_card_inputs(values: object) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    if not isinstance(values, (list, tuple)):
        return normalized
    if any(_looks_like_strategy_ocr_piece(item) for item in values):
        return _normalize_strategy_ocr_card_inputs(values)
    for order, item in enumerate(values):
        normalized_item = _normalize_strategy_card_input(item, order=order)
        if normalized_item is not None:
            normalized.append(normalized_item)
    return normalized


def _looks_like_strategy_ocr_piece(item: object) -> bool:
    if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
        return True
    if not isinstance(item, Mapping):
        return False
    if not isinstance(item.get("text") or item.get("ocr_text"), str):
        return False
    return any(key in item for key in ("box", "polygon", "points", "center"))


def _normalize_strategy_ocr_card_inputs(values: object) -> list[dict[str, object]]:
    pieces = _normalize_strategy_ocr_pieces(values)
    if not pieces:
        return []
    pieces = _filter_strategy_card_pieces(pieces)
    if not pieces:
        return []

    lanes: list[list[dict[str, float | str]]] = [[] for _ in range(STRATEGY_LANE_COUNT)]
    for piece in pieces:
        lanes[_resolve_strategy_lane_index(float(piece["center_x"]))].append(piece)

    normalized: list[dict[str, object]] = []
    for lane_index, lane_pieces in enumerate(lanes):
        lane_lines = _build_strategy_lane_lines(lane_pieces)
        refresh_count = _extract_strategy_refresh_count(" ".join(lane_lines))
        content_lines = _strategy_content_lines(lane_lines)
        if not content_lines:
            continue
        title = content_lines[0]
        description = " ".join(content_lines[1:]).strip()
        normalized.append(
            {
                "card_idx": lane_index + 1,
                "strategy_title": title,
                "strategy_description": description,
                "refresh_count": refresh_count,
                "_sort_order": lane_index,
                "_sort_left": min(float(piece["left"]) for piece in lane_pieces),
                "_sort_top": min(float(piece["top"]) for piece in lane_pieces),
                "_match_required": True,
            }
        )
    return normalized


def _filter_strategy_card_pieces(pieces: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    return [
        piece
        for piece in pieces
        if STRATEGY_CARD_MIN_CENTER_Y <= float(piece["center_y"]) <= STRATEGY_CARD_MAX_CENTER_Y
    ]


def _normalize_strategy_ocr_pieces(ocr_pieces: object) -> list[dict[str, float | str]]:
    normalized: list[dict[str, float | str]] = []
    if not isinstance(ocr_pieces, (list, tuple)):
        return normalized
    for piece in ocr_pieces:
        normalized_piece = _normalize_strategy_ocr_piece(piece)
        if normalized_piece is not None:
            normalized.append(normalized_piece)
    return normalized


def _normalize_strategy_ocr_piece(piece: object) -> dict[str, float | str] | None:
    if isinstance(piece, Mapping):
        text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        box = _extract_strategy_box_from_mapping(piece)
    elif isinstance(piece, (list, tuple)) and len(piece) >= 2:
        text = str(piece[1] or "").strip()
        box = _extract_strategy_box_from_polygon(piece[0])
    else:
        return None
    if not text or box is None:
        return None
    left, top, width, height = box
    return {
        "text": text,
        "left": left,
        "top": top,
        "center_x": left + width / 2.0,
        "center_y": top + height / 2.0,
    }


def _extract_strategy_box_from_mapping(piece: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    box = piece.get("box")
    if isinstance(box, Mapping):
        values = _extract_strategy_box_values(box)
        if values is not None:
            return values

    polygon = piece.get("polygon") or piece.get("points")
    if polygon is not None:
        values = _extract_strategy_box_from_polygon(polygon)
        if values is not None:
            return values

    center = piece.get("center")
    if isinstance(center, Mapping):
        center_x = _coerce_float(center.get("x"))
        center_y = _coerce_float(center.get("y"))
        if center_x is not None and center_y is not None:
            return center_x, center_y, 0.0, 0.0
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        center_x = _coerce_float(center[0])
        center_y = _coerce_float(center[1])
        if center_x is not None and center_y is not None:
            return center_x, center_y, 0.0, 0.0

    position = _extract_position_from_mapping(piece)
    if position is not None:
        return position[0], position[1], 0.0, 0.0
    return None


def _extract_strategy_box_values(box: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    try:
        left = float(box["left"])
        top = float(box["top"])
        width = float(box["width"])
        height = float(box["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return left, top, width, height


def _extract_strategy_box_from_polygon(polygon: object) -> tuple[float, float, float, float] | None:
    if not isinstance(polygon, (list, tuple)):
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (IndexError, TypeError, ValueError):
        return None
    if not xs or not ys:
        return None
    left = min(xs)
    top = min(ys)
    return left, top, max(xs) - left, max(ys) - top


def _resolve_strategy_lane_index(center_x: float) -> int:
    lane_width = STRATEGY_SCREEN_WIDTH / STRATEGY_LANE_COUNT
    lane_index = int(center_x // lane_width)
    return max(0, min(STRATEGY_LANE_COUNT - 1, lane_index))


def _build_strategy_lane_text(pieces: list[dict[str, float | str]]) -> str:
    return " ".join(_build_strategy_lane_lines(pieces))


def _build_strategy_lane_lines(pieces: list[dict[str, float | str]]) -> list[str]:
    ordered = sorted(pieces, key=lambda item: (float(item["top"]), float(item["left"])))
    rows: list[dict[str, object]] = []
    for piece in ordered:
        center_y = float(piece["center_y"])
        if rows and abs(center_y - float(rows[-1]["center_y"])) <= STRATEGY_ROW_MERGE_Y_DELTA:
            row = rows[-1]
            row_pieces = list(row["pieces"])
            row_pieces.append(piece)
            row["pieces"] = row_pieces
            row["center_y"] = sum(float(item["center_y"]) for item in row_pieces) / len(row_pieces)
            continue
        rows.append({"center_y": center_y, "pieces": [piece]})

    lines: list[str] = []
    for row in rows:
        row_pieces = sorted(
            list(row["pieces"]),
            key=lambda item: (float(item["left"]), float(item["top"])),
        )
        line = " ".join(str(item["text"]) for item in row_pieces if str(item["text"]))
        if line:
            lines.append(line)
    return lines


def _strip_strategy_marker_text(text: str) -> str:
    stripped = text
    for marker in STRATEGY_PAGE_MARKERS:
        stripped = stripped.replace(marker, " ")
    return STRATEGY_WHITESPACE_PATTERN.sub(" ", stripped).strip()


def _strip_strategy_refresh_text(text: str) -> str:
    return STRATEGY_WHITESPACE_PATTERN.sub(" ", STRATEGY_REFRESH_PATTERN.sub(" ", text)).strip()


def _extract_strategy_refresh_count(text: str) -> int:
    match = STRATEGY_REFRESH_PATTERN.search(text)
    if match is None:
        return 0
    return _coerce_refresh_count(match.group(1))


def _strategy_content_lines(lines: list[str]) -> list[str]:
    content: list[str] = []
    for line in lines:
        stripped = _strip_strategy_refresh_text(_strip_strategy_marker_text(line)).strip()
        if not stripped:
            continue
        content.append(stripped)
    return content


def _normalize_strategy_card_input(item: object, *, order: int) -> dict[str, object] | None:
    position = _extract_strategy_position(item) if isinstance(item, Mapping) else None
    if isinstance(item, Mapping):
        title = str(item.get("text") or item.get("strategy_title") or item.get("title") or item.get("name") or "").strip()
        description = str(item.get("strategy_description") or item.get("description") or item.get("desc") or "").strip()
        refresh_count = item.get("refresh_count")
    elif isinstance(item, str):
        title = item.strip()
        description = ""
        refresh_count = 0
    elif isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
        title = item[1].strip()
        description = ""
        refresh_count = 0
    else:
        return None

    if not title and not description:
        return None
    return {
        "strategy_title": title,
        "strategy_description": description,
        "refresh_count": _coerce_refresh_count(refresh_count),
        "_sort_order": order,
        "_sort_left": position[0] if position is not None else None,
        "_sort_top": position[1] if position is not None else None,
    }


def _order_strategy_candidates(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    if not any(candidate.get("_sort_left") is not None for candidate in candidates):
        return list(candidates)
    return sorted(candidates, key=_strategy_sort_key)


def _strategy_sort_key(candidate: Mapping[str, object]) -> tuple[int, float, float, int]:
    order = int(candidate.get("_sort_order") or 0)
    left = candidate.get("_sort_left")
    top = candidate.get("_sort_top")
    if left is None:
        return (1, float(order), float(order), order)
    return (0, float(left), 0.0 if top is None else float(top), order)


def _extract_strategy_position(item: Mapping[str, object]) -> tuple[float, float] | None:
    direct = _extract_position_from_mapping(item)
    if direct is not None:
        return direct

    box = item.get("box")
    if isinstance(box, Mapping):
        nested = _extract_position_from_mapping(box)
        if nested is not None:
            return nested

    center = item.get("center")
    if isinstance(center, Mapping):
        center_x = _coerce_float(center.get("x"))
        center_y = _coerce_float(center.get("y"))
        if center_x is not None:
            return center_x, 0.0 if center_y is None else center_y
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        center_x = _coerce_float(center[0])
        center_y = _coerce_float(center[1])
        if center_x is not None:
            return center_x, 0.0 if center_y is None else center_y

    polygon = item.get("polygon") or item.get("points")
    if isinstance(polygon, (list, tuple)):
        xs: list[float] = []
        ys: list[float] = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            point_x = _coerce_float(point[0])
            point_y = _coerce_float(point[1])
            if point_x is None or point_y is None:
                continue
            xs.append(point_x)
            ys.append(point_y)
        if xs and ys:
            return min(xs), min(ys)
    return None


def _extract_position_from_mapping(item: Mapping[str, object]) -> tuple[float, float] | None:
    left = _coerce_float(item.get("left"))
    if left is None:
        left = _coerce_float(item.get("x"))
    if left is None:
        return None
    top = _coerce_float(item.get("top"))
    if top is None:
        top = _coerce_float(item.get("y"))
    return left, 0.0 if top is None else top


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_strategy_catalog(
    title: str,
    description: str,
    catalog: list[dict[str, str]],
) -> dict[str, str] | None:
    normalized_title = _normalize_strategy_text(title)
    if not normalized_title or not catalog:
        return None

    normalized_combined = _normalize_strategy_text(" ".join(part for part in (title, description) if part))
    ranked: list[tuple[int, int, float, str, dict[str, str]]] = []
    for item in catalog:
        candidate_title = str(item.get("normalized_title") or "")
        candidate_combined = str(item.get("normalized_combined") or candidate_title)
        title_score = SequenceMatcher(a=normalized_title, b=candidate_title).ratio() if candidate_title else 0.0
        combined_score = SequenceMatcher(a=normalized_combined or normalized_title, b=candidate_combined).ratio() if candidate_combined else 0.0
        ranked.append(
            (
                int(normalized_title == candidate_title),
                int(_has_contains_relation(normalized_title, candidate_title)),
                max(title_score, combined_score),
                str(item.get("strategy_id") or ""),
                item,
            )
        )

    ranked.sort(key=lambda entry: (-entry[0], -entry[1], -entry[2], entry[3]))
    matched = ranked[0] if ranked else None
    if matched is None or matched[2] < STRATEGY_MATCH_THRESHOLD:
        return None
    return matched[4]


def _guide_match_for_strategy(title: str, guide_state: object | None) -> tuple[str, int]:
    if not _has_valid_selected_guide(guide_state):
        return "否", 0

    normalized_title = _normalize_strategy_text(title)
    primary = _normalize_guide_strategy_names(guide_state.get("first_fight_augments"))
    secondary = _normalize_guide_strategy_names(guide_state.get("second_fight_augments"))
    if normalized_title and normalized_title in primary:
        return "优选", 1
    if normalized_title and normalized_title in secondary:
        return "次选", 1
    return "否", 1


def _has_valid_selected_guide(guide_state: object | None) -> bool:
    if not isinstance(guide_state, Mapping):
        return False
    share_code = guide_state.get("share_code")
    return isinstance(share_code, str) and SHARE_CODE_PATTERN.fullmatch(share_code) is not None


def _normalize_guide_strategy_names(values: object) -> set[str]:
    normalized: set[str] = set()
    if not isinstance(values, (list, tuple)):
        return normalized
    for item in values:
        if isinstance(item, Mapping):
            name = str(item.get("title") or item.get("name") or item.get("text") or "").strip()
        else:
            name = str(item or "").strip()
        normalized_name = _normalize_strategy_text(name)
        if normalized_name:
            normalized.add(normalized_name)
    return normalized


def _normalize_strategy_text(text: str) -> str:
    lowered = text.lower()
    stripped = STRATEGY_NOISE_PATTERN.sub(" ", lowered)
    collapsed = STRATEGY_WHITESPACE_PATTERN.sub(" ", stripped)
    return collapsed.strip()


def _has_contains_relation(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))
