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
    game_path: Path | None = typer.Option(
        None,
        "--game-path",
        help=(
            "省略时：历史成功路径 -> 默认路径 -> 直接问用户；"
            "默认路径仅覆盖 official（C:\\Program Files\\miHoYo Launcher\\games\\Star Rail Game\\StarRail.exe）"
        ),
    ),
    channel: LaunchChannel = typer.Option(LaunchChannel.OFFICIAL, "--channel"),
    arg: list[str] | None = typer.Option(None, "--arg"),
    use_cmd: bool = typer.Option(False, "--use-cmd"),
) -> None:
    """启动《崩坏：星穹铁道》。

    未显式提供时按历史成功路径 -> 默认路径 -> 直接问用户。
    显式提供的 --game-path 优先级最高。
    失败时不会回退。
    显式路径不存在返回 GAME_PATH_NOT_FOUND。
    显式路径存在但启动失败返回 GAME_LAUNCH_FAILED。
    启动成功但历史回写失败时返回 success，并追加 warn code=GAME_PATH_PERSIST_FAILED。
    默认路径仅覆盖 official。
    bilibili/global 无历史路径时通常仍需显式提供 --game-path。
    """

    payload = {
        "channel": channel.value,
        "launch_args": list(arg or []),
        "use_cmd": use_cmd,
    }
    if game_path is not None:
        payload["game_path"] = str(game_path)

    print_output(
        "window.launch",
        call_daemon("window.launch", payload),
    )
