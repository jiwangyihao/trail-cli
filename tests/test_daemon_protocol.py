import json
import socket
import threading
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
import trail.daemon.server as daemon_server_module
from trail.daemon.command_service import CommandService, PersistedButResponseUnknown, SideEffectAppliedButStateNotPersisted, success
from trail.daemon.client import TrailDaemonClient, resolve_response_timeout, send_daemon_request
from trail.daemon.command_timeouts import resolve_command_execution_timeout
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.daemon.server import TrailDaemonServer
from trail.daemon.session_service import SessionServiceRegistry
from trail.output.envelope import command_failure
from trail.runtime.resources import resolve_scene_asset
from tests.support.fake_daemon import (
    FakeDaemonClient,
    build_success_response,
    fake_round_trip_transport,
    start_fake_daemon_server,
    write_installed_manifest,
    write_ready_manifest,
)


@lru_cache(maxsize=None)
def _cw_asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


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


class ScopedDebugProtocolRuntime(ProtocolRuntime):
    def __init__(self, screenshot_path: Path):
        super().__init__(screenshot_path)
        self._local = threading.local()

    def begin_capture_scope(self):
        depth = int(getattr(self._local, "depth", 0)) + 1
        self._local.depth = depth
        if depth == 1:
            self._local.trace = []
            self._local.context = {}

    def end_capture_scope(self):
        depth = int(getattr(self._local, "depth", 0))
        self._local.depth = max(0, depth - 1)

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        del optional
        capture_request_id = request_id or self._screenshot_path.stem
        return str(self._screenshot_path.parent / f"{capture_request_id}.png")

    def consume_debug_trace(self):
        trace = list(getattr(self._local, "trace", []))
        self._local.trace = []
        return trace

    def consume_debug_context(self):
        context = dict(getattr(self._local, "context", {}))
        self._local.context = {}
        return context

    def click_point(self, x: int, y: int):
        super().click_point(x, y)
        if int(getattr(self._local, "depth", 0)) > 0:
            self._local.trace.append(
                {
                    "step": "click_point",
                    "ts": "2026-04-24T08:15:30.123Z",
                    "ok": 1,
                    "point": [x, y],
                }
            )


def _complete_cw_guide_fixture(*, lineup_id: str = "selected-lineup", operation_guide: str = "前期按测试运营") -> dict[str, object]:
    return {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": lineup_id,
        "title": "测试攻略",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": operation_guide,
        "role_stages": [{"stage": "Opening", "front_roles": [{"name": "银狼"}], "back_roles": [], "traits": []}],
        "first_fight_augments": [{"name": "快攻"}],
        "second_fight_augments": [{"name": "回蓝"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
    }


def _complete_cw_constraints_fixture() -> dict[str, object]:
    return {"min_coins": 40, "min_level": 7, "mid_level": 9, "priority": {}, "positioning": {}}


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


class StartRunCaptureRuntime:
    def __init__(self, *, workspace_root: Path | None = None, probe_values: list[object] | None = None):
        self.workspace_root = workspace_root
        self.capture_requests: list[tuple[bool, str | None]] = []
        self.probe_values = list(probe_values or [])
        self.probe_requests: list[dict[str, object]] = []

    def set_workspace_root(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root)
        return self

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        self.capture_requests.append((optional, request_id))
        if self.workspace_root is None:
            raise AssertionError("workspace_root missing for capture")
        capture_request_id = request_id or "capture"
        return str(self.workspace_root / ".trail" / "shots" / f"{capture_request_id}.png")

    def capture_image(self, **kwargs):
        self.probe_requests.append(dict(kwargs))
        if self.probe_values:
            return self.probe_values.pop(0)
        return "bright"

    def collect_warnings(self):
        return []

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return []

    def consume_debug_trace(self):
        return []


class StartRunMissingCaptureRuntime(StartRunCaptureRuntime):
    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        del optional, request_id
        return None


class StartRuntimeServiceStub:
    def __init__(
        self,
        *,
        attach_binding: dict[str, object] | None = None,
        launch_result: dict[str, object] | None = None,
        attach_failures_before_success: int = 0,
        launch_status: str = "launched_needs_check",
        runtime=None,
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
        self.launch_status = launch_status
        self.runtime = runtime or StartRunCaptureRuntime()
        self.attach_attempts = 0
        self.launch_calls: list[dict[str, object]] = []
        self.start_run_calls: list[dict[str, object]] = []

    def attach_window(self, *, window_title: str):
        self.attach_attempts += 1
        if self.attach_attempts <= self.attach_failures_before_success:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return dict(self.attach_binding)

    def launch_game(self, **payload):
        self.launch_calls.append(dict(payload))
        return dict(self.launch_result)

    def get_runtime(self, *, workspace_root: str, window_binding):
        del window_binding
        if hasattr(self.runtime, "set_workspace_root"):
            self.runtime.set_workspace_root(workspace_root)
        return self.runtime

    def start_run(
        self,
        *,
        workspace_root: str | None = None,
        window_title: str,
        game_path: str | None = None,
        channel: str = "official",
        timeout_seconds: int = 30,
        interval_seconds: int = 1,
    ):
        self.start_run_calls.append(
            {
                "workspace_root": workspace_root,
                "window_title": window_title,
                "game_path": game_path,
                "channel": channel,
                "timeout_seconds": timeout_seconds,
                "interval_seconds": interval_seconds,
            }
        )
        try:
            return {**self.attach_window(window_title=window_title), "status": "attached"}
        except TrailError as error:
            if error.code != "WINDOW_NOT_FOUND":
                raise

        launch_result = self.launch_game(game_path=game_path, channel=channel)
        if not launch_result.get("started") and not launch_result.get("already_running"):
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        while True:
            try:
                return {**self.attach_window(window_title=window_title), "status": self.launch_status}
            except TrailError as error:
                if error.code != "WINDOW_NOT_FOUND":
                    raise


def test_cw_resource_service_caches_equipment_recognizer(monkeypatch, tmp_path):
    from trail.daemon.cw_resource_service import CwResourceService

    del monkeypatch
    calls = {"load": 0, "recognizer": 0}

    class Bundle:
        big_version = "3.2"
        identity = "id1"
        equipment_features = {
            "items": [],
            "equipment_feature_schema_version": 1,
            "recognizer_algorithm_version": "vector-mask-v1",
            "feature_size": [32, 32],
            "match_size": [64, 64],
            "min_score": 0.72,
            "min_gap": 0.05,
        }
        equipment_manifest = {"items": []}
        raw_config = {"rpg_game_big_version": "3.2", "equipment_list": []}
        root = tmp_path
        source_kind = "package"
        manifest = {"bundle_schema_version": 1, "resource_version": "3.2"}
        manifest_path = tmp_path / "manifest.json"
        manifest_mtime = 1.0

    def load_bundle(workspace_root=None):
        del workspace_root
        calls["load"] += 1
        return Bundle()

    class Recognizer:
        @classmethod
        def from_precomputed_features(cls, payload):
            assert payload == Bundle.equipment_features
            calls["recognizer"] += 1
            return cls()

    service = CwResourceService(
        bundle_loader=load_bundle,
        recognizer_cls=Recognizer,
        source_signature=lambda workspace_root=None: ("test", workspace_root),
    )

    first = service.equipment_recognizer(workspace_root=str(tmp_path))
    second = service.equipment_recognizer(workspace_root=str(tmp_path))

    assert first is second
    assert calls == {"load": 1, "recognizer": 1}


class StartRunDetectionRuntime:
    def __init__(self, *, ocr_results: list[list[object]]):
        self._ocr_results = [list(result) for result in ocr_results]
        self.clicks: list[tuple[int, int]] = []

    def ocr(self, *, capture=None, ocr=None, **kwargs):
        del capture, ocr, kwargs
        if self._ocr_results:
            return self._ocr_results.pop(0)
        return []

    def click_point(self, x: int, y: int):
        self.clicks.append((x, y))


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


def _set_shop_applied(session, shop: dict):
    refreshed = _set_shop(session, shop)
    return SimpleNamespace(session=refreshed, scene_state=refreshed.scene_state, response_snapshot=dict(shop))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> dict[str, object]:
    return {"left": left, "top": top, "width": width, "height": height, "source": alias}


def _portal_cards() -> list[dict[str, object]]:
    return [
        {"card_idx": 1, "portal_title": "Alpha Portal", "portal_description": "Alpha Desc", "score": 0.99},
        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
        {"card_idx": 3, "portal_title": "Gamma Portal", "portal_description": "Gamma Desc", "score": 0.77},
    ]


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


def test_command_service_routes_guide_fetch_select_through_run_mutation(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    observed: dict[str, object] = {}

    def fake_run_mutation(request, command_name, handler, **kwargs):
        observed["request"] = request
        observed["command_name"] = command_name
        observed["handler"] = handler
        observed["kwargs"] = kwargs
        return {"ok": True, "data": {"lineup_id": "abc"}}

    monkeypatch.setattr(command_service, "_run_mutation", fake_run_mutation)
    request = DaemonRequest(
        request_id="req-guide-fetch-select-route",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="guide.fetch.cw",
        payload={"url": "abc", "select": True},
    )

    payload = command_service.handle(request)

    assert payload == {"ok": True, "data": {"lineup_id": "abc"}}
    assert observed["request"] is request
    assert observed["command_name"] == "guide.fetch.cw"
    assert observed["kwargs"] == {
        "handler_persisted_state": True,
        "response_builder": observed["kwargs"]["response_builder"],
        "enforce_cw_tainted": True,
        "tainted_session_id": session.session_id,
    }


def test_command_service_routes_cw_equipment_compose_through_journaled_mutation():
    from trail.daemon.command_service import CW_MUTATING_METHODS, CW_SESSION_ONLY_MUTATION_METHODS

    assert "cw.equipment.compose" in CW_MUTATING_METHODS
    assert "cw.equipment.compose" not in CW_SESSION_ONLY_MUTATION_METHODS


def test_command_service_guide_fetch_select_stores_complete_guide_no_artifact(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": "g1",
            "title": "测试攻略",
            "share_code": "##code##",
            "version": "4.0",
            "operation_guide": "前期 按测试运营",
            "on_field": {"灵砂": 1},
            "off_field": {"星期日": 1},
            "role_stages": [{"stage": "Opening", "front_roles": [{"name": "灵砂"}], "back_roles": [], "traits": []}],
            "first_fight_augments": [{"name": "击破概念股"}],
            "second_fight_augments": [{"name": "折射棱镜"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
            "min_coins": 40,
            "min_level": 6,
            "mid_level": 9,
        },
    )
    monkeypatch.setattr(
        "trail.daemon.command_service.ArtifactStore.create",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select path must not create artifact")),
    )
    request = DaemonRequest(
        request_id="req-guide-fetch-select-no-artifact",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="guide.fetch.cw",
        payload={"url": "g1", "select": True},
    )

    payload = command_service.handle(request)
    persisted = service.load_session(session.session_id)

    assert payload["ok"] is True
    assert payload["data"]["operation_guide"] == "前期 按测试运营"
    assert "artifact_id" not in payload["data"]
    assert persisted.scene_state["cw"]["guide"]["operation_guide"] == "前期 按测试运营"
    assert "remaining_purchases" not in persisted.scene_state["cw"]["guide"]
    assert "on_field" not in persisted.scene_state["cw"]["guide"]
    assert "off_field" not in persisted.scene_state["cw"]["guide"]
    assert persisted.scene_state["cw"]["constraints"]["min_level"] == 6
    assert "artifact" not in persisted.scene_state["cw"]["guide"]
    assert "artifact_id" not in persisted.scene_state["cw"]["guide"]


def test_server_handle_payload_rejects_guide_fetch_select_missing_top_level_session_id(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-select-missing-session",
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True, "session_id": "sess-1"},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_server_handle_payload_rejects_guide_fetch_with_top_level_session_without_select(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run without select")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-session-without-select",
            method="guide.fetch.cw",
            payload={"url": "abc"},
            session_id="sess-1",
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_server_handle_payload_rejects_guide_fetch_when_top_level_session_is_empty_string(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run for empty session_id")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-empty-session",
            method="guide.fetch.cw",
            payload={"url": "abc"},
            session_id="",
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_server_handle_payload_rejects_guide_fetch_select_with_conflicting_payload_session_id(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run with payload session_id")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-conflicting-session",
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True, "session_id": "sess-payload"},
            session_id="sess-top",
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_server_handle_payload_rejects_guide_fetch_with_non_boolean_select(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run for invalid select type")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-non-bool-select",
            method="guide.fetch.cw",
            payload={"url": "abc", "select": "true"},
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "GUIDE_INPUT_INVALID"


def test_server_handle_payload_rejects_guide_fetch_select_when_session_is_tainted(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("daemon", {})["tainted"] = True
    session_service.save_session(session)
    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch should not run for tainted session")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-guide-fetch-select-session-tainted",
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
            session_id=session.session_id,
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "SESSION_RECONCILE_REQUIRED",
        "message": "session is tainted; reconcile before mutating cw commands",
    }
    assert response["debug"] == {
        "request_id": "req-guide-fetch-select-session-tainted",
        "session_id": session.session_id,
    }


@pytest.mark.parametrize("method,payload", [("cw.guide.apply", {}), ("cw.portal.select", {"card_idx": 1})])
def test_server_handle_payload_rejects_cw_methods_with_payload_only_session_id(
    tmp_path: Path,
    monkeypatch,
    method: str,
    payload: dict[str, object],
):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id=f"req-{method.replace('.', '-')}-payload-only-session",
            method=method,
            payload={"session_id": "sess-payload", **payload},
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "SESSION_REQUIRED",
        "message": f"cw method requires top-level session_id: {method}",
    }


@pytest.mark.parametrize("method,payload", [("cw.guide.apply", {}), ("cw.portal.select", {"card_idx": 1})])
def test_server_handle_payload_rejects_cw_methods_with_conflicting_payload_session_id(
    tmp_path: Path,
    monkeypatch,
    method: str,
    payload: dict[str, object],
):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id=f"req-{method.replace('.', '-')}-conflicting-session",
            method=method,
            payload={"session_id": "sess-payload", **payload},
            session_id="sess-top",
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "SESSION_CONFLICT",
        "message": f"cw method payload session_id conflicts with top-level session_id: {method}",
    }


def test_server_handle_payload_rejects_cw_guide_apply_payload_only_session_on_tainted_session(tmp_path: Path, monkeypatch):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    monkeypatch.setattr(daemon_server_module, "resolve_daemon_home", lambda: daemon_home)
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("daemon", {})["tainted"] = True
    session_service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("apply should not run for payload-only session_id")),
    )
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    server = TrailDaemonServer(command_service=command_service)

    response = server.handle_payload(
        _server_payload(
            tmp_path,
            token="token-1",
            request_id="req-cw-guide-apply-payload-only-tainted",
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "SESSION_REQUIRED",
        "message": "cw method requires top-level session_id: cw.guide.apply",
    }


def test_command_service_tracks_cw_portal_select_request_status_with_top_level_session_id(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-session-binding.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session_a = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session_b = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2})
    loaded_a = service.load_session(session_a.session_id)
    loaded_a.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(lineup_id="selected-lineup-a"),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    loaded_b = service.load_session(session_b.session_id)
    loaded_b.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(lineup_id="selected-lineup-b"),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "B"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(loaded_a)
    service.save_session(loaded_b)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": session.scene_state["cw"]["portal"]["cards"][0]["portal_title"]},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: None,
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: None,
        raising=False,
    )
    _patch_cw_portal_select_auto_collect_success(monkeypatch, [])

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-session-binding",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session_a.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session_a.session_id, "card_idx": 1},
        )
    )
    status = service.request_status("req-cw-portal-select-session-binding")

    assert response["ok"] is True
    assert response["data"]["portal_title"] == "A"
    assert status["session_id"] == session_a.session_id


def _run_cw_portal_select_with_operation_guide(tmp_path: Path, monkeypatch, *, operation_guide: str) -> dict:
    from trail.daemon.cw_service import CwService

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id or 'req-cw-portal-select'}.png"

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
        "guide": _complete_cw_guide_fixture(operation_guide=operation_guide),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "A"},
    )
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, [])

    return command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-skill-info",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )


def test_command_service_cw_portal_select_adds_operation_guide_skill_info(tmp_path: Path, monkeypatch):
    response = _run_cw_portal_select_with_operation_guide(tmp_path, monkeypatch, operation_guide="前期 先读图")

    assert response["ok"] is True
    assert response["data"]["skill_info"] == [{"name": "运营思路", "text": "前期 先读图"}]


def test_command_service_cw_portal_select_omits_blank_operation_guide_skill_info(tmp_path: Path, monkeypatch):
    response = _run_cw_portal_select_with_operation_guide(tmp_path, monkeypatch, operation_guide="")

    assert response["ok"] is True
    assert "skill_info" not in response["data"]


def test_command_service_cw_portal_select_auto_collects_equipment_before_shop(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    events: list[str] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(operation_guide="前期 先读图"),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "A"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    expected_raw_config = {"rpg_game_big_version": "3.2", "equipment_list": []}
    expected_recognizer = object()

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            events.append("equipment.resources")
            return expected_raw_config, expected_recognizer

    def fake_apply_equipment(session, runtime, *, workspace_root=None, raw_config=None, recognizer=None):
        del runtime
        assert workspace_root == str(tmp_path)
        assert raw_config is expected_raw_config
        assert recognizer is expected_recognizer
        events.append("equipment.read")
        snapshot = {
            "count": 1,
            "uncertain": 0,
            "empty": 59,
            "backend": "vector",
            "layout": "default",
            "items": [{"pos": "equipment:1", "name": "基础装甲", "score": 0.9, "uncertain": False}],
            "recommendations": {
                "priority": [{"idx": 1, "name": "高周波电锯", "basics": [], "required_roles": ["希儿"], "acquired_roles": [], "missing_roles": ["希儿"]}],
                "role_missing": [{"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}],
                "todos": [],
            },
            "stale": False,
        }
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return snapshot

    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fake_apply_equipment)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service, cw_resource_service=ResourceService()),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-equipment",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is True
    assert events.index("slots.read") < events.index("equipment.resources") < events.index("equipment.read") < events.index("shop.open")
    assert response["data"]["equipment"]["stale"] is False
    assert response["data"]["equipment"]["items"][0]["name"] == "基础装甲"
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is False
    assert persisted["items"][0]["name"] == "基础装甲"


def test_command_service_cw_portal_select_equipment_failure_is_soft_warning_and_stales_old_snapshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    events: list[str] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(operation_guide=""),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "A"}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
        "equipment": {"items": [{"pos": "equipment:1", "name": "旧装备"}], "stale": False},
    }
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fail_equipment(session, runtime, workspace_root=None):
        del session, runtime, workspace_root
        events.append("equipment.read")
        raise TrailError("CW_EQUIPMENT_LAYOUT_MISMATCH", "equipment grid requires canonical 1920x1080 screenshot")

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_portal", lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "A"})
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fail_equipment)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=CwService(runtime_service=runtime_service))

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-equipment-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is True
    assert "equipment" not in response["data"]
    assert "shop" in response["data"]
    warning = next(warning for warning in response["warnings"] if warning["code"] == "CW_EQUIPMENT_AUTO_COLLECT_FAILED")
    assert warning["message"] == "equipment grid requires canonical 1920x1080 screenshot"
    assert warning["detail_code"] == "CW_EQUIPMENT_LAYOUT_MISMATCH"
    assert events.index("equipment.read") < events.index("shop.open")
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is True
    assert persisted["items"] == [{"pos": "equipment:1", "name": "旧装备"}]


