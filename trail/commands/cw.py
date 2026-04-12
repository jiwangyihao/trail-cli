from __future__ import annotations

import json
from enum import StrEnum

import typer

from trail.commands.helpers import (
    DEFAULT_WINDOW_TITLE,
    build_default_artifact_store,
    build_default_session_store,
    build_default_runtime,
    print_json,
    run_session_command,
)
from trail.scenes.cw.entry import enter_cw
from trail.scenes.cw.guide import apply_cw_guide, resolve_guide_input
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import build_cw_stage_detector, detect_cw_stage, wait_cw_stage


runtime_factory = build_default_runtime
session_store_factory = build_default_session_store
artifact_store_factory = build_default_artifact_store
stage_detector_factory = build_cw_stage_detector

cw_app = typer.Typer(no_args_is_help=True)
cw_guide_app = typer.Typer(no_args_is_help=True)
stage_app = typer.Typer(no_args_is_help=True)
slots_app = typer.Typer(no_args_is_help=True)
shop_app = typer.Typer(no_args_is_help=True)
crystals_app = typer.Typer(no_args_is_help=True)
hand_app = typer.Typer(no_args_is_help=True)
replenish_app = typer.Typer(no_args_is_help=True)
invest_app = typer.Typer(no_args_is_help=True)
encounter_app = typer.Typer(no_args_is_help=True)
fortune_app = typer.Typer(no_args_is_help=True)
boss_preview_app = typer.Typer(no_args_is_help=True)
battle_app = typer.Typer(no_args_is_help=True)
settle_app = typer.Typer(no_args_is_help=True)
event_app = typer.Typer(no_args_is_help=True)


class EnterMode(StrEnum):
    NEW = "new"
    CONTINUE = "continue"


class EnterDifficulty(StrEnum):
    LOWEST = "lowest"
    CURRENT = "current"
    HIGHEST = "highest"


class BattleMode(StrEnum):
    STANDARD = "standard"
    OVERCLOCK = "overclock"

cw_app.add_typer(cw_guide_app, name="guide")
cw_app.add_typer(stage_app, name="stage")
cw_app.add_typer(slots_app, name="slots")
cw_app.add_typer(shop_app, name="shop")
cw_app.add_typer(crystals_app, name="crystals")
cw_app.add_typer(hand_app, name="hand")
cw_app.add_typer(replenish_app, name="replenish")
cw_app.add_typer(invest_app, name="invest")
cw_app.add_typer(encounter_app, name="encounter")
cw_app.add_typer(fortune_app, name="fortune")
cw_app.add_typer(boss_preview_app, name="boss-preview")
cw_app.add_typer(battle_app, name="battle")
cw_app.add_typer(settle_app, name="settle")
cw_app.add_typer(event_app, name="event")

def _runtime_for_session(session_id: str):
    store = session_store_factory()
    try:
        session = store.load(session_id)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None

    window_binding = session.window_binding
    window_title = DEFAULT_WINDOW_TITLE
    if isinstance(window_binding, dict):
        maybe_title = window_binding.get("title")
        if isinstance(maybe_title, str) and maybe_title:
            window_title = maybe_title
    return runtime_factory(window_title=window_title, window_binding=window_binding)


def _run(session_id: str, command_name: str, action, *, runtime=None) -> None:
    print_json(
        run_session_command(
            store=session_store_factory(),
            session_id=session_id,
            command_name=command_name,
            action=action,
            runtime=runtime,
            runtime_factory=runtime_factory,
        )
    )


@cw_app.command("enter")
def cw_enter(
    session: str = typer.Option(..., "--session"),
    mode: EnterMode = typer.Option(..., "--mode"),
    difficulty: EnterDifficulty = typer.Option(EnterDifficulty.CURRENT, "--difficulty"),
    battle_mode: BattleMode = typer.Option(BattleMode.STANDARD, "--battle-mode"),
) -> None:
    def action(loaded):
        refreshed = enter_cw(
            loaded,
            mode=mode.value,
            difficulty=difficulty.value,
            battle_mode=battle_mode.value,
        )
        return refreshed.scene_state["cw"]["entry"]

    _run(session, "cw.enter", action)


@cw_guide_app.command("apply")
def cw_guide_apply(session: str = typer.Option(..., "--session"), guide: str = typer.Option(..., "--guide")) -> None:
    def action(loaded):
        guide_data = resolve_guide_input(guide, artifact_store=artifact_store_factory())
        refreshed = apply_cw_guide(loaded, guide_data=guide_data)
        return refreshed.scene_state["cw"]["guide"]

    _run(session, "cw.guide.apply", action)


