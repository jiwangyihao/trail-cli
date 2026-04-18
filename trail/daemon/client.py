from __future__ import annotations

import json
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from trail.daemon.bootstrap import start_bootstrap, wait_until_runtime_ready
from trail.daemon.manifest import load_manifest, manifest_path_for_user
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.output.envelope import command_failure

READY_RUNTIME_STATES = {"ready", "degraded"}
RETRIABLE_TRANSPORT_ERRORS = (
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
SOCKET_RESPONSE_TIMEOUT_SECONDS = 120.0


class DaemonTransport(Protocol):
    def __call__(self, request: DaemonRequest, token: str, *, endpoint: str) -> dict[str, Any]: ...


def daemon_transport_failure(
    *,
    request_id: str,
    code: str,
    message: str,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged_debug = {"request_id": request_id}
    if debug:
        merged_debug.update(debug)
    return command_failure(
        code=code,
        message=message,
        screenshot=None,
        timing={},
        warnings=[],
        references=[],
        debug=merged_debug,
    )


def daemon_unavailable_failure(*, request_id: str, detail: str) -> dict[str, Any]:
    return daemon_transport_failure(
        request_id=request_id,
        code="DAEMON_UNAVAILABLE",
        message="daemon unavailable",
        debug={"detail": detail},
    )


def format_exception_detail(error: Exception) -> str:
    message = str(error)
    if not message:
        return type(error).__name__
    return f"{type(error).__name__}: {message}"


def format_invalid_response_detail(response: Any) -> str:
    return f"invalid daemon response type: expected object, got {type(response).__name__}"


def is_daemon_control_plane_error(response: dict[str, Any]) -> bool:
    if response.get("ok") is not False:
        return False
    error = response.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    return isinstance(code, str) and code.startswith("DAEMON_")


def send_daemon_request(
    request: DaemonRequest,
    token: str,
    *,
    endpoint: str,
    server: Any = None,
) -> dict[str, Any]:
    body = {
        "request_id": request.request_id,
        "protocol_version": request.protocol_version,
        "workspace_root": request.workspace_root,
        "session_id": request.session_id,
        "verbose": request.verbose,
        "method": request.method,
        "payload": request.payload,
        "token": token,
    }

    if server is not None:
        return server.handle({**body, "endpoint": endpoint})

    host, port_text = endpoint.split(":", 1)
    with socket.create_connection((host, int(port_text)), timeout=5) as sock:
        sock.settimeout(SOCKET_RESPONSE_TIMEOUT_SECONDS)
        sock.sendall(json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\n")
        with sock.makefile("r", encoding="utf-8") as reader:
            return json.loads(reader.readline())


class TrailDaemonClient:
    def __init__(
        self,
        *,
        workspace_root: Path,
        daemon_home: Path,
        transport: DaemonTransport,
        starter=start_bootstrap,
    ):
        self.workspace_root = Path(workspace_root)
        self.daemon_home = Path(daemon_home)
        self.transport = transport
        self.starter = starter

    def _start_runtime(self, *, manifest_path: Path, request_id: str) -> tuple[dict[str, Any] | None, Any]:
        try:
            started = self.starter(self.daemon_home)
        except Exception as error:
            return daemon_transport_failure(
                request_id=request_id,
                code="DAEMON_START_FAILED",
                message="daemon start failed",
                debug={"detail": format_exception_detail(error)},
            ), None
        if not started:
            return daemon_transport_failure(
                request_id=request_id,
                code="DAEMON_START_FAILED",
                message="daemon start failed",
            ), None

        try:
            wait_until_runtime_ready(self.daemon_home)
            return None, load_manifest(manifest_path)
        except Exception as error:
            return daemon_transport_failure(
                request_id=request_id,
                code="DAEMON_START_FAILED",
                message="daemon start failed",
                debug={"detail": format_exception_detail(error)},
            ), None

    def call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        request_id = uuid4().hex
        manifest_path = manifest_path_for_user(self.daemon_home)
        if not manifest_path.exists():
            return daemon_transport_failure(
                request_id=request_id,
                code="DAEMON_BOOTSTRAP_REQUIRED",
                message="daemon bootstrap not installed",
            )

        try:
            manifest = load_manifest(manifest_path)
        except Exception as error:
            return daemon_unavailable_failure(
                request_id=request_id,
                detail=format_exception_detail(error),
            )

        bootstrap_attempted = False
        if manifest.runtime.state not in READY_RUNTIME_STATES:
            failure, manifest = self._start_runtime(manifest_path=manifest_path, request_id=request_id)
            if failure is not None:
                return failure
            bootstrap_attempted = True

        try:
            token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
        except Exception as error:
            return daemon_unavailable_failure(
                request_id=request_id,
                detail=format_exception_detail(error),
            )

        request = DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(self.workspace_root),
            session_id=session_id,
            verbose=verbose,
            method=method,
            payload=payload,
        )

        while True:
            endpoint = manifest.runtime.endpoint
            if not endpoint:
                if bootstrap_attempted:
                    return daemon_unavailable_failure(
                        request_id=request_id,
                        detail="runtime endpoint missing",
                    )
                failure, manifest = self._start_runtime(manifest_path=manifest_path, request_id=request_id)
                if failure is not None:
                    return failure
                bootstrap_attempted = True
                try:
                    token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
                except Exception as error:
                    return daemon_unavailable_failure(
                        request_id=request_id,
                        detail=format_exception_detail(error),
                    )
                continue

            try:
                response = deepcopy(self.transport(request, token, endpoint=endpoint))
            except Exception as error:
                if not bootstrap_attempted and isinstance(error, RETRIABLE_TRANSPORT_ERRORS):
                    failure, manifest = self._start_runtime(manifest_path=manifest_path, request_id=request_id)
                    if failure is not None:
                        return failure
                    bootstrap_attempted = True
                    try:
                        token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
                    except Exception as nested_error:
                        return daemon_unavailable_failure(
                            request_id=request_id,
                            detail=format_exception_detail(nested_error),
                        )
                    continue

                return daemon_unavailable_failure(
                    request_id=request_id,
                    detail=format_exception_detail(error),
                )
            break

        if not isinstance(response, dict):
            return daemon_unavailable_failure(
                request_id=request_id,
                detail=format_invalid_response_detail(response),
            )

        returned_request_id = response.pop("request_id", request_id)
        if verbose or response.get("ok") is False or is_daemon_control_plane_error(response):
            response["debug"] = {
                **(response.get("debug") or {}),
                "request_id": returned_request_id,
            }
        return response
