import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        self.ocr_result = [{"text": "点击进入"}]
        self.ocr_calls: list[dict[str, object | None]] = []
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

    def ocr(self, *, capture=None, ocr=None, **kwargs):
        self.ocr_calls.append(
            {
                "capture": None if capture is None else dict(capture),
                "ocr": ocr,
                "kwargs": dict(kwargs),
            }
        )
        return list(self.ocr_result)

    def click_point(self, x: int, y: int):
        del x, y
        if self.click_error is not None:
            raise self.click_error


class ProtocolRuntimeService:
    def __init__(self, runtime, *, launch_result: dict | None = None):
        self._runtime = runtime
        self.launch_result = {
            "started": True,
            "already_running": False,
            "path": r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe",
            "channel": "official",
            "args": [],
        }
        if launch_result is not None:
            self.launch_result = dict(launch_result)
        self.launch_calls: list[dict[str, object]] = []

    def get_runtime(self, *, workspace_root: str, window_binding):
        del workspace_root, window_binding
        return self._runtime

    def launch_game(self, **payload):
        self.launch_calls.append(dict(payload))
        return dict(self.launch_result)


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


def _set_stage(session, stage: dict):
    session.scene_state.setdefault("cw", {})["stage"] = dict(stage)
    return session


def _set_shop(session, shop: dict):
    session.scene_state.setdefault("cw", {})["shop"] = dict(shop)
    return session


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
    request_ids: list[str] = []

    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: request_ids.append(request.request_id)
        or {
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

    assert request_ids == [payload["debug"]["request_id"]]
    assert payload["debug"] == {
        "transport": "fake",
        "request_id": request_ids[0],
    }
    assert "request_id" not in payload


def test_client_preserves_request_id_in_debug_for_daemon_transport_errors(tmp_path: Path):
    request_ids: list[str] = []

    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: request_ids.append(request.request_id)
        or {
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
    assert request_ids == [payload["debug"]["request_id"]]
    assert payload["debug"] == {
        "source": "daemon",
        "request_id": request_ids[0],
    }
    assert "request_id" not in payload


def test_client_preserves_unknown_result_debug_fields_when_daemon_unavailable(tmp_path: Path):
    request_ids: list[str] = []

    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: request_ids.append(request.request_id)
        or {
            "request_id": request.request_id,
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {
                "last_known_stage": "side_effect_applied",
                "stage_detail": "state persisted marker failed",
                "recovery_detail": "recovery finish failed",
            },
            "error": {
                "code": "DAEMON_UNAVAILABLE",
                "message": "mutation result unknown",
            },
        },
    )

    payload = client.call("input.click", {"x": 10, "y": 20})

    assert request_ids == [payload["debug"]["request_id"]]
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert payload["debug"] == {
        "request_id": request_ids[0],
        "last_known_stage": "side_effect_applied",
        "stage_detail": "state persisted marker failed",
        "recovery_detail": "recovery finish failed",
    }
    assert "request_id" not in payload


def test_command_service_handles_session_create(tmp_path: Path):
    registry = SessionServiceRegistry()
    runtime_service = SimpleNamespace(attach_window=lambda window_title: {"title": window_title, "hwnd": 1})
    command_service = CommandService(runtime_service=runtime_service, session_service=registry)
    request = DaemonRequest(
        request_id="req-session-create",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=None,
        verbose=False,
        method="session.create",
        payload={"window_title": "崩坏：星穹铁道"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["session_id"]
    assert payload["data"]["window_binding"] == {"title": "崩坏：星穹铁道", "hwnd": 1}


def test_command_service_handles_state_dump(tmp_path: Path):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(attach_window=lambda window_title: {"title": window_title, "hwnd": 1})
    command_service = CommandService(runtime_service=runtime_service, session_service=registry)
    request = DaemonRequest(
        request_id="req-state-dump",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="state.dump",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["session_id"] == session.session_id


def test_command_service_handles_cw_stage_detect(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}),
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-stage-detect",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"


def test_command_service_routes_cw_shop_buy_slot_through_mutation_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.buy_cw_shop_slot",
        lambda session, slot, expect, buyer, scanner: _set_shop(session, {"slot": slot, "expect": expect}),
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-buy-slot",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_slot",
        payload={"session_id": session.session_id, "slot": 2, "expect": "希儿"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert registry.for_workspace(str(tmp_path)).request_status("req-cw-shop-buy-slot")["final_state"] == "completed"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["shop"]["slot"] == 2


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
    assert captured["cw_service"] is not None
    assert getattr(captured["cw_service"], "runtime_service") is runtime_service
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
    assert type(response["screenshot"]) is str
    assert type(response["references"][0]) is dict
    assert response["screenshot"] == "input-fail.png"
    assert response["warnings"] == [{"code": "WINDOW_NOT_FOREGROUND", "message": "窗口未前台"}]
    assert response["references"] == [{"path": "trail/ref.png", "similarity": 0.97, "screenshot": "input-fail.png"}]
    assert response["debug"] == {"trace": [{"step": "click"}]}
    assert registry.for_workspace(str(tmp_path)).request_status("req-server-fail")["final_state"] == "failed_before_side_effect"


def test_server_handle_payload_routes_window_launch_without_game_path(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime_service = ProtocolRuntimeService(
        ProtocolRuntime(tmp_path / "window-launch.png"),
        launch_result={
            "started": True,
            "already_running": False,
            "path": r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe",
            "channel": "official",
            "args": ["-popupwindow"],
        },
    )
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-window-launch-no-path",
            method="window.launch",
            payload={
                "channel": "official",
                "launch_args": ["-popupwindow"],
                "use_cmd": False,
            },
        )
    )

    assert response["ok"] is True
    assert response["data"] == {
        "started": True,
        "already_running": False,
        "path": r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe",
        "channel": "official",
        "args": ["-popupwindow"],
    }
    assert response["screenshot"] is None
    assert runtime_service.launch_calls == [
        {
            "channel": "official",
            "launch_args": ["-popupwindow"],
            "use_cmd": False,
        }
    ]


def test_server_handle_payload_routes_window_launch_without_game_path_through_real_runtime_service(tmp_path: Path, monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    launch_calls: list[dict[str, object]] = []

    def fake_launch_game(*, game_path=None, channel="official", launch_args=None, use_cmd=False):
        launch_calls.append(
            {
                "game_path": game_path,
                "channel": channel,
                "launch_args": list(launch_args or []),
                "use_cmd": use_cmd,
            }
        )
        raise TrailError("GAME_PATH_REQUIRED", "请提供游戏路径")

    monkeypatch.setattr("trail.runtime.window.launch_game", fake_launch_game)
    command_service = CommandService(
        runtime_service=RuntimeService(),
        session_service=SessionServiceRegistry(),
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-window-launch-real-runtime-service",
            method="window.launch",
            payload={
                "channel": "official",
                "launch_args": ["-popupwindow"],
                "use_cmd": False,
            },
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-window-launch-real-runtime-service"
    assert response["error"] == {
        "code": "GAME_PATH_REQUIRED",
        "message": "请提供游戏路径",
    }
    assert response["screenshot"] is None
    assert response["warnings"] == []
    assert response["references"] == []
    assert launch_calls == [
        {
            "game_path": None,
            "channel": "official",
            "launch_args": ["-popupwindow"],
            "use_cmd": False,
        }
    ]


def test_server_handle_payload_promotes_window_launch_warnings_to_envelope(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime_service = ProtocolRuntimeService(
        ProtocolRuntime(tmp_path / "window-launch-warning.png"),
        launch_result={
            "started": True,
            "already_running": False,
            "path": r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe",
            "channel": "official",
            "args": [],
            "warnings": [
                {
                    "code": "GAME_PATH_PERSIST_FAILED",
                    "message": "游戏已成功启动，但历史路径持久化失败: disk full",
                }
            ],
        },
    )
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-window-launch-warning",
            method="window.launch",
            payload={
                "channel": "official",
                "launch_args": [],
                "use_cmd": False,
            },
        )
    )

    assert response["ok"] is True
    assert response["data"] == {
        "started": True,
        "already_running": False,
        "path": r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe",
        "channel": "official",
        "args": [],
    }
    assert response["warnings"] == [
        {
            "code": "GAME_PATH_PERSIST_FAILED",
            "message": "游戏已成功启动，但历史路径持久化失败: disk full",
        }
    ]


def test_server_handle_payload_window_launch_explicit_launch_failure_keeps_stable_error_code(tmp_path: Path, monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")

    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)
    monkeypatch.setattr("trail.runtime.window.write_launch_path", lambda *args, **kwargs: None)
    monkeypatch.setattr("trail.runtime.window.change_game_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "trail.runtime.window.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("launch explode")),
    )

    command_service = CommandService(
        runtime_service=RuntimeService(),
        session_service=SessionServiceRegistry(),
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-window-launch-explicit-fail",
            method="window.launch",
            payload={
                "game_path": str(executable),
                "channel": "official",
                "launch_args": [],
                "use_cmd": False,
            },
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-window-launch-explicit-fail"
    assert response["error"]["code"] == "GAME_LAUNCH_FAILED"
    assert "launch explode" in response["error"]["message"]
    assert response["error"]["code"] != "UNEXPECTED_ERROR"


def test_server_handle_ocr_protocol_request_routes_ocr_mode_and_retry_high_options_to_runtime(tmp_path: Path, monkeypatch):
    from trail.runtime.ocr_config import OcrRequestConfig

    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime = ProtocolRuntime(tmp_path / "ocr-provider.png")
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(runtime),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-ocr-provider",
            method="ocr.read",
            payload={
                "from_x": 1,
                "from_y": 2,
                "to_x": 3,
                "to_y": 4,
                "provider": "dml",
                "lang": "ch",
                "use_cls": "false",
                "text_score": "0.6",
                "ocr_mode": "high",
                "retry_high": "always",
            },
        )
    )

    assert response["ok"] is True
    assert response["data"] == {"result": [{"text": "点击进入"}]}
    assert response["screenshot"] == "ocr-provider.png"
    assert runtime.ocr_calls == [
        {
            "capture": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            "ocr": OcrRequestConfig(
                provider="dml",
                lang="ch",
                use_cls=False,
                text_score=0.6,
                ocr_mode="high",
                retry_high="always",
            ),
            "kwargs": {},
        }
    ]


@pytest.mark.parametrize(
    ("invalid_payload", "expected_code", "expected_message"),
    [
        ({"ocr_mode": "warp"}, "OCR_INPUT_INVALID", "unsupported ocr mode: warp"),
        ({"retry_high": "sometimes"}, "OCR_INPUT_INVALID", "unsupported ocr retry_high: sometimes"),
    ],
)
def test_server_handle_payload_rejects_invalid_ocr_mode_or_retry_high_payload_values(
    tmp_path: Path,
    monkeypatch,
    invalid_payload: dict[str, object],
    expected_code: str,
    expected_message: str,
):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime = ProtocolRuntime(tmp_path / "ocr-invalid-mode.png")
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(runtime),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-ocr-invalid-mode",
            method="ocr.read",
            payload=invalid_payload,
        )
    )

    assert response["ok"] is False
    assert response["error"] == {"code": expected_code, "message": expected_message}
    assert runtime.ocr_calls == []


