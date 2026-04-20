from __future__ import annotations

from enum import StrEnum

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


DEFAULT_CW_STAGE_WAIT_TIMEOUT = 120

cw_app = typer.Typer(no_args_is_help=True)
cw_guide_app = typer.Typer(no_args_is_help=True)
portal_app = typer.Typer(no_args_is_help=True)
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
cw_app.add_typer(portal_app, name="portal")
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


def _rpc_cw(method: str, *, session_id: str, payload: dict | None = None) -> dict:
    return call_daemon(method, {"session_id": session_id, **(payload or {})}, session_id=session_id)


def _print_cw(method: str, *, session_id: str, payload: dict | None = None) -> None:
    print_output(method, _rpc_cw(method, session_id=session_id, payload=payload))


@cw_app.command("enter")
def cw_enter(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.enter", session_id=session)


@cw_app.command("start")
def cw_start(
    session: str = typer.Option(..., "--session"),
    mode: EnterMode = typer.Option(..., "--mode"),
    difficulty: EnterDifficulty = typer.Option(EnterDifficulty.CURRENT, "--difficulty"),
    battle_mode: BattleMode = typer.Option(BattleMode.STANDARD, "--battle-mode"),
) -> None:
    _print_cw(
        "cw.start",
        session_id=session,
        payload={
            "mode": mode.value,
            "difficulty": difficulty.value,
            "battle_mode": battle_mode.value,
        },
    )


@portal_app.command("select")
def cw_portal_select(
    session: str = typer.Option(..., "--session"),
    card_idx: int = typer.Option(..., "--card-idx"),
) -> None:
    _print_cw("cw.portal.select", session_id=session, payload={"card_idx": card_idx})


@portal_app.command("refresh")
def cw_portal_refresh(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.portal.refresh", session_id=session)


@portal_app.command("restart")
def cw_portal_restart(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.portal.restart", session_id=session)


@cw_guide_app.command("apply")
def cw_guide_apply(
    session: str = typer.Option(..., "--session"),
    guide: str = typer.Option(..., "--lineup-id", "--guide"),
) -> None:
    _print_cw("cw.guide.apply", session_id=session, payload={"lineup_id": guide})


@cw_guide_app.command("current")
def cw_guide_current(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.guide.current", session_id=session)


@stage_app.command("detect")
def cw_stage_detect(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.stage.detect", session_id=session)


@stage_app.command("wait")
def cw_stage_wait(
    session: str = typer.Option(..., "--session"),
    timeout: int = typer.Option(DEFAULT_CW_STAGE_WAIT_TIMEOUT, "--timeout"),
) -> None:
    _print_cw("cw.stage.wait", session_id=session, payload={"timeout": timeout})


@slots_app.command("read")
def cw_slots_read(
    session: str = typer.Option(..., "--session"),
    slot: list[str] | None = typer.Option(None, "--slot"),
) -> None:
    _print_cw("cw.slots.read", session_id=session, payload={"slot": list(slot or []) or None})


@slots_app.command("swap")
def cw_slots_swap(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    _print_cw("cw.slots.swap", session_id=session, payload={"source": source, "target": target})


@slots_app.command("place-one")
def cw_slots_place_one(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    _print_cw("cw.slots.place_one", session_id=session, payload={"source": source, "target": target})


@crystals_app.command("collect")
def cw_crystals_collect(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.crystals.collect", session_id=session)


@hand_app.command("sell-one")
def cw_hand_sell_one(session: str = typer.Option(..., "--session"), slot: int = typer.Option(..., "--slot")) -> None:
    _print_cw("cw.hand.sell_one", session_id=session, payload={"slot": slot})


@hand_app.command("sell-plan")
def cw_hand_sell_plan(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.hand.sell_plan", session_id=session)


@shop_app.command("open")
def cw_shop_open(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.open", session_id=session)


@shop_app.command("scan")
def cw_shop_scan(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.scan", session_id=session)


@shop_app.command("buy-slot")
def cw_shop_buy_slot(
    session: str = typer.Option(..., "--session"),
    slot: int = typer.Option(..., "--slot"),
    expect: str = typer.Option(..., "--expect"),
) -> None:
    _print_cw("cw.shop.buy_slot", session_id=session, payload={"slot": slot, "expect": expect})


@shop_app.command("refresh")
def cw_shop_refresh(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.refresh", session_id=session)


@shop_app.command("close")
def cw_shop_close(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.close", session_id=session)


@shop_app.command("status")
def cw_shop_status(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.status", session_id=session)


@replenish_app.command("read")
def cw_replenish_read(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.replenish.read", session_id=session)


@replenish_app.command("choose")
def cw_replenish_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _print_cw("cw.replenish.choose", session_id=session, payload={"option": option})


@invest_app.command("read")
def cw_invest_read(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.invest.read", session_id=session)


@invest_app.command("choose")
def cw_invest_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _print_cw("cw.invest.choose", session_id=session, payload={"option": option})


@encounter_app.command("read")
def cw_encounter_read(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.encounter.read", session_id=session)


@encounter_app.command("choose")
def cw_encounter_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _print_cw("cw.encounter.choose", session_id=session, payload={"option": option})


@fortune_app.command("read")
def cw_fortune_read(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.fortune.read", session_id=session)


@fortune_app.command("choose")
def cw_fortune_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    _print_cw("cw.fortune.choose", session_id=session, payload={"option": option})


@boss_preview_app.command("confirm")
def cw_boss_preview_confirm(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.boss_preview.confirm", session_id=session)


@battle_app.command("start")
def cw_battle_start(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.battle.start", session_id=session)


@battle_app.command("continue")
def cw_battle_continue(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.battle.continue", session_id=session)


@settle_app.command("next")
def cw_settle_next(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.settle.next", session_id=session)


@event_app.command("handle")
def cw_event_handle(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.event.handle", session_id=session)
