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
from trail.output.envelope import command_failure
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


class StartRuntimeServiceStub:
    def __init__(
        self,
        *,
        attach_binding: dict[str, object] | None = None,
        launch_result: dict[str, object] | None = None,
        attach_failures_before_success: int = 0,
    ):
        self.attach_binding = attach_binding or {"title": "崩坏：星穹铁道", "hwnd": 123}
        self.launch_result = launch_result or {
            "started": True,
            "already_running": False,
            "path": "demo.exe",
            "channel": "official",
            "args": [],
        }
        self.attach_failures_before_success = attach_failures_before_success
        self.attach_attempts = 0
        self.launch_calls: list[dict[str, object]] = []

    def attach_window(self, *, window_title: str):
        self.attach_attempts += 1
        if self.attach_attempts <= self.attach_failures_before_success:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return dict(self.attach_binding)

    def launch_game(self, **payload):
        self.launch_calls.append(dict(payload))
        return dict(self.launch_result)

    def start_run(self, *, window_title: str, game_path: str | None = None, channel: str = "official"):
        try:
            return self.attach_window(window_title=window_title)
        except TrailError as error:
            if error.code != "WINDOW_NOT_FOUND":
                raise

        launch_result = self.launch_game(game_path=game_path, channel=channel)
        if not launch_result.get("started") and not launch_result.get("already_running"):
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        while True:
            try:
                return self.attach_window(window_title=window_title)
            except TrailError as error:
                if error.code != "WINDOW_NOT_FOUND":
                    raise


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


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> dict[str, object]:
    return {"left": left, "top": top, "width": width, "height": height, "source": alias}


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


def test_command_service_start_run_returns_session_and_window_facts(tmp_path: Path):
    runtime_service = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["session"]
    assert payload["data"]["reused"] in {0, 1}
    assert payload["data"]["title"] == "崩坏：星穹铁道"
    assert payload["data"]["hwnd"] == 123


@pytest.mark.parametrize(
    "launch_result",
    [
        {"started": True, "already_running": False, "path": "demo.exe", "channel": "official", "args": []},
        {"started": False, "already_running": True, "path": "demo.exe", "channel": "official", "args": []},
    ],
)
def test_start_run_waits_for_attach_after_window_launch_started_or_already_running(
    tmp_path: Path,
    launch_result: dict[str, object],
):
    runtime = StartRuntimeServiceStub(launch_result=launch_result, attach_failures_before_success=2)
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime, session_service=session_services)
    request = SimpleNamespace(
        request_id="req-start-wait",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=None,
        verbose=False,
        method="start.run",
        payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
    )

    result = service.handle(request)

    assert result["ok"] is True
    assert runtime.attach_attempts == 3
    assert runtime.launch_calls == [{"game_path": None, "channel": "official"}]


def test_runtime_service_start_run_accepts_attach_on_last_allowed_attempt(monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    attempts = {"count": 0}
    times = iter([0.0, 0.0, 0.5, 1.0, 1.0])

    def fake_attach_window(self, *, window_title: str):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return {"title": window_title, "hwnd": 123}

    monkeypatch.setattr("trail.daemon.runtime_service.monotonic", lambda: next(times))
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: None)
    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(RuntimeService, "launch_game", lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []})

    result = RuntimeService().start_run(window_title="崩坏：星穹铁道", channel="official", timeout_seconds=1, interval_seconds=1)

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123}


