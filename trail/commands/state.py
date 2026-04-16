from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon, print_json


state_app = typer.Typer(no_args_is_help=True)


@state_app.command("dump")
def state_dump(session: str = typer.Option(..., "--session")) -> None:
    print_json(call_daemon("state.dump", {"session_id": session}, session_id=session))