def test_cw_portal_select_preserves_numeric_zero_equipment_freshness(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(operation_guide=""),
        "constraints": _complete_cw_constraints_fixture(),
    }
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_select(
        session,
        *,
        runtime,
        card_idx: int,
        guide: dict,
        workspace_root: str | None = None,
        cw_resource_service=None,
        request_id: str | None = None,
    ):
        del runtime, guide, workspace_root, cw_resource_service, request_id
        snapshot = {"items": [{"pos": "equipment:1", "name": "基础装甲"}], "stale": 0}
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return {"card_idx": card_idx, "portal_title": "A", "equipment": deepcopy(snapshot)}

    monkeypatch.setattr("trail.daemon.cw_service._select_portal_and_apply_selected_guide", fake_select)
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    payload = CwService(runtime_service=runtime_service).handle_mutation(
        method="cw.portal.select",
        payload={"session_id": session.session_id, "card_idx": 1},
        workspace_root=str(tmp_path),
        session_service=service,
        request_id="req-cw-portal-select-equipment-zero-stale",
        verbose=False,
    )

    assert payload["ok"] is True
    assert payload["data"]["equipment"]["stale"] == 0
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] == 0
    assert persisted["items"] == [{"pos": "equipment:1", "name": "基础装甲"}]


def test_command_service_start_run_returns_status_and_window_facts(tmp_path: Path):
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
    assert payload["screenshot"] == ".trail/shots/req-start-1.png"
    assert payload["data"]["status"] == "attached"
    assert payload["data"]["session"]
    assert payload["data"]["reused"] in {0, 1}
    assert payload["data"]["title"] == "崩坏：星穹铁道"
    assert payload["data"]["hwnd"] == 123


def test_command_service_start_run_uses_command_execution_timeout_from_policy(tmp_path: Path):
    runtime_service = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    request_payload = {"window_title": "崩坏：星穹铁道", "channel": "official"}

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-timeout-policy",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload=request_payload,
        )
    )

    assert payload["ok"] is True
    assert runtime_service.start_run_calls == [
        {
            "workspace_root": str(tmp_path),
            "window_title": "崩坏：星穹铁道",
            "game_path": None,
            "channel": "official",
            "timeout_seconds": 180,
            "interval_seconds": 1,
        }
    ]
    assert runtime_service.start_run_calls[0]["timeout_seconds"] == resolve_command_execution_timeout("start.run", request_payload)
    assert resolve_response_timeout("start.run", request_payload) == float(runtime_service.start_run_calls[0]["timeout_seconds"])


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
    assert result["data"]["status"] == "launched_needs_check"
    assert result["screenshot"] == ".trail/shots/req-start-wait.png"
    assert runtime.attach_attempts == 3
    assert runtime.launch_calls == [{"game_path": None, "channel": "official"}]


def test_runtime_service_start_run_immediate_attach_reports_attached_status(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService

    monkeypatch.setattr(RuntimeService, "attach_window", lambda self, *, window_title: {"title": window_title, "hwnd": 123})
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: (_ for _ in ()).throw(AssertionError("launch should not run when attach succeeds immediately")),
    )

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "attached"}


def test_runtime_service_start_run_immediate_attach_accepts_window_binding(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService
    from trail.runtime.model import WindowBinding

    monkeypatch.setattr(
        RuntimeService,
        "attach_window",
        lambda self, *, window_title: WindowBinding(title=window_title, hwnd=123),
    )
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: (_ for _ in ()).throw(AssertionError("launch should not run when attach succeeds immediately")),
    )

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "attached"}


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

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_needs_check"}


def test_runtime_service_start_run_reports_launched_clicked_enter_after_click_enter_detected(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService

    attach_attempts = {"count": 0}
    runtime = StartRunDetectionRuntime(
        ocr_results=[
            [
                {
                    "text": "点击进入",
                    "box": {"left": 10, "top": 20, "width": 30, "height": 40},
                }
            ]
        ]
    )
    sleep_calls: list[float] = []

    def fake_attach_window(self, *, window_title: str):
        attach_attempts["count"] += 1
        if attach_attempts["count"] == 1:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return {"title": window_title, "hwnd": 123}

    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []},
    )
    monkeypatch.setattr(RuntimeService, "get_runtime", lambda self, *, workspace_root, window_binding: runtime)
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: sleep_calls.append(seconds))

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_clicked_enter"}
    assert runtime.clicks == [(25, 40)]
    assert sleep_calls == []


def test_runtime_service_start_run_launched_path_accepts_window_binding(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService
    from trail.runtime.model import WindowBinding

    attach_attempts = {"count": 0}
    runtime = StartRunDetectionRuntime(
        ocr_results=[
            [
                {
                    "text": "点击进入",
                    "box": {"left": 10, "top": 20, "width": 30, "height": 40},
                }
            ]
        ]
    )
    sleep_calls: list[float] = []

    def fake_attach_window(self, *, window_title: str):
        attach_attempts["count"] += 1
        if attach_attempts["count"] == 1:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return WindowBinding(title=window_title, hwnd=123)

    def fake_get_runtime(self, *, workspace_root: str, window_binding):
        assert workspace_root == str(tmp_path)
        assert window_binding == {"title": "崩坏：星穹铁道", "hwnd": 123}
        return runtime

    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []},
    )
    monkeypatch.setattr(RuntimeService, "get_runtime", fake_get_runtime)
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: sleep_calls.append(seconds))

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_clicked_enter"}
    assert runtime.clicks == [(25, 40)]
    assert sleep_calls == []


def test_runtime_service_start_run_reports_launched_clicked_enter_for_tuple_polygon_ocr_piece(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService

    attach_attempts = {"count": 0}
    runtime = StartRunDetectionRuntime(
        ocr_results=[
            [
                (
                    ((10, 20), (40, 20), (40, 60), (10, 60)),
                    "点击进入",
                    0.99,
                )
            ]
        ]
    )
    sleep_calls: list[float] = []

    def fake_attach_window(self, *, window_title: str):
        attach_attempts["count"] += 1
        if attach_attempts["count"] == 1:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return {"title": window_title, "hwnd": 123}

    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []},
    )
    monkeypatch.setattr(RuntimeService, "get_runtime", lambda self, *, workspace_root, window_binding: runtime)
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: sleep_calls.append(seconds))

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_clicked_enter"}
    assert runtime.clicks == [(25, 40)]
    assert sleep_calls == []


@pytest.mark.parametrize(
    ("ocr_piece", "expected_click"),
    [
        (
            {
                "text": "点击进入",
                "polygon": ((10, 20), (40, 20), (40, 60), (10, 60)),
            },
            (25, 40),
        ),
        (
            {
                "text": "点击进入",
                "points": ((10, 20), (40, 20), (40, 60), (10, 60)),
            },
            (25, 40),
        ),
        (
            {
                "text": "点击进入",
                "center": {"x": 25, "y": 40},
            },
            (25, 40),
        ),
    ],
)
def test_runtime_service_start_run_reports_launched_clicked_enter_for_mapping_ocr_shapes(
    monkeypatch,
    tmp_path: Path,
    ocr_piece: dict[str, object],
    expected_click: tuple[int, int],
):
    from trail.daemon.runtime_service import RuntimeService

    attach_attempts = {"count": 0}
    runtime = StartRunDetectionRuntime(ocr_results=[[ocr_piece]])
    sleep_calls: list[float] = []

    def fake_attach_window(self, *, window_title: str):
        attach_attempts["count"] += 1
        if attach_attempts["count"] == 1:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return {"title": window_title, "hwnd": 123}

    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []},
    )
    monkeypatch.setattr(RuntimeService, "get_runtime", lambda self, *, workspace_root, window_binding: runtime)
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: sleep_calls.append(seconds))

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_clicked_enter"}
    assert runtime.clicks == [expected_click]
    assert sleep_calls == []


def test_runtime_service_start_run_reports_launched_needs_check_when_click_enter_not_found(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService

    attach_attempts = {"count": 0}
    runtime = StartRunDetectionRuntime(ocr_results=[[{"text": "开始游戏"}]])
    sleep_calls: list[float] = []

    def fake_attach_window(self, *, window_title: str):
        attach_attempts["count"] += 1
        if attach_attempts["count"] == 1:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return {"title": window_title, "hwnd": 123}

    monkeypatch.setattr(RuntimeService, "attach_window", fake_attach_window)
    monkeypatch.setattr(
        RuntimeService,
        "launch_game",
        lambda self, **payload: {"started": True, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []},
    )
    monkeypatch.setattr(RuntimeService, "get_runtime", lambda self, *, workspace_root, window_binding: runtime)
    monkeypatch.setattr("trail.daemon.runtime_service.sleep", lambda seconds: sleep_calls.append(seconds))

    result = RuntimeService().start_run(
        workspace_root=str(tmp_path),
        window_title="崩坏：星穹铁道",
        channel="official",
        timeout_seconds=0,
        interval_seconds=0,
    )

    assert result == {"title": "崩坏：星穹铁道", "hwnd": 123, "status": "launched_needs_check"}
    assert runtime.clicks == []
    assert sleep_calls == []


def test_command_service_start_run_reuses_existing_session_on_second_call_with_real_runtime_service(tmp_path: Path, monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    capture_runtime = StartRunCaptureRuntime(workspace_root=tmp_path)
    monkeypatch.setattr("trail.runtime.window.attach_window", lambda window_title: {"title": window_title, "hwnd": 123})
    monkeypatch.setattr("trail.runtime.window.launch_game", lambda **payload: {"started": False, "already_running": False, "path": "demo.exe", "channel": payload.get("channel", "official"), "args": []})
    monkeypatch.setattr(RuntimeService, "get_runtime", lambda self, *, workspace_root, window_binding: capture_runtime)

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


def test_command_service_start_run_delays_capture_for_launched_clicked_enter(tmp_path: Path, monkeypatch):
    runtime = StartRunCaptureRuntime(
        workspace_root=tmp_path,
        probe_values=["bright", "black", "black", "bright"],
    )
    runtime_service = StartRuntimeServiceStub(
        attach_failures_before_success=1,
        launch_status="launched_clicked_enter",
        runtime=runtime,
    )
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    events: list[tuple[object, ...]] = []
    clock = {"now": 0.0}

    original_capture = runtime.capture_after_action

    def capture_after_action(optional: bool = False, request_id: str | None = None):
        events.append(("capture", optional, request_id))
        return original_capture(optional=optional, request_id=request_id)

    monkeypatch.setattr(runtime, "capture_after_action", capture_after_action)
    monkeypatch.setattr("trail.daemon.command_service._is_start_run_black_frame", lambda image: image == "black")
    monkeypatch.setattr("trail.daemon.command_service.monotonic", lambda: clock["now"], raising=False)

    def fake_sleep(seconds: float):
        events.append(("sleep", seconds))
        clock["now"] += seconds

    monkeypatch.setattr("trail.daemon.command_service.sleep", fake_sleep, raising=False)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-clicked-delay",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["status"] == "launched_clicked_enter"
    assert events == [
        ("sleep", 0.5),
        ("sleep", 0.5),
        ("sleep", 0.5),
        ("sleep", 3.0),
        ("capture", False, "req-start-clicked-delay"),
    ]
    assert runtime.capture_requests == [(False, "req-start-clicked-delay")]
    assert len(runtime.probe_requests) == 4


def test_command_service_start_run_black_polling_times_out_without_entering_black(tmp_path: Path, monkeypatch):
    runtime = StartRunCaptureRuntime(workspace_root=tmp_path, probe_values=["bright"] * 40)
    runtime_service = StartRuntimeServiceStub(
        attach_failures_before_success=1,
        launch_status="launched_clicked_enter",
        runtime=runtime,
    )
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    events: list[tuple[object, ...]] = []
    clock = {"now": 0.0}

    original_capture = runtime.capture_after_action

    def capture_after_action(optional: bool = False, request_id: str | None = None):
        events.append(("capture", optional, request_id))
        return original_capture(optional=optional, request_id=request_id)

    monkeypatch.setattr(runtime, "capture_after_action", capture_after_action)
    monkeypatch.setattr("trail.daemon.command_service._is_start_run_black_frame", lambda image: image == "black")
    monkeypatch.setattr("trail.daemon.command_service.monotonic", lambda: clock["now"], raising=False)

    def fake_sleep(seconds: float):
        events.append(("sleep", seconds))
        clock["now"] += seconds

    monkeypatch.setattr("trail.daemon.command_service.sleep", fake_sleep, raising=False)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-black-timeout-no-enter",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    sleep_values = [value for kind, value, *rest in events if kind == "sleep"]
    assert payload["ok"] is True
    assert payload["data"]["status"] == "launched_clicked_enter"
    assert abs(sum(sleep_values) - 15.0) < 1e-9
    assert 3.0 not in sleep_values
    assert events[-1] == ("capture", False, "req-start-black-timeout-no-enter")


def test_command_service_start_run_black_polling_times_out_without_leaving_black(tmp_path: Path, monkeypatch):
    runtime = StartRunCaptureRuntime(workspace_root=tmp_path, probe_values=["black"] * 40)
    runtime_service = StartRuntimeServiceStub(
        attach_failures_before_success=1,
        launch_status="launched_clicked_enter",
        runtime=runtime,
    )
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    events: list[tuple[object, ...]] = []
    clock = {"now": 0.0}

    original_capture = runtime.capture_after_action

    def capture_after_action(optional: bool = False, request_id: str | None = None):
        events.append(("capture", optional, request_id))
        return original_capture(optional=optional, request_id=request_id)

    monkeypatch.setattr(runtime, "capture_after_action", capture_after_action)
    monkeypatch.setattr("trail.daemon.command_service._is_start_run_black_frame", lambda image: image == "black")
    monkeypatch.setattr("trail.daemon.command_service.monotonic", lambda: clock["now"], raising=False)

    def fake_sleep(seconds: float):
        events.append(("sleep", seconds))
        clock["now"] += seconds

    monkeypatch.setattr("trail.daemon.command_service.sleep", fake_sleep, raising=False)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-black-timeout-no-exit",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    sleep_values = [value for kind, value, *rest in events if kind == "sleep"]
    assert payload["ok"] is True
    assert payload["data"]["status"] == "launched_clicked_enter"
    assert abs(sum(sleep_values) - 15.0) < 1e-9
    assert 3.0 not in sleep_values
    assert events[-1] == ("capture", False, "req-start-black-timeout-no-exit")


@pytest.mark.parametrize(
    ("attach_failures_before_success", "launch_status"),
    [
        (0, "launched_needs_check"),
        (1, "launched_needs_check"),
    ],
)
def test_command_service_start_run_does_not_delay_capture_for_other_statuses(
    tmp_path: Path,
    monkeypatch,
    attach_failures_before_success: int,
    launch_status: str,
):
    runtime = StartRunCaptureRuntime(workspace_root=tmp_path)
    runtime_service = StartRuntimeServiceStub(
        attach_failures_before_success=attach_failures_before_success,
        launch_status=launch_status,
        runtime=runtime,
    )
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    events: list[tuple[object, ...]] = []

    original_capture = runtime.capture_after_action

    def capture_after_action(optional: bool = False, request_id: str | None = None):
        events.append(("capture", optional, request_id))
        return original_capture(optional=optional, request_id=request_id)

    monkeypatch.setattr(runtime, "capture_after_action", capture_after_action)
    monkeypatch.setattr("trail.daemon.command_service.sleep", lambda seconds: events.append(("sleep", seconds)), raising=False)

    payload = service.handle(
        SimpleNamespace(
            request_id=f"req-start-no-delay-{attach_failures_before_success}",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    expected_status = "attached" if attach_failures_before_success == 0 else launch_status
    assert payload["ok"] is True
    assert payload["data"]["status"] == expected_status
    assert events == [("capture", False, f"req-start-no-delay-{attach_failures_before_success}")]
    assert runtime.capture_requests == [(False, f"req-start-no-delay-{attach_failures_before_success}")]


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
        envelope["data"] = result["data"]
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


def test_command_service_start_run_requires_success_screenshot(tmp_path: Path):
    runtime_service = StartRuntimeServiceStub(runtime=StartRunMissingCaptureRuntime())
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-no-shot",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "START_RESULT_SCREENSHOT_REQUIRED",
        "message": "start.run success requires screenshot",
    }
    assert payload["screenshot"] is None
    assert payload["debug"]["last_known_stage"] == "side_effect_applied"
    assert payload["debug"]["tainted"] is True

    status = session_services.for_workspace(tmp_path).request_status("req-start-no-shot")
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    created_session_id = status["session_id"]
    assert created_session_id is not None
    assert session_services.for_workspace(tmp_path).is_session_tainted(created_session_id) is True


def test_command_service_start_run_rejects_status_outside_allowlist_as_applied_side_effect(tmp_path: Path):
    runtime_service = StartRuntimeServiceStub(attach_failures_before_success=1, launch_status="mystery")
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-invalid-status",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "START_RESULT_INVALID",
        "message": "start.run returned invalid status",
    }
    assert payload["debug"]["last_known_stage"] == "side_effect_applied"
    assert payload["debug"]["tainted"] is True

    status = session_services.for_workspace(tmp_path).request_status("req-start-invalid-status")
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert status["session_id"] is None


def test_command_service_start_run_rejects_non_dict_result_as_applied_side_effect(tmp_path: Path):
    class RuntimeService:
        def start_run(self, **kwargs):
            del kwargs
            return None

    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=RuntimeService(), session_service=session_services)

    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-invalid-result",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "START_RESULT_INVALID",
        "message": "start.run returned invalid result",
    }
    assert payload["debug"]["last_known_stage"] == "side_effect_applied"
    assert payload["debug"]["tainted"] is True

    status = session_services.for_workspace(tmp_path).request_status("req-start-invalid-result")
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert status["session_id"] is None


