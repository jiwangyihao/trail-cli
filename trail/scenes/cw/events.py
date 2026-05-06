from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, NoReturn, NotRequired, TypedDict, cast

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import mark_cw_stage_stale, mark_cw_stage_status_stale
from trail.scenes.cw.variable_cost import (
    VARIABLE_COST_ROLE_NAME,
    VARIABLE_COST_ROLE_COSTS,
    _choice_for_cost,
    _parse_star,
    _parse_variable_cost,
    apply_cw_variable_cost_choice,
    is_cw_variable_cost_role,
    is_variable_cost_roles_stale,
)
from trail.session.models import SessionModel

OptionChooser = Callable[[int], object]
EventHandler = Callable[[], tuple[str, str]]
SceneAction = Callable[[], object]
CwEventType = Literal["lv999_choice", "special_confirm", "replenish", "invest", "encounter", "fortune", "unknown"]


class CwEventOption(TypedDict):
    role_name: str
    choice: str


class CwEventDetection(TypedDict):
    event_type: CwEventType
    text: str


class CwEventResult(TypedDict):
    event_type: CwEventType
    handled: bool
    next_action: NotRequired[str]
    handled_action: NotRequired[str]
    variable_cost_choice: NotRequired[dict[str, object]]


CwEventRouter = Callable[[CwEventOption | None], CwEventResult]

LV999_EVENT_CHOICE_TEXTS: dict[str, set[str]] = {
    "cost_up": {"提升费用", "费用提升", "升费"},
    "equipment": {"获得装备", "选择装备", "装备"},
}

