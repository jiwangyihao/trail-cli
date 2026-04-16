import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.cli import app
from trail.core.errors import TrailError
import trail.daemon.server as daemon_server_module
from trail.daemon.command_service import CommandService, PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted
from trail.daemon.client import TrailDaemonClient, send_daemon_request
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.daemon.server import TrailDaemonServer
from trail.daemon.session_service import SessionServiceRegistry
from tests.support.fake_daemon import (
    FakeDaemonClient,
    build_success_response,
    fake_round_trip_transport,
    start_fake_daemon_server,
    write_installed_manifest,
    write_ready_manifest,
)


class ProtocolRuntime:
    def __init__(self, screenshot_path: Path, *, click_error: Exception | None = None):
        self._screenshot_path = screenshot_path
        self.click_error = click_error
        self.warnings: list[dict] = []
        self.references: list[dict] = []
        self.trace: list[dict] = []

    def capture_after_action(self, optional: bool = False):
        del optional
        return str(self._screenshot_path)

    def collect_warnings(self):
        warnings = list(self.warnings)
        self.warnings.clear()
        return warnings

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return list(self.references)

    def consume_debug_trace(self):
        trace = list(self.trace)
        self.trace.clear()
        return trace

    def click_point(self, x: int, y: int):
        del x, y
        if self.click_error is not None:
            raise self.click_error


class ProtocolRuntimeService:
    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        return self._runtime


class FailingProtocolRuntimeService:
    def __init__(self, error: Exception):
        self._error = error

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        raise self._error


def _server_payload(
    tmp_path: Path,
    *,
    token: str,
    request_id: str,
    method: str,
    payload: dict,
    session_id: str | None = None,
    verbose: bool = False,
):
    return {
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "workspace_root": str(tmp_path),
        "session_id": session_id,
        "verbose": verbose,
        "method": method,
        "payload": payload,
        "token": token,
    }


def test_protocol_version_is_fixed():
    assert PROTOCOL_VERSION == 1


def test_client_returns_bootstrap_required_envelope_when_manifest_missing(tmp_path: Path):
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: None,
    )

    payload = client.call("ocr.read", {})

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_BOOTSTRAP_REQUIRED",
        "message": "daemon bootstrap not installed",
    }
    assert payload["debug"]["request_id"]


def test_client_call_builds_protocol_request(tmp_path: Path):
    requests: list[DaemonRequest] = []
    tokens: list[str] = []
    endpoints: list[str] = []

    def fake_transport(request: DaemonRequest, token: str, *, endpoint: str):
        requests.append(request)
        tokens.append(token)
        endpoints.append(endpoint)
        return build_success_response(
            request_id=request.request_id,
            data={"captured": True},
            screenshot=".trail/shots/req-1.png",
        )

    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=fake_transport,
    )

    payload = client.call("screen.shot", {"region": "main"}, session_id="session-1")

    assert payload["ok"] is True
    assert len(requests) == 1
    assert requests[0].workspace_root == str(tmp_path)
    assert requests[0].method == "screen.shot"
    assert requests[0].protocol_version == PROTOCOL_VERSION
    assert requests[0].session_id == "session-1"
    assert requests[0].payload == {"region": "main"}
    assert requests[0].request_id
    assert tokens == ["token-1"]
    assert endpoints == ["127.0.0.1:8765"]


def test_client_moves_transport_request_id_into_debug_when_verbose(tmp_path: Path):
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: {
            "request_id": request.request_id,
            "ok": True,
            "data": {"clicked": [10, 20]},
            "screenshot": ".trail/shots/req-1.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"transport": "fake"},
            "error": None,
        },
    )

    payload = client.call("input.click", {"x": 10, "y": 20}, verbose=True)

    assert payload["debug"] == {
        "transport": "fake",
        "request_id": payload["debug"]["request_id"],
    }
    assert "request_id" not in payload


def test_client_preserves_request_id_in_debug_for_daemon_transport_errors(tmp_path: Path):
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: {
            "request_id": request.request_id,
            "ok": False,
            "data": None,
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"source": "daemon"},
            "error": {
                "code": "DAEMON_AUTH_FAILED",
                "message": "daemon token mismatch",
            },
        },
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_AUTH_FAILED",
        "message": "daemon token mismatch",
    }
    assert payload["debug"] == {
        "source": "daemon",
        "request_id": payload["debug"]["request_id"],
    }
    assert "request_id" not in payload