@pytest.mark.parametrize(
    ("method", "payload", "response_data"),
    [
        ("cw.stage.detect", {}, {"value": "preparation", "stale": False}),
        ("cw.stage.wait", {"timeout": 120}, {"value": "settle", "stale": False}),
        ("cw.replenish.read", {}, {"options": [1, 2, 3]}),
        ("cw.invest.read", {}, {"options": [1, 2]}),
        ("cw.encounter.read", {}, {"options": [1, 2]}),
        ("cw.fortune.read", {}, {"options": [1, 2]}),
    ],
)
def test_command_service_routes_cw_captured_reads_through_handle_with_capture(
    tmp_path: Path,
    method: str,
    payload: dict[str, object],
    response_data: dict[str, object],
):
    class StubCwService:
        def __init__(self):
            self.capture_calls: list[dict[str, object]] = []

        def handle(self, **kwargs):
            raise AssertionError("captured cw reads must not use plain handle()")

        def handle_mutation(self, **kwargs):
            raise AssertionError("captured cw reads must not use mutation journal")

        def handle_with_capture(self, **kwargs):
            self.capture_calls.append(dict(kwargs))
            request_id = kwargs["request_id"]
            return {
                "ok": True,
                "data": response_data,
                "screenshot": tmp_path / ".trail" / "shots" / f"{request_id}.png",
                "image_guidance": {"read_image_first": 1},
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = StubCwService()
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=f"req-{method.replace('.', '-')}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method=method,
        payload={"session_id": session.session_id, **payload},
    )

    response = command_service.handle(request)

    assert response["ok"] is True
    assert response["data"] == response_data
    assert response["screenshot"] == f".trail/shots/{request.request_id}.png"
    assert cw_service.capture_calls == [
        {
            "method": method,
            "payload": {"session_id": session.session_id, **payload},
            "workspace_root": str(tmp_path),
            "session_service": service,
            "request_id": request.request_id,
            "verbose": False,
        }
    ]


def test_command_service_routes_cw_slots_read_payload_without_agent_slot_conversion(tmp_path: Path):
    class StubCwService:
        def __init__(self):
            self.capture_calls: list[dict[str, object]] = []

        def handle(self, **kwargs):
            raise AssertionError("cw.slots.read must use captured read routing")

        def handle_mutation(self, **kwargs):
            raise AssertionError("cw.slots.read must not use mutation journal")

        def handle_with_capture(self, **kwargs):
            self.capture_calls.append(dict(kwargs))
            return {
                "ok": True,
                "data": {"front": ["希儿"], "back": [], "hand": [], "stale": False},
                "screenshot": tmp_path / ".trail" / "shots" / f"{kwargs['request_id']}.png",
                "image_guidance": {"read_image_first": 1},
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = StubCwService()
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-slots-read-zero-based-routing",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.slots.read",
        payload={"session_id": session.session_id, "slot": ["front:0"]},
    )

    response = command_service.handle(request)

    assert response["ok"] is True
    assert cw_service.capture_calls[0]["payload"] == {"session_id": session.session_id, "slot": ["front:0"]}


def test_command_service_routes_cw_shop_status_through_plain_handle(tmp_path: Path):
    class StubCwService:
        def __init__(self):
            self.handle_calls: list[dict[str, object]] = []

        def handle(self, **kwargs):
            self.handle_calls.append(dict(kwargs))
            return {"items": [{"slot": 1, "name": "银狼", "price": 20}]}

        def handle_mutation(self, **kwargs):
            raise AssertionError("cw.shop.status must not use mutation journal")

        def handle_with_capture(self, **kwargs):
            raise AssertionError("cw.shop.status must not use captured read routing")

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = StubCwService()
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-status",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.status",
        payload={"session_id": session.session_id},
    )

    response = command_service.handle(request)

    assert response["ok"] is True
    assert response["data"] == {"items": [{"slot": 1, "name": "银狼", "price": 20}]}
    assert response["screenshot"] is None
    assert "image_guidance" not in response
    assert cw_service.handle_calls == [
        {
            "method": "cw.shop.status",
            "payload": {"session_id": session.session_id},
            "workspace_root": str(tmp_path),
            "session_service": service,
        }
    ]


@pytest.mark.parametrize("slots_state", [None, {"front": [], "back": [], "hand": [], "stale": True}])
def test_command_service_cw_shop_status_sanitizes_persisted_transient_shop_fields(tmp_path: Path, slots_state):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    cw_state = loaded.scene_state.setdefault("cw", {})
    if slots_state is None:
        cw_state.pop("slots", None)
    else:
        cw_state["slots"] = dict(slots_state)
    cw_state["shop"] = {
        "items": [{"slot": 1, "name": "爻光", "role_id": "1502", "price": 1, "traits": ["仙舟"]}],
        "coins": 62,
        "reserve_full": False,
        "stale": False,
        "warnings": [{"code": "CW_ROLE_MATCH_LOW_CONFIDENCE", "message": "stale warning"}],
        "trait_summary": [
            {"trait": "仙舟", "tiers": [1, 3], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.33}
        ],
    }
    service.save_session(loaded)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )
    request = DaemonRequest(
        request_id="req-cw-shop-status-sanitize-transient",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.status",
        payload={"session_id": session.session_id},
    )

    response = command_service.handle(request)
    persisted_shop = service.load_session(session.session_id).scene_state["cw"]["shop"]

    assert response["ok"] is True
    assert response["data"]["items"] == [{"slot": 1, "name": "爻光", "role_id": "1502", "price": 1, "traits": ["仙舟"]}]
    assert "warnings" not in response["data"]
    assert "trait_summary" not in response["data"]
    assert "warnings" not in persisted_shop
    assert "trait_summary" not in persisted_shop


def test_command_service_handles_cw_stage_detect_with_request_scoped_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
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
    assert payload["screenshot"] == ".trail/shots/req-cw-stage-detect.png"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"
    assert runtime.capture_requests == [(False, "req-cw-stage-detect")]
    with pytest.raises(TrailError) as exc_info:
        service.request_status(request.request_id)
    assert exc_info.value.code == "REQUEST_NOT_FOUND"


def test_cw_captured_read_promotes_scene_warnings_and_removes_saved_warnings(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    runtime_warning = {"code": "RUNTIME_WARNING", "message": "runtime warning"}
    scene_warning = {
        "code": "CW_ROLE_MATCH_LOW_CONFIDENCE",
        "query": "交光",
        "resolved": "爻光",
        "score": 0.5,
        "message": "角色名未精确命中，请先看截图确认",
    }

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return [runtime_warning]

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_detect_cw_stage(session, detector):
        del detector
        session.scene_state.setdefault("cw", {})["stage"] = {
            "value": "preparation",
            "stale": False,
            "warnings": [scene_warning],
        }
        return session

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime))
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_stage", fake_detect_cw_stage)

    payload = cw_service.handle_with_capture(
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-cw-stage-warning-promotion",
        verbose=False,
    )

    assert payload["ok"] is True
    assert payload["data"] == {"value": "preparation", "stale": False}
    assert payload["warnings"] == [runtime_warning, scene_warning]
    persisted = session_service.load_session(session.session_id)
    assert persisted.scene_state["cw"]["stage"] == {"value": "preparation", "stale": False}


def test_command_service_routes_cw_equipment_read_through_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    calls: list[str] = []

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            assert optional is False
            calls.append(f"capture:{request_id}")
            return tmp_path / ".trail" / "shots" / "equipment.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    snapshot = {
        "count": 1,
        "uncertain": 0,
        "empty": 59,
        "items": [
            {
                "pos": "equipment:1",
                "idx": 1,
                "row": 1,
                "col": 1,
                "center": {"x": 1855, "y": 275},
                "name": "生命之花",
                "equipment_id": "life-flower",
                "cache_key": "advanced-life-flower",
                "score": 0.93,
                "gap": 0.14,
                "uncertain": False,
                "alt": "光能电池",
                "alt_score": 0.79,
                "candidates": [],
            }
        ],
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }

    def fake_read_equipment(runtime, workspace_root=None, raw_config=None):
        del runtime, workspace_root, raw_config
        calls.append("read")
        return snapshot

    def fake_apply_equipment(session, runtime, workspace_root=None, request_id=None):
        assert request_id == "req-equipment-read"
        applied = fake_read_equipment(runtime, workspace_root=workspace_root)
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(applied)
        return applied

    monkeypatch.setattr("trail.scenes.cw.equipment.read_cw_equipment", fake_read_equipment)
    monkeypatch.setattr(
        "trail.scenes.cw.equipment.fetch_cw_raw_guide_config",
        lambda workspace_root=None: {"rpg_game_big_version": "test", "equipment_list": []},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.read_cw_equipment",
        fake_read_equipment,
        raising=False,
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_equipment_read",
        fake_apply_equipment,
    )
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-equipment-read",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.equipment.read",
            payload={},
        )
    )

    assert response["ok"] is True
    assert response["screenshot"] == ".trail/shots/equipment.png"
    assert response["data"] == snapshot
    assert calls == ["read", "capture:req-equipment-read"]
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is False
    assert persisted["items"][0]["pos"] == "equipment:1"
    assert persisted["items"][0]["center"] == {"x": 1855, "y": 275}


def test_command_service_cw_equipment_read_uses_resource_service_without_prepare(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    expected_raw_config = {"rpg_game_big_version": "3.2", "equipment_list": []}
    expected_recognizer = object()
    calls: list[tuple[object, object]] = []

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            calls.append((expected_raw_config, expected_recognizer))
            return expected_raw_config, expected_recognizer

    snapshot = {"count": 0, "uncertain": 0, "empty": 60, "items": [], "stale": False}

    def fake_apply_equipment(session, runtime_arg, *, workspace_root=None, raw_config=None, recognizer=None, request_id=None):
        assert runtime_arg is runtime
        assert workspace_root == str(tmp_path)
        assert raw_config is expected_raw_config
        assert recognizer is expected_recognizer
        assert request_id == "req-equipment-read-resource-service"
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return deepcopy(snapshot)

    monkeypatch.setattr(cw_service_module, "apply_cw_equipment_read", fake_apply_equipment)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service, cw_resource_service=ResourceService()),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-equipment-read-resource-service",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.equipment.read",
            payload={},
        )
    )

    assert response["ok"] is True
    assert response["data"] == snapshot
    assert calls == [(expected_raw_config, expected_recognizer)]


def test_command_service_cw_equipment_read_resource_service_path_does_not_prepare_or_load_icons(
    tmp_path: Path,
    monkeypatch,
):
    from PIL import Image
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.equipment_recognition import EquipmentRecognitionResult

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class Runtime:
        def capture_image(self, **kwargs):
            del kwargs
            return Image.new("RGBA", (1920, 1080), "black")

        def capture_after_action(self, optional=False, request_id=None):
            del optional, request_id
            return tmp_path / "equipment.jpg"

    class Recognizer:
        def recognize(self, image):
            del image
            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            return {"rpg_game_big_version": "3.2", "equipment_list": []}, Recognizer()

    monkeypatch.setattr(
        "trail.scenes.cw.equipment.fetch_cw_raw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("raw config must not be fetched")),
    )
    monkeypatch.setattr(
        "trail.scenes.cw.equipment.prepare_equipment_icon_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prepare must not be called")),
    )
    monkeypatch.setattr(
        "trail.scenes.cw.equipment.load_cached_equipment_icons",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("load icons must not be called")),
    )
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()),
        cw_resource_service=ResourceService(),
    )

    response = cw_service.handle_with_capture(
        method="cw.equipment.read",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-equipment-read-no-network",
    )

    assert response["ok"] is True
    assert response["data"]["count"] == 0
    assert response["screenshot"].endswith("equipment.jpg")


def test_command_service_uses_resource_service_for_portal_config_without_fetch(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace()
    expected_portals = [{"id": "p1", "name": "公司时刻"}]

    class ResourceService:
        def bundle(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            return SimpleNamespace(
                guide_config={"portal_list": expected_portals, "strategy_list": []},
                guide_config_enriched={"portal_list": expected_portals, "strategy_list": [], "traits": []},
            )

    def fake_detect_portal(session_arg, *, runtime, portal_list):
        assert session_arg.session_id == session.session_id
        assert portal_list == expected_portals
        return {"cards": [], "stale": False}

    monkeypatch.setattr(
        cw_service_module,
        "fetch_cw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guide config fetch must not be called")),
    )
    monkeypatch.setattr(cw_service_module, "detect_cw_portal", fake_detect_portal)
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime),
        cw_resource_service=ResourceService(),
    )

    response = cw_service.handle_with_capture(
        method="cw.portal.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-portal-config-cache",
    )

    assert response["ok"] is True


def test_cw_service_equipment_compose_uses_resource_service_and_injections(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    expected_raw_config = {"rpg_game_big_version": "4.2", "equipment_list": [{"name": "高周波电锯"}]}
    expected_recognizer = object()
    expected_guide_config = {"roles": [], "traits": [{"name": "量子"}], "source": "enriched"}
    calls: dict[str, object] = {}

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            calls["equipment_workspace"] = workspace_root
            return expected_raw_config, expected_recognizer

        def slots_read_resources(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            calls.setdefault("bundle_workspaces", []).append(workspace_root)
            assert workspace_root == str(tmp_path)
            return SimpleNamespace(
                raw_config={"rpg_game_big_version": "unused"},
                guide_config={"source": "base"},
                guide_config_enriched=expected_guide_config,
            )

    def fake_compose(
        session_arg,
        runtime,
        *,
        name,
        slot,
        role,
        workspace_root=None,
        request_id=None,
        raw_config=None,
        recognizer=None,
        slots_reader=None,
        guide_config=None,
        preflight_read_scope=None,
    ):
        assert session_arg.session_id == session.session_id
        assert name == "高周波电锯"
        assert slot == "front:1"
        assert role == "希儿"
        assert workspace_root == str(tmp_path)
        assert request_id == "req-equipment-compose-config-cache"
        assert raw_config is expected_raw_config
        assert recognizer is expected_recognizer
        assert callable(slots_reader)
        assert guide_config == expected_guide_config
        assert callable(preflight_read_scope)
        calls["runtime"] = runtime
        return {"pos": "front:1", "name": role, "equipment": name, "count": 1}

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            calls["capture"] = (optional, request_id)
            return str(tmp_path / ".trail" / "shots" / f"{request_id}.png")

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()),
        cw_resource_service=ResourceService(),
    )

    response = cw_service.handle_mutation(
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-equipment-compose-config-cache",
        verbose=False,
    )

    assert response["ok"] is True
    assert response["data"] == {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1}
    assert response["screenshot"].endswith("req-equipment-compose-config-cache.png")
    assert calls["equipment_workspace"] == str(tmp_path)
    assert calls["bundle_workspaces"] == [str(tmp_path)]


def test_cw_service_equipment_compose_preflight_scope_suppresses_click_but_drag_failure_is_unknown(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def click_point(self, x: int, y: int):
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, **kwargs):
            del kwargs
            self.drags.append((from_x, from_y, to_x, to_y))

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return str(tmp_path / ".trail" / "shots" / f"{request_id}.png")

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    runtime = Runtime()

    def fake_compose(session_arg, runtime, *, name, preflight_read_scope, **kwargs):
        del session_arg, kwargs
        with preflight_read_scope():
            runtime.click_point(11, 22)
        if name == "preflight-only":
            raise TrailError("EXPECTED_PRE_DRAG", "before drag")
        runtime.drag_to(1, 2, 3, 4)
        raise RuntimeError("after drag")

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime),
        cw_resource_service=ResourceService(),
    )

    with pytest.raises(TrailError) as preflight_error:
        cw_service.handle_mutation(
            method="cw.equipment.compose",
            payload={"session_id": session.session_id, "name": "preflight-only", "slot": "front:1", "role": "希儿"},
            workspace_root=str(tmp_path),
            session_service=session_service,
            request_id="req-equipment-compose-preflight-only",
            verbose=False,
        )

    assert preflight_error.value.code == "EXPECTED_PRE_DRAG"
    assert runtime.clicks == [(11, 22)]
    assert runtime.drags == []

    with pytest.raises(SideEffectAppliedButStateNotPersisted) as side_effect_error:
        cw_service.handle_mutation(
            method="cw.equipment.compose",
            payload={"session_id": session.session_id, "name": "after-drag", "slot": "front:1", "role": "希儿"},
            workspace_root=str(tmp_path),
            session_service=session_service,
            request_id="req-equipment-compose-after-drag",
            verbose=False,
        )

    assert runtime.clicks == [(11, 22), (11, 22)]
    assert runtime.drags == [(1, 2, 3, 4)]
    assert side_effect_error.value.envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert side_effect_error.value.envelope["debug"] == {
        "detail": "RuntimeError: after drag",
        "last_known_stage": "side_effect_applied",
    }


def test_cw_service_equipment_compose_success_without_screenshot_is_persisted_unknown(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_warning = {"code": "RUNTIME_WARNING", "message": "capture missing"}

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return None

        def collect_warnings(self):
            return [runtime_warning]

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_compose(session_arg, runtime, **kwargs):
        del runtime, kwargs
        session_arg.scene_state.setdefault("cw", {})["compose_marker"] = "persisted"
        return {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1}

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()),
        cw_resource_service=ResourceService(),
    )

    with pytest.raises(PersistedButResponseUnknown) as unknown_error:
        cw_service.handle_mutation(
            method="cw.equipment.compose",
            payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
            workspace_root=str(tmp_path),
            session_service=session_service,
            request_id="req-equipment-compose-missing-screenshot",
            verbose=False,
        )

    assert session_service.load_session(session.session_id).scene_state["cw"]["compose_marker"] == "persisted"
    assert unknown_error.value.envelope["ok"] is False
    assert unknown_error.value.envelope["screenshot"] is None
    assert unknown_error.value.envelope["warnings"] == [runtime_warning]
    assert unknown_error.value.envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert unknown_error.value.envelope["debug"] == {
        "detail": "TrailError: cw.equipment.compose success requires screenshot",
        "last_known_stage": "state_persisted",
    }


