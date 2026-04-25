from __future__ import annotations

from collections.abc import Callable, Mapping

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.stage import mark_cw_stage_stale
from trail.session.models import SessionModel

OptionChooser = Callable[[int], object]
EventHandler = Callable[[], tuple[str, str]]
SceneAction = Callable[[], object]

CW_WIDTH = 1920
CW_HEIGHT = 1080
CW_ACTION_OCR_REGION = {
    "from_x": 0.30,
    "from_y": 0.72,
    "to_x": 0.70,
    "to_y": 0.92,
}


def _point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
    return int(CW_WIDTH * x_ratio), int(CW_HEIGHT * y_ratio)


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box_center(box: object) -> tuple[int, int]:
    center = getattr(box, "center", None)
    if isinstance(center, tuple) and len(center) == 2:
        return int(center[0]), int(center[1])
    if isinstance(box, Mapping):
        return (
            int(float(box["left"]) + float(box["width"]) / 2.0),
            int(float(box["top"]) + float(box["height"]) / 2.0),
        )
    return int(getattr(box, "left") + getattr(box, "width") / 2.0), int(getattr(box, "top") + getattr(box, "height") / 2.0)


def _normalized_action_text(text: object) -> str:
    return "".join(str(text or "").split())


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
        "width": width,
        "height": height,
    }


def _find_ocr_button_box(runtime, *, allowed_texts: set[str]) -> dict[str, float | str] | None:
    try:
        pieces = runtime.ocr(capture=CW_ACTION_OCR_REGION)
    except Exception:
        return None
    normalized_allowed_texts = {_normalized_action_text(text) for text in allowed_texts}
    for piece in pieces or []:
        normalized_piece = _normalize_ocr_piece(piece)
        if normalized_piece is None:
            continue
        if _normalized_action_text(normalized_piece["text"]) not in normalized_allowed_texts:
            continue
        return normalized_piece
    return None


def _click_cw_action_button(
    runtime,
    *,
    wait_alias: str | None,
    locate_aliases: tuple[str, ...],
    allowed_texts: set[str],
    fallback_point: tuple[int, int],
) -> None:
    if wait_alias is not None:
        match = runtime.wait_img(_asset(wait_alias), timeout=3, interval=0.5)
        if match is not None:
            runtime.click_point(*_box_center(match))
            return
    for alias in locate_aliases:
        match = runtime.locate(_asset(alias))
        if match is not None:
            runtime.click_point(*_box_center(match))
            return
    ocr_match = _find_ocr_button_box(runtime, allowed_texts=allowed_texts)
    if ocr_match is not None:
        runtime.click_point(*_box_center(ocr_match))
        return
    runtime.click_point(*fallback_point)

REPLENISH_OPTION_POINTS = {
    1: _point(0.2, 0.52),
    2: _point(0.53, 0.52),
    3: _point(0.82, 0.52),
}
REPLENISH_CONFIRM_POINT = _point(0.88, 0.91)

INVEST_OPTION_POINTS = {
    1: _point(0.2, 0.3),
    2: _point(0.5, 0.3),
    3: _point(0.8, 0.3),
}
INVEST_CONFIRM_POINT = _point(0.77, 0.521)

ENCOUNTER_OPTION_POINTS = {
    1: _point(0.35, 0.5),
    2: _point(0.65, 0.5),
}
ENCOUNTER_CONFIRM_POINT = _point(0.5, 0.84)

FORTUNE_OPTION_POINTS = {
    1: _point(0.2, 0.3),
    2: _point(0.8, 0.3),
}
FORTUNE_CONFIRM_POINT = _point(0.77, 0.521)

SPECIAL_EVENT_OPTION_POINT = _point(0.5, 0.25)
SPECIAL_EVENT_CONFIRM_POINT = _point(0.77, 0.521)
BOSS_PREVIEW_CONFIRM_POINT = _point(0.5, 0.7)
SETTLE_NEXT_POINT = _point(0.5, 0.82)
BATTLE_START_POINT = _point(0.5, 0.824)
BATTLE_CONTINUE_POINT = _point(0.5, 0.824)


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


def build_cw_boss_preview_confirmer(runtime) -> SceneAction:
    return lambda: runtime.click_point(*BOSS_PREVIEW_CONFIRM_POINT)


def build_cw_settle_continuer(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias=None,
        locate_aliases=("stage.settle", "action.settle_next_page"),
        allowed_texts={"下一步", "下一页"},
        fallback_point=SETTLE_NEXT_POINT,
    )


def build_cw_battle_starter(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias="action.battle_start",
        locate_aliases=(),
        allowed_texts={"开始战斗", "开始挑战", "出战"},
        fallback_point=BATTLE_START_POINT,
    )


def build_cw_battle_continuer(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias=None,
        locate_aliases=("action.battle_continue",),
        allowed_texts={"继续挑战", "继续"},
        fallback_point=BATTLE_CONTINUE_POINT,
    )


def read_cw_replenish(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_replenish(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_invest(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_invest(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_encounter(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_encounter(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_fortune(session: SessionModel) -> dict:
    del session
    return {"options": [1, 2]}


def choose_cw_fortune(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def confirm_cw_boss_preview(session: SessionModel, *, confirmer: SceneAction) -> SessionModel:
    confirmer()
    mark_cw_stage_stale(session)
    return session


def settle_cw_next(session: SessionModel, *, continuer: SceneAction) -> SessionModel:
    continuer()
    mark_cw_stage_stale(session)
    return session


def start_cw_battle(session: SessionModel, *, starter: SceneAction) -> SessionModel:
    starter()
    mark_cw_stage_stale(session)
    return session


def continue_cw_battle(session: SessionModel, *, continuer: SceneAction) -> SessionModel:
    continuer()
    mark_cw_stage_stale(session)
    return session


def handle_cw_event(session: SessionModel, *, handler: EventHandler) -> dict:
    event_type, handled_action = handler()
    mark_cw_stage_stale(session)
    return {"event_type": event_type, "handled_action": handled_action}
