from __future__ import annotations

from collections.abc import Mapping
from time import sleep

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import STAGE_RESOURCE_ALIASES, _detect_cw_stage_from_ocr
from trail.session.models import SessionModel


CW_WIDTH = 1920
CW_HEIGHT = 1080
ENTRY_UI_WAIT_TIMEOUT = 10
LOWEST_DIFFICULTY_MAX_CLICKS = 10
ENTRY_GUIDE_HOTKEY = "f4"
ENTRY_GUIDE_OPEN_SETTLE_SECONDS = 2.0
ENTRY_COSMIC_STRIFE_SETTLE_SECONDS = 1.0
ENTRY_CURRENCY_WARS_SETTLE_SECONDS = 0.8
ENTRY_PARTICIPATE_SETTLE_SECONDS = 1.0
CURRENCY_WARS_ENTRY_POINT = (int(CW_WIDTH * 0.242), int(CW_HEIGHT * 0.30))
CURRENCY_WARS_PARTICIPATE_POINT = (int(CW_WIDTH * 0.7786), int(CW_HEIGHT * 0.8194))
STANDARD_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.2315))
OVERCLOCK_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.4167))
HOME_UNFINISHED_PROGRESS_KEYWORDS = ("继续进度", "结束并结算")


class CwEnterStateError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw enter only supports world or home, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_ENTER_ALREADY_PAST_HOME", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


class CwStartStateError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw start only supports home, pre-invest pages, or invest, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_START_PAGE_INVALID", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


class CwStartEntryConflictError(TrailError):
    def __init__(self, *, field_name: str, recorded: str, requested: str):
        super().__init__("CW_START_ENTRY_CONFLICT", f"cw start conflicts with recorded {field_name}: {recorded} != {requested}")


class CwStartEntryTruthRequiredError(TrailError):
    def __init__(self, *, page: str, fields: tuple[str, ...]):
        joined = "/".join(fields)
        super().__init__("CW_START_ENTRY_TRUTH_REQUIRED", f"cw start requires recorded {joined} before continuing from {page}")


class CwStartProgressPendingError(TrailError):
    def __init__(self):
        super().__init__(
            "CW_START_PROGRESS_PENDING",
            "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
        )
        self.data = {"page": "home"}


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box_center(box: object) -> tuple[int, int]:
    if hasattr(box, "center"):
        center = getattr(box, "center")
        if isinstance(center, tuple) and len(center) == 2:
            return int(center[0]), int(center[1])

    if isinstance(box, Mapping):
        try:
            left = int(box["left"])
            top = int(box["top"])
            width = int(box["width"])
            height = int(box["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrailError("CW_ENTRY_UI_INVALID", "货币战争进入链返回的坐标框无效") from exc
        return left + width // 2, top + height // 2

    raise TrailError("CW_ENTRY_UI_INVALID", "货币战争进入链返回的坐标框无效")


def _click_box_center(runtime, box: object) -> None:
    runtime.click_point(*_box_center(box))


def _transition_sleep(seconds: float) -> None:
    sleep(seconds)


def _locate(runtime, alias: str):
    return runtime.locate(_asset(alias))


def _wait(runtime, alias: str):
    box = runtime.wait_img(_asset(alias), timeout=ENTRY_UI_WAIT_TIMEOUT)
    if box is None:
        raise TrailError("CW_ENTRY_UI_NOT_FOUND", f"未识别到货币战争界面元素: {alias}")
    return box


def _extract_ocr_texts(ocr_result: object) -> list[str]:
    if not isinstance(ocr_result, (list, tuple)):
        return []
    texts: list[str] = []
    for piece in ocr_result:
        if isinstance(piece, Mapping):
            text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        elif isinstance(piece, (list, tuple)) and len(piece) >= 2 and isinstance(piece[1], str):
            text = piece[1].strip()
        else:
            continue
        if text:
            texts.append("".join(text.split()))
    return texts


def _home_has_unfinished_progress(runtime) -> bool:
    try:
        texts = _extract_ocr_texts(runtime.ocr())
    except Exception:
        return False
    joined = "".join(texts)
    return any(keyword in joined for keyword in HOME_UNFINISHED_PROGRESS_KEYWORDS)


def _invalidate_stage(session: SessionModel) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None


def _invalidate_entry_snapshots(session: SessionModel) -> None:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    cw_state["sell_plan"] = {"stale": True}
    cw_state["portal"] = {**cw_state.get("portal", {}), "stale": True}
    _invalidate_stage(session)


def _select_battle_mode(runtime, *, battle_mode: str) -> None:
    target = OVERCLOCK_BATTLE_MODE_POINT if battle_mode == "overclock" else STANDARD_BATTLE_MODE_POINT
    runtime.click_point(*target)


def _select_difficulty(runtime, *, difficulty: str) -> None:
    if difficulty == "current":
        return

    if difficulty == "highest":
        _click_box_center(runtime, _wait(runtime, "entry.difficulty.highest"))
        return

    if difficulty == "lowest":
        template = _asset("entry.difficulty.lowest")
        clicked = False
        for _ in range(LOWEST_DIFFICULTY_MAX_CLICKS):
            box = runtime.locate(template)
            if box is None:
                break
            _click_box_center(runtime, box)
            clicked = True
        if not clicked:
            _click_box_center(runtime, _wait(runtime, "entry.difficulty.lowest"))
        return

    raise TrailError("CW_ENTRY_DIFFICULTY_INVALID", f"不支持的货币战争难度: {difficulty}")


def _handle_boss_info_flow(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "stage.settle"))


def _consume_click_blank_prompt(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "stage.boss_preview"))


