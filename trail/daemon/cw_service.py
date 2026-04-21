from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import sleep

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.daemon.command_service import PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted
from trail.output.capture import with_auto_capture, with_selective_capture
from trail.scenes.cw.entry import enter_cw, start_cw
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
from trail.scenes.cw.guide import (
    apply_cw_guide,
    apply_cw_guide_via_ui,
    fetch_cw_guide,
    fetch_cw_guide_list,
    fetch_cw_guide_payload,
    recover_cw_guide_from_latest_artifact,
)
from trail.scenes.cw.guide import fetch_cw_guide_config
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.portal import (
    detect_portal_collection_matches,
    refresh_cw_portal,
    restart_cw_portal_to_settlement_entry,
    select_cw_portal,
    summarize_portal_cards,
    wait_cw_portal_in_game,
)
from trail.scenes.cw.shop import (
    build_cw_shop_buyer,
    build_cw_shop_closer,
    build_cw_shop_opener,
    build_cw_shop_refresher,
    build_cw_shop_scan_snapshot_reader,
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
shop_scan_snapshot_reader_factory = build_cw_shop_scan_snapshot_reader
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
PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS = 2.0


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
            try:
                result = attribute(*args, **kwargs)
            except TrailError as error:
                if getattr(error, "completed_after_side_effect", False):
                    self._tracker.mark_applied()
                raise
            except Exception:
                # Input backends can raise after the UI has already reacted.
                self._tracker.mark_applied()
                raise
            self._tracker.mark_applied()
            return result

        return wrapped


class _RequestScopedCaptureRuntime:
    def __init__(self, runtime, request_id: str, *, extra_delay_seconds: float = 0.0):
        self._runtime = runtime
        self._request_id = request_id
        self._extra_delay_seconds = extra_delay_seconds

    def capture_after_action(self, optional: bool = False):
        if self._extra_delay_seconds > 0:
            sleep(self._extra_delay_seconds)
        return self._runtime.capture_after_action(optional=optional, request_id=self._request_id)

    def __getattr__(self, name: str):
        return getattr(self._runtime, name)


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

    def handle_with_capture(
        self,
        *,
        method: str,
        payload: dict,
        workspace_root: str,
        session_service,
        request_id: str,
        verbose: bool = False,
    ) -> dict | None:
        session, _, runtime, handlers, _ = self._context(
            method=method,
            payload=payload,
            workspace_root=workspace_root,
            session_service=session_service,
        )

        capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id)
        return with_selective_capture(
            capture_runtime,
            lambda: _handle_and_save_session(handlers[method], session_service, session),
            verbose=verbose,
        )

    def handle_mutation(
        self,
        *,
        method: str,
        payload: dict,
        workspace_root: str,
        session_service,
        request_id: str,
        verbose: bool = False,
    ) -> dict | None:
        session, _, runtime, handlers, tracker = self._context(
            method=method,
            payload=payload,
            workspace_root=workspace_root,
            session_service=session_service,
            track_side_effects=True,
        )

        try:
            result = handlers[method]()
        except CwSideEffectAppliedError as error:
            raise SideEffectAppliedButStateNotPersisted(
                _unknown_result_envelope(error, last_known_stage="side_effect_applied")
            ) from error
        except Exception as error:
            if tracker.side_effect_applied or getattr(error, "completed_after_side_effect", False):
                raise SideEffectAppliedButStateNotPersisted(
                    _unknown_result_envelope(error, last_known_stage="side_effect_applied")
                ) from error
            raise

        try:
            session_service.save_session(session)
        except Exception as error:
            raise SideEffectAppliedButStateNotPersisted(
                _unknown_result_envelope(error, last_known_stage="side_effect_applied")
            ) from error

        extra_delay_seconds = PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS if method == "cw.portal.select" else 0.0
        capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id, extra_delay_seconds=extra_delay_seconds)
        try:
            return with_auto_capture(capture_runtime, lambda: result, verbose=verbose)
        except Exception as error:
            screenshot = _safe_capture_after_action(capture_runtime)
            raise PersistedButResponseUnknown(
                _unknown_result_envelope(
                    error,
                    last_known_stage="state_persisted",
                    screenshot=screenshot,
                    warnings=_safe_collect_warnings(capture_runtime),
                    references=_safe_match_references(capture_runtime, screenshot=screenshot),
                )
            ) from error

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
                    setattr(resolved_runtime, "raise_post_input_foreground_error", True)
                    resolved_runtime = _SideEffectTrackingRuntime(resolved_runtime, tracker)
                runtime_holder["runtime"] = resolved_runtime
            return runtime_holder["runtime"]

        def validated_enter_payload() -> dict:
            if any(key in payload for key in ("mode", "difficulty", "battle_mode")):
                raise TrailError(
                    "CW_ENTER_ARGS_NOT_SUPPORTED",
                    "cw enter no longer accepts mode/difficulty/battle_mode; use cw start",
                )
            return payload

        def validated_start_payload() -> tuple[str, str, str]:
            mode = payload.get("mode")
            difficulty = payload.get("difficulty")
            battle_mode = payload.get("battle_mode")
            if not isinstance(mode, str) or not isinstance(difficulty, str) or not isinstance(battle_mode, str):
                raise TrailError("CW_START_ARGS_REQUIRED", "cw start requires mode/difficulty/battle_mode")
            if mode not in {"new", "continue"}:
                raise TrailError("CW_START_MODE_INVALID", f"unsupported cw start mode: {mode}")
            if difficulty not in {"lowest", "current", "highest"}:
                raise TrailError("CW_START_DIFFICULTY_INVALID", f"unsupported cw start difficulty: {difficulty}")
            if battle_mode not in {"standard", "overclock"}:
                raise TrailError("CW_START_BATTLE_MODE_INVALID", f"unsupported cw start battle_mode: {battle_mode}")
            return mode, difficulty, battle_mode

        def run_start() -> dict:
            mode, difficulty, battle_mode = validated_start_payload()
            return _start_cw(
                session,
                runtime=runtime(),
                mode=mode,
                difficulty=difficulty,
                battle_mode=battle_mode,
            )

        handlers = {
            "cw.enter": lambda: validated_enter_payload() and enter_cw(session, runtime=runtime()).scene_state["cw"]["entry"],
            "cw.start": run_start,
            "cw.portal.select": lambda: select_cw_portal(
                session,
                card_idx=payload["card_idx"],
                runtime=runtime(),
            ),
            "cw.portal.refresh": lambda: _attach_guides_to_portal_snapshot(
                session,
                refresh_cw_portal(
                    session,
                    runtime=runtime(),
                    portal_list=fetch_cw_guide_config().get("portal_list", []),
                ),
            ),
            "cw.portal.restart": lambda: _restart_cw(session, runtime=runtime()),
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
            "cw.guide.current": lambda: _current_guide(session, artifact_store=artifact_store),
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
                scanner=shop_scan_snapshot_reader_factory(runtime()),
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
            "cw.shop.status": lambda: _shop_status(session, artifact_store=artifact_store),
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


def _handle_and_save_session(handler, session_service, session):
    result = handler()
    session_service.save_session(session)
    return result


def _current_guide(session, *, artifact_store: ArtifactStore):
    guide_state = session.scene_state.get("cw", {}).get("guide")
    if isinstance(guide_state, dict):
        return guide_state
    return recover_cw_guide_from_latest_artifact(session, artifact_store=artifact_store)


def _shop_status(session, *, artifact_store: ArtifactStore) -> dict:
    _current_guide(session, artifact_store=artifact_store)
    return shop_cw_status(session)


def _apply_guide(session, *, runtime, artifact_store: ArtifactStore, lineup_id: str):
    guide_data = fetch_cw_guide(lineup_id, fetcher=fetch_cw_guide_payload)
    apply_cw_guide_via_ui(runtime, share_code=guide_data["share_code"])
    try:
        artifact = artifact_store.create(scene="cw", kind="guide", payload=guide_data)
        refreshed = apply_cw_guide(session, guide_data={**guide_data, "artifact_id": artifact.artifact_id})
    except Exception as error:
        raise CwSideEffectAppliedError("cw.guide.apply side effect already ran") from error
    return refreshed.scene_state["cw"]["guide"]


def _start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str) -> dict:
    refreshed = start_cw(
        session,
        mode=mode,
        difficulty=difficulty,
        battle_mode=battle_mode,
        runtime=runtime,
    )
    entry_state = ensure_cw_state(refreshed).get("entry")
    cards = summarize_portal_cards(
        runtime.ocr(),
        fetch_cw_guide_config().get("portal_list", []),
        collection_matches=detect_portal_collection_matches(runtime),
    )
    portal_snapshot = {
        "cards": _attach_guides_to_cards(cards),
        "mode": entry_state.get("mode") if isinstance(entry_state, dict) else None,
        "difficulty": entry_state.get("difficulty") if isinstance(entry_state, dict) else None,
        "battle_mode": entry_state.get("battle_mode") if isinstance(entry_state, dict) else None,
        "stale": False,
    }
    ensure_cw_state(refreshed)["portal"] = portal_snapshot
    return portal_snapshot


