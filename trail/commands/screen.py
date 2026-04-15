from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon, print_json


screen_app = typer.Typer(no_args_is_help=True)


@screen_app.command("shot")
def screen_shot() -> None:
    print_json(call_daemon("screen.shot", {}))
