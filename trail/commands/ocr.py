from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


ocr_app = typer.Typer(no_args_is_help=True)


@ocr_app.command("read")
def ocr_read() -> None:
    print_output("ocr.read", call_daemon("ocr.read", {}))
