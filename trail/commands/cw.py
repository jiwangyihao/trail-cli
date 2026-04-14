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
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.scenes.cw.entry import enter_cw
from trail.scenes.cw.events import (
    build_cw_battle_continuer,
    build_cw_battle_starter,
    build_cw_boss_preview_confirmer,
    build_cw_encounter_chooser,
    build_cw_event_handler,
    build_cw_fortune_chooser,
    build_cw_invest_chooser,
    build_cw_replenish_chooser,
    build_cw_settle_continuer,
    choose_cw_encounter,
    choose_cw_fortune,
    choose_cw_invest,
    choose_cw_replenish,
    confirm_cw_boss_preview,
    continue_cw_battle,
    handle_cw_event,
    read_cw_encounter,
    read_cw_fortune,
    read_cw_invest,
    read_cw_replenish,
    settle_cw_next,
    start_cw_battle,
)
from trail.scenes.cw.guide import apply_cw_guide, apply_cw_guide_via_ui, fetch_cw_guide, fetch_cw_guide_payload
from trail.scenes.cw.shop import (
    build_cw_shop_buyer,
    build_cw_shop_closer,
    build_cw_shop_opener,
    build_cw_shop_refresher,
    build_cw_shop_scanner,
    buy_cw_shop_slot,
    close_cw_shop,
    open_cw_shop,
    refresh_cw_shop,
    scan_cw_shop,
    shop_cw_status,
)
from trail.scenes.cw.slots import (
    build_cw_crystal_collector,
    build_cw_hand_seller,
    build_cw_slot_swapper,
    build_cw_slots_reader,
    collect_cw_crystals,
    place_one_cw_slot,
    plan_cw_hand_sell,
    read_cw_slots,
    sell_one_cw_hand,
    swap_cw_slots,
)
from trail.scenes.cw.stage import build_cw_stage_detector, detect_cw_stage, wait_cw_stage


runtime_factory = build_default_runtime
session_store_factory = build_default_session_store
artifact_store_factory = build_default_artifact_store
stage_detector_factory = build_cw_stage_detector
slots_reader_factory = build_cw_slots_reader
slot_swapper_factory = build_cw_slot_swapper
slot_placer_factory = build_cw_slot_swapper
hand_seller_factory = build_cw_hand_seller
crystal_collector_factory = build_cw_crystal_collector
shop_opener_factory = build_cw_shop_opener
shop_scanner_factory = build_cw_shop_scanner
shop_buyer_factory = build_cw_shop_buyer
shop_refresher_factory = build_cw_shop_refresher
shop_closer_factory = build_cw_shop_closer
replenish_chooser_factory = build_cw_replenish_chooser
invest_chooser_factory = build_cw_invest_chooser
encounter_chooser_factory = build_cw_encounter_chooser
fortune_chooser_factory = build_cw_fortune_chooser
event_handler_factory = build_cw_event_handler
boss_preview_confirmer_factory = build_cw_boss_preview_confirmer
battle_starter_factory = build_cw_battle_starter
battle_continuer_factory = build_cw_battle_continuer
settle_continuer_factory = build_cw_settle_continuer
DEFAULT_CW_STAGE_WAIT_TIMEOUT = 120

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


def _run(session_id: str, command_name: str, action, *, runtime=None, failure_persistence=None) -> None:
    print_json(
        run_session_command(
            store=session_store_factory(),
            session_id=session_id,
            command_name=command_name,
            action=action,
            runtime=runtime,
            runtime_factory=runtime_factory,
            failure_persistence=failure_persistence,
        )
    )


def _should_persist_stage_failure(working_session, result: dict) -> bool:
    if result.get("ok"):
        return True

    error = result.get("error")
    stage_state = working_session.scene_state.get("cw", {}).get("stage")
    if not isinstance(error, dict) or not isinstance(stage_state, dict):
        return False

    stage_error = stage_state.get("error")
    return isinstance(stage_error, dict) and stage_error.get("code") == error.get("code")


