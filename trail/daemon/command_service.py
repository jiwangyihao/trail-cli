from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path
from time import monotonic
from time import sleep
from typing import Any

from PIL import ImageStat

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_detail, format_exception_message, safe_str, to_jsonable
from trail.daemon.client import daemon_transport_failure
from trail.daemon.command_timeouts import resolve_command_execution_timeout
from trail.output.envelope import build_image_guidance
from trail.output.capture import with_auto_capture
from trail.runtime.ocr_config import OCR_LANG_UNSUPPORTED, split_ocr_call


CW_MUTATING_METHODS = {
    "cw.enter",
    "cw.start",
    "cw.portal.select",
    "cw.portal.refresh",
    "cw.portal.restart",
    "cw.guide.apply",
    "cw.strategy.select",
    "cw.strategy.refresh",
    "cw.slots.swap",
    "cw.slots.place",
    "cw.shop.open",
    "cw.shop.scan",
    "cw.shop.buy_slot",
    "cw.shop.buy_exp",
    "cw.shop.refresh",
    "cw.shop.close",
    "cw.crystals.collect",
    "cw.hand.sell",
    "cw.hand.sell_plan",
    "cw.replenish.choose",
    "cw.invest.choose",
    "cw.encounter.choose",
    "cw.fortune.choose",
    "cw.boss_preview.confirm",
    "cw.battle.run",
    "cw.battle.start",
    "cw.battle.continue",
    "cw.settle.next",
    "cw.event.handle",
}

CW_CAPTURE_METHODS = {
    "cw.equipment.read",
    "cw.slots.read",
    "cw.portal.detect",
    "cw.strategy.detect",
}

CW_CAPTURED_READ_METHODS = {
    "cw.stage.detect",
    "cw.stage.wait",
    "cw.replenish.read",
    "cw.invest.read",
    "cw.encounter.read",
    "cw.fortune.read",
}

CW_SESSION_SAVE_METHODS = {
    "cw.battle.clear_in_progress",
    "cw.equipment.prepare",
}

CW_SESSION_ONLY_MUTATION_METHODS = {
    "cw.equipment.compose",
}

START_RUN_STATUS_ALLOWLIST = {
    "attached",
    "launched_needs_check",
    "launched_clicked_enter",
}
START_RUN_BLACK_TRANSITION_TIMEOUT_SECONDS = 15.0
START_RUN_BLACK_TRANSITION_POLL_INTERVAL_SECONDS = 0.5
START_RUN_BLACK_TRANSITION_SETTLE_SECONDS = 3.0
_START_RUN_PROBE_UNAVAILABLE = object()

def success(
    data: dict[str, Any],
    *,
    request_id: str | None = None,
    screenshot: str | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "request_id": request_id,
        "ok": True,
        "data": to_jsonable(data),
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": to_jsonable(references or []),
        "debug": to_jsonable(debug),
        "error": None,
    }
    guidance = build_image_guidance(screenshot)
    if guidance is not None:
        payload["image_guidance"] = guidance
    return _bind_references_to_screenshot(payload)


def _normalize_workspace_path(path_value, *, workspace_root: Path) -> str | None:
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _normalize_daemon_screenshot_path(path_value, *, workspace_root: Path) -> str | None:
    return _normalize_workspace_path(path_value, workspace_root=workspace_root)


def _bind_references_to_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    screenshot = normalized.get("screenshot")
    if screenshot is None:
        return normalized
    references = normalized.get("references")
    if not isinstance(references, list):
        return normalized
    normalized["references"] = [
        {**reference, "screenshot": screenshot} if isinstance(reference, dict) else reference
        for reference in references
    ]
    return normalized


