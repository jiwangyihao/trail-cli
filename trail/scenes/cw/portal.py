from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
import re
from time import sleep
from typing import Any

from trail.core.errors import TrailError
from trail.runtime.model import Box
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.entry import _detect_current_enter_page
from trail.scenes.cw.models import ensure_cw_state


PORTAL_SCREEN_WIDTH = 1920
PORTAL_LANE_COUNT = 3
PORTAL_ROW_MERGE_Y_DELTA = 32
PORTAL_NOISE_PATTERN = re.compile(r"[，。！？：；、“”‘’（）《》〈〉【】『』「」—…·,.;:!?\'\"()\[\]{}<>/\\|@#$%^&*_+=~-]+")
PORTAL_WHITESPACE_PATTERN = re.compile(r"\s+")
PORTAL_CARD_CENTER_Y = 540
PORTAL_CONFIRM_POINT = (1084, 992)
PORTAL_REFRESH_POINT = (1760, 140)
PORTAL_RESTART_HOME_MAX_ESC_PRESSES = 3
PORTAL_RESTART_HOME_INTERVAL = 0.2
PORTAL_SETTLE_MAX_POLLS = 3
PORTAL_SETTLE_INTERVAL = 0.2
PORTAL_RESTART_ABORT_POINT = (750, 750)
PORTAL_RESTART_EXIT_DIALOG_PRIMARY = "放弃并结算"
PORTAL_RESTART_EXIT_DIALOG_SECONDARY = "暂时离开"
PORTAL_RESTART_EXIT_DIALOG_SETTLE_SECONDS = 1.0
PORTAL_RESTART_SETTLEMENT_MAX_POLLS = 6
PORTAL_RESTART_SETTLEMENT_INTERVAL = 1.0


def summarize_portal_cards(
    ocr_pieces: object,
    portal_list: object,
    *,
    collection_matches: object | None = None,
) -> list[dict[str, object]]:
    portals = _normalize_portal_list(portal_list)
    if not portals:
        return [_empty_card_summary(card_idx) for card_idx in range(1, PORTAL_LANE_COUNT + 1)]

    pieces = _normalize_ocr_pieces(ocr_pieces)
    lane_pieces: list[list[dict[str, float | str]]] = [[] for _ in range(PORTAL_LANE_COUNT)]
    for piece in pieces:
        lane_pieces[_resolve_lane_index(float(piece["center_x"]))].append(piece)
    collection_lanes = _resolve_collection_lane_indices(collection_matches)

    summaries: list[dict[str, object]] = []
    for card_idx, lane in enumerate(lane_pieces, start=1):
        summary = _summarize_lane(card_idx, lane, portals)
        if card_idx in collection_lanes:
            summary["new"] = 1
        summaries.append(summary)
    return summaries


def select_cw_portal(session, *, card_idx: int, runtime) -> dict[str, object]:
    _require_portal_page(runtime, session=session, expected_page="invest")
    if card_idx not in {1, 2, 3}:
        raise TrailError("CW_PORTAL_CARD_IDX_INVALID", f"cw portal.select only supports card_idx 1|2|3, got: {card_idx}")

    snapshot = _require_portal_snapshot(session, command_name="cw portal.select")
    cards = snapshot["cards"]
    if len(cards) < card_idx:
        raise TrailError("CW_PORTAL_SNAPSHOT_REQUIRED", "cw portal.select requires cached portal snapshot")

    runtime.click_point(*_card_center(card_idx))
    runtime.click_point(*PORTAL_CONFIRM_POINT)

    selected = dict(cards[card_idx - 1])
    _mark_portal_stale_after_selection(session)
    return selected


def detect_cw_portal(session, *, runtime, portal_list: object) -> dict[str, object]:
    return _snapshot_cw_portal_from_invest_page(session, runtime=runtime, portal_list=portal_list)


def refresh_cw_portal(session, *, runtime, portal_list: object) -> dict[str, object]:
    _require_portal_page(runtime, session=session, expected_page="invest")
    try:
        refresh_box = runtime.locate(_asset("portal.refresh"))
        if refresh_box is None:
            runtime.click_point(*PORTAL_REFRESH_POINT)
        else:
            runtime.click_point(*_box_center(refresh_box))
    except Exception as error:
        raise TrailError("CW_PORTAL_REFRESH_UNAVAILABLE", "cw portal.refresh unavailable") from error

    sleep(PORTAL_SETTLE_INTERVAL)
    current = _wait_for_portal_page(
        runtime,
        session=session,
        expected_page="invest",
        error_code="CW_PORTAL_REFRESH_UNAVAILABLE",
        error_message="cw portal.refresh did not settle back to invest",
    )

    return _snapshot_cw_portal_from_invest_page(session, runtime=runtime, portal_list=portal_list, current_page=current)