@cw_app.command("enter")
def cw_enter(
    session: str = typer.Option(..., "--session"),
    mode: EnterMode = typer.Option(..., "--mode"),
    difficulty: EnterDifficulty = typer.Option(EnterDifficulty.CURRENT, "--difficulty"),
    battle_mode: BattleMode = typer.Option(BattleMode.STANDARD, "--battle-mode"),
) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = enter_cw(
            loaded,
            mode=mode.value,
            difficulty=difficulty.value,
            battle_mode=battle_mode.value,
            runtime=runtime,
        )
        return refreshed.scene_state["cw"]["entry"]

    _run(session, "cw.enter", action, runtime=runtime)


@cw_guide_app.command("apply")
def cw_guide_apply(
    session: str = typer.Option(..., "--session"),
    guide: str = typer.Option(..., "--lineup-id", "--guide"),
) -> None:
    """在游戏内应用指定攻略。优先使用 lineup_id 作为统一输入心智模型。"""

    runtime = _runtime_for_session(session)

    def action(loaded):
        guide_data = fetch_cw_guide(guide, fetcher=fetch_cw_guide_payload)
        apply_cw_guide_via_ui(runtime, share_code=guide_data["share_code"])
        artifact = artifact_store_factory().create(scene="cw", kind="guide", payload=guide_data)
        refreshed = apply_cw_guide(loaded, guide_data={**guide_data, "artifact_id": artifact.artifact_id})
        return refreshed.scene_state["cw"]["guide"]

    _run(session, "cw.guide.apply", action, runtime=runtime)


@cw_guide_app.command("current")
def cw_guide_current(session: str = typer.Option(..., "--session")) -> None:
    """回顾当前 session 里已应用的攻略细节，不触发网络请求。"""

    store = session_store_factory()

    def action() -> dict | None:
        try:
            loaded = store.load(session)
        except FileNotFoundError as exc:
            raise TrailError("SESSION_NOT_FOUND", f"session not found: {session}") from exc
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise TrailError("SESSION_INVALID", f"session invalid: {session}") from exc
        guide_state = loaded.scene_state.get("cw", {}).get("guide")
        return guide_state if isinstance(guide_state, dict) else None

    print_json(with_auto_capture(None, action))