def test_command_service_start_run_reuses_existing_session_on_second_call_with_real_runtime_service(tmp_path: Path, monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    monkeypatch.setattr("trail.runtime.window.attach_window", lambda window_title: {"title": window_title, "hwnd": 123})
    monkeypatch.setattr("trail.runtime.window.launch_game", lambda **payload: {"started": False, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []})

    command_service = CommandService(
        runtime_service=RuntimeService(),
        session_service=SessionServiceRegistry(),
    )

    first = command_service.handle(
        SimpleNamespace(
            request_id="req-start-first",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )
    second = command_service.handle(
        SimpleNamespace(
            request_id="req-start-second",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert first["ok"] is True
    assert first["data"]["reused"] == 0
    assert second["ok"] is True
    assert second["data"]["reused"] == 1
    assert second["data"]["session"] == first["data"]["session"]


def test_start_run_unknown_result_keeps_request_and_taints_created_session(tmp_path: Path):
    runtime = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime, session_service=session_services)
    original = service._start_run

    def exploding(request, payload, session_service):
        result = original(request, payload, session_service)
        envelope = command_failure(
            code="DAEMON_UNAVAILABLE",
            message="mutation result unknown",
            screenshot=None,
            debug={"last_known_stage": "state_persisted", "tainted": True},
        )
        envelope["data"] = result
        raise PersistedButResponseUnknown(envelope)

    service._start_run = exploding
    response = service.handle(
        SimpleNamespace(
            request_id="req-start-unknown",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-start-unknown"
    assert response["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert response["debug"]["last_known_stage"] == "state_persisted"

    status = session_services.for_workspace(tmp_path).request_status("req-start-unknown")
    assert status["tainted"] is True
    created_session_id = status["session_id"]
    assert session_services.for_workspace(tmp_path).is_session_tainted(created_session_id) is True


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


def test_command_service_handles_cw_enter_world_to_home(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.keys: list[tuple[str, int, float]] = []
            self._locate_results = {
                asset("entry.start"): None,
                asset("entry.new"): None,
                asset("entry.continue"): None,
                asset("entry.invest_environment"): None,
                asset("stage.preparation"): None,
                asset("stage.shop"): None,
                asset("stage.replenish"): None,
                asset("stage.encounter"): None,
                asset("stage.invest"): None,
                asset("stage.boss_preview"): None,
                asset("stage.fortune"): None,
                asset("stage.event"): None,
                asset("stage.settle"): None,
                asset("stage.game_over"): None,
            }
            self._wait_results = {
                asset("entry.menu"): _box("entry.menu", left=12, top=24),
                asset("entry.cosmic_strife"): _box("entry.cosmic_strife", left=36, top=48),
                asset("entry.start"): _box("entry.start", left=84, top=96),
            }

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            return self._locate_results.get(template)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            return self._wait_results.get(template)

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            self.keys.append((key, presses, interval))

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-home",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {"page": "home"}
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["entry"] == {"page": "home"}
    assert runtime.keys == [("f4", 1, 0.2)]
    assert runtime.clicks == [(56, 58), (464, 324), (1494, 884)]
    assert runtime.wait_calls == [asset("entry.menu"), asset("entry.cosmic_strife"), asset("entry.start")]


def test_command_service_handles_cw_enter_rejects_pages_past_home(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.new"):
                return _box("entry.new", left=20, top=40)
            return None

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-entry-new",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "entry.new"}
    assert payload["error"] == {
        "code": "CW_ENTER_ALREADY_PAST_HOME",
        "message": "cw enter only supports world or home, current page: entry.new",
    }
    assert registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-entry-new")["final_state"] == "failed_before_side_effect"


def test_command_service_handles_cw_enter_rejects_settle_screen_before_home(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=20, top=40)
            return None

        def ocr(self, **kwargs):
            del kwargs
            return [([0, 0], "挑战失败", 0.99), ([0, 0], "继续挑战", 0.99)]

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-settle",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "in_game", "stage": "settle"}
    assert payload["error"] == {
        "code": "CW_ENTER_ALREADY_PAST_HOME",
        "message": "cw enter only supports world or home, current page: in_game, stage: settle",
    }
    assert registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-settle")["final_state"] == "failed_before_side_effect"


def test_command_service_handles_cw_enter_rejects_recorded_game_over_before_home(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=20, top=40)
            return None

        def ocr(self, **kwargs):
            del kwargs
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"stage": {"value": "game_over", "stale": False}}
    service.save_session(session)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-game-over",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "in_game", "stage": "game_over"}
    assert payload["error"] == {
        "code": "CW_ENTER_ALREADY_PAST_HOME",
        "message": "cw enter only supports world or home, current page: in_game, stage: game_over",
    }


def test_command_service_handles_cw_enter_rejects_legacy_start_payload_fields(tmp_path: Path):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-legacy-fields",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={"session_id": session.session_id, "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "CW_ENTER_ARGS_NOT_SUPPORTED",
        "message": "cw enter no longer accepts mode/difficulty/battle_mode; use cw start",
    }
    assert registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-legacy-fields")["final_state"] == "failed_before_side_effect"


def test_command_service_handles_cw_start_and_persists_portal_snapshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    cards = [
        {"card_idx": 1, "portal_title": "Alpha Portal", "portal_description": "Alpha Desc", "score": 0.99},
        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
        {"card_idx": 3, "portal_title": "Gamma Portal", "portal_description": "Gamma Desc", "score": 0.77},
    ]
    calls: list[dict[str, object]] = []
    guide_calls: list[dict[str, object]] = []

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        calls.append(
            {
                "mode": mode,
                "difficulty": difficulty,
                "battle_mode": battle_mode,
                "runtime": runtime,
            }
        )
        session.scene_state.setdefault("cw", {})["entry"] = {
            "page": "invest",
            "mode": "new",
            "difficulty": "highest",
            "battle_mode": "overclock",
        }
        return session

    runtime = SimpleNamespace(ocr=lambda **kwargs: [{"text": "alpha"}], locate=lambda template, **kwargs: None)
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda timeout=10: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_list",
        lambda **kwargs: guide_calls.append(kwargs) or {
            "portals": [
                {
                    "portal_title": "Alpha Portal",
                    "list": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                }
            ],
            "count": 1,
            "more": False,
        },
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    persisted = service.load_session(session.session_id)

    assert payload["ok"] is True
    assert payload["data"] == {
        "cards": [
            {
                **cards[0],
                "guides": [
                    {
                        "lineup_id": "alpha-guide",
                        "title": "Alpha攻略",
                        "carry_roles": ["希儿"],
                        "support_hard": True,
                        "has_change_equip": False,
                        "has_expert": True,
                        "like": 123,
                        "favour": 45,
                    }
                ],
            },
            cards[1],
            cards[2],
        ],
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
        "stale": False,
    }
    assert len(calls) == 1
    assert calls[0]["mode"] == "continue"
    assert calls[0]["difficulty"] == "current"
    assert calls[0]["battle_mode"] == "standard"
    assert calls[0]["runtime"].ocr() == runtime.ocr()
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert persisted.scene_state["cw"]["portal"] == payload["data"]
    assert service.request_status("req-cw-start")["final_state"] == "completed"
    assert guide_calls == [
        {
            "page": 1,
            "limit": 3,
            "trait_id": None,
            "order": None,
            "next_page_token": None,
            "match_change_job": None,
            "match_hard": None,
            "portal": ["Alpha Portal", "Beta Portal", "Gamma Portal"],
            "timeout": 10,
        }
    ]


@pytest.mark.parametrize("requested_mode", ["new", "continue"])
def test_command_service_handles_cw_start_rejects_home_with_unfinished_progress(
    tmp_path: Path,
    requested_mode: str,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, object]] = []
            self._locate_results = {
                asset("entry.start"): None,
                asset("entry.new"): None,
                asset("entry.continue"): _box("entry.continue", left=84, top=96),
                asset("entry.invest_environment"): None,
                asset("stage.preparation"): None,
                asset("stage.shop"): None,
                asset("stage.replenish"): None,
                asset("stage.encounter"): None,
                asset("stage.invest"): None,
                asset("stage.boss_preview"): None,
                asset("stage.fortune"): None,
                asset("stage.event"): None,
                asset("stage.settle"): None,
                asset("stage.game_over"): None,
            }

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            return self._locate_results.get(template)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            return [{"text": "继续进度"}, {"text": "结束并结算"}, {"text": "当前进度1-1M奖励"}]

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=f"req-cw-start-home-progress-{requested_mode}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": requested_mode,
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "home"}
    assert payload["error"] == {
        "code": "CW_START_PROGRESS_PENDING",
        "message": "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
    }
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"
    assert runtime.clicks == []
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}]


def test_command_service_handles_cw_start_reports_completed_known_failure_when_progress_appears_after_start_click(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, object]] = []
            self._phase = "home"

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=84, top=96) if self._phase == "home" else None
            if template == asset("entry.continue"):
                return _box("entry.continue", left=140, top=180) if self._phase == "after_start" else None
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == (104, 106):
                self._phase = "after_start"

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            if self._phase == "home":
                return [{"text": "货币战争"}]
            return [{"text": "继续进度"}, {"text": "结束并结算"}, {"text": "当前进度1-1M奖励"}]

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start-home-late-progress-new",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "home"}
    assert payload["error"] == {
        "code": "CW_START_PROGRESS_PENDING",
        "message": "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
    }
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
    assert runtime.clicks == [(104, 106)]
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}, {}, {}]
    assert loaded.scene_state.get("daemon", {}).get("tainted", False) is False


