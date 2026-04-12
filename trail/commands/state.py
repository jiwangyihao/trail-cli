from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, build_default_session_store, print_json, run_session_command


runtime = build_default_runtime()
session_store = build_default_session_store()
state_app = typer.Typer(no_args_is_help=True)


@state_app.command("dump")
def state_dump(session: str = typer.Option(..., "--session")) -> None:
    result = run_session_command(
        store=session_store,
        session_id=session,
        runtime=runtime,
        command_name="state.dump",
        action=lambda loaded: loaded.to_dict(),
    )
    print_json(result)
