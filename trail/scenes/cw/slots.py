from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
from typing import Any

from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

SlotsSnapshotReader = Callable[[], tuple[list[Any], list[Any], list[Any]]]


def _clear_sell_plan(cw_state: dict) -> None:
    cw_state["sell_plan"] = {}


def read_cw_slots(session: SessionModel, *, reader: SlotsSnapshotReader) -> SessionModel:
    front, back, hand = reader()
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {
        "front": deepcopy(front),
        "back": deepcopy(back),
        "hand": deepcopy(hand),
        "stale": False,
    }
    _clear_sell_plan(cw_state)
    return session


def swap_cw_slots(session: SessionModel, *, source: str, target: str) -> SessionModel:
    del source, target
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


def place_one_cw_slot(session: SessionModel, *, source: str, target: str) -> SessionModel:
    del source, target
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session


def collect_cw_crystals(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    metrics = cw_state.setdefault("metrics", {})
    metrics["last_crystal_collection"] = "done"
    return session


def plan_cw_hand_sell(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    slots = cw_state.get("slots", {})
    hand = slots.get("hand", [])
    candidates = [index for index, value in enumerate(hand) if value is not None]
    cw_state["sell_plan"] = {"candidates": candidates}
    return deepcopy(cw_state["sell_plan"])


def sell_one_cw_hand(session: SessionModel, *, slot: int) -> SessionModel:
    del slot
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    _clear_sell_plan(cw_state)
    return session
