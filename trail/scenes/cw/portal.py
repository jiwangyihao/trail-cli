from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
import re
from typing import Any

from trail.core.errors import TrailError
from trail.scenes.cw.entry import _detect_current_enter_page
from trail.scenes.cw.models import ensure_cw_state


PORTAL_SCREEN_WIDTH = 1920
PORTAL_LANE_COUNT = 3
PORTAL_ROW_MERGE_Y_DELTA = 32
PORTAL_NOISE_PATTERN = re.compile(r"[，。！？：；、“”‘’（）《》〈〉【】『』「」—…·,.;:!?\'\"()\[\]{}<>/\\|@#$%^&*_+=~-]+")
PORTAL_WHITESPACE_PATTERN = re.compile(r"\s+")
PORTAL_CARD_CENTER_Y = 540
PORTAL_CONFIRM_POINT = (960, 920)
PORTAL_REFRESH_POINT = (1760, 140)
PORTAL_RESTART_HOME_MAX_ESC_PRESSES = 3
PORTAL_RESTART_HOME_INTERVAL = 0.2


def summarize_portal_cards(ocr_pieces: object, portal_list: object) -> list[dict[str, object]]:
    portals = _normalize_portal_list(portal_list)
    if not portals:
        return [_empty_card_summary(card_idx) for card_idx in range(1, PORTAL_LANE_COUNT + 1)]

    pieces = _normalize_ocr_pieces(ocr_pieces)
    lane_pieces: list[list[dict[str, float | str]]] = [[] for _ in range(PORTAL_LANE_COUNT)]
    for piece in pieces:
        lane_pieces[_resolve_lane_index(float(piece["center_x"]))].append(piece)

    summaries: list[dict[str, object]] = []
    for card_idx, lane in enumerate(lane_pieces, start=1):
        summaries.append(_summarize_lane(card_idx, lane, portals))
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


def refresh_cw_portal(session, *, runtime, portal_list: object) -> dict[str, object]:
    _require_portal_page(runtime, session=session, expected_page="invest")
    try:
        runtime.click_point(*PORTAL_REFRESH_POINT)
    except Exception as error:
        raise TrailError("CW_PORTAL_REFRESH_UNAVAILABLE", "cw portal.refresh unavailable") from error

    cards = summarize_portal_cards(runtime.ocr(), portal_list)
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


def restart_cw_portal_to_homepage(session, *, runtime) -> None:
    current = _detect_current_enter_page(runtime, session=session)
    if current.get("page") != "in_game":
        raise TrailError(
            "CW_PORTAL_RESTART_HOME_INVALID",
            f"cw portal restart helper only supports in_game, current page: {current.get('page')}",
        )

    for _ in range(PORTAL_RESTART_HOME_MAX_ESC_PRESSES):
        runtime.press_key("esc", presses=1, interval=PORTAL_RESTART_HOME_INTERVAL)
        current = _detect_current_enter_page(runtime, session=session)
        if current.get("page") == "home":
            truth = _portal_entry_truth(session)
            ensure_cw_state(session)["entry"] = {
                "page": "home",
                "mode": truth.get("mode"),
                "difficulty": truth.get("difficulty"),
                "battle_mode": truth.get("battle_mode"),
            }
            return

    raise TrailError("CW_PORTAL_RESTART_HOME_FAILED", "cw portal restart helper failed to return home")


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
        if isinstance(entry.get("page"), str) and entry.get("page"):
            current = {"page": entry.get("page")}
            stage = ensure_cw_state(session).get("stage") if isinstance(ensure_cw_state(session).get("stage"), Mapping) else {}
            if current["page"] == "in_game" and isinstance(stage.get("value"), str) and stage.get("value"):
                current["stage"] = stage.get("value")
    if current.get("page") == expected_page:
        return
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
    if isinstance(cards, list) and cards:
        return dict(portal)
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


__all__ = ["refresh_cw_portal", "restart_cw_portal_to_homepage", "select_cw_portal", "summarize_portal_cards"]
