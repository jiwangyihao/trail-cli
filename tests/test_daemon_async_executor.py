from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
import threading

import pytest

from trail.core.errors import TrailError
from trail.daemon.models import DaemonRequest, RequestCancelled, RequestControl
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.daemon.request_executor import RequestExecutor
from trail.daemon.session_service import SessionServiceRegistry
from trail.output.envelope import command_success


def _request(
    tmp_path: Path,
    *,
    method: str,
    call_id: str,
    payload: dict[str, Any],
    session_id: str | None = None,
    job_id: str | None = None,
    control: RequestControl | None = None,
    workspace_root: str | None = None,
) -> DaemonRequest:
    return DaemonRequest(
        request_id=call_id,
        call_id=call_id,
        job_id=job_id,
        protocol_version=PROTOCOL_VERSION,
        workspace_root=workspace_root or str(tmp_path),
        session_id=session_id,
        verbose=False,
        method=method,
        payload=payload,
        control=control or RequestControl(wait_timeout=0.0),
    )


class SlowBusinessService:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        self.started.set()
        self.release.wait(2)
        return command_success(data={"done": True}, screenshot=None)


class BlockingBeforeSideEffectService:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        request.control.cancellation_token.throw_if_cancelled()
        return command_success(data={"unreachable": True}, screenshot=None)


class SideEffectThenBlockService:
    def __init__(self, *, wait_timeout: float | None = 2.0, block_started: threading.Event | None = None) -> None:
        self.calls = 0
        self.side_effect_applied = threading.Event()
        self.release = threading.Event()
        self.release_started = threading.Event()
        self.block_started = block_started
        self.wait_timeout = wait_timeout
    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        request.control.side_effect_stage = "side_effect_applied"
        self.side_effect_applied.set()
        self.release_started.set()
        if self.block_started is not None:
            self.block_started.wait()
        self.release.wait(self.wait_timeout)
        request.control.cancellation_token.throw_if_cancelled()
        return command_success(data={"unreachable": True}, screenshot=None)





class BlockingBusinessService(SlowBusinessService):
    def __init__(self) -> None:
        super().__init__()
        self.entered_wait = threading.Event()

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        self.started.set()
        self.entered_wait.set()
        if request.call_id != "call-1" and request.method != "guide.list.cw":
            return command_success(data={"done": True}, screenshot=None)
        self.release.wait(30)
        return command_success(data={"done": True}, screenshot=None)




class CountingService:
    def __init__(self) -> None:
        self.calls = 0

    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        return command_success(data={"call": self.calls}, screenshot=None)


class FastControlService(CountingService):
    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        if request.method == "daemon.reconcile_session":
            return command_success(data={"session_id": request.payload["session_id"], "tainted": False}, screenshot=None)
        if request.method == "daemon.request_status":
            return command_success(data={"request_id": request.payload["request_id"]}, screenshot=None)
        raise AssertionError(f"unexpected control-plane call: {request.method}")


class SessionMutatingService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session_id = request.session_id or request.payload.get("session_id")
        session = request.session_service.load_session(session_id)
        session.scene_state["selected_guide"] = request.payload["guide_id"]
        request.session_service.save_session(session)
        return command_success(data={"selected": request.payload["guide_id"]}, screenshot=None)


class SessionCreateService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session = request.session_service.create_session(window_binding=request.payload["window_binding"])
        return command_success(data={"session_id": session.session_id}, screenshot=None)


class StateDumpService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session = request.session_service.load_session(request.payload["session_id"])
        return command_success(data={"session_id": session.session_id, "scene_state": session.scene_state}, screenshot=None)


class SessionResultWritingService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        if request.method.startswith("cw."):
            session = request.session_service.load_session(request.session_id)
            session.scene_state.setdefault("cw", {}).setdefault("shop", {})["stale"] = True
            request.session_service.save_session(session)
        return command_success(data={"method": request.method}, screenshot=Path(".trail/shots/session-result.png"))


class FailingService(CountingService):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        raise self.error


class GateBusinessService(SlowBusinessService):
    def __init__(self) -> None:
        super().__init__()
        self.enter = threading.Event()
        self.allow_return = threading.Event()

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        self.started.set()
        self.enter.wait(1)
        self.allow_return.wait(1)
        return command_success(data={"done": True}, screenshot=None)