def test_command_service_equipment_compose_missing_screenshot_returns_recoverable_unknown(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_warning = {"code": "RUNTIME_WARNING", "message": "already captured"}
    scene_warning = {"code": "SCENE_WARNING", "message": "scene warning"}
    trace_event = {"step": "capture", "ts": "2026-05-02T00:00:00.000Z", "ok": 1}

    class ResourceService:
        def equipment_read_resources(self, *, workspace_root):
            del workspace_root
            return {"rpg_game_big_version": "4.2", "equipment_list": []}, object()

        def slots_read_resources(self, *, workspace_root):
            del workspace_root
            return {}, {}, object()

        def bundle(self, *, workspace_root):
            del workspace_root
            return SimpleNamespace(guide_config={}, guide_config_enriched={})

    class Runtime:
        def __init__(self):
            self.warnings = [runtime_warning]
            self.trace = [trace_event]

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return None

        def collect_warnings(self):
            warnings = list(self.warnings)
            self.warnings.clear()
            return warnings

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            trace = list(self.trace)
            self.trace.clear()
            return trace

    def fake_compose(session_arg, runtime, **kwargs):
        del runtime, kwargs
        session_arg.scene_state.setdefault("cw", {})["compose_marker"] = "persisted"
        return {
            "pos": "front:1",
            "name": "希儿",
            "equipment": "高周波电锯",
            "count": 1,
            "warnings": [scene_warning],
        }

    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service, cw_resource_service=ResourceService())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-equipment-compose-command-missing-screenshot",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=True,
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
    )

    response = command_service.handle(request)
    status = session_service.request_status(request.request_id)

    assert response["request_id"] == request.request_id
    assert response["ok"] is False
    assert response["error"] == {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"}
    assert response["screenshot"] is None
    assert response["timing"].get("elapsed_ms") is not None
    assert response["warnings"] == [runtime_warning, scene_warning]
    assert response["debug"]["trace"] == [trace_event]
    assert response["debug"]["last_known_stage"] == "state_persisted"
    assert status["final_state"] == "persisted_but_response_unknown"
    assert session_service.load_session(session.session_id).scene_state["cw"]["compose_marker"] == "persisted"


def test_cw_service_equipment_compose_fallback_builds_recognizer_without_resource_service(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    raw_config = {"rpg_game_big_version": "4.2", "equipment_list": [{"name": "高周波电锯"}]}
    expected_catalog = ["catalog-entry"]
    expected_icons = {"icons": ["cached"]}
    expected_recognizer = object()
    calls: dict[str, object] = {}

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional
            return str(tmp_path / ".trail" / "shots" / f"{request_id}.png")

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_build_catalog(config):
        calls["catalog_config"] = config
        return expected_catalog

    def fake_load_icons(catalog, *, workspace_root):
        calls["icon_catalog"] = catalog
        calls["icon_workspace"] = workspace_root
        return expected_icons

    def fake_recognizer(icons):
        calls["recognizer_icons"] = icons
        return expected_recognizer

    def fake_compose(session_arg, runtime, *, raw_config=None, recognizer=None, **kwargs):
        del runtime, kwargs
        assert session_arg.session_id == session.session_id
        assert raw_config is raw_config_fixture
        assert recognizer is expected_recognizer
        return {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1}

    raw_config_fixture = raw_config
    monkeypatch.setattr(cw_service_module, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config_fixture)
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(cw_service_module, "build_cw_equipment_catalog", fake_build_catalog, raising=False)
    monkeypatch.setattr(cw_service_module, "load_cached_equipment_icons", fake_load_icons, raising=False)
    monkeypatch.setattr(cw_service_module, "VectorEquipmentIconRecognizer", fake_recognizer, raising=False)
    monkeypatch.setattr(cw_service_module, "slots_reader_factory", lambda runtime, targets=None, request_id=None, dismiss_initial_overlay=True: object())
    monkeypatch.setattr(cw_service_module, "compose_and_equip_cw_equipment", fake_compose)
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()), cw_resource_service=None)

    response = cw_service.handle_mutation(
        method="cw.equipment.compose",
        payload={"session_id": session.session_id, "name": "高周波电锯", "slot": "front:1", "role": "希儿"},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-equipment-compose-fallback-recognizer",
        verbose=False,
    )

    assert response["ok"] is True
    assert calls == {
        "catalog_config": raw_config_fixture,
        "icon_catalog": expected_catalog,
        "icon_workspace": str(tmp_path),
        "recognizer_icons": expected_icons,
    }


def test_command_service_uses_cw_service_for_guide_config(tmp_path: Path, monkeypatch):
    expected_config = {"season": "test", "roles": [], "traits": []}
    calls: list[tuple[str, bool]] = []

    class CwService:
        def guide_config(self, *, workspace_root, enrich_traits: bool = False):
            calls.append((workspace_root, enrich_traits))
            return expected_config

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guide config fetch must not be called")),
    )
    command_service = CommandService(
        runtime_service=SimpleNamespace(),
        session_service=SessionServiceRegistry(),
        cw_service=CwService(),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-guide-config-cached",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="guide.config.cw",
            payload={},
        )
    )

    assert response["ok"] is True
    assert response["data"] == expected_config
    assert calls == [(str(tmp_path), True)]


def test_cw_equipment_read_reuses_recognition_screenshot(monkeypatch, tmp_path):
    from PIL import Image
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_image(self, **kwargs):
            return Image.new("RGBA", (1920, 1080), "black")

        def save_capture_image_to_workspace(self, image, request_id=None):
            path = tmp_path / f"{request_id}.jpg"
            image.convert("RGB").save(path)
            return path

        def capture_after_action(self, optional=False, request_id=None):
            self.capture_after_action_calls += 1
            return tmp_path / "unexpected.jpg"

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime))

    monkeypatch.setattr(
        "trail.scenes.cw.equipment.fetch_cw_raw_guide_config",
        lambda workspace_root=None: {"rpg_game_big_version": "3.2", "equipment_list": []},
    )
    monkeypatch.setattr(
        "trail.scenes.cw.equipment.read_cw_equipment",
        lambda runtime, workspace_root=None, raw_config=None, recognizer=None, request_id=None: {
            "count": 0,
            "uncertain": 0,
            "empty": 60,
            "items": [],
            "backend": "vector",
            "layout": "default",
            "columns": 10,
            "rows": 6,
            "stale": False,
            "_screenshot": str(tmp_path / "r1.jpg"),
        },
    )

    response = cw_service.handle_with_capture(
        method="cw.equipment.read",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="r1",
    )

    assert response["ok"] is True
    assert response["screenshot"].endswith("r1.jpg")
    assert "_screenshot" not in response["data"]
    saved_session = session_service.load_session(session.session_id)
    assert "_screenshot" not in saved_session.scene_state["cw"]["equipment"]
    assert runtime.capture_after_action_calls == 0


def test_cw_equipment_read_falls_back_to_capture_after_action_when_reuse_save_fails(monkeypatch, tmp_path):
    from PIL import Image
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_image(self, **kwargs):
            return Image.new("RGBA", (1920, 1080), "black")

        def save_capture_image_to_workspace(self, image, request_id=None):
            raise OSError("disk full")

        def capture_after_action(self, optional=False, request_id=None):
            self.capture_after_action_calls += 1
            return tmp_path / "fallback.jpg"

    class Recognizer:
        def recognize(self, image):
            from trail.scenes.cw.equipment_recognition import EquipmentRecognitionResult

            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    cw_service = CwService(
        runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime),
        cw_resource_service=SimpleNamespace(
            equipment_read_resources=lambda workspace_root: ({"rpg_game_big_version": "3.2", "equipment_list": []}, Recognizer())
        ),
    )

    response = cw_service.handle_with_capture(
        method="cw.equipment.read",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="r1",
    )

    assert response["ok"] is True
    assert response["screenshot"].endswith("fallback.jpg")
    assert runtime.capture_after_action_calls == 1


def test_cw_equipment_prepare_refresh_invalidates_and_switches_to_workspace_override(monkeypatch, tmp_path):
    from trail.daemon.cw_service import CwService

    del monkeypatch
    calls = []

    class ResourceService:
        def refresh_equipment_workspace_override(self, *, workspace_root):
            calls.append(("refresh", workspace_root))
            return {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=ResourceService())

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id, "refresh": True},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}
    assert calls == [("refresh", str(tmp_path))]


def test_cw_equipment_prepare_default_uses_bundle_summary_without_downloading(monkeypatch, tmp_path):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    class ResourceService:
        def equipment_prepare_summary(self, *, workspace_root):
            assert workspace_root == str(tmp_path)
            return {"big_version": "3.2", "count": 2, "cached": 2, "downloaded": 0, "refreshed": False}

    monkeypatch.setattr(
        cw_service_module,
        "prepare_cw_equipment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("default prepare must not download or prepare cache")),
    )

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=ResourceService())

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id, "refresh": False},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 2, "cached": 2, "downloaded": 0, "refreshed": False}


def test_cw_equipment_prepare_default_without_resource_service_uses_bundle_summary(monkeypatch, tmp_path):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    class Bundle:
        big_version = "3.2"
        equipment_manifest = {"items": [{"name": "幸运星"}]}

    monkeypatch.setattr(cw_service_module, "load_default_cw_resource_bundle", lambda workspace_root=None: Bundle())
    monkeypatch.setattr(
        cw_service_module,
        "prepare_cw_equipment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("default prepare must not download or prepare cache")),
    )

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=None)

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": False}


def test_cw_resource_service_refresh_rejects_workspace_cache_symlink_escape(tmp_path: Path):
    from trail.daemon.cw_resource_service import CwResourceService

    external_cache = tmp_path / "external-cache"
    external_cache.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trail_dir = workspace / ".trail"
    trail_dir.mkdir()
    cache_link = trail_dir / "cache"
    try:
        cache_link.symlink_to(external_cache, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")

    class Bundle:
        big_version = "3.2"
        identity = "base"
        source_kind = "package"
        manifest = {"resource_version": "3.2"}
        equipment_manifest = {"items": []}

    service = CwResourceService(
        bundle_loader=lambda workspace_root=None: Bundle(),
        package_bundle_loader=lambda: Bundle(),
        source_signature=lambda workspace_root=None: ("sig",),
    )

    with pytest.raises(TrailError) as exc_info:
        service.refresh_equipment_workspace_override(workspace_root=workspace)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"
    assert not (external_cache / "cw-equipment-resource.tmp").exists()


def test_cw_resource_service_refresh_can_replace_damaged_workspace_override(monkeypatch, tmp_path: Path):
    from trail.daemon import cw_resource_service as resource_module
    from trail.daemon.cw_resource_service import CwResourceService
    from trail.scenes.cw.equipment_resources import EquipmentCatalogEntry

    workspace = tmp_path / "workspace"
    damaged_override = workspace / ".trail" / "cache" / "cw-equipment-resource"
    damaged_override.mkdir(parents=True)
    (damaged_override / "manifest.json").write_text('{"broken": true}', encoding="utf-8")
    entry = EquipmentCatalogEntry(
        cache_key="icon-a",
        id="e1",
        name="Icon A",
        kind="basic",
        category=None,
        category_name=None,
        icon_url="https://act-webstatic.mihoyo.com/icon-a.png",
        big_version="3.2",
    )

    class Bundle:
        big_version = "3.2"
        identity = "base"
        source_kind = "package"
        manifest = {"resource_version": "3.2"}
        equipment_manifest = {
            "items": [
                {
                    "cache_key": entry.cache_key,
                    "id": entry.id,
                    "name": entry.name,
                    "kind": entry.kind,
                    "category": entry.category,
                    "category_name": entry.category_name,
                    "icon_url": entry.icon_url,
                    "big_version": entry.big_version,
                    "local_path": "equipment/icons/icon-a.png",
                    "sha256": "unused",
                    "size": 1,
                }
            ]
        }

    stale_loader_calls = []

    def stale_loader(workspace_root=None):
        stale_loader_calls.append(workspace_root)
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "damaged override")

    def fake_prepare(catalog, workspace_root=None, refresh=False):
        icon_path = Path(workspace_root) / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "icon-a.png"
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_bytes(b"icon-a")
        return {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}

    monkeypatch.setattr(resource_module, "prepare_equipment_icon_cache", fake_prepare)
    monkeypatch.setattr(
        resource_module,
        "load_cached_equipment_icons",
        lambda catalog, workspace_root=None: [(entry, object())],
    )
    monkeypatch.setattr(resource_module, "build_precomputed_equipment_features", lambda icons: {"items": []})

    service = CwResourceService(
        bundle_loader=stale_loader,
        package_bundle_loader=lambda workspace_root=None: Bundle(),
        source_signature=lambda workspace_root=None: ("sig",),
    )

    result = service.refresh_equipment_workspace_override(workspace_root=workspace)

    assert result == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}
    assert stale_loader_calls == []
    assert (damaged_override / "equipment" / "manifest.json").is_file()


def test_command_service_marks_cw_equipment_snapshot_stale_after_mutation(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("cw", {})["equipment"] = {"items": [{"pos": "equipment:1"}], "stale": False}
    service.save_session(session)

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "collect.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def fake_collect_crystals(session, collector):
        del collector
        session.scene_state.setdefault("cw", {})["metrics"] = {"collected": True}
        return session

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", fake_collect_crystals)
    monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: "crystal-collector")
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-collect",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.crystals.collect",
            payload={},
        )
    )

    assert response["ok"] is True
    persisted = service.load_session(session.session_id).scene_state["cw"]["equipment"]
    assert persisted["stale"] is True
    assert persisted["items"] == [{"pos": "equipment:1"}]


def test_cw_equipment_methods_are_classified_for_command_routing():
    from trail.daemon.command_service import CW_CAPTURE_METHODS, CW_SESSION_SAVE_METHODS

    assert "cw.equipment.read" in CW_CAPTURE_METHODS
    assert "cw.equipment.prepare" in CW_SESSION_SAVE_METHODS


def test_command_service_handles_cw_enter_world_to_home(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    monkeypatch.setattr("trail.scenes.cw.entry._transition_sleep", lambda seconds: None)

    asset = _cw_asset

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

    asset = _cw_asset

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

    asset = _cw_asset

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

    asset = _cw_asset

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
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
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
        "cards": cards,
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
    assert guide_calls == []


@pytest.mark.parametrize("requested_mode", ["new", "continue"])
def test_command_service_handles_cw_start_rejects_home_with_unfinished_progress(
    tmp_path: Path,
    requested_mode: str,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    asset = _cw_asset

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

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    asset = _cw_asset

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
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert runtime.clicks == [(104, 106)]
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}, {}, {}]
    assert loaded.scene_state.get("daemon", {}).get("tainted", False) is True


def test_command_service_handles_cw_slots_place_known_failure_as_completed(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ProtocolRuntime(tmp_path / "cw-slots-place-known-fail.png")
    runtime.capture_after_action = lambda optional=False, request_id=None: str(
        tmp_path / ".trail" / "shots" / f"{request_id}.png"
    )
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def fail_after_partial_execution(session, actions, placer):
        del actions, placer
        session.scene_state.setdefault("cw", {})["slots"] = {"front": ["希儿"], "back": [], "hand": [], "stale": True}
        session.scene_state["cw"]["sell_plan"] = {}
        error = TrailError("SLOTS_CANNOT_BE_FIELDED", "target slot cannot field character: front:1")
        error.known_failure_after_save = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.place_cw_slots", fail_after_partial_execution)
    request = DaemonRequest(
        request_id="req-cw-slots-place-known-fail",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.slots.place",
        payload={
            "session_id": session.session_id,
            "actions": [{"source": "hand:0", "target": "front:0"}],
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "SLOTS_CANNOT_BE_FIELDED",
        "message": "target slot cannot field character: front:1",
    }
    assert payload["screenshot"] == ".trail/shots/req-cw-slots-place-known-fail.png"
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
    assert loaded.scene_state["cw"]["slots"]["stale"] is True


def test_command_service_handles_cw_known_failure_with_non_deepcopy_data_as_completed(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ProtocolRuntime(tmp_path / "cw-known-fail-bad-data.png")
    runtime.capture_after_action = lambda optional=False, request_id=None: str(
        tmp_path / ".trail" / "shots" / f"{request_id}.png"
    )
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    class BadDeepcopy:
        def __deepcopy__(self, memo):
            del memo
            raise RuntimeError("cannot deepcopy")

        def __str__(self) -> str:
            return "bad-deepcopy"

    def fail_after_partial_execution(session, actions, placer):
        del actions, placer
        session.scene_state.setdefault("cw", {})["slots"] = {"front": ["希儿"], "back": [], "hand": [], "stale": True}
        error = TrailError("SLOTS_CANNOT_BE_FIELDED", "target slot cannot field character: front:1")
        error.known_failure_after_save = True
        error.data = {"payload": BadDeepcopy()}
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.place_cw_slots", fail_after_partial_execution)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-known-fail-bad-data",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.place",
            payload={
                "session_id": session.session_id,
                "actions": [{"source": "hand:0", "target": "front:0"}],
            },
        )
    )
    status = service.request_status("req-cw-known-fail-bad-data")

    assert payload["ok"] is False
    assert payload["data"] == {"payload": "bad-deepcopy"}
    assert payload["error"]["code"] == "SLOTS_CANNOT_BE_FIELDED"
    assert status["final_state"] == "completed"
    assert status["tainted"] is False


def test_cw_mutation_scope_collects_trace_emitted_before_auto_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-shared-scope-success.png")
    service = CwService(runtime_service=ProtocolRuntimeService(runtime))

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.click_point(10, 20)
        return {"cards": 1}

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = service.handle_mutation(
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "lowest",
            "battle_mode": "standard",
        },
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-cw-start-shared-scope-success",
        verbose=True,
    )

    assert payload["ok"] is True
    assert payload["debug"] == {
        "trace": [
            {
                "step": "click_point",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "point": [10, 20],
            }
        ]
    }


def test_cw_mutation_promotes_scene_warnings_and_preserves_runtime_warnings(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-warning-promotion.png")
    runtime.warnings = [{"code": "RUNTIME_WARNING", "message": "runtime warning"}]
    service = CwService(runtime_service=ProtocolRuntimeService(runtime))
    scene_warning = {
        "code": "CW_ROLE_MATCH_FUZZY",
        "query": "交光",
        "resolved": "爻光",
        "score": 0.75,
        "message": "角色名未精确命中，请先看截图确认",
    }

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del runtime, mode, difficulty, battle_mode, workspace_root
        session.scene_state.setdefault("cw", {})["portal"] = {"cards": [], "warnings": [scene_warning]}
        return session.scene_state["cw"]["portal"]

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = service.handle_mutation(
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "lowest",
            "battle_mode": "standard",
        },
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-cw-start-warning-promotion",
        verbose=False,
    )

    assert payload["ok"] is True
    assert payload["data"] == {"cards": []}
    assert payload["warnings"] == [
        {"code": "RUNTIME_WARNING", "message": "runtime warning"},
        scene_warning,
    ]
    persisted = session_service.load_session(session.session_id)
    assert persisted.scene_state["cw"]["portal"] == {"cards": []}


def test_cw_mutation_scope_keeps_verbose_trace_for_unknown_side_effect_result(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-shared-scope-side-effect-unknown.png")
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=service)

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.click_point(10, 20)
        raise RuntimeError("side effect failed")

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-start-side-effect-unknown-trace",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=True,
            method="cw.start",
            payload={
                "session_id": session.session_id,
                "mode": "new",
                "difficulty": "lowest",
                "battle_mode": "standard",
            },
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert payload["debug"] == {
        "trace": [
            {
                "step": "click_point",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "point": [10, 20],
            }
        ],
        "detail": "RuntimeError: side effect failed",
        "last_known_stage": "side_effect_applied",
    }


