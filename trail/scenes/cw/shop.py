from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import os
from pathlib import Path
import re
import traceback
from typing import TYPE_CHECKING
from time import sleep
from typing import Any

from trail.core.errors import TrailError
from trail.runtime.batch_ocr import BatchOcrTarget, run_batch_ocr
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import (
    mark_cw_stage_status_stale,
    parse_cw_stage_exp as _shared_parse_cw_stage_exp,
    parse_cw_stage_level as _shared_parse_cw_stage_level,
    parse_cw_stage_team_size as _shared_parse_cw_stage_team_size,
)
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
SHOP_COINS_REGION = _pixel_region(1615, 902, 1698, 956)
SHOP_SCAN_RESET_SETTLE_SECONDS = 1.0
SHOP_SCAN_OPEN_SETTLE_SECONDS = 1.5
SHOP_BUY_CONFIRM_RETRY_SECONDS = 0.5
SHOP_BUY_CONFIRM_MAX_ATTEMPTS = 4
SHOP_LEGACY_STAGE_FIELDS = {"level", "exp", "team_size", "role_count"}

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


def _parse_shop_items(raw_items: list[Any] | None) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    slot_items: dict[int, dict[str, Any]] = {}
    reserve_full = False
    slot_lane_width = _shop_scan_slot_lane_width(raw_items or [])
    for item in raw_items or []:
        text = _read_ocr_text(item).strip()
        if not text:
            continue
        if "备" in text:
            reserve_full = True
            continue
        slot = _shop_scan_slot_for_item(item, lane_width=slot_lane_width)
        if slot is not None:
            box = _read_ocr_box(item)
            bottom = box[3] if box is not None else 0.0
            entry = slot_items.setdefault(slot, {"slot": slot, "price": None, "_price_bottom": -1.0, "_names": []})
            if text.isdecimal():
                if bottom >= entry["_price_bottom"]:
                    entry["price"] = int(text)
                    entry["_price_bottom"] = bottom
                continue
            entry["_names"].append((bottom, text))
            continue
        if text.isdecimal():
            if items and items[-1]["price"] is None:
                items[-1]["price"] = int(text)
            continue
        items.append({"name": text, "price": None})
    if slot_items:
        slotted_items: list[dict[str, Any]] = []
        for slot in sorted(SHOP_SLOT_POINTS):
            entry = slot_items.get(slot)
            if entry is None:
                slotted_items.append({"slot": slot, "name": None, "price": None})
                continue
            price = entry.get("price")
            if price is None:
                slotted_items.append({"slot": slot, "name": None, "price": None})
                continue
            name_candidates = [candidate for candidate in entry.get("_names", []) if candidate[0] <= entry["_price_bottom"] + 20.0]
            if not name_candidates:
                name_candidates = entry.get("_names", [])
            name = max(name_candidates, default=(0.0, None), key=lambda candidate: candidate[0])[1]
            slotted_items.append({"slot": slot, "name": name, "price": price})
        return slotted_items, reserve_full
    return items, reserve_full


def _shop_scan_slot_lane_width(items: list[Any]) -> float | None:
    boxes = [box for item in items if (box := _read_ocr_box(item)) is not None and box[2] > 10 and box[3] > 10]
    if not boxes:
        return None
    scan_width = max(box[2] for box in boxes)
    if scan_width < 100:
        return None
    return scan_width / float(len(SHOP_SLOT_POINTS))


def _shop_scan_slot_for_item(item: Any, *, lane_width: float | None) -> int | None:
    if lane_width is None:
        return None
    box = _read_ocr_box(item)
    if box is None:
        return None
    left, top, right, bottom = box
    if right <= 10 or bottom <= 10:
        return None
    center_x = (left + right) / 2.0
    slot = int(center_x / lane_width) + 1
    return max(1, min(len(SHOP_SLOT_POINTS), slot))


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


def _batch_ocr_items(by_key: dict[Any, Any], key: Any) -> list[Any]:
    result = by_key.get(key)
    if result is None:
        return []
    pieces = getattr(result, "pieces", None)
    if isinstance(pieces, list):
        return pieces
    if isinstance(pieces, tuple):
        return list(pieces)
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return [text]
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    return []


_parse_shop_level = _shared_parse_cw_stage_level
_parse_shop_exp = _shared_parse_cw_stage_exp
_parse_shop_team_size = _shared_parse_cw_stage_team_size


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


