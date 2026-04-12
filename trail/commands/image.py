from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, print_json, to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture


runtime = build_default_runtime()
image_app = typer.Typer(no_args_is_help=True)


@image_app.command("locate")
def image_locate(template: str) -> None:
    def action() -> dict:
        box = runtime.locate(template)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    print_json(with_auto_capture(runtime, action))


@image_app.command("wait")
def image_wait(template: str, timeout: int = typer.Option(10, "--timeout")) -> None:
    def action() -> dict:
        box = runtime.wait_img(template, timeout=timeout)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    print_json(with_auto_capture(runtime, action))
