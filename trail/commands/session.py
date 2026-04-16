from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon, print_json


session_app = typer.Typer(no_args_is_help=True)


@session_app.command("create")
def session_create(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_json(call_daemon("session.create", {"window_title": window_title}))