def sanitize_cw_shop_state(cw_state: dict) -> dict[str, Any]:
    shop_state = cw_state.get("shop")
    if not isinstance(shop_state, dict):
        shop_state = {"stale": True}
        cw_state["shop"] = shop_state
        return shop_state
    for key in SHOP_LEGACY_STAGE_FIELDS:
        shop_state.pop(key, None)
    return shop_state


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
    reserve_full: bool,
) -> dict[str, Any]:
    return {
        **_preserved_shop_flags(cw_state),
        "items": deepcopy(items),
        "coins": coins,
        "reserve_full": reserve_full,
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
            reserve_full=bool(scanned.get("reserve_full", False)),
        )
        if "opened" in scanned:
            snapshot["opened"] = bool(scanned["opened"])
        if "stale" in scanned:
            snapshot["stale"] = bool(scanned["stale"])
        return snapshot

    items, coins, _level, reserve_full, _team_size = scanned
    return _build_shop_snapshot(
        cw_state,
        items=items,
        coins=coins,
        reserve_full=reserve_full,
    )


def _normalized_shop_items(items: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if isinstance(item, dict):
            normalized_item = {"name": item.get("name"), "price": item.get("price")}
            if item.get("slot") is not None:
                normalized_item["slot"] = item.get("slot")
            normalized.append(normalized_item)
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


def _scan_until_purchase_confirmed(cw_state: dict, *, before_items: list[dict[str, Any]], slot: int, expect: str, scanner: ShopScanner) -> dict[str, Any]:
    updated_shop: dict[str, Any] | None = None
    for attempt in range(SHOP_BUY_CONFIRM_MAX_ATTEMPTS):
        if attempt > 0:
            sleep(SHOP_BUY_CONFIRM_RETRY_SECONDS)
        updated_shop = _scan_shop_snapshot(cw_state, scanner=scanner)
        after_items = _normalized_shop_items(updated_shop.get("items"))
        if _purchase_confirmed(before_items=before_items, after_items=after_items, slot=slot, expect=expect):
            return updated_shop
    raise TrailError("SHOP_BUY_NOT_CONFIRMED", f"shop purchase not confirmed for slot {slot}: {expect}")


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
    del read_team_size
    _maybe_dump_shop_capture(runtime, name="scan", capture=SHOP_SCAN_REGION)
    _maybe_dump_shop_capture(runtime, name="coins", capture=SHOP_COINS_REGION)
    batch_result = run_batch_ocr(
        runtime,
        [
            BatchOcrTarget("items", runtime.capture_image(**SHOP_SCAN_REGION, normalize=False)),
            BatchOcrTarget("coins", runtime.capture_image(**SHOP_COINS_REGION, normalize=False)),
        ],
        trace_prefix="cw_shop_batch_ocr",
    )
    items, reserve_full = _parse_shop_items(_batch_ocr_items(batch_result.by_key, "items"))
    coins = _parse_first_int(_batch_ocr_items(batch_result.by_key, "coins"), default=0)
    return {
        "items": items,
        "coins": coins,
        "reserve_full": reserve_full,
    }


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> dict[str, Any]:
        return _read_shop_page_snapshot(runtime, read_team_size=False)

    return scanner


def build_cw_shop_scan_snapshot_reader(runtime) -> ShopSnapshotReader:
    def reader() -> dict[str, Any]:
        runtime.click_point(*SHOP_SCAN_RESET_POINT)
        sleep(SHOP_SCAN_RESET_SETTLE_SECONDS)
        runtime.click_point(*SHOP_OPEN_POINT)
        sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
        snapshot = _read_shop_page_snapshot(runtime, read_team_size=False)
        return {"opened": True, "stale": False, **snapshot}

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
    updated_shop = _scan_until_purchase_confirmed(cw_state, before_items=before_items, slot=slot, expect=expect, scanner=scanner)
    _decrement_remaining_purchase(cw_state, expect=expect)
    updated_shop["guide_summary"] = {
        "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
        "constraints": _stable_constraints_summary(cw_state),
    }
    cw_state["shop"] = updated_shop
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    mark_cw_stage_status_stale(session)
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


def project_cw_shop_snapshot(session: SessionModel) -> dict[str, Any]:
    cw_state = ensure_cw_state(session)
    payload = deepcopy(sanitize_cw_shop_state(cw_state))
    stage = cw_state.get("stage")
    status = stage.get("status") if isinstance(stage, dict) else None
    if isinstance(status, dict):
        payload["stage_status"] = deepcopy(status)
        payload["stage_status_stale"] = bool(status.get("stale", True))
    else:
        payload["stage_status_stale"] = True
    return payload


def shop_cw_status(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    status = project_cw_shop_snapshot(session)
    guide_summary = _guide_summary(cw_state)
    if guide_summary is not None:
        status["guide_summary"] = guide_summary
    return status