class SessionReadService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session_id = request.session_id or request.payload.get("session_id")
        session = request.session_service.load_session(session_id)
        session.scene_state.setdefault("read", {})["business"] = True
        request.session_service.save_session(session)
        return command_success(data={"read": True}, screenshot=Path(".trail/shots/read.png"))


class FailingPersistService(CountingService):
    def __init__(self, fail_on: str) -> None:
        super().__init__()
        self.fail_on = fail_on

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session_id = request.session_id or request.payload.get("session_id")
        session = request.session_service.load_session(session_id)
        session.scene_state.setdefault("side_effect", {})["applied"] = True
        request.session_service.save_session(session)
        return command_success(data={"applied": True}, screenshot=Path(".trail/shots/persist.png"))


class FailingSubmitExecutor(ThreadPoolExecutor):
    def submit(self, *args: Any, **kwargs: Any):
        raise OSError("submit failed")


def test_executor_marks_persisted_unknown_when_session_result_save_fails(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    original_write = RequestExecutor._write_session_result

    def fail_write(self, service_arg, *, session_id: str, command: str, envelope: dict) -> None:
        original_write(self, service_arg, session_id=session_id, command=command, envelope=envelope)
        raise OSError("session result save failed")

    monkeypatch.setattr(RequestExecutor, "_write_session_result", fail_write)
    executor = RequestExecutor(command_service=FailingPersistService("session"), session_service=registry)

    response = executor.handle(
        _request(
            tmp_path,
            method="cw.shop.scan",
            call_id="call-persist-fail",
            session_id=session.session_id,
            payload={"session_id": session.session_id},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    status = service.request_status("call-persist-fail")
    assert response["ok"] is False
    assert response["error"]["code"] == "PERSISTED_BUT_RESPONSE_UNKNOWN"
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["side_effect_stage"] == "unknown"
    assert status["tainted"] is True


def test_executor_rejects_busy_only_after_active_job_record_exists(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    business = GateBusinessService()
    executor = RequestExecutor(command_service=business, session_service=registry)
    created = threading.Event()
    release_create = threading.Event()
    original_create_job_record = service.create_job_record

    def slow_create_job_record(*args: Any, **kwargs: Any):
        created.set()
        release_create.wait(1)
        return original_create_job_record(*args, **kwargs)

    monkeypatch.setattr(service, "create_job_record", slow_create_job_record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            lambda: executor.handle(
                _request(tmp_path, method="ocr.read", call_id="call-durable-one", payload={}, control=RequestControl(wait_timeout=0.0))
            )
        )
        assert created.wait(1)
        second = pool.submit(
            lambda: executor.handle(
                _request(tmp_path, method="screen.shot", call_id="call-durable-two", payload={}, control=RequestControl(wait_timeout=0.0))
            )
        )
        with pytest.raises(TimeoutError):
            second.result(timeout=0.1)
        release_create.set()
        busy = second.result(timeout=2)
        business.allow_return.set()
        running = first.result(timeout=2)

    assert running["ok"] is True
    assert busy["ok"] is False
    assert service.get_job_record(busy["data"]["active_request"]) is not None


def test_executor_submit_failure_rolls_back_game_reservation(tmp_path: Path):
    executor = RequestExecutor(command_service=CountingService(), session_service=SessionServiceRegistry())
    failing_pool = FailingSubmitExecutor(max_workers=1)
    executor._pool = failing_pool

    with pytest.raises(OSError, match="submit failed"):
        executor.handle(_request(tmp_path, method="ocr.read", call_id="call-submit-fails", payload={}, control=RequestControl(wait_timeout=0.0)))

    failing_pool.shutdown(wait=False, cancel_futures=True)
    executor._pool = ThreadPoolExecutor(max_workers=1)
    response = executor.handle(
        _request(tmp_path, method="screen.shot", call_id="call-after-submit-fail", payload={}, control=RequestControl(wait_timeout=1.0))
    )
    assert response["ok"] is True


def test_executor_preserves_trail_error_code(tmp_path: Path):
    executor = RequestExecutor(
        command_service=FailingService(TrailError("SESSION_REQUIRED", "session missing")),
        session_service=SessionServiceRegistry(),
    )

    response = executor.handle(
        _request(tmp_path, method="ocr.read", call_id="call-error", payload={}, control=RequestControl(wait_timeout=1.0))
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "SESSION_REQUIRED"


def test_executor_cleans_finished_future_and_allows_next_fast_game_command(tmp_path: Path):
    executor = RequestExecutor(command_service=CountingService(), session_service=SessionServiceRegistry())

    first = executor.handle(
        _request(tmp_path, method="ocr.read", call_id="call-fast-one", payload={}, control=RequestControl(wait_timeout=1.0))
    )
    second = executor.handle(
        _request(tmp_path, method="screen.shot", call_id="call-fast-two", payload={}, control=RequestControl(wait_timeout=1.0))
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert executor._futures == {}


def test_completed_game_future_cleanup_window_does_not_attach_or_busy(tmp_path: Path):
    executor = RequestExecutor(command_service=CountingService(), session_service=SessionServiceRegistry())
    with executor._mutex:
        executor._active_game_job_id = "stale-job"
        executor._active_game_command = "ocr.read"
        executor._active_game_session = None
        executor._active_game_job = {"job_id": "stale-job", "job_key": "stale-key"}
        done_future = Future()
        done_future.set_result(command_success(data={"stale": True}, screenshot=None))
        executor._futures["stale-job"] = done_future

    response = executor.handle(
        _request(tmp_path, method="screen.shot", call_id="call-fast-two", payload={}, control=RequestControl(wait_timeout=1.0))
    )

    assert response["ok"] is True
    assert response["data"] == {"call": 1}

def test_executor_leases_session_attached_read_result_writes(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.install_test_lease_assertion(session.session_id, required_until_call_state="completed")
    executor = RequestExecutor(command_service=SessionReadService(), session_service=registry)

    response = executor.handle(
        _request(
            tmp_path,
            method="ocr.read",
            call_id="call-read-session",
            session_id=session.session_id,
            payload={},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert response["ok"] is True
    assert service.request_status("call-read-session")["state"] == "completed"


def test_executor_leases_payload_session_attached_read_result_writes(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.install_test_lease_assertion(session.session_id, required_until_call_state="completed")
    executor = RequestExecutor(command_service=SessionReadService(), session_service=registry)

    response = executor.handle(
        _request(
            tmp_path,
            method="ocr.read",
            call_id="call-read-payload-session",
            session_id=None,
            payload={"session_id": session.session_id},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert response["ok"] is True
    assert service.request_status("call-read-payload-session")["state"] == "completed"


def test_resending_same_business_command_attaches_to_running_job_without_duplicate_execution(tmp_path: Path):
    business = BlockingBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    first = executor.handle(
        _request(
            tmp_path,
            method="cw.battle.run",
            call_id="call-1",
            session_id=None,
            payload={"timeout": 90},
            control=RequestControl(wait_timeout=0.0),
            workspace_root=str(tmp_path),
        )
    )
    assert first["ok"] is True, first
    assert business.started.wait(1)
    assert business.entered_wait.wait(1), first

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            executor.handle,
            _request(
                tmp_path,
                method="cw.battle.run",
                call_id="call-2",
                session_id=None,
                payload={"timeout": 90},
                job_id=first["data"]["request"],
                control=RequestControl(wait_timeout=0.0),
                workspace_root=str(tmp_path),
            ),
        )
        second = pending.result(timeout=1)
    assert second["data"]["request"] == first["data"]["request"]
    assert executor.session_service.for_workspace(str(tmp_path)).request_status("call-2")["job_id"] == first["data"]["request"]
    assert executor.session_service.for_workspace(str(tmp_path)).request_status("call-2")["executed"] is False
    business.release.set()


def test_without_request_id_terminal_same_payload_creates_new_job(tmp_path: Path):
    service = CountingService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())

    first = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-1", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0))
    )
    second = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-2", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0))
    )

    assert first["data"] == {"call": 1}
    assert second["data"] == {"call": 2}


def test_resending_same_non_game_business_command_attaches_to_running_job(tmp_path: Path):
    business = BlockingBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    first = executor.handle(
        _request(
            tmp_path,
            method="guide.list.cw",
            call_id="call-1",
            payload={"window_binding": {"title": "崩坏：星穹铁道"}},
            control=RequestControl(wait_timeout=0.0),
            workspace_root=str(tmp_path),
        )
    )
    assert first["ok"] is True, first
    assert business.started.wait(1)
    assert business.entered_wait.wait(1), first
    service = executor.session_service.for_workspace(str(tmp_path))
    assert executor.session_service.for_workspace(str(tmp_path)).find_latest_job_for_method("guide.list.cw")["final"] is False

    second = executor.handle(
        _request(
            tmp_path,
            method="guide.list.cw",
            call_id="call-2",
            payload={"window_binding": {"title": "崩坏：星穹铁道"}},
            control=RequestControl(wait_timeout=0.0),
            workspace_root=str(tmp_path),
        )
    )

    assert second["data"]["request"] == first["data"]["request"]
    assert business.calls == 1
    business.release.set()


def test_concurrent_same_non_game_business_command_attaches_after_reservation(tmp_path: Path, monkeypatch):
    business = BlockingBusinessService()
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    executor = RequestExecutor(command_service=business, session_service=registry)
    entered_create = threading.Event()
    release_create = threading.Event()
    original_create_job_record = service.create_job_record

    def slow_create_job_record(*args: Any, **kwargs: Any):
        entered_create.set()
        release_create.wait(5)
        return original_create_job_record(*args, **kwargs)

    monkeypatch.setattr(service, "create_job_record", slow_create_job_record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor.handle,
            _request(
                tmp_path,
                method="guide.list.cw",
                call_id="call-1",
                payload={"window_binding": {"title": "崩坏：星穹铁道"}},
                control=RequestControl(wait_timeout=0.0),
                workspace_root=str(tmp_path),
            ),
        )
        assert entered_create.wait(1)
        second = pool.submit(
            executor.handle,
            _request(
                tmp_path,
                method="guide.list.cw",
                call_id="call-2",
                payload={"window_binding": {"title": "崩坏：星穹铁道"}},
                control=RequestControl(wait_timeout=0.0),
                workspace_root=str(tmp_path),
            ),
        )
        assert second.done() is False
        release_create.set()
        assert business.started.wait(1)
        assert business.entered_wait.wait(1), first
        first_response = first.result(timeout=1)
        second_response = second.result(timeout=1)

    assert second_response["data"]["request"] == first_response["data"]["request"]
    assert business.calls == 1
    business.release.set()
def test_explicit_request_id_replays_terminal_job_without_handler_call(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    first = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-1", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0))
    )
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("input.click")["job_id"]

    replay = executor.handle(
        _request(
            tmp_path,
            method="input.click",
            call_id="call-2",
            job_id=job_id,
            payload={"x": 1, "y": 2},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert replay["data"] == first["data"]
    assert service.calls == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"method": "ocr.read"}, "REQUEST_JOB_METHOD_MISMATCH"),
        ({"session_id": "other-session"}, "REQUEST_JOB_SESSION_MISMATCH"),
        ({"workspace_root": "other"}, "REQUEST_JOB_WORKSPACE_MISMATCH"),
    ],
)
def test_explicit_job_id_mismatch_does_not_execute_handler(tmp_path: Path, change: dict[str, str], code: str):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    executor.handle(
        _request(
            tmp_path,
            method="cw.battle.run",
            call_id="call-1",
            session_id="sess-1",
            payload={"timeout": 90},
            control=RequestControl(wait_timeout=1.0),
        )
    )
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("cw.battle.run")["job_id"]
    workspace_root = str(tmp_path / change["workspace_root"]) if "workspace_root" in change else str(tmp_path)

    response = executor.handle(
        _request(
            tmp_path,
            method=change.get("method", "cw.battle.run"),
            call_id="call-2",
            job_id=job_id,
            session_id=change.get("session_id", "sess-1"),
            payload={"timeout": 90},
            workspace_root=workspace_root,
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert service.calls == 1


def test_explicit_request_id_that_matches_call_record_is_rejected_without_execution(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    executor.handle(_request(tmp_path, method="ocr.read", call_id="call-original", payload={}, control=RequestControl(wait_timeout=1.0)))

    response = executor.handle(
        _request(tmp_path, method="ocr.read", call_id="call-2", job_id="call-original", payload={}, control=RequestControl(wait_timeout=1.0))
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "REQUEST_ID_IS_CALL_ID"
    assert service.calls == 1


def test_executor_returns_running_when_wait_budget_expires(tmp_path: Path):
    session_services = SessionServiceRegistry()
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=session_services)
    request = _request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0))

    response = executor.handle(request)

    assert response["ok"] is True
    assert response["data"]["state"] == "running"
    assert response["data"]["request"]
    assert response["data"]["waited"] == 0
    assert "command" not in response["data"]
    assert business.started.wait(1)
    assert business.calls == 1
    business.release.set()


def test_request_result_returns_terminal_job_inner_envelope(tmp_path: Path):
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=CountingService(), session_service=registry)
    response = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-fast", payload={}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("ocr.read")["job_id"]

    result = executor.handle(
        _request(
            tmp_path,
            method="daemon.request_result",
            call_id="call-result",
            payload={"request_id": job_id},
            control=RequestControl(wait_timeout=0.0),
        )
    )

    assert result["ok"] is True
    assert result["data"] == {"render_command": "ocr.read", "envelope": response}
    assert result["data"]["envelope"] is not response


def test_request_result_rejects_running_job_without_envelope(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]

    try:
        result = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_result",
                call_id="call-result",
                payload={"request_id": job_id},
                control=RequestControl(wait_timeout=0.0),
            )
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "REQUEST_NOT_FINAL"
    finally:
        business.release.set()



def test_cancel_before_worker_starts_finishes_with_request_cancelled_envelope(tmp_path: Path):
    service = BlockingBeforeSideEffectService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry(), start_workers_paused=True)
    submitted = executor.submit_paused_for_testing(
        _request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0))
    )

    executor.handle(
        _request(
            tmp_path,
            method="daemon.request_cancel",
            call_id="call-cancel",
            payload={"request_id": submitted.job_id},
            control=RequestControl(wait_timeout=0.0),
        )
    )
    executor.release_paused_job_for_testing(submitted.job_id)
    final = executor.handle(
        _request(
            tmp_path,
            method="ocr.read",
            call_id="call-replay",
            job_id=submitted.job_id,
            payload={},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"
    assert service.entered.is_set() is False
    status = executor.session_service.for_workspace(str(tmp_path)).request_status(submitted.job_id)
    assert status["state"] == "cancelled"
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False


def test_cancel_before_side_effect_finishes_with_request_cancelled_envelope(tmp_path: Path):
    service = BlockingBeforeSideEffectService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert service.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    service.release.set()
    final = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"
    status = executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)
    assert status["state"] == "cancelled"
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False


def test_cancel_after_side_effect_maps_to_cancel_unknown_and_taints(tmp_path: Path):
    service = SideEffectThenBlockService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="input.click", call_id="call-click", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert service.side_effect_applied.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    service.release.set()
    final = executor.handle(_request(tmp_path, method="input.click", call_id="call-replay", job_id=job_id, payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCEL_UNKNOWN"
    status = executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)
    assert status["state"] == "cancel_unknown"
    assert status["tainted"] is True
    assert status["final_state"] == "applied_but_not_persisted"


def test_cancel_after_side_effect_taints_session_and_blocks_cw_mutation(tmp_path: Path):
    registry = SessionServiceRegistry()
    registry_service = registry.for_workspace(str(tmp_path))
    session = registry_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = SideEffectThenBlockService()
    executor = RequestExecutor(command_service=service, session_service=registry, start_workers_paused=True)
    paused = executor.submit_paused_for_testing(
        _request(
            tmp_path,
            method="input.click",
            call_id="call-click-session",
            session_id=session.session_id,
            payload={"session_id": session.session_id, "x": 1, "y": 2},
            control=RequestControl(wait_timeout=0.0),
        )
    )
    job_id = paused.job_id
    executor.release_paused_job_for_testing(job_id)
    assert service.release_started.wait(1)

    cancel = executor.handle(
        _request(
            tmp_path,
            method="daemon.request_cancel",
            call_id="call-cancel-session",
            payload={"request_id": "call-click-session"},
            control=RequestControl(wait_timeout=0.0),
        )
    )
    assert cancel["data"] == {"request_id": job_id, "state": "cancel_requested", "command": "input.click"}
    service.release.set()
    final = executor.handle(
        _request(
            tmp_path,
            method="input.click",
            call_id="call-replay-session",
            job_id=job_id,
            session_id=session.session_id,
            payload={"session_id": session.session_id, "x": 1, "y": 2},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCEL_UNKNOWN"
    assert registry_service.is_session_tainted(session.session_id) is True
    blocked = registry_service.begin_mutation(
        session_id=session.session_id,
        request_id="call-next-cw",
        command_name="cw.shop.scan",
        enforce_cw_tainted=True,
    )
    assert blocked == {"status": "session_tainted", "session_id": session.session_id}





def test_request_cancel_marks_running_job_cancel_requested(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    token = executor._cancellation_tokens[job_id]
    assert token.is_cancelled() is False

    try:
        cancel = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_cancel",
                call_id="call-cancel",
                payload={"request_id": job_id},
                control=RequestControl(wait_timeout=0.0),
            )
        )

        assert cancel["ok"] is True
        assert cancel["data"] == {"request_id": job_id, "state": "cancel_requested", "command": "ocr.read"}
        assert executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)["state"] == "cancel_requested"
        assert token.is_cancelled() is True
        assert running["ok"] is True
    finally:
        business.release.set()


def test_request_cancel_resolves_attached_call_id_to_running_job(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    service = registry.for_workspace(str(tmp_path))
    token = executor._cancellation_tokens[job_id]

    try:
        assert business.started.wait(1)
        attached = executor.handle(
            _request(
                tmp_path,
                method="ocr.read",
                call_id="call-attach",
                job_id=job_id,
                payload={},
                control=RequestControl(wait_timeout=0.0),
            )
        )
        cancel = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_cancel",
                call_id="call-cancel-attached",
                payload={"request_id": "call-attach"},
                control=RequestControl(wait_timeout=0.0),
            )
        )

        assert attached["ok"] is True
        assert attached["data"]["request"] == job_id
        assert cancel["ok"] is True
        assert cancel["data"] == {"request_id": job_id, "state": "cancel_requested", "command": "ocr.read"}
        assert token.is_cancelled() is True
        assert service.request_status(job_id)["state"] == "cancel_requested"
        assert service.request_status("call-attach")["job_id"] == job_id
    finally:
        business.release.set()


def test_request_cancel_force_is_unsupported_and_does_not_change_running_job(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    status_before = registry.for_workspace(str(tmp_path)).request_status(job_id)
    token = executor._cancellation_tokens[job_id]

    try:
        response = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_cancel",
                call_id="call-force",
                payload={"request_id": job_id, "force": True, "confirm_taint": True},
                control=RequestControl(wait_timeout=0.0),
            )
        )
        status_after = registry.for_workspace(str(tmp_path)).request_status(job_id)

        assert response["ok"] is False
        assert response["error"]["code"] == "FORCE_CANCEL_NOT_SUPPORTED"
        assert status_after["state"] == status_before["state"]
        assert token.is_cancelled() is False
    finally:
        business.release.set()


def test_request_cancel_completed_job_returns_completed_already(tmp_path: Path):
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=CountingService(), session_service=registry)
    response = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-fast", payload={}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("ocr.read")["job_id"]

    cancel = executor.handle(
        _request(
            tmp_path,
            method="daemon.request_cancel",
            call_id="call-cancel-completed",
            payload={"request_id": job_id},
            control=RequestControl(wait_timeout=0.0),
        )
    )

    assert response["ok"] is True
    assert cancel["ok"] is True
    assert cancel["data"] == {"request_id": job_id, "state": "completed_already", "command": "ocr.read"}
    assert registry.for_workspace(str(tmp_path)).request_status(job_id)["state"] == "completed"



def test_late_cancel_completed_already_does_not_change_last_envelope(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    first = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("ocr.read")["job_id"]

    cancel = executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    replay = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert cancel["data"]["state"] == "completed_already"
    assert replay["data"] == first["data"]


def test_request_cancel_resolves_active_attach_call_id_from_memory(tmp_path: Path):
    business = BlockingBusinessService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(
        _request(
            tmp_path,
            method="cw.battle.run",
            call_id="call-1",
            payload={"timeout": 90},
            control=RequestControl(wait_timeout=0.0),
        )
    )
    job_id = running["data"]["request"]
    token = executor._cancellation_tokens[job_id]

    try:
        assert business.entered_wait.wait(1)
        attached = executor.handle(
            _request(
                tmp_path,
                method="cw.battle.run",
                call_id="call-active-attach",
                payload={"timeout": 90},
                control=RequestControl(wait_timeout=0.0),
            )
        )
        service = registry.for_workspace(str(tmp_path))
        original_get_call_record = service.get_call_record

        def fail_get_call_record(call_id: str):
            if call_id == "call-active-attach":
                raise AssertionError("active attached call_id cancel must use executor memory")
            return original_get_call_record(call_id)

        service.get_call_record = fail_get_call_record
        cancel = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_cancel",
                call_id="call-cancel-active-attach",
                payload={"request_id": "call-active-attach"},
                control=RequestControl(wait_timeout=0.0),
            )
        )

        assert attached["data"]["request"] == job_id
        assert cancel["ok"] is True
        assert cancel["data"] == {"request_id": job_id, "state": "cancel_requested", "command": "cw.battle.run"}
        assert token.is_cancelled() is True
    finally:
        business.release.set()

