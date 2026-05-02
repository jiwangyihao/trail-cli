from __future__ import annotations

from copy import deepcopy
from inspect import Parameter, signature
from pathlib import Path
from time import sleep

from PIL import Image

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_detail, format_exception_message, to_jsonable
from trail.daemon.command_timeouts import DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS, resolve_command_execution_timeout
from trail.daemon.command_service import PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted
from trail.output.capture import with_auto_capture, with_selective_capture
from trail.output.envelope import build_image_guidance
from trail.scenes.cw.battle import run_cw_battle
from trail.scenes.cw.entry import enter_cw, is_cw_exact_difficulty_token, start_cw
from trail.scenes.cw.equipment import apply_cw_equipment_read, prepare_cw_equipment, record_cw_equipment_compose
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
    build_cw_slot_icon_reader,
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
from trail.scenes.cw.role_recognition import VectorRoleIconRecognizer
from trail.scenes.cw.static_resources import load_default_cw_resource_bundle
from trail.scenes.cw.strategy import detect_cw_strategy, refresh_cw_strategy, select_cw_strategy


stage_detector_factory = build_cw_stage_detector
slots_reader_factory = build_cw_slot_icon_reader
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


def _call_with_supported_keywords(fn, *args, **kwargs):
    try:
        parameters = signature(fn).parameters.values()
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    accepted: set[str] = set()
    accepts_any = False
    for parameter in parameters:
        if parameter.kind is Parameter.VAR_KEYWORD:
            accepts_any = True
            break
        if parameter.kind in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}:
            accepted.add(parameter.name)
    if accepts_any:
        return fn(*args, **kwargs)
    return fn(*args, **{key: value for key, value in kwargs.items() if key in accepted})


def _apply_cw_equipment_read_with_resources(
    session,
    runtime,
    *,
    workspace_root: str | None,
    cw_resource_service=None,
    request_id: str | None = None,
) -> dict:
    if cw_resource_service is None:
        if request_id is None:
            return apply_cw_equipment_read(session, runtime, workspace_root=workspace_root)
        return apply_cw_equipment_read(session, runtime, workspace_root=workspace_root, request_id=request_id)
    raw_config, recognizer = cw_resource_service.equipment_read_resources(workspace_root=workspace_root)
    if request_id is None:
        return apply_cw_equipment_read(
            session,
            runtime,
            workspace_root=workspace_root,
            raw_config=raw_config,
            recognizer=recognizer,
        )
    return apply_cw_equipment_read(
        session,
        runtime,
        workspace_root=workspace_root,
        raw_config=raw_config,
        recognizer=recognizer,
        request_id=request_id,
    )


def _equipment_prepare_summary_from_bundle(*, workspace_root: str) -> dict:
    bundle = load_default_cw_resource_bundle(workspace_root=workspace_root)
    items = bundle.equipment_manifest.get("items") if isinstance(bundle.equipment_manifest, dict) else []
    count = len(items) if isinstance(items, list) else 0
    return {"big_version": bundle.big_version, "count": count, "cached": count, "downloaded": 0, "refreshed": False}


def _cached_cw_guide_config(
    *, workspace_root: str | None, cw_resource_service=None, enrich_traits: bool = False
) -> dict:
    if cw_resource_service is None or not hasattr(cw_resource_service, "bundle"):
        return _call_with_supported_keywords(
            fetch_cw_guide_config,
            workspace_root=workspace_root,
            enrich_traits=enrich_traits,
        )
    bundle = cw_resource_service.bundle(workspace_root=workspace_root)
    config = bundle.guide_config_enriched if enrich_traits else bundle.guide_config
    return deepcopy(config) if isinstance(config, dict) else {}


def _cached_cw_raw_config(*, workspace_root: str | None, cw_resource_service=None) -> dict | None:
    if cw_resource_service is None or not hasattr(cw_resource_service, "bundle"):
        return None
    bundle = cw_resource_service.bundle(workspace_root=workspace_root)
    return bundle.raw_config if isinstance(bundle.raw_config, dict) else None


def _role_recognizer_from_bundle(bundle):
    empty_manifest = bundle.role_manifest["empty_templates"]
    templates = {}
    for template_key in ("field", "hand"):
        local_path = empty_manifest[template_key]["local_path"]
        with Image.open(Path(bundle.root) / local_path) as image:
            templates[template_key] = image.convert("RGBA")
    return VectorRoleIconRecognizer.from_precomputed_features(bundle.role_features, empty_templates=templates)


