from __future__ import annotations

from typing import Annotated

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


input_app = typer.Typer(no_args_is_help=True)


@input_app.command("click")
def input_click(x: int, y: int) -> None:
    print_output("input.click", call_daemon("input.click", {"x": x, "y": y}))


@input_app.command("drag")
def input_drag(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    duration: Annotated[float | None, typer.Option("--duration")] = None,
) -> None:
    payload: dict[str, int | float] = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
    if duration is not None:
        payload["duration"] = duration
    print_output("input.drag", call_daemon("input.drag", payload))


@input_app.command("key")
def input_key(key: str, presses: int = typer.Option(1, "--presses")) -> None:
    print_output("input.key", call_daemon("input.key", {"key": key, "presses": presses}))
