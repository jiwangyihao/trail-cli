from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import re
import traceback
from typing import TYPE_CHECKING
from time import sleep
from typing import Any

from PIL import Image, ImageEnhance

from trail.core.errors import TrailError
from trail.runtime.ocr_config import OcrRequestConfig
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

CW_WIDTH = 1920
CW_HEIGHT = 1080


def _point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
    return int(CW_WIDTH * x_ratio), int(CW_HEIGHT * y_ratio)


def _pixel_point(x: int, y: int) -> tuple[int, int]:
    return x, y


def _region(from_x: float, from_y: float, to_x: float, to_y: float) -> dict[str, int]:
    return {
        "from_x": int(CW_WIDTH * from_x),
        "from_y": int(CW_HEIGHT * from_y),
        "to_x": int(CW_WIDTH * to_x),
        "to_y": int(CW_HEIGHT * to_y),
    }


def _pixel_region(from_x: int, from_y: int, to_x: int, to_y: int) -> dict[str, int]:
    return {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}


SHOP_OPEN_POINT = _pixel_point(1628, 992)
SHOP_CLOSE_POINT = _point(0.5, 0.55)
SHOP_SCAN_RESET_POINT = SHOP_CLOSE_POINT
SHOP_SLOT_POINTS = {
    1: _point(0.25, 0.18),
    2: _point(0.40, 0.18),
    3: _point(0.55, 0.18),
    4: _point(0.68, 0.18),
    5: _point(0.80, 0.18),
}
SHOP_SCAN_REGION = _region(0.19, 0.26, 0.88, 0.31)
SHOP_TEAM_SIZE_REGION = _pixel_region(835, 188, 1095, 281)
SHOP_COINS_REGION = _pixel_region(1615, 902, 1698, 956)
SHOP_LEVEL_REGION = _pixel_region(220, 880, 360, 950)
SHOP_EXP_REGION = _pixel_region(256, 943, 325, 973)
SHOP_SCAN_RESET_SETTLE_SECONDS = 1.0
SHOP_SCAN_OPEN_SETTLE_SECONDS = 1.5

ShopScanner = Callable[[], dict[str, Any]]
ShopSnapshotReader = Callable[[], dict[str, Any]]
ShopSnapshotSource = ShopScanner | ShopSnapshotReader
ShopBuyer = Callable[..., object]
ShopAction = Callable[[], object]

SHOP_SUMMARY_CONSTRAINT_KEYS = ("min_coins", "min_level", "mid_level")
SHOP_DEBUG_CAPTURE_ENV = "TRAIL_DEBUG_CW_SHOP_CAPTURE"
if TYPE_CHECKING:
    from trail.runtime.operator import RuntimeOperator


def _read_ocr_text(item: Any) -> str:
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


def _debug_capture_path(runtime: "RuntimeOperator", *, name: str) -> Path | None:
    if os.getenv(SHOP_DEBUG_CAPTURE_ENV) not in {"1", "true", "TRUE", "yes", "YES"}:
        return None
    window = getattr(runtime, "window", None)
    workspace = getattr(window, "workspace", None)
    if workspace is None:
        return None
    return Path(workspace) / f"debug-shop-{name}.png"


def _maybe_dump_shop_capture(runtime: "RuntimeOperator", *, name: str, capture: dict[str, int]) -> Path | None:
    path = _debug_capture_path(runtime, name=name)
    if path is None:
        return None
    try:
        payload = runtime.screenshot(**capture)
        path.write_bytes(payload)
        return path
    except Exception:
        fallback = path.with_suffix(".error.txt")
        fallback.write_text(traceback.format_exc(), encoding="utf-8")
        return fallback


def _parse_first_int(items: list[Any], *, default: int | None) -> int | None:
    for item in items:
        match = re.search(r"\d+", _read_ocr_text(item))
        if match is not None:
            return int(match.group())
    return default


def _parse_last_int(items: list[Any], *, default: int | None) -> int | None:
    for item in reversed(items):
        match = re.search(r"\d+", _read_ocr_text(item))
        if match is not None:
            return int(match.group())
    return default


