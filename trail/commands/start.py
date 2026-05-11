from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from trail.commands.daemon import ensure_bootstrap_installed, ensure_runtime_ready
from trail.commands.helpers import DEFAULT_WINDOW_TITLE, call_daemon
from trail.core.errors import TrailError
from trail.daemon.paths import resolve_daemon_home
from trail.output.envelope import command_failure
from trail.output.rendering import print_output


start_app = typer.Typer(
    no_args_is_help=False,
    help="自动完成 daemon、游戏、窗口、session 的启动收口，并发起单个 daemon-side start.run 请求。",
)


def _local_start_failure(code: str, message: str) -> dict[str, Any]:
    return command_failure(code=code, message=message, screenshot=None)


def _ensure_local_daemon_installed() -> Path:
    daemon_home = resolve_daemon_home()
    ensure_bootstrap_installed(daemon_home=daemon_home)
    return daemon_home


def _ensure_local_daemon_ready(daemon_home: Path) -> None:
    ensure_runtime_ready(daemon_home=daemon_home)


@start_app.callback(invoke_without_command=True)
def start_command(
    window_title: str = typer.Option(DEFAULT_WINDOW_TITLE, "--window-title", help="覆盖默认窗口标题。"),
    game_path: Path | None = typer.Option(None, "--game-path", help="透传给 daemon-side start.run 的显式游戏路径。"),
    channel: str = typer.Option("official", "--channel", help="透传给 daemon-side start.run 的启动 channel。"),
    request_id: str | None = typer.Option(None, "--request-id", help="查询既有 start.run daemon job。"),
) -> None:
    """自动收口 daemon ready、游戏窗口与 session。"""

    try:
        daemon_home = _ensure_local_daemon_installed()
        _ensure_local_daemon_ready(daemon_home)
    except TrailError as error:
        print_output("start.run", _local_start_failure(error.code, str(error)))
        return

    payload: dict[str, Any] = {
        "window_title": window_title,
        "channel": channel,
    }
    if game_path is not None:
        payload["game_path"] = str(game_path)

    print_output("start.run", call_daemon("start.run", payload, request_id=request_id))
