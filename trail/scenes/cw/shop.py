from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

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


SHOP_OPEN_POINT = _point(0.8438, 0.8481)
SHOP_CLOSE_POINT = _point(0.5, 0.55)
SHOP_SLOT_POINTS = {
    1: _point(0.25, 0.18),
    2: _point(0.40, 0.18),
    3: _point(0.55, 0.18),
    4: _point(0.68, 0.18),
    5: _point(0.80, 0.18),
}
SHOP_SCAN_REGION = _region(0.19, 0.26, 0.88, 0.31)
SHOP_COINS_REGION = _region(0.84, 0.81, 0.89, 0.89)
SHOP_LEVEL_REGION = _region(0.05, 0.815, 0.3, 0.87)
SHOP_MAX_TEAM_SIZE_REGION = _region(0.505, 0.18, 0.608, 0.27)

ShopScanner = Callable[[], tuple[list[Any], int | None, int | None, bool, int | None]]
ShopBuyer = Callable[..., object]
ShopAction = Callable[[], object]

SHOP_SUMMARY_CONSTRAINT_KEYS = ("min_coins", "min_level", "mid_level")


def _read_ocr_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, (list, tuple)):
        for value in reversed(item):
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, str | int | float):
        return str(item)
    return ""


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
    reserve_full: bool,
    max_team_size: int | None,
) -> dict[str, Any]:
    return {
        **_preserved_shop_flags(cw_state),
        "items": deepcopy(items),
        "coins": coins,
        "level": level,
        "reserve_full": reserve_full,
        "max_team_size": max_team_size,
        "guide_summary": {
            "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
            "constraints": _stable_constraints_summary(cw_state),
        },
        "stale": False,
    }


def _scan_shop_snapshot(cw_state: dict, *, scanner: ShopScanner) -> dict[str, Any]:
    items, coins, level, reserve_full, max_team_size = scanner()
    return _build_shop_snapshot(
        cw_state,
        items=items,
        coins=coins,
        level=level,
        reserve_full=reserve_full,
        max_team_size=max_team_size,
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


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> tuple[list[dict[str, Any]], int | None, int | None, bool, int | None]:
        items, reserve_full = _parse_shop_items(runtime.ocr(capture=SHOP_SCAN_REGION))
        coins = _parse_first_int(runtime.ocr(capture=SHOP_COINS_REGION) or [], default=0)
        level = _parse_last_int(runtime.ocr(capture=SHOP_LEVEL_REGION) or [], default=None)
        max_team_size = _parse_last_int(runtime.ocr(capture=SHOP_MAX_TEAM_SIZE_REGION) or [], default=None)
        return items, coins, level, reserve_full, max_team_size

    return scanner


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


def scan_cw_shop(session: SessionModel, *, scanner: ShopScanner) -> SessionModel:
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
    return ensure_cw_state(session).get("shop", {"stale": True})