@pytest.mark.parametrize("requested_mode", ["new", "continue"])
def test_command_service_handles_cw_start_consumes_unfinished_progress_flag_before_run_start_chain(
    tmp_path: Path,
    requested_mode: str,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    monkeypatch.setattr(
        "trail.scenes.cw.entry._detect_current_enter_page",
        lambda runtime, session=None, preferred_mode=None: {"page": "home", "unfinished_progress": "1"},
    )
    monkeypatch.setattr(
        "trail.scenes.cw.entry._run_start_chain",
        lambda *args, **kwargs: pytest.fail("unfinished_progress should short-circuit before _run_start_chain"),
    )

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=f"req-cw-start-short-circuit-{requested_mode}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": requested_mode,
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "home"}
    assert payload["error"] == {
        "code": "CW_START_PROGRESS_PENDING",
        "message": "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
    }
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"
    assert runtime.clicks == []


def test_command_service_handles_cw_start_rejects_continue_mode_on_clean_home(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, object]] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=84, top=96)
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del template, timeout, interval
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            return [{"text": "货币战争"}]

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start-home-continue-invalid",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {"page": "home"}
    assert payload["error"] == {
        "code": "CW_START_CONTINUE_PAGE_INVALID",
        "message": "cw start --mode continue only supports whole-run settlement pages, current page: home",
    }
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"
    assert runtime.clicks == []
    assert runtime.ocr_calls == [{}, {}]


