from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from threading import Condition, Lock
from uuid import uuid4

from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_message, to_jsonable
from trail.daemon.models import DaemonRequest, RequestCancelled
from trail.output.envelope import command_failure, command_success


CONTROL_PLANE_METHODS = {
    "daemon.request_status",
    "daemon.request_result",
    "daemon.request_cancel",
    "daemon.reconcile_session",
    "daemon.ping",
}

_GAME_OPERATION_PREFIXES = ("cw.", "input.", "window.", "image.")
_GAME_OPERATION_METHODS = {"start.run", "ocr.read", "screen.shot"}
_SESSION_READ_ONLY_METHODS = {"state.dump"}


def is_game_operation(method: str) -> bool:
    return method in _GAME_OPERATION_METHODS or method.startswith(_GAME_OPERATION_PREFIXES)


def canonical_payload_digest(payload: dict | None) -> str:
    encoded = json.dumps(to_jsonable(payload or {}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _workspace_key(workspace_root: str) -> str:
    return str(workspace_root).replace("\\", "/").casefold()


def _job_key(request: DaemonRequest) -> str:
    digest = canonical_payload_digest(request.payload)
    return "\n".join([_workspace_key(request.workspace_root), _session_id_for_request(request) or "", request.method, digest])


def _session_id_for_request(request: DaemonRequest) -> str | None:
    if request.session_id:
        return request.session_id
    payload_session = request.payload.get("session_id") if isinstance(request.payload, dict) else None
    return payload_session if isinstance(payload_session, str) and payload_session else None


def _requires_session_mutation_lease(
    request: DaemonRequest,
    envelope: dict | None = None,
    *,
    resolved_session_id: str | None = None,
) -> bool:
    if request.method in _SESSION_READ_ONLY_METHODS:
        return False
    if resolved_session_id is not None:
        return True
    if request.method == "session.create":
        return True
    if request.method == "guide.fetch.cw" and bool(request.payload.get("select")):
        return True
    if envelope is not None and (envelope.get("screenshot") or envelope.get("data")):
        return True
    return False


class PausedJobForTesting:
    def __init__(self, *, job_id: str):
        self.job_id = job_id


class RequestExecutor:
    def __init__(self, *, command_service, session_service, max_workers: int = 4, start_workers_paused: bool = False):
        self.command_service = command_service
        self.session_service = session_service
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._mutex = Lock()
        self._active_game_ready = Condition(self._mutex)
        self._active_game_job_id: str | None = None
        self._active_game_command: str | None = None
        self._active_game_session: str | None = None
        self._active_game_job: dict | None = None
        self._futures: dict[str, Future] = {}
        self._non_game_active_keys: set[str] = set()
        self._future_job_keys: dict[str, str] = {}
        self._job_metadata: dict[str, dict] = {}
        self._job_key_to_job_id: dict[str, str] = {}
        self._call_id_to_job_id: dict[str, str] = {}
        self._job_ready = Condition(self._mutex)
        self._cancellation_tokens: dict[str, object] = {}
        self._start_workers_paused = bool(start_workers_paused)
        self._paused_jobs: dict[str, tuple[DaemonRequest, str, str]] = {}

    def _remember_call_mapping(self, call_id: str | None, job_id: str) -> None:
        if isinstance(call_id, str) and call_id:
            self._call_id_to_job_id[call_id] = job_id

    def handle(self, request: DaemonRequest) -> dict:
        service = self.session_service.for_workspace(request.workspace_root)
        if request.method == "daemon.request_result":
            return self._handle_request_result(request)
        if request.method == "daemon.request_cancel":
            return self._handle_request_cancel(request)
        if request.method in CONTROL_PLANE_METHODS:
            return self._handle_control_plane(request, service)



        if request.method == "state.dump":
            return self.command_service.execute_business_request(self._worker_request(request, job_id=request.call_id, service=service))

        job_id = request.job_id or uuid4().hex
        job_key = _job_key(request)
        session_id = _session_id_for_request(request)
        explicit_job_response = self._explicit_job_response(
            request,
            service,
            job_id=request.job_id,
            wait_timeout=float(request.control.wait_timeout or 0.0),
        )
        if explicit_job_response is not None:
            return explicit_job_response
        singleton_job_response = self._singleton_job_response(
            request,
            service,
            job_key=job_key,
            wait_timeout=float(request.control.wait_timeout or 0.0),
        )
        if singleton_job_response is not None:
            return singleton_job_response

        if not is_game_operation(request.method):
            active_job_response = self._reserve_non_game_job_or_active(request, service, job_key=job_key)
            if active_job_response is not None:
                return active_job_response
            non_game_reserved = True
        else:
            non_game_reserved = False
        game_operation = is_game_operation(request.method)
        if game_operation:
            busy = self._reserve_game_job_or_busy(request, service, job_id=job_id, session_id=session_id)
            if busy is not None:
                return busy

        try:
            job_record = service.create_job_record(
                job_id=job_id,
                job_key=job_key,
                method=request.method,
                payload_digest=canonical_payload_digest(request.payload),
                session_id=session_id,
            )
            service.create_call_record(
                call_id=request.call_id,
                job_id=job_id,
                method=request.method,
                session_id=session_id,
                state="attached",
                executed=True,
            )
            with self._mutex:
                self._job_metadata[job_id] = deepcopy(job_record)
                self._job_key_to_job_id[job_key] = job_id
                self._remember_call_mapping(request.call_id, job_id)
                if game_operation and self._active_game_job_id == job_id:
                    self._active_game_job = deepcopy(job_record)
                self._job_ready.notify_all()
                self._active_game_ready.notify_all()

            worker_request = self._worker_request(request, job_id=job_id, service=service)
            with self._mutex:
                self._cancellation_tokens[job_id] = worker_request.control.cancellation_token
                if self._start_workers_paused:
                    self._paused_jobs[job_id] = (worker_request, request.call_id, job_key)
                    self._job_ready.notify_all()
                    if game_operation:
                        self._active_game_ready.notify_all()
                else:
                    future = self._pool.submit(self._execute_job, worker_request, request.call_id, job_id, job_key)
                    self._futures[job_id] = future
                    self._future_job_keys[job_id] = job_key
                    self._job_ready.notify_all()
                    if game_operation:
                        self._active_game_ready.notify_all()
                    future.add_done_callback(lambda completed, job_id=job_id: self._release_completed_job(job_id))
        except Exception:
            if game_operation:
                self._clear_reserved_game_job(job_id)
            if non_game_reserved:
                self._clear_non_game_job_key(job_key)
                with self._mutex:
                    self._future_job_keys.pop(job_id, None)
                    self._job_metadata.pop(job_id, None)
                    self._job_key_to_job_id.pop(job_key, None)
                    self._call_id_to_job_id.pop(request.call_id, None)
                    self._job_ready.notify_all()
            raise

        wait_timeout = float(request.control.wait_timeout or 0.0)
        if wait_timeout <= 0:
            return command_success(
                data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
                screenshot=None,
            )
        try:
            response = future.result(timeout=wait_timeout)
            self._release_completed_job(job_id)
            return response
        except TimeoutError:
            return command_success(
                data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
                screenshot=None,
            )

    def submit_paused_for_testing(self, request: DaemonRequest) -> PausedJobForTesting:
        if not self._start_workers_paused:
            raise RuntimeError("paused worker mode is disabled")
        response = self.handle(request)
        return PausedJobForTesting(job_id=response["data"]["request"])

    def release_paused_job_for_testing(self, job_id: str) -> None:
        paused = self._paused_jobs.pop(job_id)
        worker_request, call_id, job_key = paused
        future = self._pool.submit(self._execute_job, worker_request, call_id, job_id, job_key)
        with self._mutex:
            self._futures[job_id] = future
            self._future_job_keys[job_id] = job_key
            self._job_ready.notify_all()
            if is_game_operation(worker_request.method):
                self._active_game_ready.notify_all()
        future.add_done_callback(lambda completed, job_id=job_id: self._release_completed_job(job_id))


    def _explicit_job_response(self, request: DaemonRequest, service, *, job_id: str | None, wait_timeout: float) -> dict | None:
        if not job_id:
            return None
        memory_response = self._explicit_running_job_from_memory(request, service, job_id=job_id, wait_timeout=wait_timeout)
        if memory_response is not None:
            return memory_response
        if service.get_call_record(job_id) is not None:
            return command_failure(
                code="REQUEST_ID_IS_CALL_ID",
                message="request id refers to a call record; use the returned job id",
                data={"request": job_id, "executed": 0},
                debug={"request_id": request.call_id},
            )
        job = self._find_job_across_workspaces(service, job_id)
        if job is None:
            return command_failure(
                code="REQUEST_NOT_FOUND",
                message=f"request not found: {job_id}",
                data={"request": job_id, "executed": 0},
                debug={"request_id": request.call_id},
            )
        mismatch = self._job_mismatch_code(request, job)
        if mismatch is not None:
            return command_failure(
                code=mismatch,
                message="request id does not match submitted business command",
                data={"request": job_id, "executed": 0},
                debug={"request_id": request.call_id},
            )
        if wait_timeout <= 0 and not job.get("final"):
            service.create_call_record(
                call_id=request.call_id,
                job_id=job_id,
                method=request.method,
                session_id=_session_id_for_request(request),
                state="attached",
                executed=False,
            )
            with self._mutex:
                if job_id in self._job_metadata:
                    self._remember_call_mapping(request.call_id, job_id)
            return command_success(
                data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
                screenshot=None,
            )
        service.create_call_record(
            call_id=request.call_id,
            job_id=job_id,
            method=request.method,
            session_id=_session_id_for_request(request),
            state="attached",
            executed=False,
        )
        with self._mutex:
            if job_id in self._job_metadata:
                self._remember_call_mapping(request.call_id, job_id)
        return self._job_response(job, job_id=job_id, wait_timeout=wait_timeout)

    def _active_job_response(self, request: DaemonRequest, service, *, job_key: str, wait_timeout: float) -> dict | None:
        job = service.find_active_job_by_key(job_key)
        if job is None:
            return None
        job_id = job["job_id"]
        service.create_call_record(
            call_id=request.call_id,
            job_id=job_id,
            method=request.method,
            session_id=_session_id_for_request(request),
            state="attached",
            executed=False,
        )
        with self._mutex:
            if job_id in self._job_metadata:
                self._remember_call_mapping(request.call_id, job_id)
        return self._job_response(job, job_id=job_id, wait_timeout=wait_timeout)

    def _singleton_job_response(self, request: DaemonRequest, service, *, job_key: str, wait_timeout: float) -> dict | None:
        job = self._latest_singleton_job(request, service, job_key=job_key)
        if job is None:
            return None
        job_id = job["job_id"]
        service.create_call_record(
            call_id=request.call_id,
            job_id=job_id,
            method=request.method,
            session_id=_session_id_for_request(request),
            state="attached",
            executed=False,
        )
        with self._mutex:
            if job_id in self._job_metadata:
                self._remember_call_mapping(request.call_id, job_id)
        return self._job_response(job, job_id=job_id, wait_timeout=wait_timeout)

    def _latest_singleton_job(self, request: DaemonRequest, service, *, job_key: str) -> dict | None:
        with self._mutex:
            job_id = self._job_key_to_job_id.get(job_key)
            metadata = deepcopy(self._job_metadata.get(job_id)) if job_id is not None else None
        if metadata is not None:
            return metadata
        latest = service.find_latest_job_for_method(request.method)
        if latest is None:
            return None
        if latest.get("job_key") != job_key:
            return None
        return latest

    def _find_job_across_workspaces(self, service, job_id: str) -> dict | None:
        job = service.get_job_record(job_id)
        if job is not None:
            return job
        for candidate in self.session_service._services.values():
            job = candidate.get_job_record(job_id)
            if job is not None:
                return job
        return None

    def _resolve_job_for_request_id(self, service, request_id: str) -> tuple[str, dict | None, bool]:
        with self._mutex:
            memory_job = self._job_metadata.get(request_id)
            if memory_job is not None:
                return request_id, deepcopy(memory_job), True
            mapped_job_id = self._call_id_to_job_id.get(request_id)
            if mapped_job_id is not None:
                mapped_job = self._job_metadata.get(mapped_job_id)
                if mapped_job is not None:
                    return mapped_job_id, deepcopy(mapped_job), True
        job = self._find_job_across_workspaces(service, request_id)
        if job is not None:
            return request_id, job, False
        call = service.get_call_record(request_id)
        if call is None:
            for candidate in self.session_service._services.values():
                call = candidate.get_call_record(request_id)
                if call is not None:
                    break
        if call is None:
            return request_id, None, False
        job_id = call.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return request_id, None, False
        return job_id, self._find_job_across_workspaces(service, job_id), False

    def _completed_already_response(self, *, job_id: str, job: dict) -> dict:
        return command_success(
            data={"request_id": job_id, "state": "completed_already", "command": job["method"]},
            screenshot=None,
        )

    def _handle_control_plane(self, request: DaemonRequest, service) -> dict:
        control_request = self._worker_request(request, job_id=request.call_id, service=service)
        if request.method == "daemon.request_status":
            payload = request.payload if isinstance(request.payload, dict) else {}
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                return command_failure(
                    code="REQUEST_ID_REQUIRED",
                    message="request_id is required",
                    data={"request": request_id},
                    debug={"request_id": request.call_id},
                )
            return command_success(
                data=service.request_status(request_id),
                screenshot=None,
                debug={"request_id": request.call_id},
            )
        if request.method == "daemon.reconcile_session":
            payload = request.payload if isinstance(request.payload, dict) else {}
            session_id = payload.get("session_id") or request.session_id
            if not isinstance(session_id, str) or not session_id:
                return command_failure(
                    code="SESSION_ID_REQUIRED",
                    message="session_id is required",
                    data={"session_id": session_id},
                    debug={"request_id": request.call_id},
                )
            with service.session_mutation_lock(session_id):
                return command_success(
                    data=service.reconcile_session(session_id),
                    screenshot=None,
                    debug={"request_id": request.call_id},
                )
        return self.command_service.handle_control_plane(control_request)


    def _handle_request_result(self, request: DaemonRequest) -> dict:
        request_id = request.payload.get("request_id") if isinstance(request.payload, dict) else None
        if not isinstance(request_id, str) or not request_id:
            return command_failure(
                code="REQUEST_ID_REQUIRED",
                message="request_id is required",
                data={"request": request_id},
                debug={"request_id": request.call_id},
            )
        service = self.session_service.for_workspace(request.workspace_root)
        job = self._find_job_across_workspaces(service, request_id)
        if job is None:
            return command_failure(
                code="REQUEST_NOT_FOUND",
                message=f"request not found: {request_id}",
                data={"request": request_id},
                debug={"request_id": request.call_id},
            )
        if not job.get("final"):
            return command_failure(
                code="REQUEST_NOT_FINAL",
                message=f"request not final: {request_id}",
                data={"request": request_id, "state": job.get("state")},
                debug={"request_id": request.call_id},
            )
        envelope = job.get("last_envelope")
        if not isinstance(envelope, dict):
            return command_failure(
                code="REQUEST_TERMINAL_RECORD_INVALID",
                message="terminal request record missing envelope",
                data={"request": request_id},
                debug={"request_id": request.call_id},
            )
        return command_success(
            data={"render_command": job["method"], "envelope": deepcopy(envelope)},
            screenshot=None,
        )

    def _handle_request_cancel(self, request: DaemonRequest) -> dict:
        payload = request.payload if isinstance(request.payload, dict) else {}
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return command_failure(
                code="REQUEST_ID_REQUIRED",
                message="request_id is required",
                data={"request": request_id},
                debug={"request_id": request.call_id},
            )
        service = self.session_service.for_workspace(request.workspace_root)
        job_id, job, resolved_from_memory = self._resolve_job_for_request_id(service, request_id)
        if job is None:
            return command_failure(
                code="REQUEST_NOT_FOUND",
                message=f"request not found: {request_id}",
                data={"request": request_id},
                debug={"request_id": request.call_id},
            )
        owning_service = self.session_service.for_workspace(str(job.get("workspace_root") or request.workspace_root))
        if bool(payload.get("force")):
            return command_failure(
                code="FORCE_CANCEL_NOT_SUPPORTED",
                message="force cancel requires process-isolated workers",
                data={"request_id": job_id, "state": job.get("state"), "command": job.get("method")},
            )
        if job.get("final"):
            return self._completed_already_response(job_id=job_id, job=job)
        with self._mutex:
            token = self._cancellation_tokens.get(job_id)
            future = self._futures.get(job_id)
            running = (future is not None and not future.done()) or job_id in self._paused_jobs
        if not running or token is None:
            return command_failure(
                code="REQUEST_NOT_RUNNING",
                message=f"request not running: {request_id}",
                data={"request": request_id, "state": job.get("state")},
                debug={"request_id": request.call_id},
            )
        token.cancel()
        with owning_service._mutex:
            updated_job = owning_service._load_job_record(job_id)
            if updated_job is None:
                return command_failure(
                    code="REQUEST_NOT_FOUND",
                    message=f"request not found: {request_id}",
                    data={"request": request_id},
                    debug={"request_id": request.call_id},
                )
            if not updated_job.get("final"):
                updated_job = owning_service.update_job_record(
                    job_id,
                    state="cancel_requested",
                    last_visible_stage="cancel_requested",
                )
        if updated_job.get("final") and (not resolved_from_memory or request_id == job_id):
            return self._completed_already_response(job_id=job_id, job=updated_job)
        with self._mutex:
            metadata = self._job_metadata.get(job_id)
            if metadata is not None:
                metadata["state"] = "cancel_requested"
                metadata["last_visible_stage"] = "cancel_requested"
            if self._active_game_job_id == job_id and self._active_game_job is not None:
                self._active_game_job["state"] = "cancel_requested"
                self._active_game_job["last_visible_stage"] = "cancel_requested"
        return command_success(
            data={"request_id": job_id, "state": "cancel_requested", "command": job["method"]},
            screenshot=None,
        )

    def _explicit_running_job_from_memory(self, request: DaemonRequest, service, *, job_id: str, wait_timeout: float) -> dict | None:
        with self._mutex:
            future = self._futures.get(job_id)
            metadata = deepcopy(self._job_metadata.get(job_id))
            running = future is not None and not future.done() and metadata is not None
        if not running:
            return None
        mismatch = self._job_mismatch_code(request, metadata)
        if mismatch is not None:
            return command_failure(
                code=mismatch,
                message="request id does not match submitted business command",
                data={"request": job_id, "executed": 0},
                debug={"request_id": request.call_id},
            )
        service.create_call_record(
            call_id=request.call_id,
            job_id=job_id,
            method=request.method,
            session_id=_session_id_for_request(request),
            state="attached",
            executed=False,
        )
        with self._mutex:
            if job_id in self._job_metadata:
                self._remember_call_mapping(request.call_id, job_id)
        if wait_timeout <= 0:
            return command_success(
                data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
                screenshot=None,
            )
        with self._mutex:
            future = self._futures.get(job_id)
        if future is not None:
            try:
                return future.result(timeout=wait_timeout)
            except TimeoutError:
                pass
        final_job = service.get_job_record(job_id)
        if final_job is not None and final_job.get("final"):
            envelope = final_job.get("last_envelope")
            if isinstance(envelope, dict):
                return deepcopy(envelope)
        return command_success(
            data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
            screenshot=None,
        )

    def _job_mismatch_code(self, request: DaemonRequest, job: dict) -> str | None:
        if _workspace_key(str(job.get("workspace_root") or "")) != _workspace_key(request.workspace_root):
            return "REQUEST_JOB_WORKSPACE_MISMATCH"
        if job.get("method") != request.method:
            return "REQUEST_JOB_METHOD_MISMATCH"
        if (job.get("session_id") or None) != (_session_id_for_request(request) or None):
            return "REQUEST_JOB_SESSION_MISMATCH"
        if job.get("payload_digest") != canonical_payload_digest(request.payload):
            return "REQUEST_JOB_PAYLOAD_MISMATCH"
        return None

    def _job_response(self, job: dict, *, job_id: str, wait_timeout: float) -> dict:
        if job.get("final"):
            envelope = job.get("last_envelope")
            if isinstance(envelope, dict):
                return deepcopy(envelope)
            return command_failure(
                code="REQUEST_TERMINAL_RECORD_INVALID",
                message="terminal request record missing envelope",
                data={"request": job_id},
            )
        if wait_timeout <= 0:
            return command_success(
                data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
                screenshot=None,
            )
        with self._mutex:
            future = self._futures.get(job_id)
            active_for_job = self._active_game_job_id == job_id
        if future is not None:
            try:
                response = future.result(timeout=wait_timeout)
                if active_for_job and response.get("request_id") == job_id:
                    self._release_completed_job(job_id)
                return response
            except TimeoutError:
                pass
        service = self.session_service.for_workspace(str(job.get("workspace_root") or ""))
        final_job = service.get_job_record(job_id)
        if final_job is not None and final_job.get("final"):
            envelope = final_job.get("last_envelope")
            if isinstance(envelope, dict):
                return deepcopy(envelope)
        return command_success(
            data={"state": "running", "request": job_id, "waited": int(wait_timeout)},
            screenshot=None,
        )

    def _reserve_non_game_job_or_active(self, request: DaemonRequest, service, *, job_key: str) -> dict | None:
        wait_timeout = float(request.control.wait_timeout or 0.0)
        with self._mutex:
            while True:
                job_id = self._job_key_to_job_id.get(job_key)
                if job_id is not None:
                    metadata = deepcopy(self._job_metadata.get(job_id))
                    future = self._futures.get(job_id)
                    if metadata is not None and future is not None and not future.done():
                        break
                    if metadata is not None and future is None and job_key in self._non_game_active_keys:
                        self._job_ready.wait()
                        continue
                    return None
                if job_key not in self._non_game_active_keys:
                    self._non_game_active_keys.add(job_key)
                    return None
                self._job_ready.wait()
        service.create_call_record(
            call_id=request.call_id,
            job_id=job_id,
            method=request.method,
            session_id=_session_id_for_request(request),
            state="attached",
            executed=False,
        )
        with self._mutex:
            if job_id in self._job_metadata:
                self._remember_call_mapping(request.call_id, job_id)
        return self._job_response(metadata, job_id=job_id, wait_timeout=wait_timeout)

    def _clear_non_game_job_key(self, job_key: str) -> None:
        with self._mutex:
            self._non_game_active_keys.discard(job_key)
            self._job_ready.notify_all()

    def _reserve_game_job_or_busy(self, request: DaemonRequest, service, *, job_id: str, session_id: str | None) -> dict | None:
        with self._mutex:
            while True:
                active_job_id = self._active_game_job_id
                active_command = self._active_game_command
                active_session = self._active_game_session
                if active_job_id is None:
                    self._active_game_job_id = job_id
                    self._active_game_command = request.method
                    self._active_game_session = session_id
                    return None
                future = self._futures.get(active_job_id)
                if future is not None:
                    if future.done():
                        self._release_completed_job_unlocked(active_job_id)
                        continue
                    active_job = self._active_game_job
                    if active_job is not None and active_job.get("job_key") == _job_key(request):
                        wait_timeout = float(request.control.wait_timeout or 0.0)
                        service.create_call_record(
                            call_id=request.call_id,
                            job_id=active_job_id,
                            method=request.method,
                            session_id=_session_id_for_request(request),
                            state="attached",
                            executed=False,
                        )
                        if active_job_id in self._job_metadata:
                            self._remember_call_mapping(request.call_id, active_job_id)
                        return command_success(
                            data={"state": "running", "request": active_job_id, "waited": int(wait_timeout)},
                            screenshot=None,
                        )
                    break
                self._active_game_ready.wait()
        service.create_rejected_call_record(
            call_id=request.call_id,
            method="daemon.request_submit",
            rejection_code="DAEMON_BUSY",
            active_job_id=active_job_id,
            active_command=active_command or "",
            active_session=active_session,
        )
        data = {
            "active_request": active_job_id,
            "active_command": active_command,
            "active_session": active_session,
            "executed": 0,
            "recover_action": active_command,
        }
        return command_failure(
            code="DAEMON_BUSY",
            message="daemon busy",
            data=data,
            debug={"request_id": request.call_id},
        )

    def _clear_reserved_game_job(self, job_id: str) -> None:
        with self._mutex:
            if self._active_game_job_id == job_id:
                self._active_game_job_id = None
                self._active_game_command = None
                self._active_game_session = None
                self._active_game_job = None
                self._active_game_ready.notify_all()

    def _execute_job(self, request: DaemonRequest, call_id: str, job_id: str, job_key: str) -> dict:
        service = request.session_service
        session_id = _session_id_for_request(request)
        lock_session_id = None if request.method == "session.create" else session_id
        use_lease = _requires_session_mutation_lease(request, resolved_session_id=session_id)
        try:
            request.control.cancellation_token.throw_if_cancelled()
            if use_lease:
                with service.session_mutation_lock(lock_session_id):
                    envelope = self._run_business_handler(request)
                    return self._persist_job_envelope(service, request, call_id, job_id, job_key, envelope, session_id=session_id)
            envelope = self._run_business_handler(request)
            return self._persist_job_envelope(service, request, call_id, job_id, job_key, envelope, session_id=session_id)
        except RequestCancelled:
            return self._persist_cancelled_job(service, request, call_id, job_id, job_key, session_id=session_id)

    def _run_business_handler(self, request: DaemonRequest) -> dict:
        try:
            request.control.cancellation_token.throw_if_cancelled()
            return self.command_service.execute_business_request(request)
        except RequestCancelled:
            raise
        except Exception as error:
            error_code = error.code if isinstance(error, TrailError) else type(error).__name__
            return command_failure(code=error_code, message=format_exception_message(error), data={})

    def _cancelled_envelope(self, request: DaemonRequest, *, unknown: bool) -> dict:
        return command_failure(
            code="REQUEST_CANCEL_UNKNOWN" if unknown else "REQUEST_CANCELLED",
            message="request cancelled after side effect" if unknown else "request cancelled",
            data={"tainted": bool(unknown)},
            debug={"request_id": request.request_id},
        )

    def _persist_cancelled_job(
        self,
        service,
        request: DaemonRequest,
        call_id: str,
        job_id: str,
        job_key: str,
        *,
        session_id: str | None,
    ) -> dict:
        unknown = request.control.side_effect_stage not in {None, "", "none"}
        envelope = self._cancelled_envelope(request, unknown=unknown)
        if unknown:
            service.finish_job_record(
                job_id,
                state="cancel_unknown",
                final_state="applied_but_not_persisted",
                last_visible_stage="cancel_unknown",
                side_effect_stage=request.control.side_effect_stage,
                envelope=envelope,
                tainted=True,
            )
            try:
                service.mark_session_tainted(session_id)
            except Exception:
                pass
            service.finish_call_record(
                call_id,
                job_id=job_id,
                method=request.method,
                session_id=session_id,
                state="cancel_unknown",
                executed=True,
            )
            return envelope
        service.finish_job_record(
            job_id,
            state="cancelled",
            final_state="failed_before_side_effect",
            last_visible_stage="cancelled",
            side_effect_stage="none",
            envelope=envelope,
            tainted=False,
        )
        service.finish_call_record(
            call_id,
            job_id=job_id,
            method=request.method,
            session_id=session_id,
            state="cancelled",
            executed=True,
        )
        return envelope


    def _persist_job_envelope(
        self,
        service,
        request: DaemonRequest,
        call_id: str,
        job_id: str,
        job_key: str,
        envelope: dict,
        *,
        session_id: str | None,
    ) -> dict:
        final_state = "failed_before_side_effect" if not envelope.get("ok", False) else "completed"
        try:
            self._persist_completed_job(
                service,
                request,
                call_id,
                job_id,
                job_key,
                envelope,
                session_id=session_id,
                final_state=final_state,
            )
        except Exception as error:
            unknown = command_failure(
                code="PERSISTED_BUT_RESPONSE_UNKNOWN",
                message=format_exception_message(error),
                data={},
            )
            self._persist_unknown_result(
                service,
                request,
                call_id,
                job_id,
                job_key,
                unknown,
                session_id=session_id,
            )
            return unknown
        return envelope


    def _persist_completed_job(
        self,
        service,
        request: DaemonRequest,
        call_id: str,
        job_id: str,
        job_key: str,
        envelope: dict,
        *,
        session_id: str | None,
        final_state: str = "completed",
    ) -> None:
        service.finish_job_record(
            job_id,
            state="completed" if final_state == "completed" else "failed",
            final_state=final_state,
            last_visible_stage="responded",
            side_effect_stage="state_persisted",
            envelope=envelope,
            tainted=False,
        )
        if session_id is not None:
            self._write_session_result(service, session_id=session_id, command=request.method, envelope=envelope)
        service.finish_call_record(
            call_id,
            job_id=job_id,
            method=request.method,
            session_id=session_id,
            state="completed" if final_state == "completed" else "failed",
            executed=True,
        )

    def _persist_unknown_result(
        self,
        service,
        request: DaemonRequest,
        call_id: str,
        job_id: str,
        job_key: str,
        envelope: dict,
        *,
        session_id: str | None,
    ) -> None:
        del job_key
        service.finish_job_record(
            job_id,
            state="failed",
            final_state="persisted_but_response_unknown",
            last_visible_stage="responded",
            side_effect_stage="unknown",
            envelope=envelope,
            tainted=True,
        )
        if session_id is not None:
            try:
                session = service.load_session(session_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                session = None
            if session is not None:
                session.scene_state.setdefault("daemon", {})["tainted"] = True
                service.save_session(session)
        service.finish_call_record(
            call_id,
            job_id=job_id,
            method=request.method,
            session_id=session_id,
            state="failed",
            executed=True,
        )

    def _write_session_result(self, service, *, session_id: str, command: str, envelope: dict) -> None:
        session = service.load_session(session_id)
        session.last_result = {
            "command": command,
            "ok": bool(envelope.get("ok")),
            "data": deepcopy(envelope.get("data") or {}),
            "error": deepcopy(envelope.get("error")),
        }
        screenshot = envelope.get("screenshot")
        if screenshot:
            session.last_screenshot = screenshot
        service.save_session(session)

    def _release_completed_job(self, job_id: str) -> None:
        with self._mutex:
            future = self._futures.get(job_id)
            if future is None or not future.done():
                return
            self._release_completed_job_unlocked(job_id)

    def _release_completed_job_unlocked(self, job_id: str) -> None:
        self._futures.pop(job_id, None)
        self._cancellation_tokens.pop(job_id, None)
        job_key = self._future_job_keys.pop(job_id, None)
        self._job_metadata.pop(job_id, None)
        stale_call_ids = [call_id for call_id, mapped_job_id in self._call_id_to_job_id.items() if mapped_job_id == job_id]
        for call_id in stale_call_ids:
            self._call_id_to_job_id.pop(call_id, None)
        if job_key is not None:
            self._job_key_to_job_id.pop(job_key, None)
            self._non_game_active_keys.discard(job_key)
        self._job_ready.notify_all()
        if self._active_game_job_id == job_id:
            self._active_game_job_id = None
            self._active_game_command = None
            self._active_game_session = None
            self._active_game_job = None

    def _worker_request(self, request: DaemonRequest, *, job_id: str, service) -> DaemonRequest:
        return DaemonRequest(
            request_id=job_id,
            call_id=request.call_id,
            job_id=job_id,
            protocol_version=request.protocol_version,
            workspace_root=request.workspace_root,
            session_id=request.session_id,
            verbose=request.verbose,
            method=request.method,
            payload=deepcopy(request.payload),
            control=request.control,
            session_service=service,
        )