def test_request_cancel_does_not_overwrite_job_completed_during_cancel_update(tmp_path: Path, monkeypatch):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    token = executor._cancellation_tokens[job_id]
    original_load_job_record = service._load_job_record
    race_triggered = False

    def racing_load_job_record(loaded_job_id: str):
        nonlocal race_triggered
        record = original_load_job_record(loaded_job_id)
        if (
            loaded_job_id == job_id
            and record is not None
            and record.get("state") == "accepted"
            and not record.get("final")
            and token.is_cancelled()
            and not race_triggered
        ):
            race_triggered = True
            completed = {
                **record,
                "state": "completed",
                "final": True,
                "final_state": "completed",
                "last_visible_stage": "responded",
                "side_effect_stage": "state_persisted",
                "last_envelope": command_success(data={"done": True}, screenshot=None),
                "tainted": False,
            }
            service._save_job_record(completed)
            return completed
        return record

    monkeypatch.setattr(service, "_load_job_record", racing_load_job_record)

    try:
        assert business.started.wait(1)
        cancel = executor.handle(
            _request(
                tmp_path,
                method="daemon.request_cancel",
                call_id="call-cancel-race",
                payload={"request_id": job_id},
                control=RequestControl(wait_timeout=0.0),
            )
        )
        status = service.request_status(job_id)

        assert race_triggered is True
        assert token.is_cancelled() is True
        assert cancel["ok"] is True
        assert cancel["data"] == {"request_id": job_id, "state": "completed_already", "command": "ocr.read"}
        assert status["state"] == "completed"
        assert status["final"] is True
        assert status["final_state"] == "completed"
    finally:
        business.release.set()