@pytest.mark.parametrize(
    ("invalid_payload", "expected_code", "expected_message"),
    [
        ({"provider": "gpu"}, "OCR_INPUT_INVALID", "unsupported ocr provider: gpu"),
        ({"lang": "en"}, "OCR_LANG_UNSUPPORTED", "unsupported ocr lang: en"),
        ({"text_score": "not-a-float"}, "OCR_INPUT_INVALID", "invalid ocr text score: not-a-float"),
        ({"provider": "dml", "use_clss": True}, "OCR_INPUT_INVALID", "unknown ocr payload fields: use_clss"),
    ],
)
def test_server_handle_payload_rejects_invalid_ocr_payload_values(
    tmp_path: Path,
    monkeypatch,
    invalid_payload: dict[str, object],
    expected_code: str,
    expected_message: str,
):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime = ProtocolRuntime(tmp_path / "ocr-invalid.png")
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(runtime),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-ocr-invalid",
            method="ocr.read",
            payload=invalid_payload,
        )
    )

    assert response["ok"] is False
    assert response["error"] == {"code": expected_code, "message": expected_message}
    assert runtime.ocr_calls == []


def test_server_handle_ocr_read_returns_ocr_no_result_when_fast_has_no_hits(tmp_path: Path, monkeypatch):
    from trail.runtime.ocr_config import OcrRequestConfig

    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    runtime = ProtocolRuntime(tmp_path / "ocr-no-result.png")
    runtime.ocr_result = []
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(runtime),
        session_service=registry,
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-ocr-no-result",
            method="ocr.read",
            payload={"ocr_mode": "fast", "retry_high": "auto", "provider": "cpu"},
        )
    )

    assert response["ok"] is False
    assert response["error"] == {"code": "OCR_NO_RESULT", "message": "OCR 无结果"}
    assert runtime.ocr_calls == [
        {
            "capture": {},
            "ocr": OcrRequestConfig(provider="cpu", lang="ch", use_cls=False, text_score=0.5, ocr_mode="fast", retry_high="auto"),
            "kwargs": {},
        }
    ]


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