def _normalize_capture_payload(response: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    normalized = deepcopy(response)
    normalized["screenshot"] = _normalize_daemon_screenshot_path(normalized.get("screenshot"), workspace_root=workspace_root)
    references = normalized.get("references")
    if isinstance(references, list):
        normalized["references"] = [
            {
                **reference,
                "path": _normalize_workspace_path(reference.get("path"), workspace_root=workspace_root),
            }
            if isinstance(reference, dict) and "path" in reference
            else reference
            for reference in references
        ]
    return _bind_references_to_screenshot(normalized)


def _supports_request_id(method) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True
    if "request_id" in signature.parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _parse_optional_bool(value: str | bool | None, *, option_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise TrailError("GUIDE_INPUT_INVALID", f"guide option '{option_name}' must be true or false")
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise TrailError("GUIDE_INPUT_INVALID", f"guide option '{option_name}' must be true or false")


def _capture_start_run_probe_image(runtime):
    capture_image = getattr(runtime, "capture_image", None)
    if callable(capture_image):
        try:
            return capture_image(normalize=False)
        except TypeError:
            return capture_image()
        except Exception:
            return _START_RUN_PROBE_UNAVAILABLE

    window = getattr(runtime, "window", None)
    if window is not None:
        capture_image = getattr(window, "capture_image", None)
        if callable(capture_image):
            try:
                return capture_image(normalize=False)
            except TypeError:
                return capture_image()
            except Exception:
                return _START_RUN_PROBE_UNAVAILABLE

    return _START_RUN_PROBE_UNAVAILABLE


def _is_start_run_black_frame(image) -> bool:
    if image is None or not hasattr(image, "convert"):
        return False
    try:
        grayscale = image.convert("L")
        histogram = grayscale.histogram()
        total = sum(histogram) or 1
        mean = float(ImageStat.Stat(grayscale).mean[0])
    except Exception:
        return False

    bright_ratio = sum(histogram[200:]) / total
    non_dark_ratio = sum(histogram[48:]) / total
    return mean <= 12.0 and bright_ratio <= 0.01 and non_dark_ratio <= 0.04


def _wait_for_start_run_black_transition(
    runtime,
    *,
    timeout_seconds: float = START_RUN_BLACK_TRANSITION_TIMEOUT_SECONDS,
    poll_interval_seconds: float = START_RUN_BLACK_TRANSITION_POLL_INTERVAL_SECONDS,
    settle_seconds: float = START_RUN_BLACK_TRANSITION_SETTLE_SECONDS,
) -> None:
    deadline = monotonic() + timeout_seconds
    seen_black = False

    while monotonic() < deadline:
        probe_image = _capture_start_run_probe_image(runtime)
        if probe_image is _START_RUN_PROBE_UNAVAILABLE:
            return

        if _is_start_run_black_frame(probe_image):
            seen_black = True
        elif seen_black:
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(settle_seconds, remaining))
            return

        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(poll_interval_seconds, remaining))