def test_cw_mutation_verbose_trace_is_jsonable_for_unknown_side_effect_result(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-jsonable-side-effect-unknown.png")
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=service)

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.click_point(10, 20)
        runtime._runtime._local.trace.append(
            {
                "step": "artifact",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "path": tmp_path / "artifact.png",
            }
        )
        raise RuntimeError("side effect failed")

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-start-side-effect-jsonable-trace",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=True,
            method="cw.start",
            payload={
                "session_id": session.session_id,
                "mode": "new",
                "difficulty": "lowest",
                "battle_mode": "standard",
            },
        )
    )

    json.dumps(payload, ensure_ascii=False)
    status = session_service.request_status("req-cw-start-side-effect-jsonable-trace")
    assert status["final_state"] == "applied_but_not_persisted"
    artifact_event = next(event for event in payload["debug"]["trace"] if event["step"] == "artifact")
    assert artifact_event["path"].endswith("artifact.png")
    assert type(artifact_event["path"]) is str


def test_cw_mutation_unknown_result_without_verbose_does_not_initialize_runtime_for_debug(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = session_service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
    }
    session_service.save_session(loaded)
    runtime_calls: list[dict[str, object]] = []
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime_calls.append(dict(kwargs)))
    service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(session_service, "save_session", lambda model: (_ for _ in ()).throw(OSError("save failed")))

    with pytest.raises(SideEffectAppliedButStateNotPersisted) as error:
        service.handle_mutation(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=session_service,
            request_id="req-cw-current-save-fail-no-verbose",
            verbose=False,
        )

    assert runtime_calls == []
    assert error.value.envelope["debug"] == {
        "detail": "OSError: save failed",
        "last_known_stage": "side_effect_applied",
    }


def test_cw_mutation_scope_ignores_warning_collection_failure_after_state_persisted(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-shared-scope-state-persisted-unknown.png")
    runtime.collect_warnings = lambda: (_ for _ in ()).throw(RuntimeError("warnings failed"))
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=service)

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.click_point(10, 20)
        return {"cards": 1}

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-start-state-persisted-unknown-trace",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=True,
            method="cw.start",
            payload={
                "session_id": session.session_id,
                "mode": "new",
                "difficulty": "lowest",
                "battle_mode": "standard",
            },
        )
    )

    assert payload["ok"] is True
    assert payload["data"] == {"cards": 1}
    assert payload["warnings"] == []
    assert payload["screenshot"] == "req-cw-start-state-persisted-unknown-trace.png"
    assert payload["image_guidance"] == {"read_image_first": 1}
    assert type(payload["image_guidance"]["read_image_first"]) is int
    assert payload["debug"] == {
        "trace": [
            {
                "step": "click_point",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "point": [10, 20],
            }
        ]
    }


def test_cw_mutation_scope_keeps_verbose_trace_for_failed_before_side_effect(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-shared-scope-failed-before-side-effect.png")

    def emit_trace() -> None:
        runtime._local.trace.append(
            {
                "step": "ocr",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "pieces": 0,
            }
        )

    runtime.emit_trace = emit_trace
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=service)

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.emit_trace()
        error = TrailError("CW_START_FAILED", "start failed")
        error.debug = {"artifact": tmp_path / "error-artifact.txt"}
        raise error

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-start-failed-before-side-effect-trace",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=True,
            method="cw.start",
            payload={
                "session_id": session.session_id,
                "mode": "new",
                "difficulty": "lowest",
                "battle_mode": "standard",
            },
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {"code": "CW_START_FAILED", "message": "start failed"}
    assert payload["debug"] == {
        "trace": [
            {
                "step": "ocr",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "pieces": 0,
            }
        ],
        "artifact": str(tmp_path / "error-artifact.txt"),
    }


def test_command_service_handles_cw_slots_place_known_failure_save_error_as_applied_but_not_persisted(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ProtocolRuntime(tmp_path / "cw-slots-place-known-fail-save-error.png")
    runtime.capture_after_action = lambda optional=False, request_id=None: str(
        tmp_path / ".trail" / "shots" / f"{request_id}.png"
    )
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def fail_after_partial_execution(session, actions, placer):
        del actions, placer
        session.scene_state.setdefault("cw", {})["slots"] = {"front": ["希儿"], "back": [], "hand": [], "stale": True}
        session.scene_state["cw"]["sell_plan"] = {}
        error = TrailError("SLOTS_CANNOT_BE_FIELDED", "target slot cannot field character: front:1")
        error.known_failure_after_save = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.place_cw_slots", fail_after_partial_execution)
    monkeypatch.setattr(service, "save_session", lambda model: (_ for _ in ()).throw(OSError("save failed")))
    request = DaemonRequest(
        request_id="req-cw-slots-place-known-fail-save-error",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.slots.place",
        payload={
            "session_id": session.session_id,
            "actions": [{"source": "hand:0", "target": "front:0"}],
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert payload["debug"]["last_known_stage"] == "side_effect_applied"
    assert "OSError: save failed" in payload["debug"]["detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True


def test_command_service_handles_cw_slots_place_known_failure_capture_error_as_persisted_but_response_unknown(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ProtocolRuntime(tmp_path / "cw-slots-place-known-fail-capture-error.png")
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def fail_after_partial_execution(session, actions, placer):
        del actions, placer
        session.scene_state.setdefault("cw", {})["slots"] = {"front": ["希儿"], "back": [], "hand": [], "stale": True}
        session.scene_state["cw"]["sell_plan"] = {}
        error = TrailError("SLOTS_CANNOT_BE_FIELDED", "target slot cannot field character: front:1")
        error.known_failure_after_save = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.place_cw_slots", fail_after_partial_execution)
    monkeypatch.setattr(
        "trail.daemon.cw_service.with_auto_capture",
        lambda runtime, action, verbose=False: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )
    request = DaemonRequest(
        request_id="req-cw-slots-place-known-fail-capture-error",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.slots.place",
        payload={
            "session_id": session.session_id,
            "actions": [{"source": "hand:0", "target": "front:0"}],
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert payload["debug"]["last_known_stage"] == "state_persisted"
    assert "RuntimeError: capture failed" in payload["debug"]["detail"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["tainted"] is True
    assert loaded.scene_state["cw"]["slots"]["stale"] is True


def test_cw_mutation_scope_keeps_failure_path_trace_until_known_failure_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-start-shared-scope-known-failure.png")
    service = CwService(runtime_service=ProtocolRuntimeService(runtime))

    def fake_start_cw(session, *, runtime, mode: str, difficulty: str, battle_mode: str, workspace_root: str | None = None):
        del session, mode, difficulty, battle_mode, workspace_root
        runtime.click_point(10, 20)
        error = TrailError("CW_START_DIFFICULTY_RECOVERY_REQUIRED", "recovery required")
        error.known_failure_after_save = True
        error.completed_after_side_effect = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service._start_cw", fake_start_cw)

    payload = service.handle_mutation(
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "lowest",
            "battle_mode": "standard",
        },
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-cw-start-shared-scope-known-failure",
        verbose=True,
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
        "message": "recovery required",
    }
    assert payload["debug"] == {
        "trace": [
            {
                "step": "click_point",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "point": [10, 20],
            }
        ]
    }


def test_command_service_keeps_cw_start_recovery_failure_completed_after_side_effect(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = ProtocolRuntime(tmp_path / "cw-start-recovery-known-fail.png")
    runtime.capture_after_action = lambda optional=False, request_id=None: str(
        tmp_path / ".trail" / "shots" / f"{request_id}.png"
    )
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        del session, mode, battle_mode, runtime
        error = TrailError(
            "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
        )
        error.data = {
            "requested_difficulty": difficulty,
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": 49,
            "reason": "coarse_no_progress",
            "page": "entry.new",
        }
        error.known_failure_after_save = True
        error.completed_after_side_effect = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)
    request = DaemonRequest(
        request_id="req-cw-start-recovery-known-failure",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "A7-3",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
        "message": "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
    }
    assert payload["screenshot"] == ".trail/shots/req-cw-start-recovery-known-failure.png"
    assert status["final_state"] == "completed"
    assert status["tainted"] is False


def test_command_service_keeps_cw_start_recovery_failure_failed_before_side_effect_without_input(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        del session, mode, battle_mode, runtime
        error = TrailError(
            "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
        )
        error.data = {
            "requested_difficulty": difficulty,
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": None,
            "reason": "ocr_missing",
            "page": "entry.new",
        }
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)
    request = DaemonRequest(
        request_id="req-cw-start-recovery-pre-input",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "A7-3",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
        "message": "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False


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

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)

    asset = _cw_asset

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

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    monkeypatch.setattr("trail.scenes.cw.entry._transition_sleep", lambda seconds: None)
    monkeypatch.setattr("trail.daemon.cw_service._attach_guides_to_cards", lambda cards, **kwargs: cards)

    asset = _cw_asset

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
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
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


def test_command_service_keeps_cw_start_payload_validation_before_runtime_scope_init(tmp_path: Path):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = FailingProtocolRuntimeService(TrailError("WINDOW_NOT_FOUND", "window missing"))
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start-invalid-mode-preflight",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "warp",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "CW_START_MODE_INVALID",
        "message": "unsupported cw start mode: warp",
    }
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"


def test_command_service_keeps_cw_enter_payload_validation_before_runtime_scope_init(tmp_path: Path):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = FailingProtocolRuntimeService(TrailError("WINDOW_NOT_FOUND", "window missing"))
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-enter-legacy-args-preflight",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.enter",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "current",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "CW_ENTER_ARGS_NOT_SUPPORTED",
        "message": "cw enter no longer accepts mode/difficulty/battle_mode; use cw start",
    }
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"


def test_command_service_handles_cw_hand_sell_plan_runtime_failure_after_save_as_persisted_unknown(tmp_path: Path):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("cw", {})["slots"] = {
        "stale": False,
        "hand": [None, "希儿", None, "布洛妮娅"],
    }
    service.save_session(session)

    runtime_service = FailingProtocolRuntimeService(TrailError("WINDOW_NOT_FOUND", "window missing"))
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-hand-sell-plan-runtime-fail-after-save",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.hand.sell_plan",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)
    status = service.request_status(request.request_id)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert payload["debug"]["last_known_stage"] == "state_persisted"
    assert "UnboundLocalError" not in payload["debug"]["detail"]
    assert "WINDOW_NOT_FOUND" not in payload["error"]["code"]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["tainted"] is True
    sell_plan = loaded.scene_state["cw"]["sell_plan"]
    assert sell_plan["reference_only"] is True
    assert sell_plan["candidates"] == []
    assert sell_plan["todos"] == ["stage_granularity", "stage", "team_size"]
    assert [item["slot"] for item in sell_plan["items"]] == [1, 3]
    assert [item["name"] for item in sell_plan["items"]] == ["希儿", "布洛妮娅"]
    assert all(item["category"] == "非攻略" for item in sell_plan["items"])
    assert all(item["recommendation"] == "不推荐" for item in sell_plan["items"])
    assert all(item["protected"] is False for item in sell_plan["items"])


def test_command_service_handles_cw_start_valid_ax_x_does_not_leak_public_invalid_codes(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    monkeypatch.setattr("trail.scenes.cw.entry._transition_sleep", lambda seconds: None)
    monkeypatch.setattr("trail.daemon.cw_service._attach_guides_to_cards", lambda cards, **kwargs: cards)

    asset = _cw_asset

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, object]] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == asset("entry.new"):
                return _box("entry.new", left=140, top=180)
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            if template == asset("entry.new"):
                return _box("entry.new", left=140, top=180)
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

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            return [{"text": "货币战争"}]

    cards = [{"card_idx": 1, "portal_title": "Alpha", "portal_description": "Desc", "score": 0.95}]
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-start-valid-ax-x",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "A7-3",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert asset("entry.new") in runtime.locate_calls
    if payload["ok"] is False:
        assert payload["error"]["code"] not in {
            "CW_START_DIFFICULTY_INVALID",
            "CW_ENTRY_DIFFICULTY_INVALID",
        }
    assert service.request_status(request.request_id)["final_state"] in {
        "completed",
        "failed_before_side_effect",
        "applied_but_not_persisted",
        "persisted_but_response_unknown",
    }


@pytest.mark.parametrize("difficulty", ["A3-6", "A8-41", "A9-1", "A7_3", "a7-3", "A7-03", "A8-040", "A0-00"])
def test_command_service_handles_cw_start_rejects_invalid_ax_x(
    tmp_path: Path,
    monkeypatch,
    difficulty: str,
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
        )
        or session,
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=f"req-cw-start-invalid-{difficulty}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": difficulty,
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "CW_START_DIFFICULTY_INVALID",
        "message": f"unsupported cw start difficulty: {difficulty}",
    }
    assert start_calls == []
    assert service.request_status(request.request_id)["final_state"] == "failed_before_side_effect"


def _build_cw_portal_detect_harness(tmp_path: Path, monkeypatch, *, detect_impl, guide_fetcher):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-portal-detect.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_portal", detect_impl, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_list", guide_fetcher)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-portal-detect",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.portal.detect",
        payload={"session_id": session.session_id},
    )
    return service, session, runtime, command_service, request


def test_command_service_handles_cw_portal_detect_and_updates_snapshot(tmp_path: Path, monkeypatch):
    snapshot = {
        "cards": _portal_cards(),
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
        "stale": False,
    }
    service, session, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: session.scene_state.setdefault("cw", {}).__setitem__("portal", snapshot) or snapshot,
        guide_fetcher=lambda **kwargs: {
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
                }
            ],
            "count": 1,
            "more": False,
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-detect.png"
    assert payload["data"] == snapshot
    assert runtime.capture_requests == [(False, "req-cw-portal-detect")]
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == payload["data"]


def test_command_service_cw_portal_detect_skips_guide_lookup_by_default_and_returns_raw_cards(tmp_path: Path, monkeypatch):
    snapshot = {
        "cards": _portal_cards(),
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
        "stale": False,
    }
    service, session, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: session.scene_state.setdefault("cw", {}).__setitem__("portal", snapshot) or snapshot,
        guide_fetcher=lambda **kwargs: (_ for _ in ()).throw(AssertionError("guide list must not be called")),
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == snapshot
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-detect.png"
    assert runtime.capture_requests == [(False, "req-cw-portal-detect")]
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == snapshot


def test_cw_portal_detect_does_not_fetch_guide_list_by_default(monkeypatch, tmp_path: Path):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "portal.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime))

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr(
        cw_service_module,
        "fetch_cw_guide_list",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("guide list must not be called")),
    )
    monkeypatch.setattr(
        cw_service_module,
        "detect_cw_portal",
        lambda session, runtime, portal_list: {"cards": [{"idx": 1, "portal_title": "机械城"}], "stale": False},
    )

    result = service.handle_with_capture(
        method="cw.portal.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-cw-portal-no-network",
    )

    assert result["ok"] is True
    assert "guides" not in result["data"]["cards"][0]


@pytest.mark.parametrize("method", ["cw.start", "cw.portal.refresh", "cw.portal.restart"])
def test_cw_portal_family_does_not_fetch_guide_list_by_default(monkeypatch, tmp_path: Path, method: str):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService

    class Runtime:
        def ocr(self, **kwargs):
            del kwargs
            return []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "portal.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    session_service.save_session(session)
    service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()))

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr(
        cw_service_module,
        "fetch_cw_guide_list",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("guide list must not be called")),
    )
    monkeypatch.setattr(cw_service_module, "start_cw", lambda session, **kwargs: session)
    monkeypatch.setattr(cw_service_module, "summarize_portal_cards", lambda *args, **kwargs: [{"idx": 1, "portal_title": "机械城"}])
    monkeypatch.setattr(cw_service_module, "detect_portal_collection_matches", lambda runtime: [])
    monkeypatch.setattr(cw_service_module, "refresh_cw_portal", lambda *args, **kwargs: {"cards": [{"idx": 1, "portal_title": "机械城"}], "stale": False})
    monkeypatch.setattr(cw_service_module, "select_cw_portal", lambda *args, **kwargs: None)
    monkeypatch.setattr(cw_service_module, "wait_cw_portal_in_game", lambda *args, **kwargs: None)
    monkeypatch.setattr(cw_service_module, "restart_cw_portal_to_settlement_entry", lambda *args, **kwargs: None)

    payload = {"session_id": session.session_id}
    if method == "cw.start":
        payload.update({"mode": "continue", "difficulty": "current", "battle_mode": "standard"})

    result = service.handle_with_capture(
        method=method,
        payload=payload,
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id=f"req-{method.replace('.', '-')}-no-network",
    )

    assert result["ok"] is True
    if result["data"].get("cards"):
        assert "guides" not in result["data"]["cards"][0]


def test_command_service_cw_portal_detect_failure_returns_capture_envelope(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CW_MUTATING_METHODS

    service, _, runtime, command_service, request = _build_cw_portal_detect_harness(
        tmp_path,
        monkeypatch,
        detect_impl=lambda session, runtime, portal_list: (_ for _ in ()).throw(
            TrailError("CW_PORTAL_PAGE_INVALID", "cw portal action only supports invest, current page: home")
        ),
        guide_fetcher=lambda **kwargs: {"portals": [], "count": 0, "more": False},
    )

    payload = command_service.handle(request)

    assert "cw.portal.detect" not in CW_MUTATING_METHODS
    assert payload["ok"] is False
    assert payload["request_id"] == "req-cw-portal-detect"
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "CW_PORTAL_PAGE_INVALID",
        "message": "cw portal action only supports invest, current page: home",
    }
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-detect.png"
    assert runtime.capture_requests == [(True, "req-cw-portal-detect")]
    with pytest.raises(TrailError) as exc_info:
        service.request_status(request.request_id)
    assert exc_info.value.code == "REQUEST_NOT_FOUND"


def test_strategy_methods_are_classified_between_capture_and_mutation_sets():
    from trail.daemon.command_service import CW_CAPTURE_METHODS, CW_MUTATING_METHODS

    assert "cw.strategy.detect" in CW_CAPTURE_METHODS
    assert "cw.strategy.detect" not in CW_MUTATING_METHODS
    assert "cw.strategy.select" in CW_MUTATING_METHODS
    assert "cw.strategy.refresh" in CW_MUTATING_METHODS