def test_reconcile_session_runs_sync_while_game_operation_busy(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(
        _request(
            tmp_path,
            method="ocr.read",
            call_id="call-1",
            payload={},
            control=RequestControl(wait_timeout=0.0),
        )
    )

    try:
        assert running["ok"] is True
        assert business.started.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: executor.handle(
                    _request(
                        tmp_path,
                        method="daemon.reconcile_session",
                        call_id="call-reconcile",
                        payload={"session_id": session.session_id},
                        control=RequestControl(wait_timeout=0.0),
                    )
                )
            )
            response = future.result(timeout=0.5)

        assert response["ok"] is True
        assert response["data"]["session_id"] == session.session_id
        assert response["data"].get("state") != "running"
    finally:
        business.release.set()


def test_reconcile_session_does_not_create_job_record(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=FastControlService(), session_service=registry)
    executor.handle(
        _request(
            tmp_path,
            method="daemon.reconcile_session",
            call_id="call-reconcile",
            payload={"session_id": session.session_id},
            control=RequestControl(wait_timeout=0.0),
        )
    )

    assert service.get_job_record("call-reconcile") is None
    assert service.find_latest_job_for_method("daemon.reconcile_session") is None


def test_executor_reconcile_session_waits_for_target_session_mutation_lease(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=FastControlService(), session_service=registry)
    entered = threading.Event()
    release = threading.Event()
    original_lock = service.session_mutation_lock
    lock_attempted = threading.Event()

    def observed_lock(session_id: str | None):
        if session_id == session.session_id:
            lock_attempted.set()
        return original_lock(session_id)

    service.session_mutation_lock = observed_lock

    def hold_lease():
        with original_lock(session.session_id):
            entered.set()
            release.wait(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_lease)
        assert entered.wait(1)
        second = pool.submit(
            lambda: executor.handle(
                _request(
                    tmp_path,
                    method="daemon.reconcile_session",
                    call_id="call-reconcile",
                    session_id=session.session_id,
                    payload={"session_id": session.session_id},
                    control=RequestControl(wait_timeout=0.0),
                )
            )
        )
        assert lock_attempted.wait(1)
        assert not second.done()
        release.set()
        first.result(timeout=2)
        response = second.result(timeout=2)

    assert response["ok"] is True
    assert service.get_job_record("call-reconcile") is None