@stage_app.command("detect")
def cw_stage_detect(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = detect_cw_stage(loaded, detector=stage_detector_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(
        session,
        "cw.stage.detect",
        action,
        runtime=runtime,
        failure_persistence=_should_persist_stage_failure,
    )


@stage_app.command("wait")
def cw_stage_wait(
    session: str = typer.Option(..., "--session"),
    timeout: int = typer.Option(DEFAULT_CW_STAGE_WAIT_TIMEOUT, "--timeout"),
) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = wait_cw_stage(loaded, detector=stage_detector_factory(runtime), timeout=timeout)
        return refreshed.scene_state["cw"]["stage"]

    _run(
        session,
        "cw.stage.wait",
        action,
        runtime=runtime,
        failure_persistence=_should_persist_stage_failure,
    )


@slots_app.command("read")
def cw_slots_read(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = read_cw_slots(loaded, reader=slots_reader_factory(runtime))
        return refreshed.scene_state["cw"]["slots"]

    _run(session, "cw.slots.read", action, runtime=runtime)


@slots_app.command("swap")
def cw_slots_swap(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = swap_cw_slots(loaded, source=source, target=target, swapper=slot_swapper_factory(runtime))
        return refreshed.scene_state["cw"]["slots"]

    _run(session, "cw.slots.swap", action, runtime=runtime)


@slots_app.command("place-one")
def cw_slots_place_one(
    session: str = typer.Option(..., "--session"),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = place_one_cw_slot(loaded, source=source, target=target, placer=slot_placer_factory(runtime))
        return refreshed.scene_state["cw"]["slots"]

    _run(session, "cw.slots.place-one", action, runtime=runtime)


@crystals_app.command("collect")
def cw_crystals_collect(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = collect_cw_crystals(loaded, collector=crystal_collector_factory(runtime))
        return refreshed.scene_state["cw"]["metrics"]

    _run(session, "cw.crystals.collect", action, runtime=runtime)


@hand_app.command("sell-one")
def cw_hand_sell_one(session: str = typer.Option(..., "--session"), slot: int = typer.Option(..., "--slot")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = sell_one_cw_hand(loaded, slot=slot, seller=hand_seller_factory(runtime))
        return refreshed.scene_state["cw"]["slots"]

    _run(session, "cw.hand.sell-one", action, runtime=runtime)


@hand_app.command("sell-plan")
def cw_hand_sell_plan(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.hand.sell-plan", lambda loaded: plan_cw_hand_sell(loaded))


@shop_app.command("open")
def cw_shop_open(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = open_cw_shop(loaded, opener=shop_opener_factory(runtime))
        return refreshed.scene_state["cw"]["shop"]

    _run(session, "cw.shop.open", action, runtime=runtime)


@shop_app.command("scan")
def cw_shop_scan(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = scan_cw_shop(loaded, scanner=shop_scanner_factory(runtime))
        return refreshed.scene_state["cw"]["shop"]

    _run(session, "cw.shop.scan", action, runtime=runtime)


@shop_app.command("buy-slot")
def cw_shop_buy_slot(
    session: str = typer.Option(..., "--session"),
    slot: int = typer.Option(..., "--slot"),
    expect: str = typer.Option(..., "--expect"),
) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = buy_cw_shop_slot(
            loaded,
            slot=slot,
            expect=expect,
            buyer=shop_buyer_factory(runtime),
            scanner=shop_scanner_factory(runtime),
        )
        return refreshed.scene_state["cw"]["shop"]

    _run(session, "cw.shop.buy-slot", action, runtime=runtime)


@shop_app.command("refresh")
def cw_shop_refresh(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = refresh_cw_shop(loaded, refresher=shop_refresher_factory(runtime))
        return refreshed.scene_state["cw"]["shop"]

    _run(session, "cw.shop.refresh", action, runtime=runtime)


@shop_app.command("close")
def cw_shop_close(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = close_cw_shop(loaded, closer=shop_closer_factory(runtime))
        return refreshed.scene_state["cw"]["shop"]

    _run(session, "cw.shop.close", action, runtime=runtime)


@shop_app.command("status")
def cw_shop_status(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.shop.status", lambda loaded: shop_cw_status(loaded))


@replenish_app.command("read")
def cw_replenish_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.replenish.read", read_cw_replenish)


@replenish_app.command("choose")
def cw_replenish_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = choose_cw_replenish(loaded, option=option, chooser=replenish_chooser_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.replenish.choose", action, runtime=runtime)


@invest_app.command("read")
def cw_invest_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.invest.read", read_cw_invest)


@invest_app.command("choose")
def cw_invest_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = choose_cw_invest(loaded, option=option, chooser=invest_chooser_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.invest.choose", action, runtime=runtime)


@encounter_app.command("read")
def cw_encounter_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.encounter.read", read_cw_encounter)


@encounter_app.command("choose")
def cw_encounter_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = choose_cw_encounter(loaded, option=option, chooser=encounter_chooser_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.encounter.choose", action, runtime=runtime)


@fortune_app.command("read")
def cw_fortune_read(session: str = typer.Option(..., "--session")) -> None:
    _run(session, "cw.fortune.read", read_cw_fortune)


@fortune_app.command("choose")
def cw_fortune_choose(session: str = typer.Option(..., "--session"), option: int = typer.Option(..., "--option")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = choose_cw_fortune(loaded, option=option, chooser=fortune_chooser_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.fortune.choose", action, runtime=runtime)


@boss_preview_app.command("confirm")
def cw_boss_preview_confirm(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = confirm_cw_boss_preview(loaded, confirmer=boss_preview_confirmer_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.boss-preview.confirm", action, runtime=runtime)


@battle_app.command("start")
def cw_battle_start(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = start_cw_battle(loaded, starter=battle_starter_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.battle.start", action, runtime=runtime)


@battle_app.command("continue")
def cw_battle_continue(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = continue_cw_battle(loaded, continuer=battle_continuer_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.battle.continue", action, runtime=runtime)


@settle_app.command("next")
def cw_settle_next(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        refreshed = settle_cw_next(loaded, continuer=settle_continuer_factory(runtime))
        return refreshed.scene_state["cw"]["stage"]

    _run(session, "cw.settle.next", action, runtime=runtime)


@event_app.command("handle")
def cw_event_handle(session: str = typer.Option(..., "--session")) -> None:
    runtime = _runtime_for_session(session)

    def action(loaded):
        return handle_cw_event(loaded, handler=event_handler_factory(runtime))

    _run(session, "cw.event.handle", action, runtime=runtime)
