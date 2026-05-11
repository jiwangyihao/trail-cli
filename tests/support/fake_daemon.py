from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from trail.daemon.client import is_daemon_control_plane_error
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import DaemonRequest, InstallRecord, RuntimeRecord, TrailDaemonManifest
from trail.daemon.protocol import PROTOCOL_VERSION



class _RecordedDaemonCall(dict):
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        return all(self.get(key) == value for key, value in other.items())

class FakeDaemonClient:
    def __init__(self, responses: dict[str, dict[str, Any]], *, workspace_root: str | None = None):
        self._responses = deepcopy(responses)
        self.workspace_root = workspace_root
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        workspace_root: str | None = None,
        session_id: str | None = None,
        verbose: bool = False,
        job_id: str | None = None,
        wait_timeout: float | None = None,
        no_wait: bool = False,
    ) -> dict[str, Any]:
        from trail.daemon.command_timeouts import normalize_daemon_wait_timeout

        resolved_workspace_root = self.workspace_root if workspace_root is None else workspace_root
        control = {"mode": "async_wait", "wait_timeout": normalize_daemon_wait_timeout(wait_timeout, no_wait=no_wait), "side_effect_stage": "none"}
        self.calls.append(
            _RecordedDaemonCall(
                deepcopy(
                    {
                        "method": method,
                        "payload": payload,
                        "workspace_root": resolved_workspace_root,
                        "session_id": session_id,
                        "verbose": verbose,
                        "job_id": job_id,
                        "control": control,
                    }
                )
            )
        )
        response = deepcopy(self._responses[method])
        if not isinstance(response, dict):
            return response

        returned_request_id = response.pop("request_id", f"fake-request-{len(self.calls)}")
        if verbose or is_daemon_control_plane_error(response):
            response["debug"] = {
                **(response.get("debug") or {}),
                "request_id": returned_request_id,
            }
        return response


class FakeDaemonServer:
    def __init__(self, responses: dict[str, dict[str, Any]]):
        self._responses = deepcopy(responses)
        self.requests: list[dict[str, Any]] = []

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(_RecordedDaemonCall(deepcopy(payload)))
        return deepcopy(self._responses[payload["method"]])


def build_success_response(*, request_id: str, data: dict[str, Any], screenshot: str | None = None) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ok": True,
        "data": deepcopy(data),
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def write_installed_manifest(daemon_home: Path, *, runtime_state: str) -> Path:
    token_file = Path(daemon_home) / "daemon-token.txt"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("token-0", encoding="utf-8")

    manifest = TrailDaemonManifest(
        install=InstallRecord(
            bootstrap_type="scheduled_task",
            bootstrap_id="traild-user",
            daemon_entrypoint="trail.daemon.server:main",
            manifest_version=1,
            protocol_version=PROTOCOL_VERSION,
            token_file=str(token_file),
            log_dir=str(Path(daemon_home) / "logs"),
            workspace_strategy="per-request",
        ),
        runtime=RuntimeRecord(
            instance_id=None,
            pid=None,
            state=runtime_state,
            endpoint=None,
            token_generation=None,
            updated_at=None,
            last_transition_at=None,
            last_start_error=None,
        ),
    )
    path = manifest_path_for_user(daemon_home)
    save_manifest(path, manifest)
    return path


def write_ready_manifest(daemon_home: Path, *, endpoint: str, token_value: str) -> Path:
    path = write_installed_manifest(daemon_home, runtime_state="ready")
    manifest = load_manifest(path)
    Path(manifest.install.token_file).write_text(token_value, encoding="utf-8")
    manifest.runtime = RuntimeRecord(
        instance_id="inst-1",
        pid=1234,
        state="ready",
        endpoint=endpoint,
        token_generation=1,
        updated_at="2026-04-15T00:00:00+00:00",
        last_transition_at="2026-04-15T00:00:00+00:00",
        last_start_error=None,
    )
    save_manifest(path, manifest)
    return path


def start_fake_daemon_server(responses: dict[str, dict[str, Any]]) -> FakeDaemonServer:
    return FakeDaemonServer(responses)


def fake_round_trip_transport(server: FakeDaemonServer):
    def transport(request: DaemonRequest, token: str, endpoint: str) -> dict[str, Any]:
        return server.handle(
            {
                "request_id": request.request_id,
                "protocol_version": request.protocol_version,
                "call_id": request.call_id,
                "job_id": request.job_id,
                "workspace_root": request.workspace_root,
                "session_id": request.session_id,
                "verbose": request.verbose,
                "method": request.method,
                "payload": request.payload,
                "control": request.control.to_dict(),
                "token": token,
                "endpoint": endpoint,
            }
        )

    return transport
