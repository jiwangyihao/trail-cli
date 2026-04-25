from __future__ import annotations

from time import perf_counter
from typing import Callable

from trail.core.errors import TrailError
from trail.core.jsonable import format_exception_detail, format_exception_message, to_jsonable
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
    return format_exception_detail(exc)


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


def _collect_capture_warnings(runtime) -> list[dict]:
    warnings: list[dict] = []
    if runtime is None:
        return warnings

    collect_warnings = getattr(runtime, "collect_warnings", None)
    if callable(collect_warnings):
        try:
            warnings = collect_warnings() or []
        except Exception:
            return []
    return to_jsonable(warnings) if isinstance(warnings, list) else []


def _collect_capture_references(runtime, *, screenshot) -> list[dict]:
    references: list[dict] = []
    if runtime is None:
        return references

    match_references = getattr(runtime, "match_references", None)
    if screenshot is not None and callable(match_references):
        try:
            references = match_references(screenshot) or []
        except Exception:
            return []
    return to_jsonable(references) if isinstance(references, list) else []


def _collect_capture_debug(runtime, *, verbose: bool):
    if runtime is None or not verbose:
        return None

    consume_debug_trace = getattr(runtime, "consume_debug_trace", None)
    consume_debug_context = getattr(runtime, "consume_debug_context", None)
    trace = []
    if callable(consume_debug_trace):
        trace = to_jsonable(consume_debug_trace() or [])
    debug_context = {}
    if callable(consume_debug_context):
        debug_context = to_jsonable(consume_debug_context() or {})
    filtered_debug_context = {}
    if isinstance(debug_context, dict):
        filtered_debug_context = {
            key: value
            for key, value in debug_context.items()
            if key not in {"trace", "request_id", "detail"}
        }
    if not trace and not filtered_debug_context:
        return None

    debug = {}
    if trace:
        debug["trace"] = trace
    debug.update(filtered_debug_context)
    return debug


def _collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    runtime = _resolve_runtime(runtime)
    return {
        "warnings": _collect_capture_warnings(runtime),
        "references": _collect_capture_references(runtime, screenshot=screenshot),
        "debug": _collect_capture_debug(runtime, verbose=verbose),
    }


def _safe_collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    runtime = _resolve_runtime(runtime)
    warnings = _collect_capture_warnings(runtime)
    references = _collect_capture_references(runtime, screenshot=screenshot)
    debug = None

    try:
        debug = _collect_capture_debug(runtime, verbose=verbose)
    except Exception:
        debug = None

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
            metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
            return command_failure(
                code=exc.code,
                message=format_exception_message(exc),
                screenshot=screenshot,
                timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
                **metadata,
            )
        except Exception as exc:
            screenshot = _capture_optional_screenshot(resolved_runtime)
            metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
            return command_failure(
                code="UNEXPECTED_ERROR",
                message=_format_unexpected_exception(exc),
                screenshot=screenshot,
                timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
                **metadata,
            )

        screenshot = _capture_screenshot(resolved_runtime, optional=False)
        metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_success(
            data=data,
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )
    finally:
        _end_capture_scope(resolved_runtime)


def with_selective_capture(runtime, fn: Callable[[], dict], *, verbose: bool | None = None):
    effective_verbose = _resolve_verbose(verbose)
    started = perf_counter()
    resolved_runtime = _resolve_runtime(runtime)
    _begin_capture_scope(resolved_runtime)
    try:
        try:
            data = fn()
        except TrailError as exc:
            screenshot = _capture_optional_screenshot(resolved_runtime)
            metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
            return command_failure(
                code=exc.code,
                message=format_exception_message(exc),
                screenshot=screenshot,
                timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
                **metadata,
            )

        screenshot = _capture_screenshot(resolved_runtime, optional=False)
        metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_success(
            data=data,
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )
    finally:
        _end_capture_scope(resolved_runtime)