def test_cw_strategy_detect_handler_uses_capture_route_and_strategy_list(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-strategy-detect.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    snapshot = {
        "cards": [{"card_idx": 1, "strategy_title": "快攻", "strategy_description": "desc", "refresh_count": 1}],
        "stale": False,
    }
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    recorded: dict[str, object] = {}

    def fake_detect(session, *, runtime, strategy_list):
        recorded["runtime"] = runtime
        recorded["strategy_list"] = strategy_list
        session.scene_state.setdefault("cw", {})["strategy"] = snapshot
        return snapshot

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"strategy_list": [{"title": "快攻"}]})
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_strategy", fake_detect)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-strategy-detect",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.strategy.detect",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == snapshot
    assert payload["screenshot"] == ".trail/shots/req-cw-strategy-detect.png"
    assert runtime.capture_requests == [(False, "req-cw-strategy-detect")]
    assert recorded == {"runtime": runtime, "strategy_list": [{"title": "快攻"}]}
    assert service.load_session(session.session_id).scene_state["cw"]["strategy"] == snapshot


def test_cw_strategy_select_handler_routes_through_mutation_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-strategy-select.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    recorded: dict[str, object] = {}

    def fake_select(session, *, card_idx, runtime):
        recorded["card_idx"] = card_idx
        recorded["runtime"] = runtime
        session.scene_state.setdefault("cw", {})["strategy"] = {"cards": [], "stale": True}
        return {"card_idx": card_idx, "strategy_title": "回蓝"}

    monkeypatch.setattr("trail.daemon.cw_service.select_cw_strategy", fake_select)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-strategy-select",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.strategy.select",
        payload={"session_id": session.session_id, "card_idx": 2},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {"card_idx": 2, "strategy_title": "回蓝"}
    assert payload["screenshot"] == ".trail/shots/req-cw-strategy-select.png"
    assert runtime.capture_requests == [(False, "req-cw-strategy-select")]
    assert recorded["card_idx"] == 2
    assert recorded["runtime"] is not None
    assert service.request_status("req-cw-strategy-select")["final_state"] == "completed"
    assert service.load_session(session.session_id).scene_state["cw"]["strategy"] == {"cards": [], "stale": True}


def test_cw_strategy_refresh_handler_routes_strategy_list_through_mutation_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-strategy-refresh.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    snapshot = {
        "cards": [{"card_idx": 3, "strategy_title": "暴击", "strategy_description": "desc", "refresh_count": 2}],
        "stale": False,
    }
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    recorded: dict[str, object] = {}

    def fake_refresh(session, *, card_idx, runtime, strategy_list):
        recorded["card_idx"] = card_idx
        recorded["runtime"] = runtime
        recorded["strategy_list"] = strategy_list
        session.scene_state.setdefault("cw", {})["strategy"] = snapshot
        return snapshot

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"strategy_list": [{"title": "暴击"}]})
    monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_strategy", fake_refresh)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-strategy-refresh",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.strategy.refresh",
        payload={"session_id": session.session_id, "card_idx": 3},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == snapshot
    assert payload["screenshot"] == ".trail/shots/req-cw-strategy-refresh.png"
    assert runtime.capture_requests == [(False, "req-cw-strategy-refresh")]
    assert recorded["card_idx"] == 3
    assert recorded["runtime"] is not None
    assert recorded["strategy_list"] == [{"title": "暴击"}]
    assert service.request_status("req-cw-strategy-refresh")["final_state"] == "completed"
    assert service.load_session(session.session_id).scene_state["cw"]["strategy"] == snapshot


def _patch_cw_portal_select_auto_collect_success(monkeypatch, events: list[str], *, sleeps: list[float] | None = None):
    def fake_collect(session, collector):
        events.append("crystal.collect")
        session.scene_state.setdefault("cw", {})["metrics"] = {"last_crystal_collection": "done"}
        return session

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        assert reader == "slots-reader"
        assert targets is None
        assert guide_config == {"roles": [], "traits": []}
        events.append("slots.read")
        session.scene_state.setdefault("cw", {})["slots"] = {
            "front": [{"name": "希儿"}],
            "back": [],
            "hand": [],
            "stale": False,
        }
        return session

    def fake_apply_equipment(session, runtime, workspace_root=None):
        del runtime
        assert workspace_root is not None
        events.append("equipment.read")
        snapshot = {
            "count": 1,
            "uncertain": 0,
            "empty": 59,
            "backend": "vector",
            "layout": "default",
            "items": [{"pos": "equipment:1", "name": "基础装甲", "score": 0.9, "uncertain": False}],
            "recommendations": {
                "priority": [
                    {
                        "idx": 1,
                        "name": "高周波电锯",
                        "basics": [],
                        "required_roles": ["希儿"],
                        "acquired_roles": [],
                        "missing_roles": ["希儿"],
                    }
                ],
                "role_missing": [{"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}],
                "todos": [],
            },
            "stale": False,
        }
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return snapshot

    def fake_scan_shop(session, scanner, guide_config=None):
        assert scanner == "page-reader"
        assert guide_config == {"roles": [], "traits": []}
        events.append("shop.scan")
        session.scene_state.setdefault("cw", {})["shop"] = {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
        }
        return session

    def fake_project_shop(session):
        events.append("shop.project")
        return {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
            "stage_status_stale": True,
        }

    def fake_close_shop(session, closer):
        assert closer == "shop-closer"
        events.append("shop.close")
        session.scene_state.setdefault("cw", {})["shop"] = {"opened": False, "stale": True}
        return session

    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", fake_collect)
    monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: events.append("slots.dismiss"))
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, **kwargs: events.append(f"slots.reader({kwargs})") or "slots-reader",
    )
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fake_apply_equipment)
    monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: events.append("shop.open") or session)
    monkeypatch.setattr("trail.daemon.cw_service.shop_page_snapshot_reader_factory", lambda runtime, **kwargs: "page-reader")
    monkeypatch.setattr("trail.daemon.cw_service.scan_cw_shop", fake_scan_shop)
    monkeypatch.setattr("trail.daemon.cw_service.project_cw_shop_snapshot", fake_project_shop)
    monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", fake_close_shop)
    monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: "crystal-collector")
    monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: "shop-opener")
    monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: "shop-closer")
    if sleeps is None:
        monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None)
    else:
        monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: sleeps.append(seconds))


def test_command_service_handles_cw_slots_read_with_stage_projection(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw import slots as slots_module

    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None, enrich_traits=False: {})
    expected_role_count = {"front": 1, "back": 0, "hand": 0, "field": 1, "total": 1}
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: slots_module.CwSlotsReadResult(
            front=[{"name": "希儿"}],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"},
        ),
    )

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-slots-read",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.slots.read",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["stage"] == "preparation"
    assert payload["data"]["stage_stale"] is False
    assert payload["data"]["stage_status"] == {
        "stale": False,
        "level": 3,
        "exp": "0/8",
        "team_size": "2/2",
        "role_count": expected_role_count,
    }
    assert payload["data"]["stage_status_stale"] is False


def test_command_service_cw_slots_read_reuses_precaptured_screenshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw import slots as slots_module

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            self.capture_after_action_calls += 1
            return tmp_path / ".trail" / "shots" / "unexpected.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    monkeypatch.setattr(
        "trail.daemon.cw_service.slots_reader_factory",
        lambda runtime, targets=None: lambda: slots_module.CwSlotsReadResult(
            front=[{"name": "希儿"}, None, None, None],
            back=[None] * 6,
            hand=[None] * 9,
            screenshot=str(tmp_path / ".trail" / "shots" / "slots.png"),
        ),
    )
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-slots-read-reuse-screenshot",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id},
        )
    )

    assert payload["ok"] is True
    assert payload["screenshot"] == ".trail/shots/slots.png"
    assert "_screenshot" not in payload["data"]
    assert runtime.capture_after_action_calls == 0


def test_command_service_cw_slots_read_uncertain_reuses_exception_screenshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw import slots as slots_module

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            self.capture_after_action_calls += 1
            return tmp_path / ".trail" / "shots" / "unexpected.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    unknown = {"match_kind": "unknown", "score": 0.12}
    result = slots_module.CwSlotsReadResult(
        front=[unknown, None, None, None],
        back=[None] * 6,
        hand=[None] * 9,
        screenshot="slots-uncertain.png",
    )
    direct_session = SessionServiceRegistry().for_workspace(str(tmp_path / "direct")).create_session(
        window_binding={"title": "崩坏：星穹铁道", "hwnd": 1}
    )
    with pytest.raises(slots_module.CwSlotsRecognitionUncertainError) as exc_info:
        slots_module.read_cw_slots(direct_session, reader=lambda: result)
    assert exc_info.value.screenshot == "slots-uncertain.png"

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, targets=None: lambda: result)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-slots-read-uncertain",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id},
        )
    )

    assert payload["ok"] is False
    assert payload["screenshot"] == "slots-uncertain.png"
    assert payload["error"]["code"] == "SLOTS_RECOGNITION_UNCERTAIN"
    assert payload["warnings"][0]["code"] == "SLOTS_RECOGNITION_UNCERTAIN"
    assert payload["warnings"][0]["position"] == {"kind": "slot", "area": "front", "index": 0}
    assert payload["warnings"][0]["score"] == 0.12
    assert runtime.capture_after_action_calls == 0


def test_command_service_cw_slots_read_uses_icon_reader_without_slot_detail_ocr(tmp_path: Path, monkeypatch):
    from PIL import Image
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw import slots as slots_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_calls: list[dict[str, object]] = []
            self.saved: list[str | None] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            if point in {slot_point for points in slots_module.SLOT_POINTS_BY_AREA.values() for slot_point in points}:
                raise AssertionError("cw.slots.read must not open legacy slot detail panels")
            self.clicks.append(point)

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def capture_image(self, **kwargs):
            if kwargs == {**slots_module.SLOT_NAME_REGION, "normalize": False}:
                raise AssertionError("cw.slots.read must not capture legacy slot name region")
            self.capture_calls.append(dict(kwargs))
            if kwargs == {"normalize": True}:
                return Image.new("RGBA", (1920, 1080), "black")
            return Image.new("RGBA", (64, 32), "black")

        def save_capture_image_to_workspace(self, image, request_id=None):
            del image
            self.saved.append(request_id)
            return tmp_path / ".trail" / "shots" / f"{request_id}.jpg"

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            raise AssertionError("cw.slots.read should reuse the icon-reader screenshot")

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def ocr_image(self, image, *, ocr=None):
            del image, ocr
            raise AssertionError("cw.slots.read must not OCR legacy slot names")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize_crop(self, crop, area):
            del crop
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    name="希儿",
                    role_id="1102",
                    candidates=[],
                    score=0.91,
                    empty=False,
                    star_count=2,
                    star_boxes=[],
                    fee_color="gold",
                    match_kind="exact",
                    confidence_reason="score_gap",
                    rarity="5",
                    cost="5",
                    diagnostics={"empty_score": 0.01},
                )
            return SimpleNamespace(
                name=None,
                role_id=None,
                candidates=[],
                score=None,
                empty=True,
                star_count=0,
                star_boxes=[],
                fee_color="",
                match_kind="empty",
                confidence_reason="empty_template_match",
                rarity=None,
                cost=None,
                diagnostics={"empty_score": 0.99},
            )

    class ResourceService:
        def __init__(self):
            self.recognizer = Recognizer()
            self.calls: list[str] = []

        def slots_read_resources(self, *, workspace_root):
            self.calls.append(workspace_root)
            return {"roles": []}, {"traits": [], "roles": []}, self.recognizer

    monkeypatch.setattr(slots_module.stage, "build_cw_stage_detector", lambda runtime: lambda: "preparation")
    monkeypatch.setattr(
        slots_module,
        "run_batch_ocr",
        lambda runtime, targets, trace_prefix: SimpleNamespace(
            by_key={
                ("stage_status", "level"): SimpleNamespace(pieces=["LV.3"]),
                ("stage_status", "exp"): SimpleNamespace(pieces=["0/8"]),
                ("stage_status", "team_size"): SimpleNamespace(pieces=["1/2"]),
            }
        ),
    )
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"traits": [], "roles": []})
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    resource_service = ResourceService()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service, cw_resource_service=resource_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-slots-read-icon-reader",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["front"][0]["name"] == "希儿"
    assert resource_service.calls == [str(tmp_path)]
    assert runtime.clicks == [slots_module.INFO_DISMISS_POINT]
    assert runtime.capture_calls.count({"normalize": True}) == 1
    assert runtime.saved == ["req-slots-read-icon-reader"]


def test_command_service_persists_cw_slots_read_stage_ambiguous_failure_state(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("cw", {})["stage"] = {
        "value": "shop",
        "stale": False,
        "status": {"stale": False, "level": 3},
    }
    session.last_stage = {"scene": "cw", "value": "shop"}
    service.save_session(session)

    def reader():
        raise TrailError("STAGE_AMBIGUOUS", "当前资源无法区分阶段: preparation, shop")

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None, enrich_traits=False: {})
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, targets=None: reader)

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-slots-read-stage-ambiguous",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.slots.read",
            payload={"session_id": session.session_id},
        )
    )
    persisted = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "STAGE_AMBIGUOUS"
    assert persisted.scene_state["cw"]["stage"]["stale"] is True
    assert persisted.scene_state["cw"]["stage"]["error"]["code"] == "STAGE_AMBIGUOUS"
    assert persisted.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 3}
    assert persisted.last_stage is None


def test_command_service_handles_cw_portal_select_and_auto_applies_selected_guide(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    events: list[str] = []

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []
            self.capture_after_shop_close = False

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_after_shop_close = bool(events and events[-1] == "shop.close")
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
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: session.scene_state["cw"]["portal"].__setitem__("stale", True) or {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: None,
        raising=False,
    )
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
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
    data = payload["data"]
    assert data["card_idx"] == 2
    assert data["portal_title"] == "Beta"
    assert data["portal_description"] == "Desc"
    assert data["score"] == 0.88
    assert data["skill_info"] == [{"name": "运营思路", "text": "前期按测试运营"}]
    assert data["crystals"] == {"last_crystal_collection": "done"}
    assert data["slots"] == {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
    assert data["shop"] == {
        "opened": True,
        "stale": False,
        "items": [{"slot": 1, "name": "银狼", "price": 20}],
        "coins": 40,
        "reserve_full": False,
        "stage_status_stale": True,
    }
    assert events[-2:] == ["shop.project", "shop.close"]
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-select.png"
    assert applied_share_codes == ["##demo##"]
    assert service.load_session(session.session_id).scene_state["cw"]["portal"]["stale"] is True
    assert service.request_status("req-cw-portal-select")["final_state"] == "completed"
    assert runtime.capture_requests == [(False, "req-cw-portal-select")]
    assert runtime.capture_after_shop_close is True


def test_cw_portal_select_preserves_auto_collect_response_diagnostics_and_promotes_warnings(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.shop import CwShopApplied
    from trail.scenes.cw.slots import CwSlotsReadApplied

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            self.capture_after_action_calls += 1
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
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "stale": False},
    }
    service.save_session(session)

    slot_warning = {
        "code": "CW_ROLE_MATCH_LOW_CONFIDENCE",
        "position": {"kind": "slot", "area": "front", "index": 0},
        "query": "交光",
        "resolved": "爻光",
        "score": 0.5,
        "candidates": ["爻光:0.50"],
        "message": "角色名未精确命中，请先看截图确认",
    }
    shop_warning = {
        "code": "CW_ROLE_MATCH_LOW_CONFIDENCE",
        "position": {"kind": "shop", "slot": 1},
        "query": "交光",
        "resolved": "爻光",
        "score": 0.5,
        "candidates": ["爻光:0.50"],
        "message": "角色名未精确命中，请先看截图确认",
    }

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        del reader, targets
        assert guide_config == {"roles": [], "traits": []}
        session.scene_state.setdefault("cw", {})["slots"] = {
            "front": [{"name": "爻光", "traits": ["仙舟"]}],
            "back": [],
            "hand": [],
            "stale": False,
        }
        return CwSlotsReadApplied(
            session=session,
            response_snapshot={
                "front": [
                    {
                        "name": "爻光",
                        "raw_name": "交光",
                        "match_score": 0.5,
                        "match_kind": "low_confidence",
                        "traits": ["仙舟"],
                    }
                ],
                "back": [],
                "hand": [],
                "stale": False,
                "_screenshot": str(tmp_path / ".trail" / "shots" / "slots-reused.png"),
                "warnings": [slot_warning],
            },
        )

    def fake_scan_shop(session, scanner, guide_config=None):
        del scanner
        assert guide_config == {"roles": [], "traits": []}
        session.scene_state.setdefault("cw", {})["shop"] = {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "爻光", "price": 1, "traits": ["仙舟"]}],
            "coins": 5,
            "reserve_full": False,
        }
        return CwShopApplied(
            session=session,
            response_snapshot={
                "opened": True,
                "stale": False,
                "items": [
                    {
                        "slot": 1,
                        "name": "爻光",
                        "price": 1,
                        "traits": ["仙舟"],
                        "raw_name": "交光",
                        "match_score": 0.5,
                        "match_kind": "low_confidence",
                    }
                ],
                "coins": 5,
                "reserve_full": False,
                "warnings": [shop_warning],
            },
        )

    def fake_apply_equipment(session, runtime, workspace_root=None):
        del runtime, workspace_root
        snapshot = {
            "count": 0,
            "uncertain": 0,
            "empty": 60,
            "backend": "vector",
            "layout": "default",
            "items": [],
            "stale": False,
        }
        session.scene_state.setdefault("cw", {})["equipment"] = deepcopy(snapshot)
        return snapshot

    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: session.scene_state.setdefault("cw", {}).setdefault("metrics", {}))
    monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: None)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, **kwargs: "slots-reader")
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fake_apply_equipment)
    monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: session)
    monkeypatch.setattr("trail.daemon.cw_service.shop_page_snapshot_reader_factory", lambda runtime, **kwargs: "page-reader")
    monkeypatch.setattr("trail.daemon.cw_service.scan_cw_shop", fake_scan_shop)
    monkeypatch.setattr(
        "trail.daemon.cw_service.project_cw_shop_snapshot",
        lambda session: {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "爻光", "price": 1, "traits": ["仙舟"]}],
            "coins": 5,
            "reserve_full": False,
            "stage_status_stale": True,
        },
    )
    monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: session)
    monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: "crystal-collector")
    monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: "shop-opener")
    monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: "shop-closer")
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None)

    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=cw_service,
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-diagnostics",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 2},
        )
    )

    assert payload["ok"] is True
    assert payload["screenshot"].endswith("slots-reused.png")
    assert "_screenshot" not in payload["data"]
    assert payload["data"]["slots"]["front"][0]["raw_name"] == "交光"
    assert payload["data"]["slots"]["front"][0]["match_kind"] == "low_confidence"
    assert "_screenshot" not in payload["data"]["slots"]
    assert payload["data"]["shop"]["items"][0]["raw_name"] == "交光"
    assert payload["data"]["shop"]["items"][0]["match_kind"] == "low_confidence"
    assert "warnings" not in payload["data"]["slots"]
    assert "warnings" not in payload["data"]["shop"]
    assert payload["warnings"] == [slot_warning, shop_warning]
    assert runtime.capture_after_action_calls == 0
    persisted = service.load_session(session.session_id).scene_state["cw"]
    assert "raw_name" not in persisted["slots"]["front"][0]
    assert "raw_name" not in persisted["shop"]["items"][0]


