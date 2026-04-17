from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


window_app = typer.Typer(no_args_is_help=True)


class LaunchChannel(StrEnum):
    OFFICIAL = "official"
    BILIBILI = "bilibili"
    GLOBAL = "global"


@window_app.command("attach")
def window_attach(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_output("window.attach", call_daemon("window.attach", {"window_title": window_title}))


@window_app.command("launch")
def window_launch(
    game_path: Path = typer.Option(..., "--game-path"),
    channel: LaunchChannel = typer.Option(LaunchChannel.OFFICIAL, "--channel"),
    arg: list[str] | None = typer.Option(None, "--arg"),
    use_cmd: bool = typer.Option(False, "--use-cmd"),
) -> None:
    print_output(
        "window.launch",
        call_daemon(
            "window.launch",
            {
                "game_path": str(game_path),
                "channel": channel.value,
                "launch_args": list(arg or []),
                "use_cmd": use_cmd,
            },
        )
    )
