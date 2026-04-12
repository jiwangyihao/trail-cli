from __future__ import annotations

import typer

from trail.commands.helpers import build_default_runtime, print_json, to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture


runtime_factory = build_default_runtime
ocr_app = typer.Typer(no_args_is_help=True)


@ocr_app.command("read")
def ocr_read() -> None:
    runtime = runtime_factory()

    def action() -> dict:
        result = runtime.ocr()
        if not result:
            raise TrailError("OCR_NO_RESULT", "OCR 无结果")
        return {"result": to_jsonable(result)}

    print_json(with_auto_capture(runtime, action))
