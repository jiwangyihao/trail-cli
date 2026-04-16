from __future__ import annotations

from copy import deepcopy
from typing import Any

from trail.commands.helpers import to_jsonable
from trail.core.errors import TrailError
from trail.daemon.client import daemon_transport_failure
from trail.output.capture import with_auto_capture


def success(
    data: dict[str, Any],
    *,
    request_id: str | None = None,
    screenshot: str | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ok": True,
        "data": deepcopy(data),
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": deepcopy(references or []),
        "debug": deepcopy(debug),
        "error": None,
    }


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
            return self._capture_response(
                request,
                None,
                lambda: to_jsonable(self.runtime_service.launch_game(**request.payload)),
            )

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

        raise TrailError("DAEMON_METHOD_NOT_SUPPORTED", f"unsupported method: {request.method}")

    def _capture_response(self, request, runtime, action):
        response = with_auto_capture(runtime, action, verbose=request.verbose)
        return self._response_with_request_id(request.request_id, response)

    def _mutating_capture(self, request, runtime, action):
        response = self._capture_response(request, runtime, action)
        if response["ok"]:
            return response
        raise FailedBeforeSideEffect(response)

    def _capture_mutation_with_runtime(self, request, action):
        runtime = self._runtime(request)
        return self._mutating_capture(request, runtime, lambda: action(runtime))

    def _response_with_request_id(self, request_id: str, response: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(response)
        payload["request_id"] = request_id
        return payload

    def _format_exception_detail(self, error: Exception) -> str:
        message = str(error)
        if not message:
            return type(error).__name__
        return f"{type(error).__name__}: {message}"

    def _unknown_result_envelope(
        self,
        *,
        request_id: str,
        response: dict[str, Any] | None,
        error: Exception,
        last_known_stage: str,
    ) -> dict[str, Any]:
        previous = deepcopy(response or {})
        debug = deepcopy(previous.get("debug") or {})
        debug["detail"] = self._format_exception_detail(error)
        debug["last_known_stage"] = last_known_stage
        return {
            "request_id": request_id,
            "ok": False,
            "data": {},
            "screenshot": previous.get("screenshot"),
            "timing": deepcopy(previous.get("timing") or {}),
            "warnings": deepcopy(previous.get("warnings") or []),
            "references": deepcopy(previous.get("references") or []),
            "debug": debug,
            "error": {
                "code": "DAEMON_UNAVAILABLE",
                "message": "mutation result unknown",
            },
        }

    def _attach_recovery_detail(self, envelope: dict[str, Any], recovery_error: Exception) -> dict[str, Any]:
        payload = deepcopy(envelope)
        debug = deepcopy(payload.get("debug") or {})
        debug["recovery_detail"] = self._format_exception_detail(recovery_error)
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
    ) -> dict[str, Any]:
        try:
            service.finish_mutation(
                session_id=request.session_id,
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
                )
            except Exception as finalize_error:
                payload = self._attach_recovery_detail(envelope, recovery_error)
                return self._attach_recovery_detail(payload, finalize_error)
            return self._attach_recovery_detail(envelope, recovery_error)
        return envelope

    def _failure_envelope(self, *, error: Exception) -> dict[str, Any]:
        if isinstance(error, TrailError):
            code = error.code
            message = str(error)
        else:
            code = type(error).__name__
            message = str(error) or type(error).__name__
        return {
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": {"code": code, "message": message},
        }

    def _run_mutation(self, request, command_name: str, handler):
        service = self._session_service(request)
        accepted = service.begin_mutation(
            session_id=request.session_id,
            request_id=request.request_id,
            command_name=command_name,
        )
        if accepted["status"] == "duplicate_terminal":
            terminal_envelope = accepted["record"].get("last_envelope")
            if isinstance(terminal_envelope, dict):
                return self._response_with_request_id(request.request_id, terminal_envelope)
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

        try:
            service.mark_executing(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            result = self._response_with_request_id(request.request_id, handler(service))
        except FailedBeforeSideEffect as error:
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="failed_before_side_effect",
                envelope=error.envelope,
            )
        except SideEffectAppliedButStateNotPersisted as error:
            envelope = self._response_with_request_id(request.request_id, error.envelope)
            service.mark_side_effect_applied(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="applied_but_not_persisted",
                envelope=envelope,
            )
        except PersistedButResponseUnknown as error:
            envelope = self._response_with_request_id(request.request_id, error.envelope)
            service.mark_side_effect_applied(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            service.mark_state_persisted(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="persisted_but_response_unknown",
                envelope=envelope,
            )
        except Exception as error:
            envelope = self._response_with_request_id(request.request_id, self._failure_envelope(error=error))
            return self._persist_terminal_envelope(
                service=service,
                request=request,
                command_name=command_name,
                final_state="failed_before_side_effect",
                envelope=envelope,
            )

        last_known_stage = "handler_completed"
        try:
            service.mark_side_effect_applied(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            last_known_stage = "side_effect_applied"
            service.mark_state_persisted(
                request_id=request.request_id,
                session_id=request.session_id,
                command_name=command_name,
            )
            last_known_stage = "state_persisted"
            service.finish_mutation(
                session_id=request.session_id,
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
            )

    def _read_ocr(self, runtime, payload: dict[str, Any]) -> dict[str, Any]:
        result = runtime.ocr(**payload)
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
