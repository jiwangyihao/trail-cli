from __future__ import annotations

from pathlib import Path
from typing import Any

from trail.core.jsonable import to_jsonable


def build_image_guidance(screenshot: Path | str | None) -> dict[str, int] | None:
    if screenshot is None:
        return None
    return {"read_image_first": 1}


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
    envelope = {
        "ok": True,
        "data": to_jsonable(data),
        "screenshot": None if screenshot is None else str(screenshot),
        "timing": to_jsonable(timing or {}),
        "warnings": to_jsonable(warnings or []),
        "references": to_jsonable(references or []),
        "debug": to_jsonable(debug),
        "error": None,
    }
    return _attach_image_guidance(envelope, screenshot=screenshot)


def command_failure(
    *,
    code: str,
    message: str,
    screenshot: Path | None = None,
    data: dict[str, Any] | None = None,
    timing: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = {
        "ok": False,
        "data": to_jsonable(data or {}),
        "screenshot": None if screenshot is None else str(screenshot),
        "timing": to_jsonable(timing or {}),
        "warnings": to_jsonable(warnings or []),
        "references": to_jsonable(references or []),
        "debug": to_jsonable(debug),
        "error": {"code": code, "message": message},
    }
    return _attach_image_guidance(envelope, screenshot=screenshot)
