from __future__ import annotations

import typer

from trail.commands.helpers import build_default_session_store, build_default_runtime, print_json, to_jsonable
from trail.output.capture import with_auto_capture
from trail.runtime.window import attach_window


session_store_factory = build_default_session_store
runtime_factory = build_default_runtime
session_app = typer.Typer(no_args_is_help=True)


@session_app.command("create")
def session_create(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    store = session_store_factory()
    runtime = runtime_factory(window_title=window_title)

    def action() -> dict:
        binding = attach_window(window_title)
        session = store.create(window_binding=to_jsonable(binding))
        return session.to_dict()

    print_json(with_auto_capture(runtime, action))
