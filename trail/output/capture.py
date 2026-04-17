from __future__ import annotations

from time import perf_counter
from typing import Callable

from trail.core.errors import TrailError
from trail.output.envelope import command_failure, command_success

_CAPTURE_OPTIONS = {"verbose": False}


def _resolve_runtime(runtime):
    if callable(runtime) and not hasattr(runtime, "capture_after_action"):
        return runtime()
    return runtime


def _capture_screenshot(runtime, *, optional: bool):
    runtime = _resolve_runtime(runtime)
    if runtime is None:
        return None
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


def resolve_capture_verbose(verbose: bool | None = None) -> bool:
    return _resolve_verbose(verbose)


def _begin_capture_scope(runtime) -> None:
    begin_capture_scope = getattr(runtime, "begin_capture_scope", None)
    if callable(begin_capture_scope):
        begin_capture_scope()


def _end_capture_scope(runtime) -> None:
    end_capture_scope = getattr(runtime, "end_capture_scope", None)
    if callable(end_capture_scope):
        end_capture_scope()


def _collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    runtime = _resolve_runtime(runtime)
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
    resolved_runtime = _resolve_runtime(runtime)
    _begin_capture_scope(resolved_runtime)
    try:
        try:
            data = fn()
        except TrailError as exc:
            screenshot = _capture_optional_screenshot(resolved_runtime)
            metadata = _collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
            return command_failure(
                code=exc.code,
                message=str(exc),
                screenshot=screenshot,
                timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
                **metadata,
            )
        except Exception as exc:
            screenshot = _capture_optional_screenshot(resolved_runtime)
            metadata = _collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
            return command_failure(
                code="UNEXPECTED_ERROR",
                message=_format_unexpected_exception(exc),
                screenshot=screenshot,
                timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
                **metadata,
            )

        screenshot = _capture_screenshot(resolved_runtime, optional=False)
        metadata = _collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_success(
            data=data,
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )
    finally:
        _end_capture_scope(resolved_runtime)