def _handle_invest_environment_flow(runtime) -> None:
    _wait(runtime, "entry.invest_environment")


def _enter_from_start_page(runtime, *, mode: str, difficulty: str, battle_mode: str, start_box=None) -> None:
    if start_box is None:
        start_box = _wait(runtime, "entry.start")
    _click_box_center(runtime, start_box)
    _select_battle_mode(runtime, battle_mode=battle_mode)
    if mode == "new":
        _enter_new_game(runtime, difficulty=difficulty)
        return
    _enter_continue_game(runtime)
    _handle_invest_environment_flow(runtime)


def _enter_from_world(runtime, *, mode: str, difficulty: str, battle_mode: str) -> None:
    del mode, difficulty, battle_mode
    runtime.press_key(ENTRY_GUIDE_HOTKEY)
    _transition_sleep(ENTRY_GUIDE_OPEN_SETTLE_SECONDS)
    _wait(runtime, "entry.menu")
    _click_box_center(runtime, _wait(runtime, "entry.cosmic_strife"))
    _transition_sleep(ENTRY_COSMIC_STRIFE_SETTLE_SECONDS)
    runtime.click_point(*CURRENCY_WARS_ENTRY_POINT)
    _transition_sleep(ENTRY_CURRENCY_WARS_SETTLE_SECONDS)
    runtime.click_point(*CURRENCY_WARS_PARTICIPATE_POINT)
    _transition_sleep(ENTRY_PARTICIPATE_SETTLE_SECONDS)
    _wait(runtime, "entry.start")


def _detect_current_enter_page(
    runtime,
    *,
    session: SessionModel | None = None,
    preferred_mode: str | None = None,
) -> dict[str, str]:
    start_box = _locate(runtime, "entry.start")
    if start_box is not None and _home_has_unfinished_progress(runtime):
        return {"page": "home", "unfinished_progress": "1"}

    new_box = _locate(runtime, "entry.new")
    continue_box = _locate(runtime, "entry.continue")
    if new_box is not None and continue_box is not None:
        if preferred_mode == "continue":
            return {"page": "entry.continue"}
        return {"page": "entry.new"}

    if new_box is not None:
        return {"page": "entry.new"}

    if continue_box is not None:
        return {"page": "entry.continue"}

    if _locate(runtime, "entry.invest_environment") is not None:
        return {"page": "invest"}

    try:
        ocr_stage = _detect_cw_stage_from_ocr(runtime)
    except Exception:
        ocr_stage = None
    if ocr_stage is not None:
        return {"page": "in_game", "stage": ocr_stage}

    for alias, stage in STAGE_RESOURCE_ALIASES:
        if stage in {"settle", "game_over"}:
            continue
        if _locate(runtime, alias) is None:
            continue
        if stage == "boss_preview":
            return {"page": "stage.boss_preview", "stage": stage}
        return {"page": "in_game", "stage": stage}

    if start_box is not None:
        if session is not None:
            cw_state = ensure_cw_state(session)
            recorded_stage = cw_state.get("stage", {})
            if recorded_stage.get("stale") is False and recorded_stage.get("value") == "game_over":
                return {"page": "in_game", "stage": "game_over"}
            recorded_entry = cw_state.get("entry", {})
            if recorded_entry.get("page") == "home":
                return {"page": "home", "already_home": "1"}
        return {"page": "home", "already_home": "1"}

    return {"page": "world"}


def _enter_new_game(runtime, *, difficulty: str) -> None:
    _click_box_center(runtime, _wait(runtime, "entry.new"))
    _select_difficulty(runtime, difficulty=difficulty)
    _click_box_center(runtime, _wait(runtime, "entry.start_game"))
    _handle_boss_info_flow(runtime)
    _consume_click_blank_prompt(runtime)
    _handle_invest_environment_flow(runtime)


def _enter_continue_game(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "entry.continue"))
    _click_box_center(runtime, _wait(runtime, "stage.boss_preview"))


def _run_entry_chain(runtime, *, mode: str, difficulty: str, battle_mode: str) -> None:
    current = _detect_current_enter_page(runtime)
    if current["page"] == "home":
        return {"page": "home", "already_home": True}
    if current["page"] == "world":
        _enter_from_world(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode)
        return {"page": "home"}
    raise CwEnterStateError(page=current["page"], stage=current.get("stage"))