def test_command_service_uses_portal_slots_precaptured_screenshot(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.slots import CwSlotsReadApplied

    events: list[str] = []

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            self.capture_after_action_calls += 1
            return tmp_path / ".trail" / "shots" / "unexpected-outer.png"

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
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    recorded_factory_kwargs: list[dict[str, object]] = []

    def fake_slots_reader_factory(
        runtime,
        *,
        recognizer,
        targets=None,
        request_id: str | None = None,
        dismiss_initial_overlay: bool = True,
    ):
        del runtime, recognizer
        recorded_factory_kwargs.append(
            {
                "targets": targets,
                "request_id": request_id,
                "dismiss_initial_overlay": dismiss_initial_overlay,
            }
        )
        return "slots-reader"

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        del targets
        assert reader == "slots-reader"
        assert guide_config == {"roles": [], "traits": []}
        session.scene_state.setdefault("cw", {})["slots"] = {
            "front": [{"name": "希儿"}],
            "back": [],
            "hand": [],
            "stale": False,
        }
        return CwSlotsReadApplied(
            session=session,
            response_snapshot={
                "front": [{"name": "希儿"}],
                "back": [],
                "hand": [],
                "stale": False,
                "_screenshot": str(tmp_path / ".trail" / "shots" / "slots-reused.png"),
            },
        )

    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    monkeypatch.setattr(cw_service_module, "_slots_read_resources", lambda **kwargs: ({}, {"roles": [], "traits": []}, object()))
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", fake_slots_reader_factory)
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-diagnostics",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 2},
        )
    )

    assert payload["ok"] is True
    assert payload["screenshot"].endswith("slots-reused.png")
    assert "_screenshot" not in payload["data"]
    assert "_screenshot" not in payload["data"]["slots"]
    assert recorded_factory_kwargs == [
        {
            "targets": None,
            "request_id": "req-cw-portal-select-diagnostics",
            "dismiss_initial_overlay": False,
        }
    ]
    assert runtime.capture_after_action_calls == 0


def test_cw_portal_select_warning_order_is_stable(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.shop import CwShopApplied
    from trail.scenes.cw.slots import CwSlotsReadApplied

    class Runtime:
        def __init__(self):
            self.warnings = [{"code": "RUNTIME_CAPTURE_WARNING", "message": "runtime warning"}]

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-warning-order.png"

        def collect_warnings(self):
            warnings = list(self.warnings)
            self.warnings.clear()
            return warnings

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    selected_warning = {"code": "SELECTED_DATA_WARNING", "message": "selected warning"}
    slot_warning = {"code": "SLOT_WARNING", "message": "slot warning"}
    equipment_warning = {
        "code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED",
        "message": "equipment failed",
        "detail_code": "EQUIPMENT_FAILED",
    }
    shop_warning = {"code": "SHOP_WARNING", "message": "shop warning"}

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta"}], "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        del reader, targets, guide_config
        session.scene_state.setdefault("cw", {})["slots"] = {
            "front": [{"name": "希儿"}],
            "back": [],
            "hand": [],
            "stale": False,
        }
        return CwSlotsReadApplied(
            session=session,
            response_snapshot={"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False, "warnings": [slot_warning]},
        )

    def fake_apply_equipment(session, runtime, workspace_root=None):
        del session, runtime, workspace_root
        raise TrailError("EQUIPMENT_FAILED", "equipment failed")

    def fake_scan_shop(session, scanner, guide_config=None):
        del scanner, guide_config
        session.scene_state.setdefault("cw", {})["shop"] = {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
        }
        return CwShopApplied(
            session=session,
            response_snapshot={
                "opened": True,
                "stale": False,
                "items": [{"slot": 1, "name": "银狼", "price": 20}],
                "coins": 40,
                "reserve_full": False,
                "warnings": [shop_warning],
            },
        )

    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {
            "card_idx": card_idx,
            "portal_title": "Beta",
            "warnings": [selected_warning],
        },
    )
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, [])
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_equipment_read", fake_apply_equipment)
    monkeypatch.setattr("trail.daemon.cw_service.scan_cw_shop", fake_scan_shop)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-warning-order",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 2},
        )
    )

    assert payload["ok"] is True
    assert payload["warnings"] == [
        {"code": "RUNTIME_CAPTURE_WARNING", "message": "runtime warning"},
        selected_warning,
        slot_warning,
        equipment_warning,
        shop_warning,
    ]


def test_cw_portal_select_slots_uncertain_is_soft_auto_collect_warning(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.slots import CwSlotsRecognitionUncertainError

    events: list[str] = []

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-slots-uncertain.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    previous_slots = {
        "front": [{"name": "希儿"}],
        "back": [],
        "hand": [],
        "stale": False,
        "trait_summary": [{"name": "巡猎", "count": 1}],
    }
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta"}], "stale": False},
        "slots": deepcopy(previous_slots),
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    slots_screenshot = str(tmp_path / ".trail" / "shots" / "slots-uncertain.png")
    nested_slot_warning = {
        "code": "SLOTS_RECOGNITION_UNCERTAIN",
        "position": {"kind": "slot", "area": "front", "index": 0},
        "score": 0.12,
        "match_kind": "unknown",
        "message": "槽位角色图标识别不确定，请先看截图确认",
    }

    def fake_read_slots(session, reader, targets=None, guide_config=None):
        del session, reader, targets, guide_config
        events.append("slots.read.uncertain")
        raise CwSlotsRecognitionUncertainError(
            "SLOTS_RECOGNITION_UNCERTAIN",
            "槽位角色图标识别不确定，请先看截图确认",
            screenshot=slots_screenshot,
            warnings=[nested_slot_warning],
        )

    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "Beta"},
    )
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    _patch_cw_portal_select_auto_collect_success(monkeypatch, events)
    monkeypatch.setattr("trail.daemon.cw_service.read_cw_slots", fake_read_slots)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-slots-uncertain",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 2},
        )
    )

    assert payload["ok"] is True
    assert payload["screenshot"].endswith("slots-uncertain.png")
    assert payload["data"]["slots"] == previous_slots
    assert "_screenshot" not in payload["data"]
    assert "_screenshot" not in payload["data"]["slots"]
    assert payload["data"]["slots"]["front"]
    assert payload["data"]["equipment"]["stale"] is False
    assert payload["data"]["shop"]["items"] == [{"slot": 1, "name": "银狼", "price": 20}]
    uncertain_warning = payload["warnings"][0]
    assert uncertain_warning["code"] == "CW_SLOTS_AUTO_COLLECT_UNCERTAIN"
    assert uncertain_warning["message"] == "槽位角色图标识别不确定，请先看截图确认"
    assert uncertain_warning["detail_code"] == "SLOTS_RECOGNITION_UNCERTAIN"
    assert uncertain_warning["preserved_previous"] == 1
    assert uncertain_warning["warnings"] == [nested_slot_warning]
    assert [warning["code"] for warning in payload["warnings"]] == ["CW_SLOTS_AUTO_COLLECT_UNCERTAIN"]
    assert events.index("slots.read.uncertain") < events.index("equipment.read") < events.index("shop.scan")


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
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 2, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "Beta", "portal_description": "Desc", "score": 0.88},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: None,
        raising=False,
    )
    _patch_cw_portal_select_auto_collect_success(monkeypatch, [], sleeps=sleeps)
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
    assert applied_share_codes == ["##demo##"]
    assert sleeps == [1.5, 2.0]
    assert runtime.capture_requests == [(False, "req-cw-portal-select-delay")]


def test_command_service_marks_cw_portal_select_auto_collect_failure_as_recoverable_and_blocks_followup_mutations(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    session_services = SessionServiceRegistry()
    service = session_services.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "mode": "new", "difficulty": "normal", "battle_mode": "standard", "stale": False},
    }
    service.save_session(loaded)

    class Runtime:
        def click_point(self, x, y):
            return None

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            return tmp_path / ".trail" / "shots" / "req-cw-portal-select-auto-collect-fail.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            return []

    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **_: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_services, cw_service=cw_service)
    events: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: events.append("select")
        or runtime.click_point(1, 1)
        or session.scene_state["cw"]["portal"].__setitem__("stale", True)
        or {"card_idx": card_idx, "portal_title": "商店"},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: events.append("wait") or None,
        raising=False,
    )
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: events.append("apply"))
    monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: events.append("crystal.collect") or session)
    monkeypatch.setattr("trail.daemon.cw_service.dismiss_cw_slots_overlay", lambda runtime: events.append("slots.dismiss"))
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"roles": [], "traits": []})
    monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: "crystal-collector")
    slot_reader_kwargs: list[dict[str, object]] = []

    def fake_slots_reader_factory(runtime, **kwargs):
        del runtime
        events.append("slots.reader")
        slot_reader_kwargs.append(dict(kwargs))
        return "slots-reader"

    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", fake_slots_reader_factory)
    monkeypatch.setattr(
        "trail.daemon.cw_service.read_cw_slots",
        lambda session, reader, targets=None, guide_config=None: events.append("slots.read")
        or (_ for _ in ()).throw(TrailError("SLOTS_READ_EMPTY", "empty")),
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-auto-collect-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )
    status_response = command_service.handle(
        DaemonRequest(
            request_id="req-daemon-request-status-portal-select-auto-collect-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="daemon.request_status",
            payload={"request_id": "req-cw-portal-select-auto-collect-fail"},
        )
    )
    blocked = command_service.handle(
        DaemonRequest(
            request_id="req-cw-guide-apply-blocked-after-auto-collect-taint",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-cw-portal-select-auto-collect-fail"
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert status_response["ok"] is True
    assert status_response["data"]["final_state"] == "applied_but_not_persisted"
    assert status_response["data"]["tainted"] is True
    assert status_response["data"]["last_visible_stage"] == "responded"
    assert events == ["select", "wait", "apply", "crystal.collect", "slots.dismiss", "slots.reader", "slots.read"]
    assert slot_reader_kwargs == [
        {
            "targets": None,
            "request_id": "req-cw-portal-select-auto-collect-fail",
            "dismiss_initial_overlay": False,
        }
    ]
    assert service.is_session_tainted(session.session_id) is True
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "SESSION_RECONCILE_REQUIRED"


def test_command_service_marks_cw_portal_select_post_click_apply_failure_as_recoverable_and_blocks_followup_mutations(
    tmp_path: Path,
    monkeypatch,
):
    from trail.daemon.cw_service import CwService

    session_services = SessionServiceRegistry()
    service = session_services.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "mode": "new", "difficulty": "normal", "battle_mode": "standard", "stale": False},
    }
    service.save_session(loaded)

    runtime_service = SimpleNamespace(get_runtime=lambda **_: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_services, cw_service=cw_service)
    events: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: events.append("select")
        or session.scene_state["cw"]["portal"].__setitem__("stale", True)
        or {"card_idx": card_idx, "portal_title": "商店"},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: events.append("apply") or (_ for _ in ()).throw(RuntimeError("apply failed")),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: events.append("wait") or None,
        raising=False,
    )

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-post-click-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )
    status_response = command_service.handle(
        DaemonRequest(
            request_id="req-daemon-request-status-portal-select-post-click-fail",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="daemon.request_status",
            payload={"request_id": "req-cw-portal-select-post-click-fail"},
        )
    )
    blocked = command_service.handle(
        DaemonRequest(
            request_id="req-cw-guide-apply-blocked-after-portal-taint",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-cw-portal-select-post-click-fail"
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert response["debug"]["last_known_stage"] == "side_effect_applied"
    assert status_response["ok"] is True
    assert status_response["data"]["final_state"] == "applied_but_not_persisted"
    assert status_response["data"]["tainted"] is True
    assert status_response["data"]["last_visible_stage"] == "responded"
    assert events == ["select", "wait", "apply"]
    assert service.is_session_tainted(session.session_id) is True
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "SESSION_RECONCILE_REQUIRED"


def test_command_service_keeps_cw_portal_select_side_effect_failure_verbose_trace(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    session_services = SessionServiceRegistry()
    service = session_services.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_cw_guide_fixture(),
        "constraints": _complete_cw_constraints_fixture(),
        "portal": {"cards": [{"card_idx": 1, "portal_title": "商店"}], "mode": "new", "difficulty": "normal", "battle_mode": "standard", "stale": False},
    }
    service.save_session(loaded)

    runtime = ScopedDebugProtocolRuntime(tmp_path / "cw-portal-select-side-effect-trace.png")
    runtime_service = ProtocolRuntimeService(runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_services, cw_service=cw_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: {"card_idx": card_idx, "portal_title": "商店"},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: None,
        raising=False,
    )

    def fail_after_apply_click(runtime, share_code: str) -> None:
        del share_code
        runtime.click_point(10, 20)
        raise RuntimeError("apply failed")

    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", fail_after_apply_click)

    response = command_service.handle(
        DaemonRequest(
            request_id="req-cw-portal-select-side-effect-trace",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=True,
            method="cw.portal.select",
            payload={"session_id": session.session_id, "card_idx": 1},
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    trace = response["debug"]["trace"]
    assert any(event["step"] == "click_point" and event["ts"] and event["ok"] == 1 for event in trace)
    assert response["debug"]["last_known_stage"] == "side_effect_applied"


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
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
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
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {"portal_list": []})
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
    assert payload["data"] == snapshot
    assert payload["screenshot"] == ".trail/shots/req-cw-portal-refresh.png"
    assert service.load_session(session.session_id).scene_state["cw"]["portal"] == payload["data"]
    assert service.request_status("req-cw-portal-refresh")["final_state"] == "completed"
    assert runtime.capture_requests == [(False, "req-cw-portal-refresh")]


@pytest.mark.parametrize(
    ("method", "request_id", "mutation_name"),
    [
        ("cw.battle.start", "req-cw-battle-start", "start_cw_battle"),
        ("cw.battle.continue", "req-cw-battle-continue", "continue_cw_battle"),
        ("cw.settle.next", "req-cw-settle-next", "settle_cw_next"),
    ],
)
def test_command_service_cw_stage_mutations_use_request_scoped_capture(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    mutation_name: str,
):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "stage": {"value": "battle", "stale": False},
    }
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)

    def fake_stage_mutation(session, **kwargs):
        del kwargs
        session.scene_state["cw"]["stage"] = {"stale": True}
        return session

    monkeypatch.setattr(f"trail.daemon.cw_service.{mutation_name}", fake_stage_mutation)
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=request_id,
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method=method,
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert payload["screenshot"] == f".trail/shots/{request_id}.png"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"] == {"stale": True}
    assert service.request_status(request_id)["final_state"] == "completed"
    assert runtime.capture_requests == [(False, request_id)]


def test_command_service_handles_cw_battle_run_and_persists_last_result(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-run.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.run_cw_battle",
        lambda session, *, runtime, timeout: {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": timeout},
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-battle-run",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.battle.run",
        payload={"session_id": session.session_id, "timeout": 42},
    )

    payload = command_service.handle(request)

    loaded = service.load_session(session.session_id)
    assert payload["ok"] is True
    assert payload["data"]["status"] == "in_progress"
    assert payload["screenshot"] == ".trail/shots/req-cw-battle-run.png"
    assert service.request_status("req-cw-battle-run")["final_state"] == "completed"
    assert loaded.last_result["command"] == "cw.battle.run"
    assert loaded.last_result["data"]["status"] == "in_progress"
    assert loaded.last_screenshot == ".trail/shots/req-cw-battle-run.png"
    assert runtime.capture_requests == [(False, "req-cw-battle-run")]


def _run_cw_battle_run_timeout_capture(tmp_path: Path, monkeypatch, raw_timeout=object()):
    from trail.daemon.cw_service import CwService

    sentinel = _run_cw_battle_run_timeout_capture.sentinel

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-run-timeout-normalized.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    captured: list[int] = []

    def fake_run_cw_battle(session, *, runtime, timeout):
        del session, runtime
        captured.append(timeout)
        return {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": timeout}

    monkeypatch.setattr("trail.daemon.cw_service.run_cw_battle", fake_run_cw_battle)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request_payload = {"session_id": session.session_id}
    if raw_timeout is not sentinel:
        request_payload["timeout"] = raw_timeout
    request = DaemonRequest(
        request_id="req-cw-battle-run-timeout-normalized",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.battle.run",
        payload=request_payload,
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert captured[0] == resolve_command_execution_timeout("cw.battle.run", request_payload)
    assert payload["data"]["timeout_seconds"] == captured[0]
    return captured[0]


_run_cw_battle_run_timeout_capture.sentinel = object()


def test_cw_battle_run_timeout_invalid_value_falls_back_to_default(tmp_path: Path, monkeypatch):
    assert _run_cw_battle_run_timeout_capture(tmp_path, monkeypatch, 0) == 90


def test_cw_battle_run_timeout_keeps_positive_int(tmp_path: Path, monkeypatch):
    assert _run_cw_battle_run_timeout_capture(tmp_path, monkeypatch, 45) == 45


def test_command_service_clear_in_progress_preserves_last_battle_summary(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.models import ensure_cw_state

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.last_result = {
        "command": "cw.battle.run",
        "ok": True,
        "data": {"status": "in_progress", "in_battle": True, "round": "1-4"},
        "error": None,
    }
    session.last_screenshot = ".trail/shots/req-battle.png"
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = {"scene": "cw", "value": "shop"}
    session_service.save_session(session)

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: None)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-clear-in-progress",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.battle.clear_in_progress",
            payload={},
        )
    )

    refreshed = session_service.load_session(session.session_id)
    assert payload["ok"] is True
    assert payload["data"] == {"cleared": True}
    assert refreshed.last_result == session.last_result
    assert refreshed.last_screenshot == session.last_screenshot
    assert ensure_cw_state(refreshed).get("stage") == {"stale": True}
    assert refreshed.last_stage == {"scene": "cw", "value": "shop"}
    assert ensure_cw_state(refreshed).get("battle_resume") == {}


def test_command_service_clear_in_progress_without_hint_preserves_last_battle_summary(tmp_path: Path):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.models import ensure_cw_state

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.last_result = {
        "command": "cw.battle.run",
        "ok": True,
        "data": {"status": "in_progress", "stage": "settle", "in_battle": False, "round": "1-4"},
        "error": None,
    }
    session.last_screenshot = ".trail/shots/req-battle-settle.png"
    ensure_cw_state(session)["battle_resume"] = {}
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None
    session_service.save_session(session)

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: None)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    payload = command_service.handle(
        DaemonRequest(
            request_id="req-clear-in-progress-empty",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.battle.clear_in_progress",
            payload={},
        )
    )

    refreshed = session_service.load_session(session.session_id)
    assert payload["ok"] is True
    assert payload["data"] == {"cleared": False}
    assert refreshed.last_result == session.last_result
    assert refreshed.last_screenshot == session.last_screenshot
    assert ensure_cw_state(refreshed).get("stage") == {"stale": True}
    assert refreshed.last_stage is None
    assert ensure_cw_state(refreshed).get("battle_resume") == {}


def test_command_service_handles_cw_battle_start_waits_extra_before_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-start-delay.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    sleeps: list[float] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"stage": {"value": "battle", "stale": False}}
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.start_cw_battle", lambda session, *, starter: _set_stage(session, {"stale": True}))
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: sleeps.append(seconds))
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-battle-start-delay",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.battle.start",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert sleeps == [3.0]
    assert runtime.capture_requests == [(False, "req-cw-battle-start-delay")]
    assert service.request_status("req-cw-battle-start-delay")["final_state"] == "completed"


