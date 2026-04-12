from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


def command_success(*, data: dict[str, Any], screenshot: Path | None, timing: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "data": deepcopy(data),
        "screenshot": None if screenshot is None else str(screenshot),
        "timing": deepcopy(timing or {}),
        "error": None,
    }


def command_failure(
    *,
    code: str,
    message: str,
    screenshot: Path | None,
    timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": {},
        "screenshot": None if screenshot is None else str(screenshot),
        "timing": deepcopy(timing or {}),
        "error": {"code": code, "message": message},
    }
