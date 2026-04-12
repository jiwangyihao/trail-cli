from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

SHOP_OPEN_POINT = (0.8438, 0.8481)
SHOP_CLOSE_POINT = (0.5, 0.55)
SHOP_SLOT_POINTS = {
    1: (0.25, 0.18),
    2: (0.40, 0.18),
    3: (0.55, 0.18),
    4: (0.68, 0.18),
    5: (0.80, 0.18),
}
SHOP_SCAN_REGION = {"from_x": 0.19, "from_y": 0.26, "to_x": 0.88, "to_y": 0.31}
SHOP_COINS_REGION = {"from_x": 0.84, "from_y": 0.81, "to_x": 0.89, "to_y": 0.89}
SHOP_LEVEL_REGION = {"from_x": 0.05, "from_y": 0.815, "to_x": 0.3, "to_y": 0.87}
SHOP_MAX_TEAM_SIZE_REGION = {"from_x": 0.505, "from_y": 0.18, "to_x": 0.608, "to_y": 0.27}

ShopScanner = Callable[[], tuple[list[Any], int | None, int | None, bool, int | None]]
ShopBuyer = Callable[..., object]
ShopAction = Callable[[], object]


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


def build_cw_shop_opener(runtime) -> ShopAction:
    return lambda: runtime.click_point(*SHOP_OPEN_POINT)


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> tuple[list[dict[str, Any]], int | None, int | None, bool, int | None]:
        items, reserve_full = _parse_shop_items(runtime.ocr(**SHOP_SCAN_REGION))
        coins = _parse_first_int(runtime.ocr(**SHOP_COINS_REGION) or [], default=0)
        level = _parse_last_int(runtime.ocr(**SHOP_LEVEL_REGION) or [], default=None)
        max_team_size = _parse_last_int(runtime.ocr(**SHOP_MAX_TEAM_SIZE_REGION) or [], default=None)
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
    items, coins, level, reserve_full, max_team_size = scanner()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {
        "items": deepcopy(items),
        "coins": coins,
        "level": level,
        "reserve_full": reserve_full,
        "max_team_size": max_team_size,
        "guide_summary": {
            "remaining_purchases": deepcopy(cw_state.get("guide", {}).get("remaining_purchases", {})),
            "constraints": deepcopy(cw_state.get("constraints", {})),
        },
        "stale": False,
    }
    return session


def buy_cw_shop_slot(session: SessionModel, *, slot: int, expect: str, buyer: ShopBuyer, scanner: ShopScanner) -> SessionModel:
    buyer(slot=slot, expect=expect)
    cw_state = ensure_cw_state(session)
    guide_state = cw_state.setdefault("guide", {})
    remaining = guide_state.setdefault("remaining_purchases", {})
    remaining[expect] = max(0, remaining.get(expect, 0) - 1)
    scan_cw_shop(session, scanner=scanner)
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
