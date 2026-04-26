from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import sleep

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_detail, to_jsonable
from trail.daemon.command_timeouts import DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS, resolve_command_execution_timeout
from trail.daemon.command_service import PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted
from trail.output.capture import with_auto_capture, with_selective_capture
from trail.output.envelope import build_image_guidance
from trail.scenes.cw.battle import run_cw_battle
from trail.scenes.cw.entry import enter_cw, is_cw_exact_difficulty_token, start_cw
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
    apply_cw_guide_via_ui,
    complete_cw_guide_or_none,
    fetch_cw_guide_list,
    invalidate_cw_guide_runtime_state,
    require_complete_cw_guide,
)
from trail.scenes.cw.guide import fetch_cw_guide_config
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.portal import (
    detect_cw_portal,
    detect_portal_collection_matches,
    refresh_cw_portal,
    restart_cw_portal_to_settlement_entry,
    select_cw_portal,
    summarize_portal_cards,
    wait_cw_portal_preparation,
    wait_cw_portal_in_game,
)
from trail.scenes.cw.shop import (
    SHOP_SCAN_OPEN_SETTLE_SECONDS,
    build_cw_shop_buyer,
    build_cw_shop_closer,
    build_cw_shop_exp_buyer,
    build_cw_shop_opener,
    build_cw_shop_page_snapshot_reader,
    build_cw_shop_refresher,
    build_cw_shop_scan_snapshot_reader,
    build_cw_shop_scanner,
    buy_cw_shop_exp,
    buy_cw_shop_slot,
    close_cw_shop,
    open_cw_shop,
    project_cw_shop_snapshot,
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
    dismiss_cw_slots_overlay,
    place_cw_slots,
    plan_cw_hand_sell,
    read_cw_slots,
    sell_cw_hand_slots,
    swap_cw_slots,
)
from trail.scenes.cw.stage import build_cw_stage_detector, detect_cw_stage, wait_cw_stage
from trail.scenes.cw.strategy import detect_cw_strategy, refresh_cw_strategy, select_cw_strategy


stage_detector_factory = build_cw_stage_detector
slots_reader_factory = build_cw_slots_reader
slot_swapper_factory = build_cw_slot_swapper
slot_placer_factory = build_cw_slot_swapper
hand_seller_factory = build_cw_hand_seller
crystal_collector_factory = build_cw_crystal_collector
shop_scanner_factory = build_cw_shop_scanner
shop_scan_snapshot_reader_factory = build_cw_shop_scan_snapshot_reader
shop_page_snapshot_reader_factory = build_cw_shop_page_snapshot_reader
shop_buyer_factory = build_cw_shop_buyer
shop_exp_buyer_factory = build_cw_shop_exp_buyer
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
CW_BATTLE_START_EXTRA_CAPTURE_DELAY_SECONDS = 3.0
DEFAULT_CW_BATTLE_RUN_TIMEOUT = DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS


def _build_shop_snapshot_reader(runtime, *, read_stage_status: bool = False):
    try:
        return shop_scan_snapshot_reader_factory(runtime, read_stage_status=read_stage_status)
    except TypeError as exc:
        if "read_stage_status" not in str(exc):
            raise
        return shop_scan_snapshot_reader_factory(runtime)


def _is_valid_cw_start_difficulty(value: str) -> bool:
    return value in {"lowest", "current", "highest"} or is_cw_exact_difficulty_token(value)


def _validated_start_payload(payload: dict) -> tuple[str, str, str]:
    mode = payload.get("mode")
    difficulty = payload.get("difficulty")
    battle_mode = payload.get("battle_mode")
    if not isinstance(mode, str) or not isinstance(difficulty, str) or not isinstance(battle_mode, str):
        raise TrailError("CW_START_ARGS_REQUIRED", "cw start requires mode/difficulty/battle_mode")
    if mode not in {"new", "continue"}:
        raise TrailError("CW_START_MODE_INVALID", f"unsupported cw start mode: {mode}")
    if not _is_valid_cw_start_difficulty(difficulty):
        raise TrailError("CW_START_DIFFICULTY_INVALID", f"unsupported cw start difficulty: {difficulty}")
    if battle_mode not in {"standard", "overclock"}:
        raise TrailError("CW_START_BATTLE_MODE_INVALID", f"unsupported cw start battle_mode: {battle_mode}")
    return mode, difficulty, battle_mode


