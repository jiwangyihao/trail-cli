from __future__ import annotations

from pathlib import Path

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.daemon.command_service import SideEffectAppliedButStateNotPersisted
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


stage_detector_factory = build_cw_stage_detector
slots_reader_factory = build_cw_slots_reader
slot_swapper_factory = build_cw_slot_swapper
slot_placer_factory = build_cw_slot_swapper
hand_seller_factory = build_cw_hand_seller
crystal_collector_factory = build_cw_crystal_collector
shop_scanner_factory = build_cw_shop_scanner
shop_buyer_factory = build_cw_shop_buyer
shop_opener_factory = build_cw_shop_opener
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
SIDE_EFFECT_RUNTIME_METHODS = {"click_point", "drag_to", "press_key", "type_text"}


class _RuntimeSideEffectTracker:
    def __init__(self):
        self.side_effect_applied = False

    def mark_applied(self) -> None:
        self.side_effect_applied = True


class _SideEffectTrackingRuntime:
    def __init__(self, runtime, tracker: _RuntimeSideEffectTracker):
        self._runtime = runtime
        self._tracker = tracker

    def __getattr__(self, name: str):
        attribute = getattr(self._runtime, name)
        if not callable(attribute) or name not in SIDE_EFFECT_RUNTIME_METHODS:
            return attribute

        def wrapped(*args, **kwargs):
            result = attribute(*args, **kwargs)
            self._tracker.mark_applied()
            return result

        return wrapped


