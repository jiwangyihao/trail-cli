from __future__ import annotations

from collections.abc import Mapping

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel


CW_WIDTH = 1920
CW_HEIGHT = 1080
ENTRY_UI_WAIT_TIMEOUT = 10
LOWEST_DIFFICULTY_MAX_CLICKS = 10
ENTRY_GUIDE_HOTKEY = "f4"
CURRENCY_WARS_ENTRY_POINT = (int(CW_WIDTH * 0.242), int(CW_HEIGHT * 0.30))
CURRENCY_WARS_PARTICIPATE_POINT = (int(CW_WIDTH * 0.7786), int(CW_HEIGHT * 0.8194))
STANDARD_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.2315))
OVERCLOCK_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.4167))


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


def _locate(runtime, alias: str):
    return runtime.locate(_asset(alias))


def _wait(runtime, alias: str):
    box = runtime.wait_img(_asset(alias), timeout=ENTRY_UI_WAIT_TIMEOUT)
    if box is None:
        raise TrailError("CW_ENTRY_UI_NOT_FOUND", f"未识别到货币战争界面元素: {alias}")
    return box


def _invalidate_stage(session: SessionModel) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None


def _invalidate_entry_snapshots(session: SessionModel) -> None:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    cw_state["sell_plan"] = {"stale": True}
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


def _enter_from_world(runtime, *, mode: str, difficulty: str, battle_mode: str) -> None:
    runtime.press_key(ENTRY_GUIDE_HOTKEY)
    _wait(runtime, "entry.menu")
    _click_box_center(runtime, _wait(runtime, "entry.cosmic_strife"))
    runtime.click_point(*CURRENCY_WARS_ENTRY_POINT)
    runtime.click_point(*CURRENCY_WARS_PARTICIPATE_POINT)
    start_box = _wait(runtime, "entry.start")
    _enter_from_start_page(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode, start_box=start_box)


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
    if _locate(runtime, "stage.preparation") is not None:
        return

    start_box = _locate(runtime, "entry.start")
    if start_box is not None:
        _enter_from_start_page(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode, start_box=start_box)
        return

    if _locate(runtime, "entry.new") is not None or _locate(runtime, "entry.continue") is not None:
        if mode == "new":
            _enter_new_game(runtime, difficulty=difficulty)
        else:
            _enter_continue_game(runtime)
        return

    if _locate(runtime, "stage.invest") is not None:
        return

    if _locate(runtime, "entry.invest_environment") is not None:
        return

    _enter_from_world(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode)


def enter_cw(
    session: SessionModel,
    *,
    mode: str,
    difficulty: str | None = None,
    battle_mode: str | None = None,
    runtime=None,
) -> SessionModel:
    entry = {
        "mode": mode,
        "difficulty": difficulty or "current",
        "battle_mode": battle_mode or "standard",
    }

    if runtime is not None:
        _run_entry_chain(
            runtime,
            mode=entry["mode"],
            difficulty=entry["difficulty"],
            battle_mode=entry["battle_mode"],
        )

    cw_state = ensure_cw_state(session)
    cw_state["entry"] = entry
    _invalidate_entry_snapshots(session)
    return session