def _parse_shop_items(raw_items: list[Any] | None) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    reserve_full = False
    for item in raw_items or []:
        text = _read_ocr_text(item).strip()
        if not text:
            continue
        if "备" in text:
            reserve_full = True
            continue
        if text.isdecimal():
            if items and items[-1]["price"] is None:
                items[-1]["price"] = int(text)
            continue
        items.append({"name": text, "price": None})
    return items, reserve_full


def _parse_shop_level_value(text: str) -> int | None:
    if not re.fullmatch(r"[1-9]\d*", text):
        return None
    return int(text)


def _parse_shop_level(items: list[Any], *, default: int | None) -> int | None:
    normalized_texts: list[str] = []
    for item in items:
        text = _read_ocr_text(item).strip()
        if text:
            normalized_texts.append(re.sub(r"\s+", "", text))

    for normalized in normalized_texts:
        match = re.fullmatch(r"lv\.?([0-9]+)", normalized, re.IGNORECASE)
        if match is not None:
            value = _parse_shop_level_value(match.group(1))
            if value is not None:
                return value

    for index, normalized in enumerate(normalized_texts[:-1]):
        next_text = normalized_texts[index + 1]
        if re.fullmatch(r"lv\.?", normalized, re.IGNORECASE):
            value = _parse_shop_level_value(next_text)
            if value is not None:
                return value
            continue
        if re.fullmatch(r"lv\.?", next_text, re.IGNORECASE):
            value = _parse_shop_level_value(normalized)
            if value is not None:
                return value

    if any("/" in text for text in normalized_texts):
        return default

    digit_values = [_parse_shop_level_value(text) for text in normalized_texts]
    digit_values = [value for value in digit_values if value is not None]
    if len(digit_values) == 1:
        return digit_values[0]

    return default