def _snapshot_cw_portal_from_invest_page(
    session,
    *,
    runtime,
    portal_list: object,
    current_page: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if current_page is None:
        _require_portal_page(runtime, session=session, expected_page="invest")
    elif current_page.get("page") != "invest":
        _raise_portal_page_invalid(expected_page="invest", current=current_page)
    cards = summarize_portal_cards(runtime.ocr(), portal_list, collection_matches=detect_portal_collection_matches(runtime))
    snapshot = {
        "cards": cards,
        **_portal_entry_truth(session),
        "stale": False,
    }
    cw_state = ensure_cw_state(session)
    cw_state["portal"] = snapshot
    cw_state["entry"] = {
        "page": "invest",
        "mode": snapshot.get("mode"),
        "difficulty": snapshot.get("difficulty"),
        "battle_mode": snapshot.get("battle_mode"),
    }
    return snapshot


def restart_cw_portal_to_settlement_entry(session, *, runtime) -> None:
    current = _detect_current_enter_page(runtime, session=session)
    if current.get("page") != "in_game":
        raise TrailError(
            "CW_PORTAL_RESTART_SETTLEMENT_INVALID",
            f"cw portal restart helper only supports in_game, current page: {current.get('page')}",
        )

    for _ in range(PORTAL_RESTART_HOME_MAX_ESC_PRESSES):
        runtime.press_key("esc", presses=1, interval=PORTAL_RESTART_HOME_INTERVAL)
        sleep(PORTAL_RESTART_EXIT_DIALOG_SETTLE_SECONDS)
        if _detect_portal_restart_exit_dialog(runtime):
            runtime.click_point(*PORTAL_RESTART_ABORT_POINT)
            sleep(PORTAL_RESTART_SETTLEMENT_INTERVAL)
            for attempt in range(PORTAL_RESTART_SETTLEMENT_MAX_POLLS):
                current = _detect_current_enter_page(runtime, session=session)
                if str(current.get("page", "")).startswith("settlement."):
                    return
                if attempt < PORTAL_RESTART_SETTLEMENT_MAX_POLLS - 1:
                    sleep(PORTAL_RESTART_SETTLEMENT_INTERVAL)
            raise TrailError(
                "CW_PORTAL_RESTART_SETTLEMENT_TIMEOUT",
                "cw portal restart helper did not reach settlement chain after abandon",
            )

    raise TrailError("CW_PORTAL_RESTART_EXIT_DIALOG_TIMEOUT", "cw portal restart helper failed to reach exit dialog")


def wait_cw_portal_in_game(session, *, runtime) -> None:
    _wait_for_portal_page(
        runtime,
        session=session,
        expected_page="in_game",
        error_code="CW_PORTAL_RESTART_ENTER_GAME_TIMEOUT",
        error_message="cw portal.restart did not reach in_game after select",
    )


def _empty_card_summary(card_idx: int) -> dict[str, object]:
    return {
        "card_idx": card_idx,
        "portal_title": "",
        "portal_description": "",
        "score": 0.0,
    }


def _require_portal_page(runtime, *, session, expected_page: str) -> None:
    current = _detect_current_enter_page(runtime, session=session)
    if current.get("page") == "world":
        entry = ensure_cw_state(session).get("entry") if isinstance(ensure_cw_state(session).get("entry"), Mapping) else {}
        cached_page = entry.get("page") if isinstance(entry.get("page"), str) else None
        if cached_page and cached_page != expected_page:
            current = {"page": cached_page}
            stage = ensure_cw_state(session).get("stage") if isinstance(ensure_cw_state(session).get("stage"), Mapping) else {}
            if cached_page == "in_game" and isinstance(stage.get("value"), str) and stage.get("value"):
                current["stage"] = stage.get("value")
    if current.get("page") == expected_page:
        return
    _raise_portal_page_invalid(expected_page=expected_page, current=current)


def _raise_portal_page_invalid(*, expected_page: str, current: Mapping[str, object]) -> None:
    message = f"cw portal action only supports {expected_page}, current page: {current.get('page')}"
    if current.get("stage") is not None:
        message += f", stage: {current.get('stage')}"
    error = TrailError("CW_PORTAL_PAGE_INVALID", message)
    error.data = {"page": current.get("page")}
    if current.get("stage") is not None:
        error.data["stage"] = current.get("stage")
    raise error


def _require_portal_snapshot(session, *, command_name: str) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    portal = cw_state.get("portal") if isinstance(cw_state.get("portal"), Mapping) else None
    cards = portal.get("cards") if isinstance(portal, Mapping) else None
    if isinstance(cards, list) and cards and portal.get("stale") is not True:
        return dict(portal)
    if isinstance(portal, Mapping) and portal.get("stale") is True and isinstance(cards, list) and cards:
        raise TrailError("CW_PORTAL_SNAPSHOT_REQUIRED", f"{command_name} requires fresh portal snapshot")
    raise TrailError("CW_PORTAL_SNAPSHOT_REQUIRED", f"{command_name} requires cached portal snapshot")


def _portal_entry_truth(session) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    portal = cw_state.get("portal") if isinstance(cw_state.get("portal"), Mapping) else {}
    entry = cw_state.get("entry") if isinstance(cw_state.get("entry"), Mapping) else {}
    return {
        "mode": entry.get("mode") if entry.get("mode") is not None else portal.get("mode"),
        "difficulty": entry.get("difficulty") if entry.get("difficulty") is not None else portal.get("difficulty"),
        "battle_mode": entry.get("battle_mode") if entry.get("battle_mode") is not None else portal.get("battle_mode"),
    }


def _mark_portal_stale_after_selection(session) -> None:
    cw_state = ensure_cw_state(session)
    portal = cw_state.get("portal") if isinstance(cw_state.get("portal"), Mapping) else {}
    cw_state["portal"] = {**portal, "stale": True}
    truth = _portal_entry_truth(session)
    cw_state["entry"] = {
        "page": "in_game",
        "mode": truth.get("mode"),
        "difficulty": truth.get("difficulty"),
        "battle_mode": truth.get("battle_mode"),
    }
    cw_state["stage"] = {"stale": True}


def _card_center(card_idx: int) -> tuple[int, int]:
    lane_width = PORTAL_SCREEN_WIDTH // PORTAL_LANE_COUNT
    return (lane_width * (card_idx - 1) + lane_width // 2, PORTAL_CARD_CENTER_Y)


def _box_center(box: Box) -> tuple[int, int]:
    return (int(box.left + box.width / 2), int(box.top + box.height / 2))


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def detect_portal_collection_matches(runtime) -> list[Box]:
    match = runtime.locate(_asset("portal.collection"))
    if match is None:
        return []
    return [match]


def _resolve_collection_lane_indices(collection_matches: object) -> set[int]:
    lanes: set[int] = set()
    matches = collection_matches if isinstance(collection_matches, list) else [collection_matches]
    for match in matches:
        center_x = _extract_match_center_x(match)
        if center_x is None:
            continue
        lanes.add(_resolve_lane_index(center_x) + 1)
    return lanes


def _extract_match_center_x(match: object) -> float | None:
    if isinstance(match, Box):
        return float(match.left) + float(match.width) / 2.0
    if isinstance(match, Mapping):
        try:
            left = float(match["left"])
            width = float(match["width"])
        except (KeyError, TypeError, ValueError):
            return None
        return left + width / 2.0
    return None


def _wait_for_portal_page(
    runtime,
    *,
    session,
    expected_page: str,
    error_code: str,
    error_message: str,
    max_polls: int = PORTAL_SETTLE_MAX_POLLS,
    interval: float = PORTAL_SETTLE_INTERVAL,
) -> dict[str, str]:
    for attempt in range(max_polls):
        current = _detect_current_enter_page(runtime, session=session)
        if current.get("page") == expected_page:
            return current
        if attempt < max_polls - 1:
            sleep(interval)
    raise TrailError(error_code, error_message)


def _detect_portal_restart_exit_dialog(runtime) -> bool:
    try:
        pieces = runtime.ocr()
    except Exception:
        return False
    texts: list[str] = []
    for piece in pieces:
        if isinstance(piece, Mapping):
            texts.append(str(piece.get("text") or ""))
        elif isinstance(piece, (list, tuple)) and len(piece) >= 2:
            texts.append(str(piece[1] or ""))
    joined = " ".join(_normalize_text(text) for text in texts if text)
    return PORTAL_RESTART_EXIT_DIALOG_PRIMARY in joined and PORTAL_RESTART_EXIT_DIALOG_SECONDARY in joined


def _normalize_portal_list(portal_list: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(portal_list, list):
        return normalized
    for item in portal_list:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "portal_id": str(item.get("portal_id") or item.get("id") or ""),
                "title": title,
                "description": str(item.get("description") or item.get("desc") or "").strip(),
            }
        )
    return normalized


def _normalize_ocr_pieces(ocr_pieces: object) -> list[dict[str, float | str]]:
    normalized: list[dict[str, float | str]] = []
    if not isinstance(ocr_pieces, (list, tuple)):
        return normalized
    for piece in ocr_pieces:
        normalized_piece = _normalize_ocr_piece(piece)
        if normalized_piece is not None:
            normalized.append(normalized_piece)
    return normalized


def _normalize_ocr_piece(piece: object) -> dict[str, float | str] | None:
    if isinstance(piece, Mapping):
        text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        box = _extract_box_from_mapping(piece)
    elif isinstance(piece, (list, tuple)) and len(piece) >= 2:
        text = str(piece[1] or "").strip()
        box = _extract_box_from_polygon(piece[0])
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


def _extract_box_from_mapping(piece: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    box = piece.get("box")
    if isinstance(box, Mapping):
        return _extract_box_values(box)

    polygon = piece.get("polygon") or piece.get("points")
    if polygon is not None:
        return _extract_box_from_polygon(polygon)

    center = piece.get("center")
    if isinstance(center, Mapping):
        try:
            center_x = float(center["x"])
            center_y = float(center["y"])
        except (KeyError, TypeError, ValueError):
            return None
        return center_x, center_y, 0.0, 0.0

    if isinstance(center, (list, tuple)) and len(center) == 2:
        try:
            center_x = float(center[0])
            center_y = float(center[1])
        except (TypeError, ValueError):
            return None
        return center_x, center_y, 0.0, 0.0

    return _extract_box_values(piece)


def _extract_box_values(box: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    try:
        left = float(box["left"])
        top = float(box["top"])
        width = float(box["width"])
        height = float(box["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return left, top, width, height


def _extract_box_from_polygon(polygon: object) -> tuple[float, float, float, float] | None:
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


def _resolve_lane_index(center_x: float) -> int:
    lane_width = PORTAL_SCREEN_WIDTH / PORTAL_LANE_COUNT
    lane_index = int(center_x // lane_width)
    return max(0, min(PORTAL_LANE_COUNT - 1, lane_index))


def _summarize_lane(card_idx: int, pieces: list[dict[str, float | str]], portals: list[dict[str, str]]) -> dict[str, object]:
    card_text = _build_lane_text(pieces)
    if not _normalize_text(card_text):
        return _empty_card_summary(card_idx)
    best_portal, score = _match_best_portal(card_text, portals)
    return {
        "card_idx": card_idx,
        "portal_title": best_portal["title"],
        "portal_description": best_portal["description"],
        "score": score,
    }


def _build_lane_text(pieces: list[dict[str, float | str]]) -> str:
    ordered = sorted(pieces, key=lambda item: (float(item["top"]), float(item["left"])))
    rows: list[dict[str, object]] = []
    for piece in ordered:
        center_y = float(piece["center_y"])
        if rows and abs(center_y - float(rows[-1]["center_y"])) <= PORTAL_ROW_MERGE_Y_DELTA:
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
    return " ".join(lines)


def _match_best_portal(card_text: str, portals: list[dict[str, str]]) -> tuple[dict[str, str], float]:
    normalized_card_text = _normalize_text(card_text)
    ranked: list[tuple[float, float, str, dict[str, str]]] = []
    for portal in portals:
        normalized_title = _normalize_text(portal["title"])
        normalized_combined = _normalize_text(" ".join(part for part in [portal["title"], portal["description"]] if part))
        title_score = SequenceMatcher(a=normalized_card_text, b=normalized_title).ratio() if normalized_title else 0.0
        combined_score = SequenceMatcher(a=normalized_card_text, b=normalized_combined).ratio() if normalized_combined else 0.0
        ranked.append((max(title_score, combined_score), title_score, portal["portal_id"], portal))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    best_score, _, _, best_portal = ranked[0]
    return best_portal, round(best_score, 4)


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    stripped = PORTAL_NOISE_PATTERN.sub(" ", lowered)
    collapsed = PORTAL_WHITESPACE_PATTERN.sub(" ", stripped)
    return collapsed.strip()


__all__ = [
    "detect_portal_collection_matches",
    "refresh_cw_portal",
    "restart_cw_portal_to_settlement_entry",
    "select_cw_portal",
    "summarize_portal_cards",
    "wait_cw_portal_in_game",
]