def _default_slots_read_resources(*, workspace_root: str | None) -> tuple[dict, dict, object]:
    bundle = load_default_cw_resource_bundle(workspace_root=workspace_root)
    raw_config = bundle.raw_config if isinstance(bundle.raw_config, dict) else {}
    guide_config = bundle.guide_config_enriched if isinstance(bundle.guide_config_enriched, dict) else {}
    return raw_config, guide_config, _role_recognizer_from_bundle(bundle)


def _slots_reader_factory_accepts_recognizer(factory) -> bool:
    try:
        parameters = signature(factory).parameters
    except (TypeError, ValueError):
        return True
    return "recognizer" in parameters


def _slots_read_resources(*, workspace_root: str | None, cw_resource_service=None) -> tuple[dict, dict, object]:
    if cw_resource_service is not None and hasattr(cw_resource_service, "slots_read_resources"):
        return cw_resource_service.slots_read_resources(workspace_root=workspace_root)
    return _default_slots_read_resources(workspace_root=workspace_root)


def _build_slots_reader_for_service(
    runtime,
    *,
    targets: list[str] | None,
    workspace_root: str | None,
    cw_resource_service=None,
    request_id: str | None = None,
    dismiss_initial_overlay: bool = True,
) -> tuple[object, dict]:
    if _slots_reader_factory_accepts_recognizer(slots_reader_factory):
        _raw_config, guide_config, role_recognizer = _slots_read_resources(
            workspace_root=workspace_root,
            cw_resource_service=cw_resource_service,
        )
        reader = _call_with_supported_keywords(
            slots_reader_factory,
            runtime,
            recognizer=role_recognizer,
            targets=targets,
            request_id=request_id,
            dismiss_initial_overlay=dismiss_initial_overlay,
        )
        return reader, deepcopy(guide_config) if isinstance(guide_config, dict) else {}

    reader = _call_with_supported_keywords(
        slots_reader_factory,
        runtime,
        targets=targets,
        request_id=request_id,
        dismiss_initial_overlay=dismiss_initial_overlay,
    )
    guide_config = _cached_cw_guide_config(
        workspace_root=workspace_root,
        cw_resource_service=cw_resource_service,
        enrich_traits=True,
    )
    return reader, guide_config


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
    def __init__(self, *, runtime_service, cw_resource_service=None):
        self.runtime_service = runtime_service
        self.cw_resource_service = cw_resource_service

    def guide_config(self, *, workspace_root: str | None, enrich_traits: bool = False) -> dict:
        return _cached_cw_guide_config(
            workspace_root=workspace_root,
            cw_resource_service=self.cw_resource_service,
            enrich_traits=enrich_traits,
        )

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
            request_id=request_id,
        )

        capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id)
        scene_warnings: list[dict] = []
        response = with_selective_capture(
            capture_runtime,
            lambda: _handle_and_save_session(handlers[method], session_service, session, scene_warnings),
            verbose=verbose,
        )
        return _merge_envelope_warnings(response, scene_warnings)

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
            request_id=request_id,
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

            result, scene_warnings = _pop_scene_warnings(result)
            if not _has_fresh_portal_select_equipment(method, result):
                _mark_cw_equipment_stale(session)

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
                response = with_auto_capture(capture_runtime, lambda: result, verbose=verbose)
                return _merge_envelope_warnings(response, scene_warnings)
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
        request_id: str | None = None,
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

        def guide_config(*, enrich_traits: bool = False) -> dict:
            return _cached_cw_guide_config(
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
                enrich_traits=enrich_traits,
            )

        def validated_enter_payload() -> dict:
            if any(key in payload for key in ("mode", "difficulty", "battle_mode")):
                raise TrailError(
                    "CW_ENTER_ARGS_NOT_SUPPORTED",
                    "cw enter no longer accepts mode/difficulty/battle_mode; use cw start",
                )
            return payload

        def run_start() -> dict:
            mode, difficulty, battle_mode = _validated_start_payload(payload)
            return _call_with_supported_keywords(
                _start_cw,
                session,
                runtime=runtime(),
                mode=mode,
                difficulty=difficulty,
                battle_mode=battle_mode,
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
            )

        def run_portal_select() -> dict:
            guide = _require_selected_guide(session)
            return _select_portal_and_apply_selected_guide(
                session,
                runtime=runtime(),
                card_idx=payload["card_idx"],
                guide=guide,
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
                request_id=request_id,
            )

        def run_guide_apply() -> dict:
            _validate_cw_guide_apply_payload(payload)
            guide = _require_selected_guide(session)
            return _apply_selected_guide_via_ui(session, runtime=runtime(), guide=guide)

        def run_shop_scan() -> dict:
            base_config = guide_config()
            applied = scan_cw_shop(
                session,
                scanner=_build_shop_snapshot_reader(runtime()),
                guide_config=base_config,
            )
            trait_config = guide_config(enrich_traits=True) if _shop_has_fresh_slots(session) else None
            return project_cw_shop_snapshot(
                applied.session,
                guide_config=trait_config,
                include_field_trait_summary=trait_config is not None,
                shop_snapshot=applied.response_snapshot,
            )

        def run_shop_buy_slot() -> dict:
            base_config = guide_config()
            return buy_cw_shop_slot(
                session,
                slot=payload["slot"],
                expect=payload["expect"],
                buyer=shop_buyer_factory(runtime()),
                scanner=shop_scanner_factory(runtime()),
                guide_config=base_config,
            ).response_snapshot

        def run_shop_buy_exp() -> dict:
            base_config = guide_config()
            return buy_cw_shop_exp(
                session,
                buyer=shop_exp_buyer_factory(runtime()),
                scanner=_build_shop_snapshot_reader(runtime(), read_stage_status=True),
                guide_config=base_config,
            ).response_snapshot

        def run_equipment_read() -> dict:
            return _apply_cw_equipment_read_with_resources(
                session,
                runtime(),
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
                request_id=request_id,
            )

        def run_equipment_prepare() -> dict:
            if bool(payload.get("refresh")) and self.cw_resource_service is not None:
                return self.cw_resource_service.refresh_equipment_workspace_override(workspace_root=workspace_root)
            if self.cw_resource_service is not None:
                return self.cw_resource_service.equipment_prepare_summary(workspace_root=workspace_root)
            return _equipment_prepare_summary_from_bundle(workspace_root=workspace_root)

        def run_slots_read() -> dict:
            slots_reader, trait_config = _build_slots_reader_for_service(
                runtime(),
                targets=payload.get("slot"),
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
                request_id=request_id,
            )
            return read_cw_slots(
                session,
                reader=slots_reader,
                targets=payload.get("slot"),
                guide_config=trait_config,
            ).response_snapshot

        handlers = {
            "cw.enter": lambda: validated_enter_payload() and enter_cw(session, runtime=runtime()).scene_state["cw"]["entry"],
            "cw.start": run_start,
            "cw.portal.select": run_portal_select,
            "cw.portal.detect": lambda: _attach_guides_to_portal_snapshot(
                session,
                detect_cw_portal(
                    session,
                    runtime=runtime(),
                    portal_list=guide_config().get("portal_list", []),
                ),
                workspace_root=workspace_root,
            ),
            "cw.portal.refresh": lambda: _attach_guides_to_portal_snapshot(
                session,
                refresh_cw_portal(
                    session,
                    runtime=runtime(),
                    portal_list=guide_config().get("portal_list", []),
                ),
                workspace_root=workspace_root,
            ),
            "cw.portal.restart": lambda: _call_with_supported_keywords(
                _restart_cw,
                session,
                runtime=runtime(),
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
            ),
            "cw.strategy.detect": lambda: detect_cw_strategy(
                session,
                runtime=runtime(),
                strategy_list=guide_config().get("strategy_list", []),
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
                strategy_list=guide_config().get("strategy_list", []),
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
            "cw.equipment.prepare": run_equipment_prepare,
            "cw.equipment.read": run_equipment_read,
            "cw.equipment.compose": lambda: _call_with_supported_keywords(
                record_cw_equipment_compose,
                session,
                name=payload["name"],
                slot=payload["slot"],
                role=payload["role"],
                workspace_root=workspace_root,
                raw_config=_cached_cw_raw_config(
                    workspace_root=workspace_root,
                    cw_resource_service=self.cw_resource_service,
                ),
            ),
            "cw.slots.read": run_slots_read,
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
            "cw.shop.scan": run_shop_scan,
            "cw.shop.buy_slot": run_shop_buy_slot,
            "cw.shop.buy_exp": run_shop_buy_exp,
            "cw.shop.refresh": lambda: refresh_cw_shop(
                session,
                refresher=shop_refresher_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.close": lambda: close_cw_shop(
                session,
                closer=shop_closer_factory(runtime()),
            ).scene_state["cw"]["shop"],
            "cw.shop.status": lambda: _shop_status(
                session,
                artifact_store=artifact_store,
                workspace_root=workspace_root,
                cw_resource_service=self.cw_resource_service,
            ),
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


def _handle_and_save_session(handler, session_service, session, scene_warnings: list[dict] | None = None):
    try:
        result = handler()
    except TrailError as error:
        if _safe_error_attr(error, "known_failure_after_save"):
            session_service.save_session(session)
        raise
    result, warnings = _pop_scene_warnings(result)
    if scene_warnings is not None:
        scene_warnings.extend(warnings)
    session_service.save_session(session)
    return result


def _mark_cw_equipment_stale(session) -> None:
    equipment = ensure_cw_state(session).get("equipment")
    if isinstance(equipment, dict) and equipment.get("stale") is not True:
        equipment["stale"] = True


def _cw_equipment_auto_collect_warning(error: Exception) -> dict:
    warning = {
        "code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED",
        "message": format_exception_message(error),
    }
    detail_code = getattr(error, "code", None)
    if isinstance(detail_code, str) and detail_code:
        warning["detail_code"] = detail_code
    return warning


def _cw_slots_auto_collect_uncertain_warning(error: TrailError) -> dict:
    warning = {
        "code": "CW_SLOTS_AUTO_COLLECT_UNCERTAIN",
        "message": format_exception_message(error),
    }
    detail_code = getattr(error, "code", None)
    if isinstance(detail_code, str) and detail_code:
        warning["detail_code"] = detail_code
    position = getattr(error, "position", None)
    if position is not None:
        warning["position"] = to_jsonable(position)
    nested_warnings = getattr(error, "warnings", None)
    if isinstance(nested_warnings, list) and nested_warnings:
        warning["warnings"] = to_jsonable(nested_warnings)
    return warning


def _fresh_previous_slots_snapshot(session) -> dict | None:
    slots = ensure_cw_state(session).get("slots")
    if not isinstance(slots, dict) or slots.get("stale") is not False:
        return None
    areas = [slots.get(area) for area in ("front", "back", "hand")]
    if not any(isinstance(area, list) for area in areas):
        return None
    if not any(item is not None for area in areas if isinstance(area, list) for item in area):
        return None
    return deepcopy(slots)


def _has_fresh_portal_select_equipment(method: str, result: object) -> bool:
    if method != "cw.portal.select" or not isinstance(result, dict):
        return False
    equipment = result.get("equipment")
    if not isinstance(equipment, dict):
        return False
    stale = equipment.get("stale")
    return stale is False or (type(stale) in (int, float) and stale == 0)


def _pop_scene_warnings(data: object) -> tuple[object, list[dict]]:
    if not isinstance(data, dict):
        return data, []
    raw = data.pop("warnings", None)
    return deepcopy(data), deepcopy(raw) if isinstance(raw, list) else []


def _merge_envelope_warnings(envelope: dict, warnings: list[dict]) -> dict:
    if not warnings:
        return envelope
    merged = deepcopy(envelope)
    merged["warnings"] = [*deepcopy(merged.get("warnings") or []), *deepcopy(warnings)]
    return merged


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


def _shop_has_fresh_slots(session) -> bool:
    cw_state = session.scene_state.get("cw")
    slots = cw_state.get("slots") if isinstance(cw_state, dict) else None
    if not isinstance(slots, dict) or slots.get("stale", True) is not False:
        return False
    return isinstance(slots.get("front"), list) or isinstance(slots.get("back"), list)


def _shop_status(session, *, artifact_store: ArtifactStore, workspace_root: str, cw_resource_service=None) -> dict:
    del artifact_store
    trait_config = (
        _cached_cw_guide_config(
            workspace_root=workspace_root,
            cw_resource_service=cw_resource_service,
            enrich_traits=True,
        )
        if _shop_has_fresh_slots(session)
        else None
    )
    payload = shop_cw_status(
        session,
        guide_config=trait_config,
        include_field_trait_summary=trait_config is not None,
    )
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


def _response_snapshot_or_fallback(result: object, fallback: object) -> dict:
    snapshot = getattr(result, "response_snapshot", None)
    if isinstance(snapshot, dict):
        return deepcopy(snapshot)
    return deepcopy(fallback) if isinstance(fallback, dict) else {}


def _pop_response_snapshot_warnings(snapshot: dict) -> list[dict]:
    raw = snapshot.pop("warnings", None)
    return deepcopy(raw) if isinstance(raw, list) else []


def _select_portal_and_apply_selected_guide(
    session,
    *,
    runtime,
    card_idx: int,
    guide: dict | None = None,
    workspace_root: str | None = None,
    cw_resource_service=None,
    request_id: str | None = None,
) -> dict:
    selected_guide = guide if guide is not None else _require_selected_guide(session)
    selected = select_cw_portal(session, card_idx=card_idx, runtime=runtime)
    wait_cw_portal_preparation(session, runtime=runtime)
    previous_slots_snapshot = _fresh_previous_slots_snapshot(session)
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
    slots_reader, guide_config = _build_slots_reader_for_service(
        runtime,
        targets=None,
        workspace_root=workspace_root,
        cw_resource_service=cw_resource_service,
        request_id=request_id,
        dismiss_initial_overlay=False,
    )
    slots_snapshot = None
    slot_warnings: list[dict] = []
    try:
        slots_result = read_cw_slots(
            session,
            reader=slots_reader,
            guide_config=guide_config,
        )
        slots_snapshot = _response_snapshot_or_fallback(slots_result, ensure_cw_state(session).get("slots") or {})
        slots_screenshot = slots_snapshot.pop("_screenshot", None)
        if slots_screenshot is not None and selected_data.get("_screenshot") is None:
            selected_data["_screenshot"] = slots_screenshot
        slot_warnings = _pop_response_snapshot_warnings(slots_snapshot)
    except TrailError as error:
        if error.code != "SLOTS_RECOGNITION_UNCERTAIN":
            raise
        error_screenshot = getattr(error, "screenshot", None)
        if error_screenshot is not None and selected_data.get("_screenshot") is None:
            selected_data["_screenshot"] = error_screenshot
        warning = _cw_slots_auto_collect_uncertain_warning(error)
        previous_slots = _fresh_previous_slots_snapshot(session) or previous_slots_snapshot
        if previous_slots is not None:
            slots_snapshot = previous_slots
            warning["preserved_previous"] = 1
        slot_warnings = [warning]
    equipment_snapshot = None
    equipment_warnings: list[dict] = []
    try:
        equipment_snapshot = deepcopy(
            _apply_cw_equipment_read_with_resources(
                session,
                runtime,
                workspace_root=workspace_root,
                cw_resource_service=cw_resource_service,
            )
        )
    except Exception as error:
        equipment_warnings.append(_cw_equipment_auto_collect_warning(error))
    open_cw_shop(session, opener=shop_opener_factory(runtime))
    sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
    shop_result = scan_cw_shop(
        session,
        scanner=shop_page_snapshot_reader_factory(runtime),
        guide_config=guide_config,
    )
    shop_projection = project_cw_shop_snapshot(session)
    shop_response = _response_snapshot_or_fallback(shop_result, {})
    shop_warnings = _pop_response_snapshot_warnings(shop_response)
    shop_snapshot = {**shop_projection, **shop_response}
    close_cw_shop(session, closer=shop_closer_factory(runtime))
    selected_data["crystals"] = deepcopy(ensure_cw_state(session).get("metrics") or {})
    if slots_snapshot is not None:
        selected_data["slots"] = slots_snapshot
    if equipment_snapshot is not None:
        selected_data["equipment"] = deepcopy(equipment_snapshot)
    selected_data["shop"] = shop_snapshot
    response_warnings = [*slot_warnings, *equipment_warnings, *shop_warnings]
    if response_warnings:
        selected_data["warnings"] = [*deepcopy(selected_data.get("warnings") or []), *response_warnings]
    return selected_data


def _start_cw(
    session,
    *,
    runtime,
    mode: str,
    difficulty: str,
    battle_mode: str,
    workspace_root: str | None = None,
    cw_resource_service=None,
) -> dict:
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
        _cached_cw_guide_config(workspace_root=workspace_root, cw_resource_service=cw_resource_service).get(
            "portal_list", []
        ),
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


def _restart_cw(session, *, runtime, workspace_root: str | None = None, cw_resource_service=None) -> dict:
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
    return _start_cw(
        session,
        runtime=runtime,
        mode="continue",
        difficulty=difficulty,
        battle_mode=battle_mode,
        workspace_root=workspace_root,
        cw_resource_service=cw_resource_service,
    )


def _attach_guides_to_cards(
    cards: list[dict[str, object]],
    *,
    timeout: int = 10,
    workspace_root: str | None = None,
    allow_network: bool = False,
) -> list[dict[str, object]]:
    if not allow_network:
        return cards

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


def _attach_guides_to_portal_snapshot(
    session,
    snapshot: dict[str, object],
    *,
    timeout: int = 10,
    workspace_root: str | None = None,
    allow_network: bool = False,
) -> dict[str, object]:
    enriched = {
        **snapshot,
        "cards": _attach_guides_to_cards(
            snapshot.get("cards", []),
            timeout=timeout,
            workspace_root=workspace_root,
            allow_network=allow_network,
        ),
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
