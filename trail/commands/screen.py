from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


screen_app = typer.Typer(no_args_is_help=True)


@screen_app.command("shot")
def screen_shot() -> None:
    print_output("screen.shot", call_daemon("screen.shot", {}))