def _persist_start_entry(
    session: SessionModel,
    *,
    mode: str | None,
    difficulty: str | None,
    battle_mode: str | None,
    guard_conflicts: bool,
) -> None:
    if mode is None or difficulty is None or battle_mode is None:
        missing_fields = tuple(
            field_name
            for field_name, value in (("mode", mode), ("difficulty", difficulty), ("battle_mode", battle_mode))
            if value is None
        )
        raise CwStartEntryTruthRequiredError(page="cw.start.persist", fields=missing_fields)
    cw_state = ensure_cw_state(session)
    existing = cw_state.get("entry") if isinstance(cw_state.get("entry"), Mapping) else {}
    if guard_conflicts:
        for field_name, requested in (("mode", mode), ("difficulty", difficulty), ("battle_mode", battle_mode)):
            recorded = existing.get(field_name)
            if recorded is not None and recorded != requested:
                raise CwStartEntryConflictError(field_name=field_name, recorded=str(recorded), requested=requested)
    _invalidate_entry_snapshots(session)
    cw_state["entry"] = {
        "page": "invest",
        "mode": mode,
        "difficulty": difficulty,
        "battle_mode": battle_mode,
    }


def _run_start_chain(
    runtime,
    *,
    current: dict[str, str],
    mode: str,
    difficulty: str,
    battle_mode: str,
    existing_entry: Mapping[str, object],
) -> dict[str, str | None]:
    page = current["page"]
    if page == "home":
        if current.get("unfinished_progress") == "1":
            raise CwStartProgressPendingError()
        start_box = _locate(runtime, "entry.start")
        _enter_from_start_page(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode, start_box=start_box)
        return {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}
    if page == "entry.new":
        _select_battle_mode(runtime, battle_mode=battle_mode)
        _enter_new_game(runtime, difficulty=difficulty)
        return {"mode": "new", "difficulty": difficulty, "battle_mode": battle_mode}
    if page == "entry.continue":
        recorded_difficulty = existing_entry.get("difficulty") if isinstance(existing_entry.get("difficulty"), str) else None
        if recorded_difficulty is None:
            raise CwStartEntryTruthRequiredError(page="entry.continue", fields=("difficulty",))
        _select_battle_mode(runtime, battle_mode=battle_mode)
        _enter_continue_game(runtime)
        _handle_invest_environment_flow(runtime)
        return {"mode": "continue", "difficulty": recorded_difficulty, "battle_mode": battle_mode}
    if page == "stage.boss_preview":
        recorded_mode = existing_entry.get("mode") if isinstance(existing_entry.get("mode"), str) else None
        recorded_difficulty = existing_entry.get("difficulty") if isinstance(existing_entry.get("difficulty"), str) else None
        recorded_battle_mode = existing_entry.get("battle_mode") if isinstance(existing_entry.get("battle_mode"), str) else None
        if recorded_mode is None or recorded_difficulty is None or recorded_battle_mode is None:
            raise CwStartEntryTruthRequiredError(
                page="stage.boss_preview",
                fields=("mode", "difficulty", "battle_mode"),
            )
        _consume_click_blank_prompt(runtime)
        _handle_invest_environment_flow(runtime)
        return {
            "mode": recorded_mode,
            "difficulty": recorded_difficulty,
            "battle_mode": recorded_battle_mode,
        }
    if page == "invest":
        return {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}
    raise CwStartStateError(page=page, stage=current.get("stage"))


def start_cw(
    session: SessionModel,
    *,
    mode: str,
    difficulty: str,
    battle_mode: str,
    runtime,
) -> SessionModel:
    cw_state = ensure_cw_state(session)
    existing_entry = cw_state.get("entry") if isinstance(cw_state.get("entry"), Mapping) else {}
    current = _detect_current_enter_page(runtime, session=session, preferred_mode=mode)
    if current["page"] == "invest":
        _persist_start_entry(
            session,
            mode=mode,
            difficulty=difficulty,
            battle_mode=battle_mode,
            guard_conflicts=True,
        )
        return session

    resolved_entry = _run_start_chain(
        runtime,
        current=current,
        mode=mode,
        difficulty=difficulty,
        battle_mode=battle_mode,
        existing_entry=existing_entry,
    )
    _persist_start_entry(
        session,
        mode=resolved_entry["mode"],
        difficulty=resolved_entry["difficulty"],
        battle_mode=resolved_entry["battle_mode"],
        guard_conflicts=False,
    )
    return session


def enter_cw(
    session: SessionModel,
    *,
    mode: str | None = None,
    difficulty: str | None = None,
    battle_mode: str | None = None,
    runtime=None,
) -> SessionModel:
    del mode, difficulty, battle_mode
    entry: dict[str, object] = {"page": "home"}

    if runtime is not None:
        current = _detect_current_enter_page(runtime, session=session)
        if current["page"] == "home":
            entry = {"page": "home", "already_home": True}
        elif current["page"] == "world":
            entry = _run_entry_chain(
                runtime,
                mode="ignored",
                difficulty="current",
                battle_mode="standard",
            )
        else:
            raise CwEnterStateError(page=current["page"], stage=current.get("stage"))

    cw_state = ensure_cw_state(session)
    cw_state["entry"] = entry
    _invalidate_entry_snapshots(session)
    return session