def test_request_status_runs_sync_while_game_operation_busy(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(
        _request(
            tmp_path,
            method="cw.battle.run",
            call_id="call-1",
            session_id=session.session_id,
            payload={"session_id": session.session_id, "timeout": 90},
            control=RequestControl(wait_timeout=0.0),
        )
    )
    job_id = running["data"]["request"]

    try:
        assert business.started.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: executor.handle(
                    _request(
                        tmp_path,
                        method="daemon.request_status",
                        call_id="call-status",
                        payload={"request_id": job_id},
                        control=RequestControl(wait_timeout=0.0),
                    )
                )
            )
            response = future.result(timeout=0.5)

        assert response["ok"] is True
        assert response["data"]["request_id"] == job_id
        assert response["data"]["state"] in {"accepted", "running", "completed"}
        assert registry.for_workspace(str(tmp_path)).get_job_record("call-status") is None
        assert service.find_latest_job_for_method("daemon.request_status") is None
    finally:
        business.release.set()


def test_executor_returns_final_business_envelope_within_wait_budget(tmp_path: Path):
    executor = RequestExecutor(command_service=CountingService(), session_service=SessionServiceRegistry())
    response = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-fast", payload={}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert response["data"] == {"call": 1}




def test_executor_holds_session_mutation_lease_through_business_and_journal_writes(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=SessionMutatingService(), session_service=registry)

    response = executor.handle(
        _request(
            tmp_path,
            method="guide.fetch.cw",
            call_id="call-guide",
            session_id=session.session_id,
            payload={"session_id": session.session_id, "guide_id": "g-1", "select": True},
            control=RequestControl(wait_timeout=1.0),
        )
    )

    assert response["ok"] is True
    loaded = service.load_session(session.session_id)
    assert loaded.scene_state["selected_guide"] == "g-1"
    status = service.request_status("call-guide")
    assert status["state"] == "completed"
    assert status["executed"] is True


