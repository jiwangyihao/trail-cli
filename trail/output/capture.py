from __future__ import annotations

from time import perf_counter
from typing import Callable

from trail.core.errors import TrailError
from trail.output.envelope import command_failure, command_success


def _capture_screenshot(runtime, *, optional: bool):
    try:
        return runtime.capture_after_action(optional=optional)
    except Exception:
        return None


def _capture_optional_screenshot(runtime):
    return _capture_screenshot(runtime, optional=True)


def _format_unexpected_exception(exc: Exception) -> str:
    message = str(exc)
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"


def with_auto_capture(runtime, fn: Callable[[], dict]):
    started = perf_counter()
    try:
        data = fn()
    except TrailError as exc:
        screenshot = _capture_optional_screenshot(runtime)
        return command_failure(
            code=exc.code,
            message=str(exc),
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
        )
    except Exception as exc:
        screenshot = _capture_optional_screenshot(runtime)
        return command_failure(
            code="UNEXPECTED_ERROR",
            message=_format_unexpected_exception(exc),
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
        )

    screenshot = _capture_screenshot(runtime, optional=False)
    return command_success(
        data=data,
        screenshot=screenshot,
        timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
    )
