from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from typing import Annotated
from pathlib import Path
import os
import signal
import socket

import typer

from trail.commands.helpers import call_daemon, print_json
from trail.daemon.bootstrap import install_bootstrap, resolve_daemon_home, start_bootstrap
from trail.daemon.client import daemon_transport_failure, format_exception_detail
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.output.envelope import command_success


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def terminate_daemon_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def runtime_endpoint_is_traild(
    *,
    endpoint: str,
    token: str,
    protocol_version: int,
    timeout_seconds: float = 0.2,
) -> bool:
    try:
        host, port_text = endpoint.split(":", 1)
        with socket.create_connection((host, int(port_text)), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            body = {
                "request_id": "daemon-control-ping",
                "protocol_version": protocol_version,
                "workspace_root": str(Path.cwd()),
                "session_id": None,
                "verbose": False,
                "method": "daemon.ping",
                "payload": {},
                "token": token,
            }
            sock.sendall(json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\n")
            with sock.makefile("r", encoding="utf-8") as reader:
                response = json.loads(reader.readline())
    except (OSError, ValueError, json.JSONDecodeError):
        return False

    if not isinstance(response, dict):
        return False

    return response.get("ok") is True and response.get("data") == {"alive": True}


def runtime_manifest_is_live(manifest) -> bool:
    runtime = manifest.runtime
    if runtime.state not in {"ready", "degraded"} or not runtime.endpoint or not runtime.pid:
        return False

    try:
        token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not token:
        return False

    return runtime_endpoint_is_traild(
        endpoint=runtime.endpoint,
        token=token,
        protocol_version=manifest.install.protocol_version,
    )


def clear_runtime_manifest_state(*, manifest_path: Path, manifest) -> None:
    manifest.runtime.state = "stopped"
    manifest.runtime.endpoint = None
    manifest.runtime.pid = None
    manifest.runtime.updated_at = _utc_now()
    manifest.runtime.last_transition_at = manifest.runtime.updated_at
    manifest.runtime.last_start_error = None
    token_path = Path(manifest.install.token_file)
    if token_path.exists():
        token_path.write_text("", encoding="utf-8")
    save_manifest(manifest_path, manifest)


def _load_manifest_for_command(*, daemon_home: Path, request_id: str):
    manifest_path = manifest_path_for_user(daemon_home)
    if not manifest_path.exists():
        return None, daemon_transport_failure(
            request_id=request_id,
            code="DAEMON_BOOTSTRAP_REQUIRED",
            message="daemon bootstrap not installed",
        )

    try:
        return load_manifest(manifest_path), None
    except Exception as error:
        return None, daemon_transport_failure(
            request_id=request_id,
            code="DAEMON_MANIFEST_INVALID",
            message="daemon manifest invalid",
            debug={"detail": format_exception_detail(error)},
        )


daemon_app = typer.Typer(no_args_is_help=True)


@daemon_app.command("install")
def daemon_install() -> None:
    path = install_bootstrap(resolve_daemon_home())
    print_json(command_success(data={"manifest_path": str(path)}, screenshot=None))


@daemon_app.command("start")
def daemon_start() -> None:
    daemon_home = resolve_daemon_home()
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-start")
    if failure is not None:
        print_json(failure)
        return

    if runtime_manifest_is_live(manifest):
        print_json(command_success(data={"started": False, "already_running": True}, screenshot=None))
        return

    if not start_bootstrap(daemon_home):
        print_json(
            daemon_transport_failure(
                request_id="local-start",
                code="DAEMON_START_FAILED",
                message="daemon start failed",
            )
        )
        return

    print_json(command_success(data={"started": True}, screenshot=None))


@daemon_app.command("status")
def daemon_status() -> None:
    daemon_home = resolve_daemon_home()
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-status")
    if failure is not None:
        print_json(failure)
        return

    print_json(
        command_success(
            data={
                "install": asdict(manifest.install),
                "runtime": asdict(manifest.runtime),
            },
            screenshot=None,
        )
    )


@daemon_app.command("stop")
def daemon_stop() -> None:
    daemon_home = resolve_daemon_home()
    manifest_path = manifest_path_for_user(daemon_home)
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-stop")
    if failure is not None:
        print_json(failure)
        return

    runtime_is_live = runtime_manifest_is_live(manifest)
    if not runtime_is_live and (manifest.runtime.endpoint or manifest.runtime.pid):
        clear_runtime_manifest_state(manifest_path=manifest_path, manifest=manifest)
        print_json(command_success(data={"stopped": True}, screenshot=None))
        return

    if manifest.runtime.pid is not None:
        try:
            terminate_daemon_process(manifest.runtime.pid)
        except OSError as error:
            print_json(
                daemon_transport_failure(
                    request_id="local-stop",
                    code="DAEMON_STOP_FAILED",
                    message="daemon stop failed",
                    debug={
                        "detail": format_exception_detail(error),
                        "pid": manifest.runtime.pid,
                    },
                )
            )
            return

    clear_runtime_manifest_state(manifest_path=manifest_path, manifest=manifest)
    print_json(command_success(data={"stopped": True}, screenshot=None))


@daemon_app.command("logs")
def daemon_logs() -> None:
    daemon_home = resolve_daemon_home()
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-logs")
    if failure is not None:
        print_json(failure)
        return

    print_json(command_success(data={"log_dir": manifest.install.log_dir}, screenshot=None))


@daemon_app.command("request-status")
def daemon_request_status(request_id: Annotated[str, typer.Option("--request-id")]) -> None:
    print_json(call_daemon("daemon.request_status", {"request_id": request_id}))


@daemon_app.command("reconcile-session")
def daemon_reconcile_session(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(call_daemon("daemon.reconcile_session", {"session_id": session}))
