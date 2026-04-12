from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, print_json, to_jsonable
from trail.output.capture import with_auto_capture
from trail.runtime.window import attach_window


runtime = build_default_runtime()
window_app = typer.Typer(no_args_is_help=True)


@window_app.command("attach")
def window_attach(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_json(with_auto_capture(runtime, lambda: to_jsonable(attach_window(window_title))))
