from __future__ import annotations

import json
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from trail.daemon.manifest import load_manifest, manifest_path_for_user
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.output.envelope import command_failure


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
    ):
        self.workspace_root = Path(workspace_root)
        self.daemon_home = Path(daemon_home)
        self.transport = transport

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

        manifest = load_manifest(manifest_path)
        token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
        request = DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(self.workspace_root),
            session_id=session_id,
            verbose=verbose,
            method=method,
            payload=payload,
        )
        response = deepcopy(
            self.transport(request, token, endpoint=str(manifest.runtime.endpoint))
        )
        returned_request_id = response.pop("request_id", request_id)
        if verbose:
            response["debug"] = {
                **(response.get("debug") or {}),
                "request_id": returned_request_id,
            }
        return response