def _parse_shop_ratio(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_shop_team_size_candidate(text: str) -> str | None:
    ratio = _parse_shop_ratio(text)
    if ratio is None:
        return None
    current_size, max_size = ratio
    if current_size < 2 or max_size < current_size:
        return None
    return f"{current_size}/{max_size}"


def _parse_shop_exp_candidate(text: str) -> str | None:
    ratio = _parse_shop_ratio(text)
    if ratio is None:
        return None
    current_exp, max_exp = ratio
    if current_exp < 0 or max_exp <= 0 or max_exp < current_exp:
        return None
    return f"{current_exp}/{max_exp}"


def _parse_shop_exp(items: list[Any], *, default: str | None) -> str | None:
    normalized_texts: list[str] = []
    for item in items:
        text = _read_ocr_text(item).strip()
        if text:
            normalized_texts.append(re.sub(r"\s+", "", text))

    for normalized in normalized_texts:
        value = _parse_shop_exp_candidate(normalized)
        if value is not None:
            return value

    return default


def _ocr_shop_capture_items(runtime, *, capture: dict[str, int], contrast: float | None = None) -> list[Any]:
    screenshot = getattr(runtime, "screenshot", None)
    ocr_image = getattr(runtime, "ocr_image", None)
    if callable(screenshot) and callable(ocr_image):
        try:
            payload = screenshot(**capture)
            image = Image.open(BytesIO(payload)).convert("RGB")
            if contrast is not None:
                image = ImageEnhance.Contrast(image.convert("L")).enhance(contrast)
            try:
                return ocr_image(image, ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"))
            except TypeError:
                return ocr_image(image)
        except Exception:
            pass
    return runtime.ocr(capture=capture) or []


def _ocr_shop_exp_items(runtime) -> list[Any]:
    try:
        full_ocr_items = runtime.ocr(capture=None) or []
    except Exception:
        return _ocr_shop_capture_items(runtime, capture=SHOP_EXP_REGION)
    filtered_items = _filter_ocr_items_to_region(full_ocr_items, SHOP_EXP_REGION)
    if filtered_items:
        return filtered_items
    return _ocr_shop_capture_items(runtime, capture=SHOP_EXP_REGION)


def _read_ocr_box(item: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    raw_box = item[0]
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
        return None

    points: list[tuple[float, float]] = []
    for raw_point in raw_box:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            return None
        raw_x, raw_y = raw_point[0], raw_point[1]
        if not isinstance(raw_x, int | float) or not isinstance(raw_y, int | float):
            return None
        points.append((float(raw_x), float(raw_y)))

    if len(points) < 4:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _ocr_box_center_in_region(box: tuple[float, float, float, float], region: dict[str, int]) -> bool:
    left, top, right, bottom = box
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    return region["from_x"] <= center_x <= region["to_x"] and region["from_y"] <= center_y <= region["to_y"]


def _filter_ocr_items_to_region(items: list[Any], region: dict[str, int]) -> list[Any]:
    filtered: list[Any] = []
    for item in items:
        box = _read_ocr_box(item)
        if box is None:
            continue
        if _ocr_box_center_in_region(box, region):
            filtered.append(item)
    return filtered


def _team_size_tokens_are_contiguous(boxes: list[tuple[float, float, float, float]]) -> bool:
    if len(boxes) < 2:
        return False

    heights = [bottom - top for _, top, _, bottom in boxes]
    average_height = sum(heights) / len(heights)
    max_center_y_delta = max(4.0, average_height * 0.35)
    centers_y = [((top + bottom) / 2.0) for _, top, _, bottom in boxes]
    if max(centers_y) - min(centers_y) > max_center_y_delta:
        return False

    max_gap = max(6.0, average_height * 0.5)
    previous_left, _, previous_right, _ = boxes[0]
    previous_center_x = (previous_left + previous_right) / 2.0
    for left, _, right, _ in boxes[1:]:
        center_x = (left + right) / 2.0
        if center_x <= previous_center_x:
            return False
        if left - previous_right > max_gap:
            return False
        previous_right = right
        previous_center_x = center_x

    return True


def _parse_shop_team_size(items: list[Any], *, default: str | None) -> str | None:
    tokens: list[tuple[str, tuple[float, float, float, float] | None]] = []
    for item in items:
        text = _read_ocr_text(item).strip()
        if text:
            tokens.append((re.sub(r"\s+", "", text), _read_ocr_box(item)))

    for text, _ in tokens:
        value = _parse_shop_team_size_candidate(text)
        if value is not None:
            return value

    for window_size in (2, 3):
        for index in range(len(tokens) - window_size + 1):
            window = tokens[index : index + window_size]
            boxes = [box for _, box in window]
            if not all(box is not None for box in boxes):
                continue
            candidate = "".join(text for text, _ in window)
            value = _parse_shop_team_size_candidate(candidate)
            if value is None:
                continue
            if _team_size_tokens_are_contiguous(boxes):
                return value

    return default


def _guide_state(cw_state: dict) -> dict | None:
    guide = cw_state.get("guide")
    return guide if isinstance(guide, dict) else None


def _remaining_purchases(cw_state: dict) -> dict[str, Any]:
    guide = _guide_state(cw_state)
    if guide is None:
        return {}
    remaining = guide.get("remaining_purchases")
    return remaining if isinstance(remaining, dict) else {}


def _stable_constraints_summary(cw_state: dict) -> dict[str, Any]:
    constraints = cw_state.get("constraints")
    if not isinstance(constraints, dict):
        return {}
    return {key: deepcopy(constraints.get(key)) for key in SHOP_SUMMARY_CONSTRAINT_KEYS if key in constraints}


def _guide_summary(cw_state: dict) -> dict[str, Any] | None:
    if _guide_state(cw_state) is None:
        return None
    return {
        "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
        "constraints": _stable_constraints_summary(cw_state),
    }


def _shop_state(cw_state: dict) -> dict[str, Any]:
    shop_state = cw_state.get("shop")
    return shop_state if isinstance(shop_state, dict) else {}


def _preserved_shop_flags(cw_state: dict) -> dict[str, Any]:
    shop_state = _shop_state(cw_state)
    if "opened" not in shop_state:
        return {}
    return {"opened": shop_state["opened"]}


def _build_shop_snapshot(
    cw_state: dict,
    *,
    items: list[Any],
    coins: int | None,
    level: int | None,
    exp: str | None,
    reserve_full: bool,
    team_size: str | None,
) -> dict[str, Any]:
    preserved_team_size = _shop_state(cw_state).get("team_size") if team_size is None else team_size
    return {
        **_preserved_shop_flags(cw_state),
        "items": deepcopy(items),
        "coins": coins,
        "level": level,
        "exp": exp,
        "reserve_full": reserve_full,
        "team_size": preserved_team_size,
        "guide_summary": {
            "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
            "constraints": _stable_constraints_summary(cw_state),
        },
        "stale": False,
    }


def _scan_shop_snapshot(cw_state: dict, *, scanner: ShopSnapshotSource) -> dict[str, Any]:
    scanned = scanner()
    if isinstance(scanned, dict):
        snapshot = _build_shop_snapshot(
            cw_state,
            items=_normalized_shop_items(scanned.get("items")),
            coins=scanned.get("coins"),
            level=scanned.get("level"),
            exp=scanned.get("exp"),
            reserve_full=bool(scanned.get("reserve_full", False)),
            team_size=scanned.get("team_size"),
        )
        if "opened" in scanned:
            snapshot["opened"] = bool(scanned["opened"])
        if "stale" in scanned:
            snapshot["stale"] = bool(scanned["stale"])
        return snapshot

    items, coins, level, reserve_full, team_size = scanned
    return _build_shop_snapshot(
        cw_state,
        items=items,
        coins=coins,
        level=level,
        exp=None,
        reserve_full=reserve_full,
        team_size=team_size,
    )


def _normalized_shop_items(items: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if isinstance(item, dict):
            normalized.append({"name": item.get("name"), "price": item.get("price")})
            continue
        if isinstance(item, str):
            normalized.append({"name": item, "price": None})
    return normalized


def _require_fresh_shop_item(cw_state: dict, *, slot: int, expect: str) -> dict[str, Any]:
    shop_state = _shop_state(cw_state)
    if shop_state.get("stale", True):
        raise TrailError("SHOP_STALE", "商店快照已失效，请先执行 trail cw shop scan")

    items = _normalized_shop_items(shop_state.get("items"))
    index = slot - 1
    if index < 0 or index >= len(items):
        raise TrailError("SHOP_SLOT_MISMATCH", f"shop slot {slot} expected {expect}, got: empty")

    current = items[index]
    if current.get("name") != expect:
        actual = current.get("name") or "empty"
        raise TrailError("SHOP_SLOT_MISMATCH", f"shop slot {slot} expected {expect}, got: {actual}")
    return current


def _purchase_confirmed(*, before_items: list[dict[str, Any]], after_items: list[dict[str, Any]], slot: int, expect: str) -> bool:
    index = slot - 1
    before_item = before_items[index] if 0 <= index < len(before_items) else None
    after_item = after_items[index] if 0 <= index < len(after_items) else None
    after_name = after_item.get("name") if isinstance(after_item, dict) else None
    return before_item != after_item and after_name != expect


def _decrement_remaining_purchase(cw_state: dict, *, expect: str) -> None:
    guide = _guide_state(cw_state)
    if guide is None:
        return
    remaining = guide.get("remaining_purchases")
    if not isinstance(remaining, dict):
        remaining = {}
        guide["remaining_purchases"] = remaining
    remaining[expect] = max(0, remaining.get(expect, 0) - 1)


def build_cw_shop_opener(runtime) -> ShopAction:
    return lambda: runtime.click_point(*SHOP_OPEN_POINT)


def _read_shop_page_snapshot(runtime, *, read_team_size: bool) -> dict[str, Any]:
    _maybe_dump_shop_capture(runtime, name="scan", capture=SHOP_SCAN_REGION)
    _maybe_dump_shop_capture(runtime, name="coins", capture=SHOP_COINS_REGION)
    _maybe_dump_shop_capture(runtime, name="level", capture=SHOP_LEVEL_REGION)
    _maybe_dump_shop_capture(runtime, name="exp", capture=SHOP_EXP_REGION)
    items, reserve_full = _parse_shop_items(runtime.ocr(capture=SHOP_SCAN_REGION))
    coins = _parse_first_int(runtime.ocr(capture=SHOP_COINS_REGION) or [], default=0)
    level = _parse_shop_level(_ocr_shop_capture_items(runtime, capture=SHOP_LEVEL_REGION, contrast=1.8), default=None)
    exp = _parse_shop_exp(_ocr_shop_exp_items(runtime), default=None)
    team_size = None
    if read_team_size:
        _maybe_dump_shop_capture(runtime, name="team-size", capture=SHOP_TEAM_SIZE_REGION)
        team_size = _parse_shop_team_size(runtime.ocr(capture=SHOP_TEAM_SIZE_REGION) or [], default=None)
    snapshot = {
        "items": items,
        "coins": coins,
        "level": level,
        "exp": exp,
        "reserve_full": reserve_full,
    }
    if read_team_size:
        snapshot["team_size"] = team_size
    return snapshot


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> dict[str, Any]:
        return _read_shop_page_snapshot(runtime, read_team_size=False)

    return scanner


def build_cw_shop_scan_snapshot_reader(runtime) -> ShopSnapshotReader:
    def reader() -> dict[str, Any]:
        runtime.click_point(*SHOP_SCAN_RESET_POINT)
        sleep(SHOP_SCAN_RESET_SETTLE_SECONDS)
        team_size = None
        try:
            _maybe_dump_shop_capture(runtime, name="team-size", capture=SHOP_TEAM_SIZE_REGION)
            team_size_items = runtime.ocr(capture=SHOP_TEAM_SIZE_REGION) or []
        except Exception:
            team_size_items = None
        if team_size_items is not None:
            team_size = _parse_shop_team_size(team_size_items, default=None)
        runtime.click_point(*SHOP_OPEN_POINT)
        sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
        snapshot = _read_shop_page_snapshot(runtime, read_team_size=False)
        return {"opened": True, "stale": False, **snapshot, "team_size": team_size}

    return reader


def build_cw_shop_buyer(runtime) -> ShopBuyer:
    def buyer(*, slot: int, expect: str) -> None:
        del expect
        target = SHOP_SLOT_POINTS.get(slot)
        if target is None:
            raise TrailError("SHOP_SLOT_INVALID", f"invalid shop slot: {slot}")
        runtime.click_point(*target)

    return buyer


def build_cw_shop_refresher(runtime) -> ShopAction:
    return lambda: runtime.press_key("d")


def build_cw_shop_closer(runtime) -> ShopAction:
    return lambda: runtime.click_point(*SHOP_CLOSE_POINT)


def open_cw_shop(session: SessionModel, *, opener: ShopAction | None = None) -> SessionModel:
    if opener is not None:
        opener()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {**cw_state.get("shop", {}), "opened": True, "stale": True}
    return session


def scan_cw_shop(session: SessionModel, *, scanner: ShopSnapshotSource) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = _scan_shop_snapshot(cw_state, scanner=scanner)
    return session


def buy_cw_shop_slot(session: SessionModel, *, slot: int, expect: str, buyer: ShopBuyer, scanner: ShopScanner) -> SessionModel:
    cw_state = ensure_cw_state(session)
    before_items = _normalized_shop_items(_shop_state(cw_state).get("items"))
    _require_fresh_shop_item(cw_state, slot=slot, expect=expect)
    buyer(slot=slot, expect=expect)
    updated_shop = _scan_shop_snapshot(cw_state, scanner=scanner)
    after_items = _normalized_shop_items(updated_shop.get("items"))
    if not _purchase_confirmed(before_items=before_items, after_items=after_items, slot=slot, expect=expect):
        raise TrailError("SHOP_BUY_NOT_CONFIRMED", f"shop purchase not confirmed for slot {slot}: {expect}")
    _decrement_remaining_purchase(cw_state, expect=expect)
    updated_shop["guide_summary"] = {
        "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
        "constraints": _stable_constraints_summary(cw_state),
    }
    cw_state["shop"] = updated_shop
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    return session


def refresh_cw_shop(session: SessionModel, *, refresher: ShopAction | None = None) -> SessionModel:
    if refresher is not None:
        refresher()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    return session


def close_cw_shop(session: SessionModel, *, closer: ShopAction | None = None) -> SessionModel:
    if closer is not None:
        closer()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {**cw_state.get("shop", {}), "opened": False, "stale": True}
    return session


def shop_cw_status(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    status = deepcopy(cw_state.get("shop", {"stale": True}))
    guide_summary = _guide_summary(cw_state)
    if guide_summary is not None:
        status["guide_summary"] = guide_summary
    return status
