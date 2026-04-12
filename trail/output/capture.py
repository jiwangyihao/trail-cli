from __future__ import annotations

from time import perf_counter
from typing import Callable

from trail.core.errors import TrailError
from trail.output.envelope import command_failure, command_success


def _capture_optional_screenshot(runtime):
    try:
        return runtime.capture_after_action(optional=True)
    except Exception:
        return None


def with_auto_capture(runtime, fn: Callable[[], dict]):
    started = perf_counter()
    try:
        data = fn()
        screenshot = runtime.capture_after_action()
        return command_success(
            data=data,
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
        )
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
            message=str(exc),
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
        )