@pytest.mark.parametrize("method", ["cw.battle.start", "cw.battle.run"])
def test_command_service_keeps_cw_battle_team_count_failure_known_after_cancel(tmp_path: Path, method: str):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_requests: list[tuple[bool, str | None]] = []

        def wait_img(self, template: str, timeout: int = 3, interval: float = 0.5):
            del template, timeout, interval
            return {"left": 100, "top": 200, "width": 60, "height": 20}

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def ocr(self, **kwargs):
            capture = kwargs.get("capture")
            if capture is None:
                return [{"text": "开始战斗"}]
            return [
                {"text": "提示", "box": {"left": 820, "top": 300, "width": 100, "height": 36}},
                {"text": "可出战角色人数未达上限，是否确认出战？", "box": {"left": 620, "top": 430, "width": 680, "height": 36}},
                {"text": "取消", "box": {"left": 380, "top": 600, "width": 100, "height": 36}},
                {"text": "确认", "box": {"left": 1160, "top": 600, "width": 100, "height": 36}},
            ]

        def click_point(self, x: int, y: int):
            self.clicks.append((x, y))

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    request_id = f"req-{method.replace('.', '-')}-team-count"
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    command_service = CommandService(
        runtime_service=runtime_service,
        session_service=registry,
        cw_service=CwService(runtime_service=runtime_service),
    )

    payload = command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method=method,
            payload={"session_id": session.session_id, "timeout": 2} if method == "cw.battle.run" else {"session_id": session.session_id},
        )
    )
    status = service.request_status(request_id)
    loaded = service.load_session(session.session_id)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "CW_BATTLE_TEAM_COUNT_INSUFFICIENT"
    assert "请先检查场上人数" in payload["error"]["message"]
    assert payload["error"]["code"] != "DAEMON_UNAVAILABLE"
    assert "mutation result unknown" not in payload["error"]["message"]
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
    assert service.is_session_tainted(session.session_id) is False
    assert loaded.scene_state.get("daemon", {}).get("tainted", False) is False
    assert runtime.clicks == [(130, 210), (430, 618)]


def test_command_service_handles_state_dump_returns_latest_battle_run_snapshot(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-run-state-dump.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.run_cw_battle",
        lambda session, *, runtime, timeout: {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": timeout},
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    battle_payload = command_service.handle(
        DaemonRequest(
            request_id="req-cw-battle-run-state-dump",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="cw.battle.run",
            payload={"session_id": session.session_id, "timeout": 57},
        )
    )
    dump_payload = command_service.handle(
        DaemonRequest(
            request_id="req-state-dump-battle-run",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=session.session_id,
            verbose=False,
            method="state.dump",
            payload={"session_id": session.session_id},
        )
    )

    assert battle_payload["ok"] is True
    assert dump_payload["ok"] is True
    assert dump_payload["data"]["last_result"]["command"] == "cw.battle.run"
    assert dump_payload["data"]["last_result"]["data"]["status"] == "in_progress"
    assert dump_payload["data"]["last_result"]["data"]["in_battle"] is True
    assert dump_payload["data"]["last_screenshot"] == ".trail/shots/req-cw-battle-run-state-dump.png"


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
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.buy_cw_shop_slot",
        lambda session, slot, expect, buyer, scanner, guide_config: _set_shop_applied(
            session,
            {"slot": slot, "expect": expect},
        ),
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


def test_command_service_handles_cw_shop_buy_exp_through_mutation_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-shop-buy-exp.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    buyer_calls = []
    scanner_calls = []

    def fake_buyer_factory(factory_runtime):
        assert getattr(factory_runtime, "_runtime", factory_runtime) is runtime
        return lambda: buyer_calls.append("clicked")

    def fake_scanner_factory(factory_runtime):
        assert getattr(factory_runtime, "_runtime", factory_runtime) is runtime

        def scanner():
            scanner_calls.append("scanned")
            return {
                "opened": True,
                "stale": False,
                "items": [],
                "coins": 36,
                "level": 4,
                "exp": "0/8",
                "reserve_full": False,
                "team_size": "4/4",
            }

        return scanner

    def fake_buy_exp(session, *, buyer, scanner, guide_config):
        del guide_config
        buyer()
        session.scene_state.setdefault("cw", {})["shop"] = scanner()
        return SimpleNamespace(session=session, scene_state=session.scene_state, response_snapshot=session.scene_state["cw"]["shop"])

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {})
    monkeypatch.setattr("trail.daemon.cw_service.shop_exp_buyer_factory", fake_buyer_factory)
    monkeypatch.setattr("trail.daemon.cw_service.shop_scan_snapshot_reader_factory", fake_scanner_factory)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr("trail.daemon.cw_service.buy_cw_shop_exp", fake_buy_exp)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-buy-exp",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_exp",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["coins"] == 36
    assert payload["screenshot"] == ".trail/shots/req-cw-shop-buy-exp.png"
    assert buyer_calls == ["clicked"]
    assert scanner_calls == ["scanned"]
    assert runtime.capture_requests == [(False, "req-cw-shop-buy-exp")]
    assert service.load_session(session.session_id).scene_state["cw"]["shop"]["level"] == 4
    assert service.request_status("req-cw-shop-buy-exp")["final_state"] == "completed"


def test_command_service_marks_cw_shop_buy_exp_post_click_failure_recoverable(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.shop import SHOP_EXP_BUY_POINT

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "req-cw-shop-buy-exp-fail.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)

    def fake_buyer_factory(factory_runtime):
        assert getattr(factory_runtime, "_runtime", factory_runtime) is runtime
        return lambda: factory_runtime.click_point(*SHOP_EXP_BUY_POINT)

    def fake_buy_exp(session, *, buyer, scanner, guide_config):
        del session, scanner, guide_config
        buyer()
        raise TrailError("CW_SHOP_SCAN_FAILED", "shop scan failed after buying exp")

    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda workspace_root=None: {})
    monkeypatch.setattr("trail.daemon.cw_service.shop_exp_buyer_factory", fake_buyer_factory)
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr("trail.daemon.cw_service.buy_cw_shop_exp", fake_buy_exp)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-buy-exp-fail",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_exp",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert runtime.clicks == [SHOP_EXP_BUY_POINT]
    assert service.request_status("req-cw-shop-buy-exp-fail")["final_state"] == "applied_but_not_persisted"


def test_server_main_injects_session_service_registry(monkeypatch):
    from trail.daemon.cw_resource_service import CwResourceService

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
    assert isinstance(getattr(captured["cw_service"], "cw_resource_service"), CwResourceService)
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


def test_daemon_response_line_serializes_non_json_values(tmp_path: Path):
    from trail.daemon.server import _serialize_response_line

    class CustomValue:
        def __str__(self) -> str:
            return "custom-value"

    line = _serialize_response_line(
        {
            "ok": False,
            "data": {},
            "screenshot": tmp_path / "shot.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"custom": CustomValue()},
            "error": {"code": "DEMO", "message": "demo"},
        },
        request_id="req-jsonable-response",
    )

    decoded = json.loads(line.decode("utf-8"))
    assert decoded["screenshot"] == str(tmp_path / "shot.png")
    assert decoded["debug"] == {"custom": "custom-value"}


def test_daemon_response_line_returns_failure_when_serialization_fails(monkeypatch):
    import trail.daemon.server as server_module

    class BrokenException(Exception):
        def __str__(self) -> str:
            raise RuntimeError("broken str")

    def fail_to_jsonable(value):
        del value
        raise BrokenException()

    monkeypatch.setattr(server_module, "to_jsonable", fail_to_jsonable)

    line = server_module._serialize_response_line(
        {
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {},
            "error": {"code": "DEMO", "message": "demo"},
        },
        request_id="req-serialization-fallback",
    )

    decoded = json.loads(line.decode("utf-8"))
    assert decoded["ok"] is False
    assert decoded["request_id"] == "req-serialization-fallback"
    assert decoded["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert decoded["error"]["message"] == "daemon response serialization failed"
    assert decoded["debug"]["detail"] == "BrokenException: <unprintable RuntimeError: broken str>"


def test_command_service_unknown_result_envelope_includes_image_guidance_when_screenshot_present(tmp_path: Path):
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())

    envelope = command_service._unknown_result_envelope(
        request_id="req-unknown-guidance",
        response={
            "screenshot": ".trail/shots/req-unknown-guidance.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"detail": "previous"},
        },
        error=OSError("state persisted marker failed"),
        last_known_stage="state_persisted",
    )

    assert envelope["ok"] is False
    assert envelope["screenshot"] == ".trail/shots/req-unknown-guidance.png"
    assert envelope["image_guidance"] == {"read_image_first": 1}
    assert type(envelope["image_guidance"]["read_image_first"]) is int


def test_command_service_failure_envelope_handles_unprintable_exception_message():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())

    class BrokenException(Exception):
        def __str__(self) -> str:
            raise RuntimeError("broken str")

    envelope = command_service._failure_envelope(error=BrokenException())

    assert envelope["error"] == {
        "code": "BrokenException",
        "message": "<unjsonable BrokenException: RuntimeError: broken str>",
    }


def test_command_service_failure_envelope_handles_non_deepcopy_error_payloads():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())

    class BadDeepcopy:
        def __deepcopy__(self, memo):
            del memo
            raise RuntimeError("cannot deepcopy")

        def __str__(self) -> str:
            return "bad-deepcopy"

    error = TrailError("BROKEN_PAYLOAD", "broken payload")
    error.data = {"payload": BadDeepcopy()}
    error.debug = {"detail": BadDeepcopy()}

    envelope = command_service._failure_envelope(error=error)

    assert envelope["data"] == {"payload": "bad-deepcopy"}
    assert envelope["debug"] == {"detail": "bad-deepcopy"}
    assert envelope["error"] == {"code": "BROKEN_PAYLOAD", "message": "broken payload"}


def test_failure_envelope_promotes_error_warnings():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    error = TrailError("CW_EQUIPMENT_MATERIALS_MISSING", "missing")
    error.warnings = [{"code": "CW_EQUIPMENT_MATERIALS_MISSING", "需求": "A:0/1", "持有": "equipment:1:B"}]

    envelope = command_service._failure_envelope(error=error)

    assert envelope["warnings"] == [
        {"code": "CW_EQUIPMENT_MATERIALS_MISSING", "需求": "A:0/1", "持有": "equipment:1:B"}
    ]
    assert envelope["data"] == {}


def test_command_service_failure_envelope_ignores_broken_error_diagnostic_properties():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())

    class BrokenDiagnosticProperties(Exception):
        @property
        def data(self):
            raise RuntimeError("data unavailable")

        @property
        def debug(self):
            raise RuntimeError("debug unavailable")

        @property
        def tainted(self):
            raise RuntimeError("tainted unavailable")

        def __str__(self) -> str:
            return "broken diagnostics"

    envelope = command_service._failure_envelope(error=BrokenDiagnosticProperties())

    assert envelope["data"] == {}
    assert envelope["debug"] is None
    assert envelope["error"] == {"code": "BrokenDiagnosticProperties", "message": "broken diagnostics"}


def test_daemon_success_jsonifies_non_deepcopy_payloads_and_metadata():
    class BadDeepcopy:
        def __deepcopy__(self, memo):
            del memo
            raise RuntimeError("cannot deepcopy")

        def __str__(self) -> str:
            return "bad-deepcopy"

    result = success(
        {"payload": BadDeepcopy()},
        request_id="req-success-jsonable",
        references=[{"reference": BadDeepcopy()}],
        debug={"debug": BadDeepcopy()},
    )

    assert result["data"] == {"payload": "bad-deepcopy"}
    assert result["references"] == [{"reference": "bad-deepcopy"}]
    assert result["debug"] == {"debug": "bad-deepcopy"}


def test_command_service_unknown_result_envelope_jsonifies_previous_response_metadata():
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())

    class BadDeepcopy:
        def __deepcopy__(self, memo):
            del memo
            raise RuntimeError("cannot deepcopy")

        def __str__(self) -> str:
            return "bad-deepcopy"

    envelope = command_service._unknown_result_envelope(
        request_id="req-unknown-jsonable",
        response={
            "timing": {"value": BadDeepcopy()},
            "warnings": [{"warning": BadDeepcopy()}],
            "references": [{"reference": BadDeepcopy()}],
            "debug": {"debug": BadDeepcopy()},
        },
        error=RuntimeError("unknown"),
        last_known_stage="state_persisted",
    )

    assert envelope["timing"] == {"value": "bad-deepcopy"}
    assert envelope["warnings"] == [{"warning": "bad-deepcopy"}]
    assert envelope["references"] == [{"reference": "bad-deepcopy"}]
    assert envelope["debug"]["debug"] == "bad-deepcopy"


def test_cw_unknown_result_envelope_handles_unprintable_exception_detail():
    import trail.daemon.cw_service as cw_service_module

    class BrokenException(Exception):
        def __str__(self) -> str:
            raise RuntimeError("broken str")

    envelope = cw_service_module._unknown_result_envelope(BrokenException(), last_known_stage="side_effect_applied")

    assert envelope["debug"]["detail"] == "BrokenException: <unprintable RuntimeError: broken str>"
    assert envelope["debug"]["last_known_stage"] == "side_effect_applied"


def test_cw_unknown_result_envelope_jsonifies_non_deepcopy_warnings_and_references():
    import trail.daemon.cw_service as cw_service_module

    class BadDeepcopy:
        def __deepcopy__(self, memo):
            del memo
            raise RuntimeError("cannot deepcopy")

        def __str__(self) -> str:
            return "bad-deepcopy"

    envelope = cw_service_module._unknown_result_envelope(
        RuntimeError("unknown"),
        warnings=[{"warning": BadDeepcopy()}],
        references=[{"reference": BadDeepcopy()}],
        debug={"debug": BadDeepcopy()},
    )

    assert envelope["warnings"] == [{"warning": "bad-deepcopy"}]
    assert envelope["references"] == [{"reference": "bad-deepcopy"}]
    assert envelope["debug"]["debug"] == "bad-deepcopy"


def test_side_effect_tracking_runtime_preserves_error_when_completed_flag_property_breaks():
    from trail.daemon.cw_service import _RuntimeSideEffectTracker, _SideEffectTrackingRuntime

    class BrokenCompletedFlag(TrailError):
        @property
        def completed_after_side_effect(self):
            raise RuntimeError("completed flag unavailable")

    class Runtime:
        def click_point(self, x, y):
            del x, y
            raise BrokenCompletedFlag("BROKEN_FLAG", "original failure")

    tracker = _RuntimeSideEffectTracker()
    runtime = _SideEffectTrackingRuntime(Runtime(), tracker)

    with pytest.raises(BrokenCompletedFlag):
        runtime.click_point(1, 2)

    assert tracker.side_effect_applied is False


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
    assert response["debug"] == {
        "detail": "TrailError: late failure after ui action",
        "last_known_stage": "side_effect_applied",
    }
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


def test_start_run_and_battle_run_timeouts_share_unified_command_policy():
    assert resolve_response_timeout("ocr.read", {}) == 120.0
    assert resolve_command_execution_timeout("start.run", {}) == 180
    assert resolve_response_timeout("start.run", {}) == 180.0
    assert resolve_response_timeout("start.run", {"window_title": "崩坏：星穹铁道"}) == 180.0
    assert resolve_command_execution_timeout("cw.battle.run", {}) == 90
    assert resolve_command_execution_timeout("cw.battle.run", None) == 90
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": 45}) == 45
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": 0}) == 90
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": -1}) == 90
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": False}) == 90
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": True}) == 90
    assert resolve_command_execution_timeout("cw.battle.run", {"timeout": "45"}) == 90
    assert resolve_response_timeout("cw.battle.run", {}) == 120.0
    assert resolve_response_timeout("cw.battle.run", None) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 90}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 45}) == 75.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 0}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": -1}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": False}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": True}) == 120.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": "45"}) == 120.0


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


def test_send_daemon_request_uses_battle_run_timeout(monkeypatch, tmp_path: Path):
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
            method="cw.battle.run",
            payload={"timeout": 570},
        ),
        "token-1",
        endpoint="127.0.0.1:8765",
    )

    assert payload["ok"] is True
    assert settimeouts == [600.0]
    assert sent


def test_trail_daemon_client_call_keeps_timeout_mapping_on_real_path(monkeypatch, tmp_path: Path):
    settimeouts: list[float] = []
    methods: list[str] = []

    class Reader:
        def __init__(self, request_id: str):
            self.request_id = request_id

        def readline(self):
            return json.dumps(build_success_response(request_id=self.request_id, data={}), ensure_ascii=False)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def __init__(self):
            self.request_id = "req-timeout"

        def settimeout(self, value):
            settimeouts.append(value)

        def sendall(self, payload):
            request_body = json.loads(payload.decode("utf-8"))
            methods.append(request_body["method"])
            self.request_id = request_body["request_id"]

        def makefile(self, *args, **kwargs):
            return Reader(self.request_id)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("trail.daemon.client.socket.create_connection", lambda *args, **kwargs: Connection())
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=send_daemon_request,
    )

    battle_payload = client.call("cw.battle.run", {"timeout": 570}, session_id="session-1")
    ocr_payload = client.call("ocr.read", {}, session_id="session-1")

    assert battle_payload["ok"] is True
    assert ocr_payload["ok"] is True
    assert methods == ["cw.battle.run", "ocr.read"]
    assert settimeouts == [600.0, 120.0]