CW_WIDTH = 1920
CW_HEIGHT = 1080
CW_ACTION_OCR_REGION = {
    "from_x": 0.30,
    "from_y": 0.72,
    "to_x": 0.70,
    "to_y": 0.92,
}
CW_BATTLE_TEAM_COUNT_CONFIRM_REGION = {
    "from_x": 0.25,
    "from_y": 0.25,
    "to_x": 0.75,
    "to_y": 0.75,
}
CW_EVENT_NEXT_ACTIONS: dict[CwEventType, str] = {
    "replenish": "cw.replenish.choose",
    "invest": "cw.invest.choose",
    "encounter": "cw.encounter.choose",
    "fortune": "cw.fortune.choose",
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
        values = _extract_box_values(box)
        if values is None:
            raise ValueError("invalid box")
        left, top, width, height = values
        return (
            int(left + width / 2.0),
            int(top + height / 2.0),
        )
    return int(getattr(box, "left") + getattr(box, "width") / 2.0), int(getattr(box, "top") + getattr(box, "height") / 2.0)


def _normalized_action_text(text: object) -> str:
    return "".join(str(text or "").split())


def _read_ocr_piece_text(piece: object) -> str:
    if isinstance(piece, Mapping):
        for key in ("text", "ocr_text", "value", "name"):
            value = piece.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(piece, (list, tuple)):
        if len(piece) >= 2 and isinstance(piece[1], str | int | float):
            return str(piece[1])
        for value in piece:
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(piece, str | int | float):
        return str(piece)
    return ""


def _joined_ocr_text(runtime, *, capture: Mapping[str, float] | None = None) -> str:
    try:
        pieces = runtime.ocr(capture=capture)
    except Exception:
        return ""
    return "".join(_read_ocr_piece_text(piece).strip() for piece in pieces or [])


def _extract_box_values(box: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    try:
        left = float(str(box["left"]))
        top = float(str(box["top"]))
        width = float(str(box["width"]))
        height = float(str(box["height"]))
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
            center_x = float(str(center["x"]))
            center_y = float(str(center["y"]))
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


def _find_ocr_button_box(
    runtime,
    *,
    allowed_texts: set[str],
    capture: Mapping[str, float] | None = CW_ACTION_OCR_REGION,
) -> dict[str, float | str] | None:
    try:
        pieces = runtime.ocr(capture=capture)
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
BATTLE_TEAM_COUNT_CANCEL_POINT = _point(0.43, 0.572)


def _build_option_chooser(runtime, *, option_points: Mapping[int, tuple[int, int]], confirm_point: tuple[int, int]):
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


def _cw_event_type_from_text(text: str) -> CwEventType | None:
    normalized = _normalized_action_text(text)
    normalized_lv = normalized.replace(".", "").lower()
    if "银狼" in normalized and "lv999" in normalized_lv:
        return "lv999_choice"
    if "特殊事件" in normalized or ("特殊" in normalized and "确认" in normalized):
        return "special_confirm"
    if "补给" in normalized:
        return "replenish"
    if "投资" in normalized:
        return "invest"
    if "遭遇" in normalized:
        return "encounter"
    if "命运卜者" in normalized or "命运" in normalized:
        return "fortune"
    return None


def detect_cw_event(runtime) -> CwEventDetection:
    text = _joined_ocr_text(runtime)
    event_type = _cw_event_type_from_text(text)
    if event_type is not None:
        return {"event_type": event_type, "text": text}
    return {"event_type": "unknown", "text": text}


def build_cw_event_router(runtime) -> CwEventRouter:
    legacy_handler = build_cw_event_handler(runtime)

    def router(variable_cost_choice: CwEventOption | None = None) -> CwEventResult:
        event_type = detect_cw_event(runtime)["event_type"]
        if variable_cost_choice is not None:
            if event_type != "lv999_choice":
                raise TrailError("CW_EVENT_CHOICE_MISMATCH", "variable cost choice can only be applied to 银狼LV.999 event")
            choice = variable_cost_choice["choice"]
            target = _find_ocr_button_box(runtime, allowed_texts=LV999_EVENT_CHOICE_TEXTS[choice], capture=None)
            if target is None:
                raise TrailError("CW_EVENT_CHOICE_TARGET_MISSING", "未找到银狼LV.999 事件选项位置")
            runtime.click_point(*_box_center(target))
            return {"event_type": "lv999_choice", "handled": True, "handled_action": "confirm"}
        if event_type == "special_confirm":
            _, handled_action = legacy_handler()
            return {"event_type": "special_confirm", "handled": True, "handled_action": handled_action}
        if event_type in CW_EVENT_NEXT_ACTIONS:
            return {"event_type": event_type, "handled": False, "next_action": CW_EVENT_NEXT_ACTIONS[event_type]}
        return {"event_type": event_type, "handled": False, "next_action": "manual"}

    return router


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
    def starter() -> None:
        _click_cw_action_button(
            runtime,
            wait_alias="action.battle_start",
            locate_aliases=(),
            allowed_texts={"开始战斗", "开始挑战", "出战"},
            fallback_point=BATTLE_START_POINT,
        )
        _fail_if_battle_team_count_confirm_dialog(runtime)

    return starter


def _fail_if_battle_team_count_confirm_dialog(runtime) -> None:
    dialog_text = _normalized_action_text(_joined_ocr_text(runtime, capture=CW_BATTLE_TEAM_COUNT_CONFIRM_REGION))
    if "人数未达上限" not in dialog_text or "是否确认出战" not in dialog_text:
        return

    cancel_box = _find_ocr_button_box(
        runtime,
        allowed_texts={"取消"},
        capture=CW_BATTLE_TEAM_COUNT_CONFIRM_REGION,
    )
    if cancel_box is not None:
        runtime.click_point(*_box_center(cancel_box))
    else:
        runtime.click_point(*BATTLE_TEAM_COUNT_CANCEL_POINT)
    error = TrailError("CW_BATTLE_TEAM_COUNT_INSUFFICIENT", "可出战角色人数未达上限，请先检查场上人数")
    setattr(error, "known_failure_after_save", True)
    setattr(error, "data", {"reason": "team_count_insufficient"})
    raise error


def build_cw_battle_continuer(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias=None,
        locate_aliases=("action.battle_continue",),
        allowed_texts={"继续挑战", "继续"},
        fallback_point=BATTLE_CONTINUE_POINT,
    )


def read_cw_replenish(session: SessionModel) -> dict[str, list[int]]:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_replenish(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_invest(session: SessionModel) -> dict[str, list[int]]:
    del session
    return {"options": [1, 2, 3]}


def choose_cw_invest(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_encounter(session: SessionModel) -> dict[str, list[int]]:
    del session
    return {"options": [1, 2]}


def choose_cw_encounter(session: SessionModel, *, option: int, chooser: OptionChooser) -> SessionModel:
    chooser(option)
    mark_cw_stage_stale(session)
    return session


def read_cw_fortune(session: SessionModel) -> dict[str, list[int]]:
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


def _coerce_cw_event_option(variable_cost_choice: Mapping[str, object] | None) -> CwEventOption | None:
    if variable_cost_choice is None:
        return None
    role_name = variable_cost_choice.get("role_name")
    choice = variable_cost_choice.get("choice")
    if not isinstance(role_name, str) or not isinstance(choice, str):
        raise TrailError("CW_EVENT_CHOICE_MISMATCH", "invalid variable cost choice")
    if not is_cw_variable_cost_role(role_name) or choice not in LV999_EVENT_CHOICE_TEXTS:
        raise TrailError("CW_EVENT_CHOICE_MISMATCH", "invalid variable cost choice")
    return {"role_name": role_name, "choice": choice}


def mark_cw_event_unknown_stale(session: SessionModel) -> None:
    cw_state = ensure_cw_state(session)
    mark_cw_stage_stale(session)
    mark_cw_stage_status_stale(session)
    for key in ("slots", "shop", "equipment", "sell_plan"):
        value = cw_state.get(key)
        if isinstance(value, dict):
            value["stale"] = True
        else:
            cw_state[key] = {"stale": True}
    strategy = cw_state.get("strategy")
    if isinstance(strategy, dict):
        strategy["stale"] = True
    else:
        cw_state["strategy"] = {"cards": [], "stale": True}
    cw_state["variable_cost_roles_stale"] = True


def _raise_lv999_state_stale() -> NoReturn:
    raise TrailError("CW_EVENT_LV999_STATE_STALE", "银狼LV.999 状态已过期，请先运行 cw.event.reconcile")


def _raise_lv999_choice_mismatch(message: str = "invalid variable cost choice") -> NoReturn:
    raise TrailError("CW_EVENT_CHOICE_MISMATCH", message)


def _validate_cw_event_option_state(session: SessionModel, event_option: CwEventOption) -> None:
    cw_state = ensure_cw_state(session)
    if is_variable_cost_roles_stale(cw_state):
        _raise_lv999_state_stale()
    roles_value = cw_state.get("variable_cost_roles")
    roles = roles_value if isinstance(roles_value, dict) else {}
    state = roles.get(VARIABLE_COST_ROLE_NAME)
    if not isinstance(state, dict):
        _raise_lv999_choice_mismatch("银狼LV.999 current state is unknown")
    state = cast(dict[str, object], state)
    current_cost = _parse_variable_cost(state.get("cost"))
    if current_cost is None:
        _raise_lv999_choice_mismatch("银狼LV.999 current cost is unknown")
    if _parse_star(state.get("star")) != 2 or state.get("choice_available") is not True:
        _raise_lv999_choice_mismatch("银狼LV.999 choice is not available")
    confirmed_value = state.get("confirmed_choices_by_cost")
    confirmed_by_cost = confirmed_value if isinstance(confirmed_value, dict) else {}
    if _choice_for_cost(confirmed_by_cost, current_cost) is not None:
        _raise_lv999_choice_mismatch("银狼LV.999 choice already confirmed for current cost")
    if event_option["choice"] == "cost_up" and current_cost == max(VARIABLE_COST_ROLE_COSTS):
        _raise_lv999_choice_mismatch("银狼LV.999 already at max cost")


def handle_cw_event(
    session: SessionModel,
    *,
    router: CwEventRouter | None = None,
    handler: EventHandler | None = None,
    variable_cost_choice: Mapping[str, object] | None = None,
) -> dict[str, object]:
    event_option = _coerce_cw_event_option(variable_cost_choice)
    used_router = router is not None
    if event_option is not None and used_router:
        _validate_cw_event_option_state(session, event_option)
    if router is not None:
        result: dict[str, object] = dict(router(event_option))
    else:
        if handler is None:
            raise ValueError("cw event router is required")
        event_type, handled_action = handler()
        result = {"event_type": event_type, "handled": True, "handled_action": handled_action}

    if event_option is not None:
        if used_router and result.get("handled") is not True:
            raise ValueError("unhandled cw event cannot apply variable cost choice")
        cw_state = session.scene_state.get("cw")
        if not isinstance(cw_state, dict):
            raise ValueError("银狼LV.999 current cost is unknown")
        apply_cw_variable_cost_choice(cw_state, role_name=event_option["role_name"], choice=event_option["choice"])
        result["variable_cost_choice"] = dict(event_option)
    if result.get("event_type") == "unknown" and result.get("handled") is not True:
        mark_cw_event_unknown_stale(session)
    if result.get("handled") is True:
        mark_cw_stage_stale(session)
    return result
