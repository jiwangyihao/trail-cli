from __future__ import annotations

from collections.abc import Callable

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel

OptionChooser = Callable[[int], object]
EventHandler = Callable[[], tuple[str, str]]

REPLENISH_OPTION_POINTS = {
    1: (0.2, 0.52),
    2: (0.53, 0.52),
    3: (0.82, 0.52),
}
REPLENISH_CONFIRM_POINT = (0.88, 0.91)

INVEST_OPTION_POINTS = {
    1: (0.2, 0.3),
    2: (0.5, 0.3),
    3: (0.8, 0.3),
}
INVEST_CONFIRM_POINT = (0.77, 0.521)

ENCOUNTER_OPTION_POINTS = {
    1: (0.35, 0.5),
    2: (0.65, 0.5),
}
ENCOUNTER_CONFIRM_POINT = (0.5, 0.84)

FORTUNE_OPTION_POINTS = {
    1: (0.2, 0.3),
    2: (0.8, 0.3),
}
FORTUNE_CONFIRM_POINT = (0.77, 0.521)

SPECIAL_EVENT_OPTION_POINT = (0.5, 0.25)
SPECIAL_EVENT_CONFIRM_POINT = (0.77, 0.521)


def _invalidate_cw_stage(session: SessionModel) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None


def _build_option_chooser(runtime, *, option_points: dict[int, tuple[float, float]], confirm_point: tuple[float, float]):
    def chooser(option: int) -> None:
        point = option_points.get(option)
        if point is None:
            raise TrailError("CW_OPTION_INVALID", f"invalid currency wars option: {option}")
        runtime.click_point(*point)
        runtime.click_point(*confirm_point)

    return chooser


def build_cw_replenish_chooser(runtime) -> OptionChooser:
    return _build_option_chooser(runtime, option_points=REPLENISH_OPTION_POINTS, confirm_point=REPLENISH_CONFIRM_POINT)


def build_cw_invest_chooser(runtime) -> OptionChooser:
    return _build_option_chooser(runtime, option_points=INVEST_OPTION_POINTS, confirm_point=INVEST_CONFIRM_POINT)


def build_cw_encounter_chooser(runtime) -> OptionChooser:
    return _build_option_chooser(runtime, option_points=ENCOUNTER_OPTION_POINTS, confirm_point=ENCOUNTER_CONFIRM_POINT)


def build_cw_fortune_chooser(runtime) -> OptionChooser:
    return _build_option_chooser(runtime, option_points=FORTUNE_OPTION_POINTS, confirm_point=FORTUNE_CONFIRM_POINT)


def build_cw_event_handler(runtime) -> EventHandler:
    def handler() -> tuple[str, str]:
        runtime.click_point(*SPECIAL_EVENT_OPTION_POINT)
        runtime.click_point(*SPECIAL_EVENT_CONFIRM_POINT)
        return "special", "confirm"

    return handler


def read_cw_replenish(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_replenish(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    _invalidate_cw_stage(session)
    return session


def read_cw_invest(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_invest(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    _invalidate_cw_stage(session)
    return session


def read_cw_encounter(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_encounter(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    _invalidate_cw_stage(session)
    return session


def read_cw_fortune(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_fortune(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    _invalidate_cw_stage(session)
    return session


def handle_cw_event(session: SessionModel, *, handler: EventHandler) -> dict:
    event_type, handled_action = handler()
    _invalidate_cw_stage(session)
    return {"event_type": event_type, "handled_action": handled_action}
