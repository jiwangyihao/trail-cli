from __future__ import annotations

from time import perf_counter
from typing import Callable

from trail.core.errors import TrailError
from trail.output.envelope import command_failure, command_success

_CAPTURE_OPTIONS = {"verbose": False}


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


def set_capture_options(*, verbose: bool) -> None:
    _CAPTURE_OPTIONS["verbose"] = bool(verbose)


def _resolve_verbose(verbose: bool | None) -> bool:
    if verbose is None:
        return bool(_CAPTURE_OPTIONS.get("verbose", False))
    return bool(verbose)


def _collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    warnings: list[dict] = []
    references: list[dict] = []
    debug = None

    if runtime is not None:
        collect_warnings = getattr(runtime, "collect_warnings", None)
        if callable(collect_warnings):
            warnings = collect_warnings() or []

        match_references = getattr(runtime, "match_references", None)
        if screenshot is not None and callable(match_references):
            references = match_references(screenshot) or []

        consume_debug_trace = getattr(runtime, "consume_debug_trace", None)
        if verbose and callable(consume_debug_trace):
            trace = consume_debug_trace() or []
            debug = {"trace": trace}

    return {
        "warnings": warnings,
        "references": references,
        "debug": debug,
    }


def with_auto_capture(runtime, fn: Callable[[], dict], *, verbose: bool | None = None):
    effective_verbose = _resolve_verbose(verbose)
    started = perf_counter()
    try:
        data = fn()
    except TrailError as exc:
        screenshot = _capture_optional_screenshot(runtime)
        metadata = _collect_capture_metadata(runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_failure(
            code=exc.code,
            message=str(exc),
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )
    except Exception as exc:
        screenshot = _capture_optional_screenshot(runtime)
        metadata = _collect_capture_metadata(runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_failure(
            code="UNEXPECTED_ERROR",
            message=_format_unexpected_exception(exc),
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )

    screenshot = _capture_screenshot(runtime, optional=False)
    metadata = _collect_capture_metadata(runtime, screenshot=screenshot, verbose=effective_verbose)
    return command_success(
        data=data,
        screenshot=screenshot,
        timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
        **metadata,
    )
