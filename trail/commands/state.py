from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


state_app = typer.Typer(no_args_is_help=True)


@state_app.command("dump")
def state_dump(session: str = typer.Option(..., "--session")) -> None:
    print_output("state.dump", call_daemon("state.dump", {"session_id": session}, session_id=session))