def _resolve_start_session_id(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if isinstance(data, dict):
        return _resolve_start_session_id(data)
    session_id = result.get("session")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


class _RequestScopedCaptureRuntime:
    def __init__(self, runtime, request_id: str, *, extra_delay_seconds: float = 0.0, pre_capture_hook=None):
        self._runtime = runtime
        self._request_id = request_id
        self._extra_delay_seconds = extra_delay_seconds
        self._pre_capture_hook = pre_capture_hook

    def _resolve(self):
        if callable(self._runtime) and not hasattr(self._runtime, "capture_after_action"):
            return self._runtime()
        return self._runtime

    def capture_after_action(self, optional: bool = False):
        runtime = self._resolve()
        if runtime is None:
            return None
        if callable(self._pre_capture_hook):
            self._pre_capture_hook(runtime)
        if self._extra_delay_seconds > 0:
            sleep(self._extra_delay_seconds)
        capture_after_action = getattr(runtime, "capture_after_action", None)
        if not callable(capture_after_action):
            return None
        if _supports_request_id(capture_after_action):
            return capture_after_action(optional=optional, request_id=self._request_id)
        return capture_after_action(optional=optional)

    def __getattr__(self, name: str):
        runtime = self._resolve()
        if runtime is None:
            raise AttributeError(name)
        return getattr(runtime, name)


class CommandService:
    def __init__(self, *, runtime_service, session_service=None, cw_service=None):
        self.runtime_service = runtime_service
        self.session_service = session_service
        self.cw_service = cw_service

    def _session_service(self, request):
        if self.session_service is None:
            raise TrailError("DAEMON_UNAVAILABLE", "session service not configured")
        return self.session_service.for_workspace(request.workspace_root)

    def _runtime(self, request):
        return self.runtime_service.get_runtime(
            workspace_root=request.workspace_root,
            window_binding=None,
        )

    def _cw_service(self):
        if self.cw_service is None:
            raise TrailError("DAEMON_UNAVAILABLE", "cw service not configured")
        return self.cw_service

    def _run_cw(self, request, *, service, payload: dict | None = None):
        if payload is None:
            payload, _ = self._canonicalize_cw_payload(request)
        return self._cw_service().handle(
            method=request.method,
            payload=payload,
            workspace_root=request.workspace_root,
            session_service=service,
        )

    def _run_cw_with_capture(self, request, *, service, payload: dict | None = None):
        if payload is None:
            payload, _ = self._canonicalize_cw_payload(request)
        response = self._response_with_request_id(
            request.request_id,
            self._cw_service().handle_with_capture(
                method=request.method,
                payload=payload,
                workspace_root=request.workspace_root,
                session_service=service,
                request_id=request.request_id,
                verbose=request.verbose,
            ),
        )
        return _normalize_capture_payload(response, workspace_root=Path(request.workspace_root))

    def _run_cw_mutation(self, request, *, service, payload: dict | None = None):
        if payload is None:
            payload, _ = self._canonicalize_cw_payload(request)
        try:
            response = self._cw_service().handle_mutation(
                method=request.method,
                payload=payload,
                workspace_root=request.workspace_root,
                session_service=service,
                request_id=request.request_id,
                verbose=request.verbose,
            )
        except TrailError as error:
            if getattr(error, "completed_after_side_effect", False):
                envelope = self._response_with_request_id(request.request_id, self._failure_envelope(error=error))
                raise CompletedKnownFailure(envelope)
            raise
        try:
            return _normalize_capture_payload(response, workspace_root=Path(request.workspace_root))
        except Exception as error:
            raise PersistedButResponseUnknown(
                envelope=self._unknown_result_envelope(
                    request_id=request.request_id,
                    response=response,
                    error=error,
                    last_known_stage="state_persisted",
                )
            ) from error

    def handle(self, request):
        if request.method == "daemon.ping":
            return success(
                {"alive": True},
                request_id=request.request_id,
            )

        if request.method == "daemon.request_status":
            service = self._session_service(request)
            return success(
                service.request_status(request.payload["request_id"]),
                request_id=request.request_id,
            )

        if request.method == "daemon.reconcile_session":
            service = self._session_service(request)
            return success(
                service.reconcile_session(request.payload["session_id"]),
                request_id=request.request_id,
            )

        if request.method == "session.create":
            binding = to_jsonable(self.runtime_service.attach_window(window_title=request.payload["window_title"]))
            service = self._session_service(request)
            session = service.create_session(window_binding=binding)
            return success(session.to_dict(), request_id=request.request_id)

        if request.method == "start.run":
            return self._run_mutation(
                request,
                "start.run",
                lambda service: self._start_run(request, request.payload, service),
                response_builder=lambda payload: self._capture_start_run_response(request, payload),
                session_id_resolver=_resolve_start_session_id,
            )

        if request.method == "state.dump":
            service = self._session_service(request)
            session_id = request.payload.get("session_id") or request.session_id
            return success(service.dump_state(session_id=session_id), request_id=request.request_id)

        if request.method == "ocr.read":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._read_ocr(runtime, request.payload),
            )

        if request.method == "window.attach":
            runtime_holder = {"runtime": None}

            def action():
                binding = to_jsonable(self.runtime_service.attach_window(window_title=request.payload["window_title"]))
                runtime_holder["runtime"] = self.runtime_service.get_runtime(
                    workspace_root=request.workspace_root,
                    window_binding=binding,
                )
                return binding

            return self._capture_response(request, lambda: runtime_holder["runtime"], action)

        if request.method == "window.launch":
            return self._capture_window_launch(request)

        if request.method == "screen.shot":
            runtime = self._runtime(request)
            return self._capture_response(request, runtime, lambda: {"captured": True})

        if request.method == "image.locate":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._locate_image(runtime, request.payload["template"]),
            )

        if request.method == "image.wait":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._wait_image(
                    runtime,
                    request.payload["template"],
                    timeout=request.payload.get("timeout", 10),
                ),
            )

        if request.method == "input.click":
            return self._run_mutation(
                request,
                "input.click",
                lambda service: self._capture_mutation_with_runtime(
                    request,
                    lambda runtime: self._click(runtime, x=request.payload["x"], y=request.payload["y"]),
                ),
            )

        if request.method == "input.drag":
            return self._run_mutation(
                request,
                "input.drag",
                lambda service: self._capture_mutation_with_runtime(
                    request,
                    lambda runtime: self._drag(
                        runtime,
                        from_x=request.payload["from_x"],
                        from_y=request.payload["from_y"],
                        to_x=request.payload["to_x"],
                        to_y=request.payload["to_y"],
                    ),
                ),
            )

        if request.method == "input.key":
            return self._run_mutation(
                request,
                "input.key",
                lambda service: self._capture_mutation_with_runtime(
                    request,
                    lambda runtime: self._press_key(
                        runtime,
                        key=request.payload["key"],
                        presses=request.payload.get("presses", 1),
                    ),
                ),
            )

        if request.method.startswith("guide.fetch."):
            self._guide_scene(request.method, "guide.fetch.")
            session_id = self._guide_fetch_select_session_id(request)
            if session_id is not None:
                return self._run_mutation(
                    request,
                    request.method,
                    lambda session_service: self._handle_guide_fetch_select(request, session_service),
                    handler_persisted_state=True,
                    response_builder=lambda payload: success(payload),
                    enforce_cw_tainted=True,
                    tainted_session_id=session_id,
                )
            return self._handle_guide_fetch(request)

        if request.method.startswith("guide.config."):
            return self._handle_guide_config(request)

        if request.method.startswith("guide.list."):
            return self._handle_guide_list(request)

        if request.method.startswith("cw."):
            service = self._session_service(request)
            payload, session_id = self._canonicalize_cw_payload(request)
            if request.method in CW_CAPTURE_METHODS or request.method in CW_CAPTURED_READ_METHODS:
                return self._run_cw_with_capture(request, service=service, payload=payload)
            if request.method in CW_SESSION_SAVE_METHODS:
                return success(self._run_cw(request, service=service, payload=payload), request_id=request.request_id)
            if request.method in CW_SESSION_ONLY_MUTATION_METHODS:
                return self._run_mutation(
                    request,
                    request.method,
                    lambda session_service: self._run_cw(request, service=session_service, payload=payload),
                    handler_persisted_state=True,
                    response_builder=lambda payload: success(payload),
                    enforce_cw_tainted=True,
                    tainted_session_id=session_id,
                )
            if request.method in CW_MUTATING_METHODS:
                return self._run_mutation(
                    request,
                    request.method,
                    lambda session_service: self._run_cw_mutation(request, service=session_service, payload=payload),
                    handler_persisted_state=True,
                    response_builder=lambda payload: payload,
                    enforce_cw_tainted=True,
                    tainted_session_id=session_id,
                )
            return success(self._run_cw(request, service=service, payload=payload), request_id=request.request_id)

        raise TrailError("DAEMON_METHOD_NOT_SUPPORTED", f"unsupported method: {request.method}")

    def _guide_scene(self, method: str, prefix: str) -> str:
        scene = method.removeprefix(prefix)
        if scene != "cw":
            raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
        return scene

    def _canonicalize_cw_payload(self, request) -> tuple[dict[str, Any], str]:
        payload = deepcopy(request.payload)
        session_id = request.session_id
        if not isinstance(session_id, str) or not session_id:
            raise TrailError("SESSION_REQUIRED", f"cw method requires top-level session_id: {request.method}")

        if "session_id" in payload:
            payload_session_id = payload.get("session_id")
            if not isinstance(payload_session_id, str) or not payload_session_id or payload_session_id != session_id:
                raise TrailError(
                    "SESSION_CONFLICT",
                    f"cw method payload session_id conflicts with top-level session_id: {request.method}",
                )

        payload["session_id"] = session_id
        return payload, session_id

    def _guide_fetch_select_session_id(self, request) -> str | None:
        select_value = request.payload.get("select")
        if select_value is None:
            select = False
        elif isinstance(select_value, bool):
            select = select_value
        else:
            raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw select must be a boolean")

        raw_session_id = request.session_id
        payload_has_session_id = "session_id" in request.payload
        if select:
            if payload_has_session_id:
                raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw select only accepts top-level session_id")
            if raw_session_id is None:
                raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw select requires top-level session_id")
            if not isinstance(raw_session_id, str) or raw_session_id == "":
                raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw top-level session_id must be a non-empty string")
            return raw_session_id
        if raw_session_id is not None or payload_has_session_id:
            raise TrailError("GUIDE_INPUT_INVALID", "guide.fetch.cw session_id requires select")
        return None

    def _fetch_cw_guide_payload(self, request):
        self._guide_scene(request.method, "guide.fetch.")
        from trail.scenes.cw import guide as cw_guide

        return to_jsonable(cw_guide.fetch_cw_guide(request.payload["url"], fetcher=cw_guide.fetch_cw_guide_payload))

    def _fetch_cw_guide_with_artifact(self, request):
        guide_payload = self._fetch_cw_guide_payload(request)
        artifact = ArtifactStore(Path(request.workspace_root) / ".trail" / "artifacts").create(
            scene="cw",
            kind="guide",
            payload={
                **guide_payload,
                "recovery_origin": "guide.fetch.cw",
            },
        )
        return guide_payload, artifact

    def _handle_guide_fetch(self, request):
        guide_payload, _artifact = self._fetch_cw_guide_with_artifact(request)
        return success(
            guide_payload,
            request_id=request.request_id,
        )

    def _handle_guide_fetch_select(self, request, service):
        from trail.scenes.cw.guide import select_cw_guide

        session = service.load_session(request.session_id)
        guide_payload = self._fetch_cw_guide_payload(request)
        select_cw_guide(session, guide_data=guide_payload)
        service.save_session(session)
        return guide_payload

    def _handle_guide_config(self, request):
        self._guide_scene(request.method, "guide.config.")
        from trail.scenes.cw.guide import fetch_cw_guide_config

        return success(
            to_jsonable(fetch_cw_guide_config(workspace_root=request.workspace_root, enrich_traits=True)),
            request_id=request.request_id,
        )

    def _handle_guide_list(self, request):
        self._guide_scene(request.method, "guide.list.")
        from trail.scenes.cw.guide import GuidePortalLookupError, GuideTraitLookupError, fetch_cw_guide_list

        kwargs = {
            "page": request.payload["page"],
            "limit": request.payload["limit"],
            "trait": request.payload.get("trait"),
            "trait_id": request.payload.get("trait_id"),
            "role": request.payload.get("role"),
            "role_id": request.payload.get("role_id"),
            "order": request.payload.get("order"),
            "next_page_token": request.payload.get("next_page_token"),
            "match_change_job": _parse_optional_bool(
                request.payload.get("match_change_job"),
                option_name="match-change-job",
            ),
            "match_hard": _parse_optional_bool(
                request.payload.get("match_hard"),
                option_name="match-hard",
            ),
        }
        if request.payload.get("portal") is not None:
            kwargs["portal"] = request.payload.get("portal")
        if request.payload.get("portal_id") is not None:
            kwargs["portal_id"] = request.payload.get("portal_id")
        kwargs["workspace_root"] = request.workspace_root

        try:
            payload = fetch_cw_guide_list(**kwargs)
        except GuidePortalLookupError as error:
            return {
                "request_id": request.request_id,
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [
                    {"portal": candidate["title"], "score": candidate["score"]}
                    for candidate in error.candidates
                ],
                "references": [],
                "debug": None,
                "error": {"code": error.code, "message": str(error)},
            }
        except GuideTraitLookupError as error:
            return {
                "request_id": request.request_id,
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [
                    {
                        "trait": candidate["trait"],
                        "trait_id": candidate["trait_id"],
                        "score": candidate["score"],
                    }
                    for candidate in error.candidates
                ],
                "references": [],
                "debug": None,
                "error": {"code": error.code, "message": str(error)},
            }

        data = to_jsonable(payload)
        promoted_warnings: list[dict[str, Any]] = []
        if isinstance(data, dict):
            raw_role_warnings = data.pop("role_warnings", None)
            if isinstance(raw_role_warnings, list):
                promoted_warnings = deepcopy(raw_role_warnings)

        response = success(
            data,
            request_id=request.request_id,
        )
        if promoted_warnings:
            response["warnings"] = promoted_warnings
        return response

    def _capture_response(self, request, runtime, action, *, extra_delay_seconds: float = 0.0, pre_capture_hook=None):
        capture_runtime = (
            None
            if runtime is None
            else _RequestScopedCaptureRuntime(
                runtime,
                request.request_id,
                extra_delay_seconds=extra_delay_seconds,
                pre_capture_hook=pre_capture_hook,
            )
        )
        response = with_auto_capture(capture_runtime, action, verbose=request.verbose)
        response = _normalize_capture_payload(response, workspace_root=Path(request.workspace_root))
        return self._response_with_request_id(request.request_id, response)

    def _capture_window_launch(self, request):
        promoted_warnings: list[dict[str, Any]] = []

        def action():
            result = to_jsonable(self.runtime_service.launch_game(**request.payload))
            if not isinstance(result, dict):
                return result
            raw_warnings = result.pop("warnings", None)
            if isinstance(raw_warnings, list):
                promoted_warnings.extend(deepcopy(raw_warnings))
            return result

        response = self._capture_response(request, None, action)
        if not response.get("ok") or not promoted_warnings:
            return response

        payload = deepcopy(response)
        payload["warnings"] = [
            *deepcopy(payload.get("warnings") or []),
            *promoted_warnings,
        ]
        return payload

    def _mutating_capture(self, request, runtime, action):
        response = self._capture_response(request, runtime, action)
        if response["ok"]:
            return response
        raise FailedBeforeSideEffect(response)

    def _capture_mutation_with_runtime(self, request, action):
        runtime = self._runtime(request)
        return self._mutating_capture(request, runtime, lambda: action(runtime))

    def _capture_start_run_response(self, request, payload):
        runtime = None
        data = payload
        if isinstance(payload, dict):
            runtime = payload.get("runtime")
            maybe_data = payload.get("data")
            if isinstance(maybe_data, dict):
                data = maybe_data
        status = data.get("status") if isinstance(data, dict) else None
        response = self._capture_response(
            request,
            runtime,
            lambda: deepcopy(data),
            pre_capture_hook=_wait_for_start_run_black_transition if status == "launched_clicked_enter" else None,
        )
        if response.get("ok") is True and not response.get("screenshot"):
            envelope = self._mark_start_run_post_side_effect_failure(
                deepcopy(response),
                request_id=request.request_id,
            )
            envelope["ok"] = False
            envelope["error"] = {
                "code": "START_RESULT_SCREENSHOT_REQUIRED",
                "message": "start.run success requires screenshot",
            }
            raise SideEffectAppliedButStateNotPersisted(envelope)
        return response

    def _raise_start_run_post_side_effect_failure(self, error: Exception) -> None:
        envelope = self._mark_start_run_post_side_effect_failure(self._failure_envelope(error=error))
        raise SideEffectAppliedButStateNotPersisted(envelope) from error

    def _mark_start_run_post_side_effect_failure(
        self,
        envelope: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        payload = deepcopy(envelope)
        debug = deepcopy(payload.get("debug") or {})
        if request_id is not None and not debug.get("request_id"):
            debug["request_id"] = request_id
        debug["last_known_stage"] = "side_effect_applied"
        debug["tainted"] = True
        payload["debug"] = debug
        return payload

    def _response_with_request_id(self, request_id: str, response: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(response)
        payload["request_id"] = request_id
        return payload

    def _format_exception_detail(self, error: Exception) -> str:
        return format_exception_detail(error)

    def _unknown_result_envelope(
        self,
        *,
        request_id: str,
        response: dict[str, Any] | None,
        error: Exception,
        last_known_stage: str,
    ) -> dict[str, Any]:
        previous = to_jsonable(response or {})
        screenshot = previous.get("screenshot")
        debug = to_jsonable(previous.get("debug") or {})
        debug["detail"] = self._format_exception_detail(error)
        debug["last_known_stage"] = last_known_stage
        payload = {
            "request_id": request_id,
            "ok": False,
            "data": {},
            "screenshot": screenshot,
            "timing": to_jsonable(previous.get("timing") or {}),
            "warnings": to_jsonable(previous.get("warnings") or []),
            "references": to_jsonable(previous.get("references") or []),
            "debug": debug,
            "error": {
                "code": "DAEMON_UNAVAILABLE",
                "message": "mutation result unknown",
            },
        }
        guidance = build_image_guidance(screenshot)
        if guidance is not None:
            payload["image_guidance"] = guidance
        return payload

    def _attach_recovery_detail(self, envelope: dict[str, Any], recovery_error: Exception) -> dict[str, Any]:
        payload = deepcopy(envelope)
        debug = deepcopy(payload.get("debug") or {})
        debug["recovery_detail"] = self._format_exception_detail(recovery_error)
        payload["debug"] = debug
        return payload

    def _attach_stage_detail(self, envelope: dict[str, Any], stage_error: Exception) -> dict[str, Any]:
        payload = deepcopy(envelope)
        debug = deepcopy(payload.get("debug") or {})
        debug["stage_detail"] = self._format_exception_detail(stage_error)
        payload["debug"] = debug
        return payload

    def _persist_terminal_envelope(
        self,
        *,
        service,
        request,
        command_name: str,
        final_state: str,
        envelope: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        effective_session_id = request.session_id if session_id is None else session_id
        try:
            service.finish_mutation(
                session_id=effective_session_id,
                request_id=request.request_id,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
            )
        except Exception as recovery_error:
            try:
                service.finalize_journal_record(
                    request_id=request.request_id,
                    command_name=command_name,
                    final_state=final_state,
                    envelope=envelope,
                    session_id=effective_session_id,
                )
            except Exception as finalize_error:
                payload = self._attach_recovery_detail(envelope, recovery_error)
                return self._attach_recovery_detail(payload, finalize_error)
            return self._attach_recovery_detail(envelope, recovery_error)
        return envelope

    def _start_run(self, request, payload, service):
        window_title = str(payload.get("window_title") or "崩坏：星穹铁道")
        execution_timeout = resolve_command_execution_timeout("start.run", request.payload)
        start_result = to_jsonable(
            self.runtime_service.start_run(
                workspace_root=request.workspace_root,
                window_title=window_title,
                game_path=payload.get("game_path"),
                channel=str(payload.get("channel") or "official"),
                timeout_seconds=execution_timeout if execution_timeout is not None else 30,
            )
        )
        try:
            if not isinstance(start_result, dict):
                raise TrailError("START_RESULT_INVALID", "start.run returned invalid result")
            status = start_result.get("status")
            if not isinstance(status, str) or status not in START_RUN_STATUS_ALLOWLIST:
                raise TrailError("START_RESULT_INVALID", "start.run returned invalid status")
            binding = {key: value for key, value in start_result.items() if key != "status"}
            runtime = self.runtime_service.get_runtime(
                workspace_root=request.workspace_root,
                window_binding=binding,
            )
            session = service.find_reusable_session(window_binding=binding)
            if session is not None:
                return {
                    "data": {
                        "status": status,
                        "session": session.session_id,
                        "reused": 1,
                        "title": binding["title"],
                        "hwnd": binding["hwnd"],
                    },
                    "runtime": runtime,
                }
            created = service.create_session(window_binding=binding)
            return {
                "data": {
                    "status": status,
                    "session": created.session_id,
                    "reused": 0,
                    "title": binding["title"],
                    "hwnd": binding["hwnd"],
                },
                "runtime": runtime,
            }
        except SideEffectAppliedButStateNotPersisted:
            raise
        except Exception as error:
            self._raise_start_run_post_side_effect_failure(error)

    def _failure_envelope(self, *, error: Exception) -> dict[str, Any]:
        if isinstance(error, TrailError):
            code = error.code
            message = format_exception_message(error)
        else:
            code = type(error).__name__
            message = safe_str(error) or type(error).__name__
        raw_data = self._error_attr(error, "data")
        data = to_jsonable(raw_data) if isinstance(raw_data, dict) else {}
        tainted = self._error_attr(error, "tainted")
        if tainted is not None and "tainted" not in data:
            data["tainted"] = bool(tainted)
        raw_debug = self._error_attr(error, "debug")
        debug = to_jsonable(raw_debug) if isinstance(raw_debug, dict) else None
        return {
            "ok": False,
            "data": data,
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": debug,
            "error": {"code": code, "message": message},
        }

    def _error_attr(self, error: Exception, name: str):
        try:
            return getattr(error, name, None)
        except Exception:
            return None

    def _run_mutation(
        self,
        request,
        command_name: str,
        handler,
        *,
        handler_persisted_state: bool = False,
        response_builder=None,
        enforce_cw_tainted: bool = False,
        tainted_session_id: str | None = None,
        session_id_resolver=None,
    ):
        if response_builder is None:
            response_builder = lambda payload: payload
        service = self._session_service(request)
        effective_session_id = request.session_id
        accepted = service.begin_mutation(
            session_id=request.session_id,
            request_id=request.request_id,
            command_name=command_name,
            enforce_cw_tainted=enforce_cw_tainted,
        )
        if accepted["status"] == "duplicate_terminal":
            terminal_envelope = accepted["record"].get("last_envelope")
            if isinstance(terminal_envelope, dict):
                normalized = _normalize_capture_payload(terminal_envelope, workspace_root=Path(request.workspace_root))
                return self._response_with_request_id(request.request_id, normalized)
            return daemon_transport_failure(
                request_id=request.request_id,
                code="REQUEST_TERMINAL_RECORD_INVALID",
                message="terminal request record missing envelope",
                debug={"record": accepted["record"]},
            )
        if accepted["status"] == "duplicate_in_progress":
            return daemon_transport_failure(
                request_id=request.request_id,
                code="REQUEST_IN_PROGRESS",
                message="matching request is still executing",
                debug={"record": accepted["record"]},
            )
        if accepted["status"] == "request_id_conflict":
            raise TrailError("REQUEST_ID_CONFLICT", "request_id reused across a different session or command")
        if accepted["status"] == "session_tainted":
            return daemon_transport_failure(
                request_id=request.request_id,
                code="SESSION_RECONCILE_REQUIRED",
                message="session is tainted; reconcile before mutating cw commands",
                debug={"session_id": tainted_session_id},
            )

        last_known_stage = "accepted"
        handler_result = None
        response = None
        try:
            service.mark_executing(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            last_known_stage = "executing"
            handler_result = handler(service)
            if session_id_resolver is not None:
                effective_session_id = session_id_resolver(handler_result) or request.session_id
            last_known_stage = "state_persisted" if handler_persisted_state else "handler_completed"
            response = response_builder(handler_result)
            result = self._response_with_request_id(request.request_id, response)
        except CompletedKnownFailure as error:
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="completed",
                envelope=error.envelope,
            )
        except FailedBeforeSideEffect as error:
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="failed_before_side_effect",
                envelope=error.envelope,
                session_id=effective_session_id,
            )
        except SideEffectAppliedButStateNotPersisted as error:
            envelope = self._response_with_request_id(
                request.request_id,
                _normalize_capture_payload(error.envelope, workspace_root=Path(request.workspace_root)),
            )
            data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
            if session_id_resolver is not None:
                effective_session_id = session_id_resolver(data_payload) or request.session_id
            try:
                service.mark_side_effect_applied(
                    request_id=request.request_id,
                    session_id=effective_session_id,
                    command_name=command_name,
                )
            except Exception as stage_error:
                envelope = self._attach_stage_detail(envelope, stage_error)
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="applied_but_not_persisted",
                envelope=envelope,
                session_id=effective_session_id,
            )
        except PersistedButResponseUnknown as error:
            envelope = self._response_with_request_id(
                request.request_id,
                _normalize_capture_payload(error.envelope, workspace_root=Path(request.workspace_root)),
            )
            data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
            if session_id_resolver is not None:
                effective_session_id = session_id_resolver(data_payload) or request.session_id
            try:
                service.mark_side_effect_applied(
                    request_id=request.request_id,
                    session_id=effective_session_id,
                    command_name=command_name,
                )
                service.mark_state_persisted(
                    request_id=request.request_id,
                    session_id=effective_session_id,
                    command_name=command_name,
                )
            except Exception as stage_error:
                envelope = self._attach_stage_detail(envelope, stage_error)
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="persisted_but_response_unknown",
                envelope=envelope,
                session_id=effective_session_id,
            )
        except Exception as error:
            if last_known_stage in {"handler_completed", "state_persisted"}:
                final_state = "persisted_but_response_unknown" if last_known_stage == "state_persisted" else "applied_but_not_persisted"
                envelope = self._unknown_result_envelope(
                    request_id=request.request_id,
                    response=response,
                    error=error,
                    last_known_stage=last_known_stage,
                )
            else:
                final_state = "failed_before_side_effect"
                envelope = self._response_with_request_id(request.request_id, self._failure_envelope(error=error))
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
                session_id=effective_session_id,
            )

        last_known_stage = "state_persisted" if handler_persisted_state else "handler_completed"
        try:
            service.mark_side_effect_applied(
                request_id=request.request_id,
                session_id=effective_session_id,
                command_name=command_name,
            )
            if not handler_persisted_state:
                last_known_stage = "side_effect_applied"
            service.mark_state_persisted(
                request_id=request.request_id,
                session_id=effective_session_id,
                command_name=command_name,
            )
            last_known_stage = "state_persisted"
            service.finish_mutation(
                session_id=effective_session_id,
                request_id=request.request_id,
                command_name=command_name,
                final_state="completed",
                envelope=result,
            )
            return result
        except Exception as error:
            final_state = "persisted_but_response_unknown" if last_known_stage == "state_persisted" else "applied_but_not_persisted"
            envelope = self._unknown_result_envelope(
                request_id=request.request_id,
                response=result,
                error=error,
                last_known_stage=last_known_stage,
            )
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
                session_id=effective_session_id,
            )
    def _read_ocr(self, runtime, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            ocr_call = split_ocr_call(payload)
        except ValueError as error:
            message = str(error)
            if message.startswith("unsupported ocr lang:"):
                raise TrailError(OCR_LANG_UNSUPPORTED, message) from error
            raise TrailError("OCR_INPUT_INVALID", message) from error
        result = runtime.ocr(capture=ocr_call.capture, ocr=ocr_call.ocr)
        if not result:
            raise TrailError("OCR_NO_RESULT", "OCR 无结果")
        return {"result": to_jsonable(result)}

    def _locate_image(self, runtime, template: str) -> dict[str, Any]:
        box = runtime.locate(template)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    def _wait_image(self, runtime, template: str, *, timeout: int) -> dict[str, Any]:
        box = runtime.wait_img(template, timeout=timeout)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    def _click(self, runtime, *, x: int, y: int) -> dict[str, Any]:
        runtime.click_point(x, y)
        return {"clicked": [x, y]}

    def _drag(self, runtime, *, from_x: int, from_y: int, to_x: int, to_y: int) -> dict[str, Any]:
        runtime.drag_to(from_x, from_y, to_x, to_y)
        return {"dragged": [from_x, from_y, to_x, to_y]}

    def _press_key(self, runtime, *, key: str, presses: int) -> dict[str, Any]:
        runtime.press_key(key, presses=presses)
        return {"key": key, "presses": presses}


class SideEffectAppliedButStateNotPersisted(Exception):
    def __init__(self, envelope: dict[str, Any]):
        super().__init__("side effect applied but state not persisted")
        self.envelope = deepcopy(envelope)


class PersistedButResponseUnknown(Exception):
    def __init__(self, envelope: dict[str, Any]):
        super().__init__("persisted but response unknown")
        self.envelope = deepcopy(envelope)


class FailedBeforeSideEffect(Exception):
    def __init__(self, envelope: dict[str, Any]):
        super().__init__("failed before side effect")
        self.envelope = deepcopy(envelope)


class CompletedKnownFailure(Exception):
    def __init__(self, envelope: dict[str, Any]):
        super().__init__("completed with known business failure")
        self.envelope = deepcopy(envelope)
