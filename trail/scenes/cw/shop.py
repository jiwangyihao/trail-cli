from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

ShopScanner = Callable[[], tuple[list[Any], int | None, int | None, bool, int | None]]
ShopBuyer = Callable[..., object]


def open_cw_shop(session: SessionModel) -> SessionModel:
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


def buy_cw_shop_slot(session: SessionModel, *, slot: int, expect: str, buyer: ShopBuyer) -> SessionModel:
    buyer(slot=slot, expect=expect)
    cw_state = ensure_cw_state(session)
    guide_state = cw_state.setdefault("guide", {})
    remaining = guide_state.setdefault("remaining_purchases", {})
    remaining[expect] = max(0, remaining.get(expect, 0) - 1)
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    return session


def refresh_cw_shop(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    return session


def close_cw_shop(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = {**cw_state.get("shop", {}), "opened": False, "stale": True}
    return session


def shop_cw_status(session: SessionModel) -> dict:
    return ensure_cw_state(session).get("shop", {"stale": True})
