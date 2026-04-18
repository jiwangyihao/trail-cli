from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
import re
from typing import Any


PORTAL_SCREEN_WIDTH = 1920
PORTAL_LANE_COUNT = 3
PORTAL_ROW_MERGE_Y_DELTA = 32
PORTAL_NOISE_PATTERN = re.compile(r"[，。！？：；、“”‘’（）《》〈〉【】『』「」—…·,.;:!?\'\"()\[\]{}<>/\\|@#$%^&*_+=~-]+")
PORTAL_WHITESPACE_PATTERN = re.compile(r"\s+")


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


def _empty_card_summary(card_idx: int) -> dict[str, object]:
    return {
        "card_idx": card_idx,
        "portal_title": "",
        "portal_description": "",
        "score": 0.0,
    }


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


__all__ = ["summarize_portal_cards"]
