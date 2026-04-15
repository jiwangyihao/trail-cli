from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon, print_json


ocr_app = typer.Typer(no_args_is_help=True)


@ocr_app.command("read")
def ocr_read() -> None:
    print_json(call_daemon("ocr.read", {}))
