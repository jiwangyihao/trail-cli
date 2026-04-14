from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import typer

from trail.commands.helpers import build_default_runtime, print_json, to_jsonable
from trail.output.capture import with_auto_capture
from trail.runtime.window import attach_window, launch_game


runtime_factory = build_default_runtime
window_app = typer.Typer(no_args_is_help=True)


class LaunchChannel(StrEnum):
    OFFICIAL = "official"
    BILIBILI = "bilibili"
    GLOBAL = "global"


@window_app.command("attach")
def window_attach(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    runtime = runtime_factory(window_title=window_title)
    print_json(with_auto_capture(runtime, lambda: to_jsonable(attach_window(window_title))))


@window_app.command("launch")
def window_launch(
    game_path: Path = typer.Option(..., "--game-path"),
    channel: LaunchChannel = typer.Option(LaunchChannel.OFFICIAL, "--channel"),
    arg: list[str] | None = typer.Option(None, "--arg"),
    use_cmd: bool = typer.Option(False, "--use-cmd"),
) -> None:
    print_json(
        with_auto_capture(
            None,
            lambda: launch_game(
                game_path=game_path,
                channel=channel.value,
                launch_args=list(arg or []),
                use_cmd=use_cmd,
            ),
        )
    )