def test_command_service_handles_cw_start_continue_from_whole_run_settlement_chain(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.runtime.resources import resolve_scene_asset

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    def asset(alias: str) -> str:
        return str(resolve_scene_asset("cw", alias))

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, object]] = []
            self._phase = "settlement.entry"

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=84, top=96) if self._phase == "home" else None
            if template == asset("entry.new"):
                return _box("entry.new", left=140, top=180) if self._phase == "after_home_start" else None
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            if template == asset("entry.start"):
                return _box("entry.start", left=84, top=96) if self._phase == "home" else None
            if template == asset("entry.new"):
                return _box("entry.new", left=140, top=180) if self._phase == "after_home_start" else None
            if template == asset("entry.start_game"):
                return _box("entry.start_game", left=240, top=280)
            if template == asset("stage.settle"):
                return _box("stage.settle", left=340, top=380)
            if template == asset("stage.boss_preview"):
                return _box("stage.boss_preview", left=440, top=480)
            if template == asset("entry.invest_environment"):
                return _box("entry.invest_environment", left=540, top=580)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == (960, 908):
                if self._phase == "settlement.entry":
                    self._phase = "settlement.followup"
                elif self._phase == "settlement.followup":
                    self._phase = "settlement.return"
                elif self._phase == "settlement.return":
                    self._phase = "home"
            elif (x, y) == (104, 106):
                self._phase = "after_home_start"

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            if self._phase == "settlement.entry":
                return [{"text": "挑战失败"}, {"text": "对局评价"}, {"text": "下一步"}]
            if self._phase == "settlement.followup":
                return [{"text": "1-1M奖励"}, {"text": "标准博弈"}, {"text": "下一页"}]
            if self._phase == "settlement.return":
                return [{"text": "小队生命值"}, {"text": "总经济"}, {"text": "返回货币战争"}]
            return [{"text": "货币战争"}]

    cards = [{"card_idx": 1, "portal_title": "Alpha", "portal_description": "Desc", "score": 0.95}]
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda timeout=10: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start-settlement-continue",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is True
    assert payload["data"]["cards"] == cards
    assert payload["data"]["mode"] == "new"
    assert payload["data"]["difficulty"] == "current"
    assert payload["data"]["battle_mode"] == "standard"
    assert service.request_status(request.request_id)["final_state"] == "completed"
    assert loaded.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.clicks == [
        (960, 908),
        (960, 908),
        (960, 908),
        (104, 106),
        (300, 250),
        (160, 190),
        (260, 290),
        (360, 390),
        (460, 490),
    ]

