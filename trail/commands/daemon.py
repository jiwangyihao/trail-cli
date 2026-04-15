from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import os
import signal

import typer

from trail.commands.helpers import print_json
from trail.daemon.bootstrap import install_bootstrap, resolve_daemon_home, start_bootstrap
from trail.daemon.client import daemon_transport_failure, format_exception_detail
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.output.envelope import command_success


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def terminate_daemon_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


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
    _manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-start")
    if failure is not None:
        print_json(failure)
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
    print_json(command_success(data={"stopped": True}, screenshot=None))


@daemon_app.command("logs")
def daemon_logs() -> None:
    daemon_home = resolve_daemon_home()
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-logs")
    if failure is not None:
        print_json(failure)
        return

    print_json(command_success(data={"log_dir": manifest.install.log_dir}, screenshot=None))
