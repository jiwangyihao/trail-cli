from __future__ import annotations

from collections.abc import Callable

from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

OptionChooser = Callable[[int], object]
EventHandler = Callable[[], tuple[str, str]]


def read_cw_replenish(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_replenish(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    ensure_cw_state(session)["stage"] = {"stale": True}
    return session


def read_cw_invest(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_invest(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    ensure_cw_state(session)["stage"] = {"stale": True}
    return session


def read_cw_encounter(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_encounter(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    ensure_cw_state(session)["stage"] = {"stale": True}
    return session


def read_cw_fortune(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_fortune(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    ensure_cw_state(session)["stage"] = {"stale": True}
    return session


def handle_cw_event(session: SessionModel, *, handler: EventHandler) -> dict:
    del session
    event_type, handled_action = handler()
    return {"event_type": event_type, "handled_action": handled_action}