def test_daemon_request_status_command_calls_daemon_method(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "daemon.request_status": build_success_response(
                request_id="req-daemon-request-status",
                data={
                    "request_id": "req-42",
                    "method": "input.click",
                    "workspace_root": str(tmp_path),
                    "session_id": None,
                    "final_state": "completed",
                    "last_visible_stage": "responded",
                    "tainted": False,
                    "started_at": "2026-04-16T00:00:00+00:00",
                    "updated_at": "2026-04-16T00:00:01+00:00",
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "request-status", "--request-id", "req-42"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["request_id"] == "req-42"
    assert client.calls == [
        {
            "method": "daemon.request_status",
            "payload": {"request_id": "req-42"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_daemon_reconcile_session_command_calls_daemon_method(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "daemon.reconcile_session": build_success_response(
                request_id="req-daemon-reconcile-session",
                data={"session_id": "session-1", "tainted": False},
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "reconcile-session", "--session", "session-1"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"session_id": "session-1", "tainted": False}
    assert client.calls == [
        {
            "method": "daemon.reconcile_session",
            "payload": {"session_id": "session-1"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_server_main_injects_session_service_registry(monkeypatch):
    captured: dict[str, object] = {}
    runtime_service = object()

    monkeypatch.setattr(daemon_server_module, "RuntimeService", lambda: runtime_service)

    def fake_command_service(*, runtime_service, session_service=None, cw_service=None):
        captured["runtime_service"] = runtime_service
        captured["session_service"] = session_service
        captured["cw_service"] = cw_service
        return SimpleNamespace(session_service=session_service)

    class FakeTrailDaemonServer:
        def __init__(self, *, command_service):
            captured["command_service"] = command_service

        def serve_forever(self):
            captured["served"] = True

    monkeypatch.setattr(daemon_server_module, "CommandService", fake_command_service)
    monkeypatch.setattr(daemon_server_module, "TrailDaemonServer", FakeTrailDaemonServer)

    daemon_server_module.main()

    assert captured["runtime_service"] is runtime_service
    assert isinstance(captured["session_service"], SessionServiceRegistry)
    assert captured["served"] is True


def test_server_handle_payload_preserves_captured_mutation_failure_envelope(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    runtime = ProtocolRuntime(
        tmp_path / "input-fail.png",
        click_error=TrailError("INPUT_BACKEND_MISSING", "input backend missing"),
    )
    runtime.warnings = [{"code": "WINDOW_NOT_FOREGROUND", "message": "窗口未前台"}]
    runtime.references = [{"path": "trail/ref.png", "similarity": 0.97}]
    runtime.trace = [{"step": "click"}]
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(runtime),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
            verbose=True,
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "INPUT_BACKEND_MISSING",
        "message": "input backend missing",
    }
    assert response["screenshot"] == str(tmp_path / "input-fail.png")
    assert response["warnings"] == [{"code": "WINDOW_NOT_FOREGROUND", "message": "窗口未前台"}]
    assert response["references"] == [{"path": "trail/ref.png", "similarity": 0.97}]
    assert response["debug"] == {"trace": [{"step": "click"}]}
    assert registry.for_workspace(str(tmp_path)).request_status("req-server-fail")["final_state"] == "failed_before_side_effect"


def test_server_handle_payload_records_runtime_prefight_failure_in_journal(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=FailingProtocolRuntimeService(TrailError("WINDOW_NOT_FOUND", "window not found")),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-runtime-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-runtime-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": "window not found",
    }
    assert status["final_state"] == "failed_before_side_effect"


@pytest.mark.parametrize(
    ("error_type", "final_state"),
    [
        (SideEffectAppliedButStateNotPersisted, "applied_but_not_persisted"),
        (PersistedButResponseUnknown, "persisted_but_response_unknown"),
    ],
)
def test_server_handle_payload_preserves_unknown_result_envelope(
    tmp_path: Path,
    monkeypatch,
    error_type,
    final_state: str,
):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "ok.png")),
        session_service=registry,
    )
    envelope = {
        "ok": False,
        "data": {},
        "screenshot": f".trail/shots/{final_state}.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"detail": final_state},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": final_state},
    }
    monkeypatch.setattr(
        command_service,
        "_mutating_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(error_type(envelope=envelope)),
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id=f"req-{final_state}",
            method="input.click",
            payload={"x": 10, "y": 20},
            session_id=session.session_id,
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status(f"req-{final_state}")
    loaded = registry.for_workspace(str(tmp_path)).load_session(session.session_id)
    assert response["error"] == envelope["error"]
    assert response["screenshot"] == envelope["screenshot"]
    assert response["debug"] == envelope["debug"]
    assert status["final_state"] == final_state
    assert loaded.scene_state["daemon"]["tainted"] is True


def test_server_handle_payload_keeps_unknown_result_envelope_for_post_handler_failure(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "post-handler-fail.png")),
        session_service=registry,
    )
    service = registry.for_workspace(str(tmp_path))
    original_finish_mutation = service.finish_mutation

    def fail_completed_finish_mutation(**kwargs):
        if kwargs["final_state"] == "completed":
            raise OSError("flush failed")
        return original_finish_mutation(**kwargs)

    monkeypatch.setattr(service, "finish_mutation", fail_completed_finish_mutation)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-post-handler-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
            session_id=session.session_id,
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-post-handler-fail")
    loaded = registry.for_workspace(str(tmp_path)).load_session(session.session_id)
    assert response["ok"] is False
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["screenshot"] == str(tmp_path / "post-handler-fail.png")
    assert response["debug"]["last_known_stage"] == "state_persisted"
    assert "OSError" in response["debug"]["detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert loaded.scene_state["daemon"]["tainted"] is True


def test_client_attempts_bootstrap_when_ready_runtime_is_missing_endpoint(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="ready")
    started: list[Path] = []

    def fake_start(home: Path) -> bool:
        started.append(home)
        write_ready_manifest(home, endpoint="127.0.0.1:9001", token_value="token-2")
        return True

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=daemon_home,
        transport=lambda request, token, *, endpoint: build_success_response(
            request_id=request.request_id,
            data={"captured": True, "token": token, "endpoint": endpoint},
        ),
        starter=fake_start,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is True
    assert payload["data"] == {
        "captured": True,
        "token": "token-2",
        "endpoint": "127.0.0.1:9001",
    }
    assert started == [daemon_home]


def test_client_attempts_bootstrap_when_runtime_not_ready(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")

    started: list[Path] = []

    def fake_start(home: Path) -> bool:
        started.append(home)
        write_ready_manifest(home, endpoint="127.0.0.1:8765", token_value="token-1")
        return True

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=daemon_home,
        transport=lambda request, token, *, endpoint: {
            "request_id": request.request_id,
            "ok": True,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
        starter=fake_start,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is True
    assert started == [daemon_home]


def test_client_returns_daemon_start_failed_when_bootstrap_cannot_start(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="starting")
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=daemon_home,
        transport=lambda request, token, *, endpoint: None,
        starter=lambda home: False,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_START_FAILED",
        "message": "daemon start failed",
    }
    assert payload["debug"]["request_id"]


@pytest.mark.parametrize(
    ("transport_error",),
    [
        (ConnectionRefusedError("connection refused"),),
    ],
)
def test_client_retries_connection_failures_by_bootstrapping(tmp_path: Path, transport_error: Exception):
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    calls: list[tuple[str, str]] = []
    started: list[Path] = []

    def fake_start(home: Path) -> bool:
        started.append(home)
        write_ready_manifest(home, endpoint="127.0.0.1:9002", token_value="token-2")
        return True

    def fake_transport(request: DaemonRequest, token: str, *, endpoint: str):
        calls.append((token, endpoint))
        if len(calls) == 1:
            raise transport_error
        return build_success_response(
            request_id=request.request_id,
            data={"captured": True, "token": token, "endpoint": endpoint},
        )

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=fake_transport,
        starter=fake_start,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is True
    assert payload["data"] == {
        "captured": True,
        "token": "token-2",
        "endpoint": "127.0.0.1:9002",
    }
    assert calls == [
        ("token-1", "127.0.0.1:8765"),
        ("token-2", "127.0.0.1:9002"),
    ]
    assert started == [tmp_path / "daemon-home"]


def test_client_does_not_bootstrap_on_socket_timeout(tmp_path: Path):
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    started: list[Path] = []

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: (_ for _ in ()).throw(socket.timeout("timed out")),
        starter=lambda home: started.append(home) or True,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "daemon unavailable",
    }
    assert "TimeoutError" in payload["debug"]["detail"]
    assert started == []


def test_client_returns_daemon_unavailable_when_transport_returns_non_object_json(tmp_path: Path):
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(5)

    def handle_once() -> None:
        with server:
            connection, _ = server.accept()
            with connection:
                chunks = b""
                while not chunks.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks += chunk
                connection.sendall(b"[]\n")

    thread = threading.Thread(target=handle_once)
    thread.start()
    endpoint = f"127.0.0.1:{server.getsockname()[1]}"
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint=endpoint,
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=send_daemon_request,
    )

    payload = client.call("screen.shot", {})
    thread.join(timeout=5)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert payload["debug"]["request_id"]
    assert "list" in payload["debug"]["detail"]


def test_fake_daemon_client_records_request_metadata():
    responses = {
        "ocr.read": {
            "ok": True,
            "data": {"result": [{"text": "点击进入"}]},
            "screenshot": ".trail/shots/req-1.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        }
    }
    client = FakeDaemonClient(
        responses=responses
    )
    responses["ocr.read"]["data"]["result"][0]["text"] = "外部污染"
    request_payload = {"query": {"lang": "zh"}}

    payload = client.call(
        method="ocr.read",
        payload=request_payload,
        workspace_root="C:/repo",
        session_id=None,
        verbose=False,
    )
    request_payload["query"]["lang"] = "en"
    payload["data"]["result"][0]["text"] = "调用方污染"

    fresh_payload = client.call(
        method="ocr.read",
        payload={},
        workspace_root="C:/repo",
        session_id=None,
        verbose=False,
    )

    assert payload["ok"] is True
    assert client.calls[0] == {
        "method": "ocr.read",
        "payload": {"query": {"lang": "zh"}},
        "workspace_root": "C:/repo",
        "session_id": None,
        "verbose": False,
    }
    assert fresh_payload["data"]["result"][0]["text"] == "点击进入"


def test_build_success_response_snapshots_nested_data():
    data = {"result": [{"text": "点击进入"}]}

    payload = build_success_response(request_id="req-1", data=data)
    data["result"][0]["text"] = "外部污染"

    assert payload["data"] == {"result": [{"text": "点击进入"}]}


def test_fake_round_trip_transport_forwards_request_metadata_and_auth():
    responses = {
        "ocr.read": build_success_response(
            request_id="req-1",
            data={"result": [{"text": "点击进入"}]},
            screenshot=".trail/shots/req-1.png",
        )
    }
    server = start_fake_daemon_server(responses)
    responses["ocr.read"]["data"]["result"][0]["text"] = "外部污染"
    transport = fake_round_trip_transport(server)
    request_payload = {"query": {"lang": "zh"}}

    payload = transport(
        DaemonRequest(
            request_id="req-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root="C:/repo",
            session_id=None,
            verbose=False,
            method="ocr.read",
            payload=request_payload,
        ),
        token="token-1",
        endpoint="npipe://traild",
    )
    request_payload["query"]["lang"] = "en"
    payload["data"]["result"][0]["text"] = "调用方污染"

    fresh_payload = transport(
        DaemonRequest(
            request_id="req-2",
            protocol_version=PROTOCOL_VERSION,
            workspace_root="C:/repo",
            session_id=None,
            verbose=False,
            method="ocr.read",
            payload={},
        ),
        token="token-1",
        endpoint="npipe://traild",
    )

    assert server.requests[0] == {
        "request_id": "req-1",
        "protocol_version": PROTOCOL_VERSION,
        "workspace_root": "C:/repo",
        "session_id": None,
        "verbose": False,
        "method": "ocr.read",
        "payload": {"query": {"lang": "zh"}},
        "token": "token-1",
        "endpoint": "npipe://traild",
    }
    assert fresh_payload == {
        "request_id": "req-1",
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": ".trail/shots/req-1.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def test_send_daemon_request_round_trips_with_fake_server(tmp_path: Path):
    response = build_success_response(
        request_id="req-1",
        data={"captured": True},
        screenshot=".trail/shots/req-1.png",
    )
    fake_server = start_fake_daemon_server({"screen.shot": response})

    payload = send_daemon_request(
        DaemonRequest(
            request_id="req-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="screen.shot",
            payload={},
        ),
        "token-1",
        endpoint="127.0.0.1:8765",
        server=fake_server,
    )

    assert payload["ok"] is True
    assert fake_server.requests == [
        {
            "request_id": "req-1",
            "protocol_version": PROTOCOL_VERSION,
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
            "method": "screen.shot",
            "payload": {},
            "token": "token-1",
            "endpoint": "127.0.0.1:8765",
        }
    ]


def test_send_daemon_request_round_trips_over_real_socket(tmp_path: Path):
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(5)
    received: list[dict[str, object]] = []

    def handle_once() -> None:
        with server:
            connection, _ = server.accept()
            with connection:
                chunks = b""
                while not chunks.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks += chunk
                received.append(json.loads(chunks.decode("utf-8")))
                connection.sendall(
                    json.dumps(
                        build_success_response(
                            request_id="req-socket-1",
                            data={"captured": True},
                            screenshot=".trail/shots/req-socket-1.png",
                        ),
                        ensure_ascii=False,
                    ).encode("utf-8")
                    + b"\n"
                )

    thread = threading.Thread(target=handle_once)
    thread.start()
    endpoint = f"127.0.0.1:{server.getsockname()[1]}"

    payload = send_daemon_request(
        DaemonRequest(
            request_id="req-socket-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id="session-1",
            verbose=True,
            method="screen.shot",
            payload={"region": "main"},
        ),
        "token-1",
        endpoint=endpoint,
    )
    thread.join(timeout=5)

    assert payload["ok"] is True
    assert received == [
        {
            "request_id": "req-socket-1",
            "protocol_version": PROTOCOL_VERSION,
            "workspace_root": str(tmp_path),
            "session_id": "session-1",
            "verbose": True,
            "method": "screen.shot",
            "payload": {"region": "main"},
            "token": "token-1",
        }
    ]
