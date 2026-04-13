from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, print_json
from trail.output.capture import with_auto_capture


runtime_factory = build_default_runtime
input_app = typer.Typer(no_args_is_help=True)


@input_app.command("click")
def input_click(x: int, y: int) -> None:
    runtime = runtime_factory()

    def action() -> dict:
        runtime.click_point(x, y)
        return {"clicked": [x, y]}

    print_json(with_auto_capture(runtime, action))


@input_app.command("drag")
def input_drag(from_x: int, from_y: int, to_x: int, to_y: int) -> None:
    runtime = runtime_factory()

    def action() -> dict:
        runtime.drag_to(from_x, from_y, to_x, to_y)
        return {"dragged": [from_x, from_y, to_x, to_y]}

    print_json(with_auto_capture(runtime, action))


@input_app.command("key")
def input_key(key: str, presses: int = typer.Option(1, "--presses")) -> None:
    runtime = runtime_factory()

    def action() -> dict:
        runtime.press_key(key, presses=presses)
        return {"key": key, "presses": presses}

    print_json(with_auto_capture(runtime, action))