def _capture_delay_seconds_for_method(method: str) -> float:
    if method == "cw.portal.select":
        return PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS
    if method == "cw.battle.start":
        return CW_BATTLE_START_EXTRA_CAPTURE_DELAY_SECONDS
    return 0.0


def _begin_runtime_scope(runtime) -> None:
    begin_capture_scope = getattr(runtime, "begin_capture_scope", None)
    if callable(begin_capture_scope):
        begin_capture_scope()


def _end_runtime_scope(runtime) -> None:
    end_capture_scope = getattr(runtime, "end_capture_scope", None)
    if callable(end_capture_scope):
        end_capture_scope()


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
                if _safe_error_attr(error, "completed_after_side_effect"):
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
        session, _, _, handlers, _, _, _ = self._context(
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
        session, _, runtime, handlers, _, _, _ = self._context(
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
        session, _, runtime, handlers, tracker, end_runtime_scope_if_started, runtime_if_started = self._context(
            method=method,
            payload=payload,
            workspace_root=workspace_root,
            session_service=session_service,
            track_side_effects=True,
            shared_capture_scope=True,
        )

        def collect_runtime_debug(debug_runtime=None) -> dict | None:
            if not verbose:
                return None
            if debug_runtime is None:
                debug_runtime = runtime_if_started()
                if debug_runtime is None:
                    return None
            return _safe_collect_debug(debug_runtime, verbose=verbose)

        def unknown_result_envelope(
            error: Exception,
            *,
            last_known_stage: str | None = None,
            screenshot: str | None = None,
            warnings: list[dict] | None = None,
            references: list[dict] | None = None,
            debug_runtime=None,
        ) -> dict:
            return _unknown_result_envelope(
                error,
                last_known_stage=last_known_stage,
                screenshot=screenshot,
                warnings=warnings,
                references=references,
                debug=collect_runtime_debug(debug_runtime),
            )

        def attach_runtime_debug(error: Exception) -> None:
            debug = collect_runtime_debug()
            if not debug:
                return
            raw_debug = _safe_error_attr(error, "debug")
            if isinstance(raw_debug, dict):
                debug = to_jsonable({**debug, **raw_debug})
            try:
                setattr(error, "debug", debug)
            except Exception:
                return

        try:
            if method == "cw.start":
                _validated_start_payload(payload)
            try:
                result = handlers[method]()
            except CwSideEffectAppliedError as error:
                raise SideEffectAppliedButStateNotPersisted(
                    unknown_result_envelope(error, last_known_stage="side_effect_applied")
                ) from error
            except TrailError as error:
                if _safe_error_attr(error, "known_failure_after_save"):
                    try:
                        session_service.save_session(session)
                    except Exception as save_error:
                        raise SideEffectAppliedButStateNotPersisted(
                            unknown_result_envelope(save_error, last_known_stage="side_effect_applied")
                        ) from save_error

                    extra_delay_seconds = PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS if method == "cw.portal.select" else 0.0
                    capture_runtime = None
                    try:
                        capture_runtime = _RequestScopedCaptureRuntime(
                            runtime(),
                            request_id,
                            extra_delay_seconds=extra_delay_seconds,
                        )
                        response = with_auto_capture(capture_runtime, lambda: (_ for _ in ()).throw(error), verbose=verbose)
                        if isinstance(response, dict):
                            response_data = _error_data(error)
                            if response_data:
                                response["data"] = response_data
                        return response
                    except Exception as capture_error:
                        screenshot = _safe_capture_after_action(capture_runtime)
                        raise PersistedButResponseUnknown(
                            unknown_result_envelope(
                                capture_error,
                                last_known_stage="state_persisted",
                                screenshot=screenshot,
                                warnings=_safe_collect_warnings(capture_runtime),
                                references=_safe_match_references(capture_runtime, screenshot=screenshot),
                                debug_runtime=capture_runtime,
                            )
                        ) from capture_error
                if tracker.side_effect_applied or _safe_error_attr(error, "completed_after_side_effect"):
                    raise SideEffectAppliedButStateNotPersisted(
                        unknown_result_envelope(error, last_known_stage="side_effect_applied")
                    ) from error
                attach_runtime_debug(error)
                raise
            except Exception as error:
                if tracker.side_effect_applied or _safe_error_attr(error, "completed_after_side_effect"):
                    raise SideEffectAppliedButStateNotPersisted(
                        unknown_result_envelope(error, last_known_stage="side_effect_applied")
                    ) from error
                attach_runtime_debug(error)
                raise

            try:
                session_service.save_session(session)
            except Exception as error:
                raise SideEffectAppliedButStateNotPersisted(
                    unknown_result_envelope(error, last_known_stage="side_effect_applied")
                ) from error

            extra_delay_seconds = _capture_delay_seconds_for_method(method)
            capture_runtime = None
            try:
                capture_runtime = _RequestScopedCaptureRuntime(
                    runtime(),
                    request_id,
                    extra_delay_seconds=extra_delay_seconds,
                )
                return with_auto_capture(capture_runtime, lambda: result, verbose=verbose)
            except Exception as error:
                screenshot = _safe_capture_after_action(capture_runtime)
                raise PersistedButResponseUnknown(
                    unknown_result_envelope(
                        error,
                        last_known_stage="state_persisted",
                        screenshot=screenshot,
                        warnings=_safe_collect_warnings(capture_runtime),
                        references=_safe_match_references(capture_runtime, screenshot=screenshot),
                        debug_runtime=capture_runtime,
                    )
                ) from error
        finally:
            end_runtime_scope_if_started()

    def _context(
        self,
        *,
        method: str,
        payload: dict,
        workspace_root: str,
        session_service,
        track_side_effects: bool = False,
        shared_capture_scope: bool = False,
    ):
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise TrailError("SESSION_REQUIRED", f"cw method requires session: {method}")

        session = session_service.load_session(session_id)
        artifact_store = ArtifactStore(Path(workspace_root) / ".trail" / "artifacts")
        runtime_holder: dict[str, object] = {}
        scope_state = {"started": False}
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
            if shared_capture_scope and not scope_state["started"]:
                _begin_runtime_scope(runtime_holder["runtime"])
                scope_state["started"] = True
            return runtime_holder["runtime"]

        def end_runtime_scope_if_started() -> None:
            if not scope_state["started"]:
                return
            _end_runtime_scope(runtime_holder["runtime"])
            scope_state["started"] = False

        def runtime_if_started():
            return runtime_holder.get("runtime")

        def validated_enter_payload() -> dict:
            if any(key in payload for key in ("mode", "difficulty", "battle_mode")):
                raise TrailError(
                    "CW_ENTER_ARGS_NOT_SUPPORTED",
                    "cw enter no longer accepts mode/difficulty/battle_mode; use cw start",
                )
            return payload

        def run_start() -> dict:
            mode, difficulty, battle_mode = _validated_start_payload(payload)
            return _start_cw(
                session,
                runtime=runtime(),
                mode=mode,
                difficulty=difficulty,
                battle_mode=battle_mode,
                workspace_root=workspace_root,
            )

        def run_portal_select() -> dict:
            guide = _require_selected_guide(session)
            return _select_portal_and_apply_selected_guide(
                session,
                runtime=runtime(),
                card_idx=payload["card_idx"],
                guide=guide,
                workspace_root=workspace_root,
            )

        def run_guide_apply() -> dict:
            _validate_cw_guide_apply_payload(payload)
            guide = _require_selected_guide(session)
            return _apply_selected_guide_via_ui(session, runtime=runtime(), guide=guide)

        handlers = {
            "cw.enter": lambda: validated_enter_payload() and enter_cw(session, runtime=runtime()).scene_state["cw"]["entry"],
            "cw.start": run_start,
            "cw.portal.select": run_portal_select,
            "cw.portal.detect": lambda: _attach_guides_to_portal_snapshot(
                session,
                detect_cw_portal(
                    session,
                    runtime=runtime(),
                    portal_list=fetch_cw_guide_config(workspace_root=workspace_root).get("portal_list", []),
                ),
                workspace_root=workspace_root,
            ),
            "cw.portal.refresh": lambda: _attach_guides_to_portal_snapshot(
                session,
                refresh_cw_portal(
                    session,
                    runtime=runtime(),
                    portal_list=fetch_cw_guide_config(workspace_root=workspace_root).get("portal_list", []),
                ),
                workspace_root=workspace_root,
            ),
            "cw.portal.restart": lambda: _restart_cw(session, runtime=runtime()),
            "cw.strategy.detect": lambda: detect_cw_strategy(
                session,
                runtime=runtime(),
                strategy_list=fetch_cw_guide_config(workspace_root=workspace_root).get("strategy_list", []),
            ),
            "cw.strategy.select": lambda: select_cw_strategy(
                session,
                card_idx=payload["card_idx"],
                runtime=runtime(),
            ),
            "cw.strategy.refresh": lambda: refresh_cw_strategy(
                session,
                card_idx=payload["card_idx"],
                runtime=runtime(),
                strategy_list=fetch_cw_guide_config(workspace_root=workspace_root).get("strategy_list", []),
            ),
            "cw.stage.detect": lambda: detect_cw_stage(
                session,
                detector=stage_detector_factory(runtime()),
            ).scene_state["cw"]["stage"],
            "cw.stage.wait": lambda: wait_cw_stage(
                session,
                detector=stage_detector_factory(runtime()),
                timeout=payload.get("timeout", DEFAULT_CW_STAGE_WAIT_TIMEOUT),
            ).scene_state["cw"]["stage"],
            "cw.guide.apply": run_guide_apply,
            "cw.guide.current": lambda: _current_guide(session, artifact_store=artifact_store),
            "cw.slots.read": lambda: read_cw_slots(
                session,
                reader=slots_reader_factory(runtime(), targets=payload.get("slot")),
                targets=payload.get("slot"),
                guide_config=fetch_cw_guide_config(workspace_root=workspace_root),
            ).scene_state["cw"]["slots"],
            "cw.slots.swap": lambda: swap_cw_slots(
                session,
                source=payload["source"],
                target=payload["target"],
                swapper=slot_swapper_factory(runtime()),
            ).scene_state["cw"]["slots"],
            "cw.slots.place": lambda: place_cw_slots(
                session,
                actions=payload["actions"],
                placer=slot_placer_factory(runtime()),
            ).scene_state["cw"]["slots"],
            "cw.shop.open": lambda: open_cw_shop(
                session,
                opener=shop_opener_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.scan": lambda: project_cw_shop_snapshot(
                scan_cw_shop(
                    session,
                    scanner=_build_shop_snapshot_reader(runtime()),
                )
            ),
            "cw.shop.buy_slot": lambda: buy_cw_shop_slot(
                session,
                slot=payload["slot"],
                expect=payload["expect"],
                buyer=shop_buyer_factory(runtime()),
                scanner=shop_scanner_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.buy_exp": lambda: buy_cw_shop_exp(
                session,
                buyer=shop_exp_buyer_factory(runtime()),
                scanner=_build_shop_snapshot_reader(runtime(), read_stage_status=True),
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
            "cw.hand.sell": lambda: sell_cw_hand_slots(
                session,
                slots=payload["slots"],
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
            "cw.battle.run": lambda: run_cw_battle(
                session,
                runtime=runtime(),
                timeout=(
                    resolve_command_execution_timeout("cw.battle.run", payload)
                    if resolve_command_execution_timeout("cw.battle.run", payload) is not None
                    else DEFAULT_CW_BATTLE_RUN_TIMEOUT
                ),
            ),
            "cw.battle.clear_in_progress": lambda: clear_cw_battle_resume_hint(session),
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

        return session, artifact_store, runtime, handlers, tracker, end_runtime_scope_if_started, runtime_if_started


def _handle_and_save_session(handler, session_service, session):
    try:
        result = handler()
    except TrailError as error:
        if _safe_error_attr(error, "known_failure_after_save"):
            session_service.save_session(session)
        raise
    session_service.save_session(session)
    return result


def _current_guide(session, *, artifact_store: ArtifactStore):
    del artifact_store
    return _require_selected_guide(session)


def clear_cw_battle_resume_hint(session) -> dict:
    resume = ensure_cw_state(session).get("battle_resume")
    had_hint = isinstance(resume, dict) and bool(resume.get("in_battle_hint"))
    if isinstance(resume, dict):
        resume.pop("in_battle_hint", None)
    return {"cleared": had_hint}


def _require_selected_guide(session) -> dict:
    cw_state = session.scene_state.get("cw")
    return require_complete_cw_guide(cw_state if isinstance(cw_state, dict) else {})


def _operation_guide_skill_info(guide: dict | None) -> list[dict[str, str]]:
    if not isinstance(guide, dict):
        return []
    operation_guide = str(guide.get("operation_guide") or "").strip()
    if not operation_guide:
        return []
    return [{"name": "运营思路", "text": operation_guide}]


def _shop_status(session, *, artifact_store: ArtifactStore) -> dict:
    del artifact_store
    payload = shop_cw_status(session)
    cw_state = session.scene_state.get("cw")
    guide_state = cw_state.get("guide") if isinstance(cw_state, dict) else None
    if not isinstance(guide_state, dict):
        payload.pop("guide_summary", None)
    return payload


def _validate_cw_guide_apply_payload(payload: dict) -> None:
    if any(key in payload for key in ("lineup_id", "guide", "artifact", "artifact_id")):
        raise TrailError(
            "CW_GUIDE_APPLY_ARGS_NOT_SUPPORTED",
            "cw guide.apply no longer accepts lineup_id/guide; use guide.fetch.cw --select",
        )


def _apply_selected_guide_via_ui(session, *, runtime, guide: dict | None = None) -> dict:
    selected_guide = guide if guide is not None else _require_selected_guide(session)
    apply_cw_guide_via_ui(runtime, share_code=selected_guide["share_code"])
    invalidate_cw_guide_runtime_state(session)
    return selected_guide


def _select_portal_and_apply_selected_guide(
    session,
    *,
    runtime,
    card_idx: int,
    guide: dict | None = None,
    workspace_root: str | None = None,
) -> dict:
    selected_guide = guide if guide is not None else _require_selected_guide(session)
    selected = select_cw_portal(session, card_idx=card_idx, runtime=runtime)
    wait_cw_portal_preparation(session, runtime=runtime)
    try:
        _apply_selected_guide_via_ui(session, runtime=runtime, guide=selected_guide)
    except Exception as error:
        raise CwSideEffectAppliedError("cw.portal.select guide apply side effect already ran") from error
    selected_data = dict(selected)
    skill_info = _operation_guide_skill_info(selected_guide)
    if skill_info:
        selected_data["skill_info"] = skill_info
    collect_cw_crystals(session, collector=crystal_collector_factory(runtime))
    dismiss_cw_slots_overlay(runtime)
    guide_config = fetch_cw_guide_config(workspace_root=workspace_root)
    read_cw_slots(
        session,
        reader=slots_reader_factory(runtime, dismiss_initial_overlay=False),
        guide_config=guide_config,
    )
    open_cw_shop(session, opener=shop_opener_factory(runtime))
    sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
    scan_cw_shop(
        session,
        scanner=shop_page_snapshot_reader_factory(runtime),
    )
    shop_snapshot = project_cw_shop_snapshot(session)
    slots_snapshot = deepcopy(ensure_cw_state(session).get("slots") or {})
    close_cw_shop(session, closer=shop_closer_factory(runtime))
    selected_data["crystals"] = deepcopy(ensure_cw_state(session).get("metrics") or {})
    selected_data["slots"] = slots_snapshot
    selected_data["shop"] = shop_snapshot
    return selected_data


def _start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None) -> dict:
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
        fetch_cw_guide_config(workspace_root=workspace_root).get("portal_list", []),
        collection_matches=detect_portal_collection_matches(runtime),
    )
    portal_snapshot = {
        "cards": _attach_guides_to_cards(cards, workspace_root=workspace_root),
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
    return _start_cw(session, runtime=runtime, mode="continue", difficulty=difficulty, battle_mode=battle_mode, workspace_root=None)


def _attach_guides_to_cards(cards: list[dict[str, object]], *, timeout: int = 10, workspace_root: str | None = None) -> list[dict[str, object]]:
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
            workspace_root=workspace_root,
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


def _attach_guides_to_portal_snapshot(session, snapshot: dict[str, object], *, timeout: int = 10, workspace_root: str | None = None) -> dict[str, object]:
    enriched = {
        **snapshot,
        "cards": _attach_guides_to_cards(snapshot.get("cards", []), timeout=timeout, workspace_root=workspace_root),
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
    return to_jsonable(references) if isinstance(references, list) else []


def _safe_collect_warnings(runtime) -> list[dict]:
    collect_warnings = getattr(runtime, "collect_warnings", None)
    if not callable(collect_warnings):
        return []
    try:
        warnings = collect_warnings() or []
    except Exception:
        return []
    return to_jsonable(warnings) if isinstance(warnings, list) else []


def _safe_collect_debug(runtime, *, verbose: bool) -> dict | None:
    if runtime is None or not verbose:
        return None

    debug: dict = {}
    consume_debug_trace = getattr(runtime, "consume_debug_trace", None)
    if callable(consume_debug_trace):
        try:
            trace = consume_debug_trace() or []
        except Exception:
            trace = []
        if isinstance(trace, list) and trace:
            debug["trace"] = to_jsonable(trace)

    consume_debug_context = getattr(runtime, "consume_debug_context", None)
    if callable(consume_debug_context):
        try:
            context = consume_debug_context() or {}
        except Exception:
            context = {}
        if isinstance(context, dict):
            filtered_context = {
                    key: value
                    for key, value in context.items()
                    if key not in {"trace", "request_id", "detail"}
            }
            debug.update(to_jsonable(filtered_context))

    return to_jsonable(debug) or None


def _unknown_result_envelope(
    error: Exception,
    *,
    last_known_stage: str | None = None,
    screenshot: str | None = None,
    warnings: list[dict] | None = None,
    references: list[dict] | None = None,
    debug: dict | None = None,
) -> dict:
    debug_payload = to_jsonable(debug or {})
    debug_payload["detail"] = _format_exception_detail(error)
    if isinstance(last_known_stage, str) and last_known_stage:
        debug_payload["last_known_stage"] = last_known_stage
    payload = {
        "ok": False,
        "data": {},
        "screenshot": screenshot,
        "timing": {},
        "warnings": to_jsonable(warnings or []),
        "references": to_jsonable(references or []),
        "debug": debug_payload,
        "error": {
            "code": "DAEMON_UNAVAILABLE",
            "message": "mutation result unknown",
        },
    }
    guidance = build_image_guidance(screenshot)
    if guidance is not None:
        payload["image_guidance"] = guidance
    return payload


def _format_exception_detail(error: Exception) -> str:
    return format_exception_detail(error)


def _safe_error_attr(error: Exception, name: str):
    try:
        return getattr(error, name, None)
    except Exception:
        return None


def _error_data(error: Exception) -> dict:
    raw_data = _safe_error_attr(error, "data")
    data = to_jsonable(raw_data) if isinstance(raw_data, dict) else {}
    tainted = _safe_error_attr(error, "tainted")
    if tainted is not None and "tainted" not in data:
        data["tainted"] = bool(tainted)
    return data


class CwSideEffectAppliedError(Exception):
    pass