@pytest.mark.parametrize(
    ("payload_override", "expected_code", "expected_message"),
    [
        ({"mode": "warp"}, "CW_START_MODE_INVALID", "unsupported cw start mode: warp"),
        ({"difficulty": "nightmare"}, "CW_START_DIFFICULTY_INVALID", "unsupported cw start difficulty: nightmare"),
        ({"battle_mode": "turbo"}, "CW_START_BATTLE_MODE_INVALID", "unsupported cw start battle_mode: turbo"),
    ],
)
def test_command_service_handles_cw_start_rejects_invalid_enums(
    tmp_path: Path,
    monkeypatch,
    payload_override: dict[str, str],
    expected_code: str,
    expected_message: str,
):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace(ocr=lambda **kwargs: [{"text": "alpha"}])
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    start_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.start_cw",
        lambda session, *, mode, difficulty, battle_mode, runtime: start_calls.append(
            {
                "mode": mode,
                "difficulty": difficulty,
                "battle_mode": battle_mode,
            }
        ) or session,
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=f"req-cw-start-{expected_code.lower()}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
            **payload_override,
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": expected_code,
        "message": expected_message,
    }
    assert start_calls == []
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"


def test_command_service_handles_cw_portal_select_and_marks_snapshot_stale(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: session.scene_state["cw"]["portal"].__setitem__("stale", True) or {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-select",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.select",
        payload={"session_id": session.session_id, "card_idx": 2},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-select.png"
    assert service.load_session(session.session_id).scene_state["cw"]["portal"]["stale"] is True
    assert service.request_status("req-cw-portal-select")["final_state"] == "completed"
    assert runtime.capture_requests == [(False, "req-cw-portal-select")]


def test_command_service_handles_cw_portal_select_waits_extra_before_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-delay.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    sleeps: list[float] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: sleeps.append(seconds))
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-select-delay",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.select",
        payload={"session_id": session.session_id, "card_idx": 2},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert sleeps == [2.0]
    assert runtime.capture_requests == [(False, "req-cw-portal-select-delay")]


def test_command_service_handles_cw_portal_refresh_and_updates_snapshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-portal-refresh.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    snapshot = {
        "cards": [{"card_idx": 1, "portal_title": "New Alpha", "portal_description": "New Desc", "score": 0.91}],
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_list",
        lambda **kwargs: {
            "portals": [
                {
                    "portal_title": "New Alpha",
                    "list": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                }
            ],
            "count": 1,
            "more": False,
        },
    )
    monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_portal", lambda session, runtime, portal_list: session.scene_state["cw"].__setitem__("portal", snapshot) or snapshot)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-refresh",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.refresh",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {
        **snapshot,
        "cards": [
            {
                **snapshot["cards"][0],
                "guides": [
                    {
                        "lineup_id": "alpha-guide",
                        "title": "Alpha攻略",
                        "carry_roles": ["希儿"],
                        "support_hard": True,
                        "has_change_equip": False,
                        "has_expert": True,
                        "like": 123,
                        "favour": 45,
                    }
                ],
            }
        ],
    }
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-refresh.png"
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == payload["data"]
    assert service.request_status("req-cw-portal-refresh")["final_state"] == "completed"
    assert runtime.capture_requests == [(False, "req-cw-portal-refresh")]


def test_command_service_handles_cw_portal_restart_and_reuses_request_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    snapshot = {
        "cards": [{"card_idx": 1, "portal_title": "Restarted", "portal_description": "Desc", "score": 0.91}],
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "new", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 1, "portal_title": "Old", "portal_description": "Old", "score": 0.5}], "mode": "new", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    restart_calls: list[dict[str, object]] = []

    def fake_restart(session, *, runtime):
        restart_calls.append(
            {
                "entry_mode": session.scene_state["cw"]["entry"]["mode"],
                "portal_mode": session.scene_state["cw"]["portal"]["mode"],
            }
        )
        session.scene_state["cw"]["portal"] = snapshot
        return snapshot

    monkeypatch.setattr("trail.daemon.cw_service._restart_cw", fake_restart)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-restart",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.restart",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == snapshot
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == snapshot
    assert service.request_status("req-cw-portal-restart")["final_state"] == "completed"
    assert restart_calls == [{"entry_mode": "new", "portal_mode": "new"}]


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

    def late_failure(session, runtime):
        del session
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
            payload={"session_id": session.session_id},
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
