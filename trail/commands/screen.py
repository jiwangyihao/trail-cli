from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, print_json
from trail.output.capture import with_auto_capture


runtime = build_default_runtime()
screen_app = typer.Typer(no_args_is_help=True)


@screen_app.command("shot")
def screen_shot() -> None:
    print_json(with_auto_capture(runtime, lambda: {"captured": True}))
