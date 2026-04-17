from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


image_app = typer.Typer(no_args_is_help=True)


@image_app.command("locate")
def image_locate(template: str) -> None:
    print_output("image.locate", call_daemon("image.locate", {"template": template}))


@image_app.command("wait")
def image_wait(template: str, timeout: int = typer.Option(10, "--timeout")) -> None:
    print_output("image.wait", call_daemon("image.wait", {"template": template, "timeout": timeout}))
