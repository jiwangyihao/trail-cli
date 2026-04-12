from __future__ import annotations

import typer

from trail.commands.helpers import build_default_session_store, build_default_runtime, print_json, run_session_command


runtime = build_default_runtime()
session_store = build_default_session_store()

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


def _ensure_cw_state(session) -> dict:
    return session.scene_state.setdefault(
        "cw",
        {
            "entry": None,
            "guide": {"stale": True},
            "stage": {"stale": True},
            "slots": {"stale": True},
            "shop": {"stale": True},
            "metrics": {},
        },
    )


def _run(session_id: str, command_name: str, action) -> None:
    print_json(
        run_session_command(
            store=session_store,
            session_id=session_id,
            runtime=runtime,
            command_name=command_name,
            action=action,
        )
    )


@cw_app.command("enter")
def cw_enter(
    session: str = typer.Option(..., "--session"),
    mode: str = typer.Option(..., "--mode"),
    difficulty: str = typer.Option("current", "--difficulty"),
    battle_mode: str = typer.Option("standard", "--battle-mode"),
) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["entry"] = {
            "mode": mode,
            "difficulty": difficulty,
            "battle_mode": battle_mode,
            "status": "stub",
        }
        return state["entry"]

    _run(session, "cw.enter", action)


@cw_guide_app.command("apply")
def cw_guide_apply(session: str = typer.Option(..., "--session"), guide: str = typer.Option(..., "--guide")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["guide"] = {"guide": guide, "applied": False, "status": "stub"}
        return state["guide"]

    _run(session, "cw.guide.apply", action)


@stage_app.command("detect")
def cw_stage_detect(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["stage"] = {"value": "unknown", "stale": False, "status": "stub"}
        return state["stage"]

    _run(session, "cw.stage.detect", action)


@stage_app.command("wait")
def cw_stage_wait(session: str = typer.Option(..., "--session"), timeout: int = typer.Option(30, "--timeout")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["stage"] = {"value": "unknown", "timeout": timeout, "stale": False, "status": "stub"}
        return state["stage"]

    _run(session, "cw.stage.wait", action)


@slots_app.command("read")
def cw_slots_read(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
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
        state = _ensure_cw_state(loaded)
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
        state = _ensure_cw_state(loaded)
        state["slots"] = {"stale": True, "placed": {"source": source, "target": target}, "status": "stub"}
        return state["slots"]

    _run(session, "cw.slots.place-one", action)


@crystals_app.command("collect")
def cw_crystals_collect(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["metrics"]["crystals_collected"] = True
        return state["metrics"]

    _run(session, "cw.crystals.collect", action)


@hand_app.command("sell-one")
def cw_hand_sell_one(session: str = typer.Option(..., "--session"), slot: int = typer.Option(..., "--slot")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["slots"] = {"stale": True, "sold_slot": slot, "status": "stub"}
        return state["slots"]

    _run(session, "cw.hand.sell-one", action)


@hand_app.command("sell-plan")
def cw_hand_sell_plan(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.hand.sell-plan", lambda loaded: {"recommendations": [], "status": "stub"})


@shop_app.command("open")
def cw_shop_open(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["shop"] = {"open": True, "stale": False, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.open", action)


@shop_app.command("scan")
def cw_shop_scan(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
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
        state = _ensure_cw_state(loaded)
        state["shop"] = {"slot": slot, "expect": expect, "purchased": False, "stale": False, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.buy-slot", action)


@shop_app.command("refresh")
def cw_shop_refresh(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["shop"] = {"stale": True, "refreshed": True, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.refresh", action)


@shop_app.command("close")
def cw_shop_close(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
        state["shop"] = {"open": False, "stale": True, "status": "stub"}
        return state["shop"]

    _run(session, "cw.shop.close", action)


@shop_app.command("status")
def cw_shop_status(session: str = typer.Option(..., "--session")) -> None:
    def action(loaded):
        state = _ensure_cw_state(loaded)
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
