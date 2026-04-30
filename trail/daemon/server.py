from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from typing import Any
from uuid import uuid4

from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_detail as _format_exception_detail
from trail.core.jsonable import format_exception_message as _format_exception_message
from trail.core.jsonable import to_jsonable
from trail.daemon.command_service import (
    CommandService,
    PersistedButResponseUnknown,
    SideEffectAppliedButStateNotPersisted,
)
from trail.daemon.cw_service import CwService
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import DaemonRequest
from trail.daemon.paths import resolve_daemon_home
from trail.daemon.runtime_service import RuntimeService
from trail.daemon.session_service import SessionServiceRegistry
from trail.output.envelope import command_failure


DAEMON_SERVER_POLL_INTERVAL = 0.05


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_response(*, request_id: str, code: str, message: str, debug: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = command_failure(
        code=code,
        message=message,
        screenshot=None,
        timing={},
        warnings=[],
        references=[],
        debug=debug,
    )
    payload["request_id"] = request_id
    return payload


def _serialize_response_line(response: dict[str, Any], *, request_id: str) -> bytes:
    try:
        payload = to_jsonable(response)
        return json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    except Exception as error:
        fallback = _error_response(
            request_id=request_id,
            code="DAEMON_UNAVAILABLE",
            message="daemon response serialization failed",
            debug={"detail": _format_exception_detail(error)},
        )
        return json.dumps(fallback, ensure_ascii=False).encode("utf-8") + b"\n"


class _DaemonTcpServer(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _RequestHandler(StreamRequestHandler):
    def handle(self) -> None:
        server: TrailDaemonServer = self.server.trail_daemon_server  # type: ignore[attr-defined]
        request_id = "unknown"

        try:
            payload = json.loads(self.rfile.readline().decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError(f"expected object payload, got {type(payload).__name__}")
            request_id = str(payload.get("request_id") or request_id)
            response = server.handle_payload(payload)
        except Exception as error:
            response = _error_response(
                request_id=request_id,
                code="DAEMON_UNAVAILABLE",
                message="daemon request failed",
                debug={"detail": _format_exception_detail(error)},
            )

        self.wfile.write(_serialize_response_line(response, request_id=request_id))


class TrailDaemonServer:
    def __init__(self, *, command_service: CommandService):
        self.command_service = command_service
        self._listener: _DaemonTcpServer | None = None

    @property
    def daemon_home(self) -> Path:
        return resolve_daemon_home()

    @property
    def manifest_path(self) -> Path:
        return manifest_path_for_user(self.daemon_home)

    def _ensure_manifest(self):
        return load_manifest(self.manifest_path)

    def _write_ready_manifest(self, listener: _DaemonTcpServer) -> None:
        manifest = self._ensure_manifest()
        token_file = Path(manifest.install.token_file)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(uuid4().hex, encoding="utf-8")

        host, port = listener.server_address
        manifest.runtime.instance_id = f"traild-{uuid4().hex}"
        manifest.runtime.pid = os.getpid()
        manifest.runtime.endpoint = f"{host}:{port}"
        manifest.runtime.state = "ready"
        manifest.runtime.token_generation = (manifest.runtime.token_generation or 0) + 1
        manifest.runtime.updated_at = _utc_now()
        manifest.runtime.last_transition_at = manifest.runtime.updated_at
        manifest.runtime.last_start_error = None
        save_manifest(self.manifest_path, manifest)

    def handle_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or "unknown")
        manifest = self._ensure_manifest()
        expected_token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()

        if payload.get("protocol_version") != manifest.install.protocol_version:
            return _error_response(
                request_id=request_id,
                code="DAEMON_VERSION_MISMATCH",
                message="protocol version mismatch",
            )
        if payload.get("token") != expected_token:
            return _error_response(
                request_id=request_id,
                code="DAEMON_AUTH_FAILED",
                message="daemon token mismatch",
            )

        try:
            request = DaemonRequest(
                request_id=request_id,
                protocol_version=int(payload["protocol_version"]),
                workspace_root=str(payload["workspace_root"]),
                session_id=payload.get("session_id"),
                verbose=bool(payload.get("verbose", False)),
                method=str(payload["method"]),
                payload=deepcopy(payload.get("payload") or {}),
            )
            response = self.command_service.handle(request)
        except TrailError as error:
            return _error_response(
                request_id=request_id,
                code=error.code,
                message=_format_exception_message(error),
            )
        except (SideEffectAppliedButStateNotPersisted, PersistedButResponseUnknown) as error:
            envelope = deepcopy(error.envelope)
            envelope.setdefault("request_id", request_id)
            return envelope
        except Exception as error:
            return _error_response(
                request_id=request_id,
                code="DAEMON_UNAVAILABLE",
                message="daemon request failed",
                debug={"detail": _format_exception_detail(error)},
            )

        if not isinstance(response, dict):
            return _error_response(
                request_id=request_id,
                code="DAEMON_UNAVAILABLE",
                message="daemon request failed",
                debug={"detail": f"invalid handler response type: {type(response).__name__}"},
            )
        response.setdefault("request_id", request_id)
        return response

    def serve_forever(self) -> None:
        with _DaemonTcpServer(("127.0.0.1", 0), _RequestHandler) as listener:
            listener.trail_daemon_server = self  # type: ignore[attr-defined]
            self._listener = listener
            self._write_ready_manifest(listener)
            listener.serve_forever(poll_interval=DAEMON_SERVER_POLL_INTERVAL)

    def shutdown(self) -> None:
        if self._listener is None:
            return
        self._listener.shutdown()
        self._listener.server_close()
        self._listener = None


def main() -> None:
    runtime_service = RuntimeService()
    session_service = SessionServiceRegistry()
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_service, cw_service=cw_service)
    TrailDaemonServer(command_service=command_service).serve_forever()