def _restart_cw(session, *, runtime) -> dict:
    entry_state = ensure_cw_state(session).get("entry") if isinstance(ensure_cw_state(session).get("entry"), dict) else {}
    portal_state = ensure_cw_state(session).get("portal") if isinstance(ensure_cw_state(session).get("portal"), dict) else {}
    mode = entry_state.get("mode") if entry_state.get("mode") is not None else portal_state.get("mode")
    difficulty = entry_state.get("difficulty") if entry_state.get("difficulty") is not None else portal_state.get("difficulty")
    battle_mode = entry_state.get("battle_mode") if entry_state.get("battle_mode") is not None else portal_state.get("battle_mode")
    if not isinstance(mode, str) or not isinstance(difficulty, str) or not isinstance(battle_mode, str):
        raise TrailError("CW_PORTAL_ENTRY_TRUTH_REQUIRED", "cw portal.restart requires recorded mode/difficulty/battle_mode")

    select_cw_portal(session, card_idx=1, runtime=runtime)
    wait_cw_portal_in_game(session, runtime=runtime)
    restart_cw_portal_to_settlement_entry(session, runtime=runtime)
    return _start_cw(session, runtime=runtime, mode="continue", difficulty=difficulty, battle_mode=battle_mode)


def _attach_guides_to_cards(cards: list[dict[str, object]], *, timeout: int = 10) -> list[dict[str, object]]:
    portal_titles: list[str] = []
    seen_titles: set[str] = set()
    for card in cards:
        title = card.get("portal_title")
        if not isinstance(title, str) or not title or title in seen_titles:
            continue
        seen_titles.add(title)
        portal_titles.append(title)

    if not portal_titles:
        return cards

    try:
        guide_payload = fetch_cw_guide_list(
            page=1,
            limit=3,
            trait_id=None,
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
            portal=portal_titles if len(portal_titles) > 1 else portal_titles[0],
            timeout=timeout,
        )
    except TrailError:
        return cards

    guides_by_portal: dict[str, list[dict[str, object]]] = {}
    portal_groups = guide_payload.get("portals")
    if isinstance(portal_groups, list):
        for group in portal_groups:
            if not isinstance(group, dict):
                continue
            title = group.get("portal_title")
            guides = group.get("list")
            if isinstance(title, str) and isinstance(guides, list):
                guides_by_portal[title] = guides[:3]
    else:
        title = portal_titles[0]
        guides = guide_payload.get("list")
        if isinstance(guides, list):
            guides_by_portal[title] = guides[:3]

    enriched: list[dict[str, object]] = []
    for card in cards:
        enriched_card = dict(card)
        title = card.get("portal_title")
        guides = guides_by_portal.get(title) if isinstance(title, str) else None
        if guides:
            enriched_card["guides"] = guides
        enriched.append(enriched_card)
    return enriched