class CwService:
    def __init__(self, *, runtime_service):
        self.runtime_service = runtime_service

    def handle(self, *, method: str, payload: dict, workspace_root: str, session_service) -> dict | None:
        session, _, _, handlers, _ = self._context(
            method=method,
            payload=payload,
            workspace_root=workspace_root,
            session_service=session_service,
        )

        result = handlers[method]()
        session_service.save_session(session)
        return result

    def handle_mutation(self, *, method: str, payload: dict, workspace_root: str, session_service) -> dict | None:
        session, _, _, handlers, tracker = self._context(
            method=method,
            payload=payload,
            workspace_root=workspace_root,
            session_service=session_service,
            track_side_effects=True,
        )

        try:
            result = handlers[method]()
        except CwSideEffectAppliedError as error:
            raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(error)) from error
        except Exception as error:
            if tracker.side_effect_applied:
                raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(error)) from error
            raise

        try:
            session_service.save_session(session)
        except Exception as error:
            raise SideEffectAppliedButStateNotPersisted(_unknown_result_envelope(error)) from error

        return result

    def _context(self, *, method: str, payload: dict, workspace_root: str, session_service, track_side_effects: bool = False):
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise TrailError("SESSION_REQUIRED", f"cw method requires session: {method}")

        session = session_service.load_session(session_id)
        artifact_store = ArtifactStore(Path(workspace_root) / ".trail" / "artifacts")
        runtime_holder: dict[str, object] = {}
        tracker = _RuntimeSideEffectTracker()

        def runtime():
            if "runtime" not in runtime_holder:
                resolved_runtime = self.runtime_service.get_runtime(
                    workspace_root=workspace_root,
                    window_binding=session.window_binding,
                )
                if track_side_effects:
                    resolved_runtime = _SideEffectTrackingRuntime(resolved_runtime, tracker)
                runtime_holder["runtime"] = resolved_runtime
            return runtime_holder["runtime"]

        handlers = {
            "cw.enter": lambda: enter_cw(session, runtime=runtime()).scene_state["cw"]["entry"],
            "cw.stage.detect": lambda: detect_cw_stage(
                session,
                detector=stage_detector_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.stage.wait": lambda: wait_cw_stage(
                session,
                detector=stage_detector_factory(runtime()),
                timeout=payload.get("timeout", DEFAULT_CW_STAGE_WAIT_TIMEOUT),
            ).scene_state["cw"]["stage"],
            "cw.guide.apply": lambda: _apply_guide(
                session,
                runtime=runtime(),
                artifact_store=artifact_store,
                lineup_id=payload["lineup_id"],
            ),
            "cw.guide.current": lambda: _current_guide(session),
            "cw.slots.read": lambda: read_cw_slots(
                session,
                reader=slots_reader_factory(runtime(), targets=payload.get("slot")),
                targets=payload.get("slot"),
            ).scene_state["cw"]["slots"],
            "cw.slots.swap": lambda: swap_cw_slots(
                session,
                source=payload["source"],
                target=payload["target"],
                swapper=slot_swapper_factory(runtime()),
            ).scene_state["cw"]["slots"],
            "cw.slots.place_one": lambda: place_one_cw_slot(
                session,
                source=payload["source"],
                target=payload["target"],
                placer=slot_placer_factory(runtime()),
            ).scene_state["cw"]["slots"],
            "cw.shop.open": lambda: open_cw_shop(
                session,
                opener=shop_opener_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.scan": lambda: scan_cw_shop(
                session,
                scanner=shop_scanner_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.buy_slot": lambda: buy_cw_shop_slot(
                session,
                slot=payload["slot"],
                expect=payload["expect"],
                buyer=shop_buyer_factory(runtime()),
                scanner=shop_scanner_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.refresh": lambda: refresh_cw_shop(
                session,
                refresher=shop_refresher_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.close": lambda: close_cw_shop(
                session,
                closer=shop_closer_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.status": lambda: shop_cw_status(session),
            "cw.crystals.collect": lambda: collect_cw_crystals(
                session,
                collector=crystal_collector_factory(runtime()),
            ).scene_state["cw"]["metrics"],
            "cw.hand.sell_one": lambda: sell_one_cw_hand(
                session,
                slot=payload["slot"],
                seller=hand_seller_factory(runtime()),
            ).scene_state["cw"]["slots"],
            "cw.hand.sell_plan": lambda: plan_cw_hand_sell(session),
            "cw.replenish.read": lambda: read_cw_replenish(session),
            "cw.replenish.choose": lambda: choose_cw_replenish(
                session,
                option=payload["option"],
                chooser=replenish_chooser_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.invest.read": lambda: read_cw_invest(session),
            "cw.invest.choose": lambda: choose_cw_invest(
                session,
                option=payload["option"],
                chooser=invest_chooser_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.encounter.read": lambda: read_cw_encounter(session),
            "cw.encounter.choose": lambda: choose_cw_encounter(
                session,
                option=payload["option"],
                chooser=encounter_chooser_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.fortune.read": lambda: read_cw_fortune(session),
            "cw.fortune.choose": lambda: choose_cw_fortune(
                session,
                option=payload["option"],
                chooser=fortune_chooser_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.boss_preview.confirm": lambda: confirm_cw_boss_preview(
                session,
                confirmer=boss_preview_confirmer_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.battle.start": lambda: start_cw_battle(
                session,
                starter=battle_starter_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.battle.continue": lambda: continue_cw_battle(
                session,
                continuer=battle_continuer_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.settle.next": lambda: settle_cw_next(
                session,
                continuer=settle_continuer_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.event.handle": lambda: handle_cw_event(
                session,
                handler=event_handler_factory(runtime()),
            ),
        }
        if method not in handlers:
            raise TrailError("DAEMON_METHOD_NOT_SUPPORTED", f"unsupported method: {method}")

        return session, artifact_store, runtime, handlers, tracker


def _current_guide(session):
    guide_state = session.scene_state.get("cw", {}).get("guide")
    return guide_state if isinstance(guide_state, dict) else None


def _apply_guide(session, *, runtime, artifact_store: ArtifactStore, lineup_id: str):
    guide_data = fetch_cw_guide(lineup_id, fetcher=fetch_cw_guide_payload)
    apply_cw_guide_via_ui(runtime, share_code=guide_data["share_code"])
    try:
        artifact = artifact_store.create(scene="cw", kind="guide", payload=guide_data)
        refreshed = apply_cw_guide(session, guide_data={**guide_data, "artifact_id": artifact.artifact_id})
    except Exception as error:
        raise CwSideEffectAppliedError("cw.guide.apply side effect already ran") from error
    return refreshed.scene_state["cw"]["guide"]


def _unknown_result_envelope(error: Exception) -> dict:
    return {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"detail": _format_exception_detail(error)},
        "error": {
            "code": "DAEMON_UNAVAILABLE",
            "message": "mutation result unknown",
        },
    }


def _format_exception_detail(error: Exception) -> str:
    message = str(error)
    if not message:
        return type(error).__name__
    return f"{type(error).__name__}: {message}"


class CwSideEffectAppliedError(Exception):
    pass
