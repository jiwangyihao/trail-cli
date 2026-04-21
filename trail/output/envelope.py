from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


def build_image_guidance(screenshot: Path | str | None) -> dict[str, bool] | None:
    if screenshot is None:
        return None
    return {"read_image_first": True}


def _attach_image_guidance(payload: dict[str, Any], *, screenshot: Path | str | None) -> dict[str, Any]:
    guidance = build_image_guidance(screenshot)
    if guidance is None:
        return payload
    return {**payload, "image_guidance": guidance}


def command_success(
    *,
    data: dict[str, Any],
    screenshot: Path | None,
    timing: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attach_image_guidance(
        {
            "ok": True,
            "data": deepcopy(data),
            "screenshot": None if screenshot is None else str(screenshot),
            "timing": deepcopy(timing or {}),
            "warnings": deepcopy(warnings or []),
            "references": deepcopy(references or []),
            "debug": deepcopy(debug),
            "error": None,
        },
        screenshot=screenshot,
    )


def command_failure(
    *,
    code: str,
    message: str,
    screenshot: Path | None,
    timing: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attach_image_guidance(
        {
            "ok": False,
            "data": {},
            "screenshot": None if screenshot is None else str(screenshot),
            "timing": deepcopy(timing or {}),
            "warnings": deepcopy(warnings or []),
            "references": deepcopy(references or []),
            "debug": deepcopy(debug),
            "error": {"code": code, "message": message},
        },
        screenshot=screenshot,
    )