def test_executor_session_create_uses_workspace_lease_through_journal(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    executor = RequestExecutor(command_service=SessionCreateService(), session_service=registry)
    entered = threading.Event()
    release = threading.Event()
    service.install_test_lease_probe(session_id=None, entered=entered, release=release)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: executor.handle(_request(tmp_path, method="session.create", call_id="call-create", payload={"window_binding": {"title": "崩坏：星穹铁道", "hwnd": 1}}, control=RequestControl(wait_timeout=1.0))))
        assert entered.wait(1)
        second = pool.submit(lambda: service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2}))
        assert not second.done()
        release.set()
        response = first.result(timeout=2)
        second.result(timeout=2)

    assert response["ok"] is True
    assert service.request_status("call-create")["state"] == "completed"


def test_executor_state_dump_is_read_only_and_does_not_hold_session_mutation_lease(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=StateDumpService(), session_service=registry)
    service.install_test_lease_probe(session_id=session.session_id, fail_if_entered=True)

    response = executor.handle(_request(tmp_path, method="state.dump", call_id="call-dump", session_id=session.session_id, payload={"session_id": session.session_id}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert service.get_job_record("call-dump") is None
    assert service.load_session(session.session_id).scene_state == session.scene_state


@pytest.mark.parametrize("method,payload", [
    ("start.run", {"window_title": "崩坏：星穹铁道", "channel": "official"}),
    ("input.click", {"x": 1, "y": 2}),
    ("cw.shop.scan", {"session_id": "sess-1"}),
])
def test_session_result_writing_business_commands_hold_lease_until_job_and_call_journal(tmp_path: Path, method: str, payload: dict[str, Any]):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    if "session_id" in payload:
        payload = {**payload, "session_id": session.session_id}
    executor = RequestExecutor(command_service=SessionResultWritingService(), session_service=registry)
    service.install_test_lease_assertion(session.session_id, required_until_call_state="completed")

    response = executor.handle(_request(tmp_path, method=method, call_id=f"call-{method}", session_id=session.session_id, payload=payload, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert service.request_status(f"call-{method}")["state"] == "completed"
    loaded = service.load_session(session.session_id)
    assert loaded.last_result is not None
    assert loaded.last_screenshot == ".trail/shots/session-result.png"
    if method.startswith("cw."):
        assert loaded.scene_state["cw"]["shop"]["stale"] is True
