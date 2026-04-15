from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon, print_json


input_app = typer.Typer(no_args_is_help=True)


@input_app.command("click")
def input_click(x: int, y: int) -> None:
    print_json(call_daemon("input.click", {"x": x, "y": y}))


@input_app.command("drag")
def input_drag(from_x: int, from_y: int, to_x: int, to_y: int) -> None:
    print_json(call_daemon("input.drag", {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}))


@input_app.command("key")
def input_key(key: str, presses: int = typer.Option(1, "--presses")) -> None:
    print_json(call_daemon("input.key", {"key": key, "presses": presses}))
