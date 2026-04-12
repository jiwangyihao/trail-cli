from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, build_default_session_store, print_json, run_session_command


runtime_factory = build_default_runtime
session_store_factory = build_default_session_store
state_app = typer.Typer(no_args_is_help=True)


@state_app.command("dump")
def state_dump(session: str = typer.Option(..., "--session")) -> None:
    result = run_session_command(
        store=session_store_factory(),
        session_id=session,
        command_name="state.dump",
        action=lambda loaded: loaded.to_dict(),
        runtime_factory=runtime_factory,
    )
    print_json(result)