@stage_app.command("detect")
def cw_stage_detect(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = detect_cw_stage(loaded, detector=stage_detector_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.stage.detect", action, runtime=runtime)


@stage_app.command("wait")
def cw_stage_wait(session: str = typer.Option(..., "--session"), timeout: int = typer.Option(30, "--timeout")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = wait_cw_stage(loaded, detector=stage_detector_factory(runtime), timeout=timeout)
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.stage.wait", action, runtime=runtime)


@slots_app.command("read")
def cw_slots_read(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["slots"] = {"front": [], "back": [], "hand": [], "stale": False, "status": "stub"}
        return state["slots"]

    _run(session, "cw.slots.read", action)


@slots_app.command("swap")
def cw_slots_swap(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["slots"] = {"stale": True, "last_move": {"source": source, "target": target}, "status": "stub"}
        return state["slots"]

    _run(session, "cw.slots.swap", action)


@slots_app.command("place-one")
def cw_slots_place_one(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["slots"] = {"stale": True, "placed": {"source": source, "target": target}, "status": "stub"}
        return state["slots"]

    _run(session, "cw.slots.place-one", action)


@crystals_app.command("collect")
def cw_crystals_collect(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["metrics"]["crystals_collected"] = True
        return state["metrics"]

    _run(session, "cw.crystals.collect", action)


@hand_app.command("sell-one")
def cw_hand_sell_one(session: str = typer.Option(..., "--session"), slot: int = typer.Option(..., "--slot")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["slots"] = {"stale": True, "sold_slot": slot, "status": "stub"}
        return state["slots"]

    _run(session, "cw.hand.sell-one", action)


@hand_app.command("sell-plan")
def cw_hand_sell_plan(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.hand.sell-plan", lambda loaded: {"recommendations": [], "status": "stub"})


@shop_app.command("open")
def cw_shop_open(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["shop"] = {"open": True, "stale": False, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.open", action)


@shop_app.command("scan")
def cw_shop_scan(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["shop"] = {
            "open": True,
            "items": [],
            "coins": None,
            "level": None,
            "reserve_full": False,
            "max_team_size": None,
            "stale": False,
            "status": "stub",
        }
        return state["shop"]

    _run(session, "cw.shop.scan", action)


@shop_app.command("buy-slot")
def cw_shop_buy_slot(
    session: str = typer.Option(..., "--session"),
    slot: int = typer.Option(..., "--slot"),
    expect: str = typer.Option(..., "--expect"),
) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["shop"] = {"slot": slot, "expect": expect, "purchased": False, "stale": False, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.buy-slot", action)


@shop_app.command("refresh")
def cw_shop_refresh(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["shop"] = {"stale": True, "refreshed": True, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.refresh", action)


@shop_app.command("close")
def cw_shop_close(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        state["shop"] = {"open": False, "stale": True, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.close", action)


@shop_app.command("status")
def cw_shop_status(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = ensure_cw_state(loaded)
        return state["shop"]

    _run(session, "cw.shop.status", action)


@replenish_app.command("read")
def cw_replenish_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.replenish.read", lambda loaded: {"options": [], "status": "stub"})


@replenish_app.command("choose")
def cw_replenish_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _run(session, "cw.replenish.choose", lambda loaded: {"option": option, "status": "stub"})


@invest_app.command("read")
def cw_invest_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.invest.read", lambda loaded: {"options": [], "status": "stub"})


@invest_app.command("choose")
def cw_invest_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _run(session, "cw.invest.choose", lambda loaded: {"option": option, "status": "stub"})


@encounter_app.command("read")
def cw_encounter_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.encounter.read", lambda loaded: {"options": [], "status": "stub"})


@encounter_app.command("choose")
def cw_encounter_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _run(session, "cw.encounter.choose", lambda loaded: {"option": option, "status": "stub"})


@fortune_app.command("read")
def cw_fortune_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.fortune.read", lambda loaded: {"options": [], "status": "stub"})


@fortune_app.command("choose")
def cw_fortune_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _run(session, "cw.fortune.choose", lambda loaded: {"option": option, "status": "stub"})


@boss_preview_app.command("confirm")
def cw_boss_preview_confirm(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.boss-preview.confirm", lambda loaded: {"confirmed": True, "status": "stub"})


@battle_app.command("start")
def cw_battle_start(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.battle.start", lambda loaded: {"started": True, "status": "stub"})


@battle_app.command("continue")
def cw_battle_continue(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.battle.continue", lambda loaded: {"continued": True, "status": "stub"})


@settle_app.command("next")
def cw_settle_next(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.settle.next", lambda loaded: {"next": True, "status": "stub"})


@event_app.command("handle")
def cw_event_handle(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.event.handle", lambda loaded: {"event_type": "unknown", "handled_action": "noop", "status": "stub"})