def test_server_handle_payload_marks_terminal_failure_when_mark_executing_fails(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "mark-executing-fail.png")),
        session_service=registry,
    )
    service = registry.for_workspace(str(tmp_path))

    def fail_mark_executing(**kwargs):
        raise OSError("executing marker failed")

    monkeypatch.setattr(service, "mark_executing", fail_mark_executing)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-mark-executing-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-mark-executing-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "OSError",
        "message": "executing marker failed",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert status["last_visible_stage"] == "responded"


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
    assert type(response["screenshot"]) is str
    assert response["screenshot"] == "post-handler-fail.png"
    assert response["debug"]["last_known_stage"] == "state_persisted"
    assert "OSError" in response["debug"]["detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert loaded.scene_state["daemon"]["tainted"] is True


def test_server_handle_payload_keeps_unknown_result_envelope_when_unknown_marker_fails(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "unknown-marker-fail.png")),
        session_service=registry,
    )
    service = registry.for_workspace(str(tmp_path))
    envelope = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/unknown-marker-fail.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"detail": "persisted_but_response_unknown"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "persisted_but_response_unknown"},
    }

    def fail_mark_state_persisted(**kwargs):
        raise OSError("state persisted marker failed")

    monkeypatch.setattr(service, "mark_state_persisted", fail_mark_state_persisted)
    monkeypatch.setattr(
        command_service,
        "_mutating_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(PersistedButResponseUnknown(envelope=envelope)),
    )
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-unknown-marker-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
            session_id=session.session_id,
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-unknown-marker-fail")
    assert response["ok"] is False
    assert response["error"] == envelope["error"]
    assert response["screenshot"] == envelope["screenshot"]
    assert "state persisted marker failed" in response["debug"]["stage_detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["last_visible_stage"] == "responded"


def test_server_handle_payload_keeps_cw_unknown_result_envelope_for_late_ui_failure(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = ProtocolRuntimeService(ProtocolRuntime(tmp_path / "cw-enter-late-fail.png"))
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def late_failure(session, mode, difficulty, battle_mode, runtime):
        del session, mode, difficulty, battle_mode
        runtime.click_point(640, 360)
        raise TrailError("CW_ENTRY_UI_NOT_FOUND", "late failure after ui action")

    monkeypatch.setattr("trail.daemon.cw_service.enter_cw", late_failure)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-cw-enter-late-fail",
            method="cw.enter",
            payload={"session_id": session.session_id, "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
            session_id=session.session_id,
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-cw-enter-late-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["debug"] == {"detail": "TrailError: late failure after ui action"}
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True


def test_server_handle_payload_keeps_risky_terminal_state_when_recovery_finish_fails(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(
        runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "recovery-finish-fail.png")),
        session_service=registry,
    )
    service = registry.for_workspace(str(tmp_path))
    original_finish_mutation = service.finish_mutation

    def fail_mark_state_persisted(**kwargs):
        raise OSError("persist marker failed")

    def fail_recovery_finish_mutation(**kwargs):
        if kwargs["final_state"] != "completed":
            raise OSError("recovery finish failed")
        return original_finish_mutation(**kwargs)

    monkeypatch.setattr(service, "mark_state_persisted", fail_mark_state_persisted)
    monkeypatch.setattr(service, "finish_mutation", fail_recovery_finish_mutation)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-server-recovery-finish-fail",
            method="input.click",
            payload={"x": 10, "y": 20},
            session_id=session.session_id,
        )
    )

    status = registry.for_workspace(str(tmp_path)).request_status("req-server-recovery-finish-fail")
    assert response["ok"] is False
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert "recovery finish failed" in response["debug"]["recovery_detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["last_visible_stage"] == "responded"
    assert status["tainted"] is True


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


def test_daemon_socket_timeout_budget_covers_long_running_scene_commands():
    import trail.daemon.client as client_module

    assert client_module.SOCKET_RESPONSE_TIMEOUT_SECONDS >= 120.0


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


def test_send_daemon_request_uses_extended_read_timeout(monkeypatch, tmp_path: Path):
    settimeouts: list[float] = []
    sent: list[bytes] = []

    class Reader:
        def readline(self):
            return json.dumps(build_success_response(request_id="req-timeout", data={"captured": True}), ensure_ascii=False)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def settimeout(self, value):
            settimeouts.append(value)

        def sendall(self, payload):
            sent.append(payload)

        def makefile(self, *args, **kwargs):
            return Reader()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("trail.daemon.client.socket.create_connection", lambda *args, **kwargs: Connection())

    payload = send_daemon_request(
        DaemonRequest(
            request_id="req-timeout",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="ocr.read",
            payload={},
        ),
        "token-1",
        endpoint="127.0.0.1:8765",
    )

    assert payload["ok"] is True
    assert settimeouts == [120.0]
    assert sent