def _attach_guides_to_portal_snapshot(session, snapshot: dict[str, object], *, timeout: int = 10) -> dict[str, object]:
    enriched = {
        **snapshot,
        "cards": _attach_guides_to_cards(snapshot.get("cards", []), timeout=timeout),
    }
    ensure_cw_state(session)["portal"] = enriched
    return enriched


def _safe_capture_after_action(runtime) -> str | None:
    capture_after_action = getattr(runtime, "capture_after_action", None)
    if not callable(capture_after_action):
        return None
    try:
        return capture_after_action(optional=False)
    except Exception:
        return None


def _safe_match_references(runtime, *, screenshot) -> list[dict]:
    if screenshot is None:
        return []
    match_references = getattr(runtime, "match_references", None)
    if not callable(match_references):
        return []
    try:
        references = match_references(screenshot) or []
    except Exception:
        return []
    return references if isinstance(references, list) else []


def _safe_collect_warnings(runtime) -> list[dict]:
    collect_warnings = getattr(runtime, "collect_warnings", None)
    if not callable(collect_warnings):
        return []
    try:
        warnings = collect_warnings() or []
    except Exception:
        return []
    return warnings if isinstance(warnings, list) else []


def _unknown_result_envelope(
    error: Exception,
    *,
    last_known_stage: str | None = None,
    screenshot: str | None = None,
    warnings: list[dict] | None = None,
    references: list[dict] | None = None,
) -> dict:
    debug = {"detail": _format_exception_detail(error)}
    if isinstance(last_known_stage, str) and last_known_stage:
        debug["last_known_stage"] = last_known_stage
    return {
        "ok": False,
        "data": {},
        "screenshot": screenshot,
        "timing": {},
        "warnings": deepcopy(warnings or []),
        "references": deepcopy(references or []),
        "debug": debug,
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
