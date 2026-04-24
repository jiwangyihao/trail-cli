from __future__ import annotations

from typing import Any


def _quote(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def _encode_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    if not text:
        return _quote(text)
    if any(ch.isspace() or ch in {'"', '\\', '=', ','} for ch in text):
        return _quote(text)
    return text


def _format_box(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None

    keys = ("left", "top", "width", "height")
    if any(key not in value for key in keys):
        return None
    box_values = [value[key] for key in keys]
    if any(box_value is None for box_value in box_values):
        return None
    return ",".join(str(box_value) for box_value in box_values)


def _normalize_trace_event(trace: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"kind": "trace", "step": trace.get("step") or "unknown"}
    for key, value in trace.items():
        if key == "step":
            continue
        if key == "box":
            formatted_box = _format_box(value)
            if formatted_box is not None:
                event[key] = formatted_box
                continue
        event[key] = value
    return event


def collect_debug_events(debug: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(debug, dict):
        return []

    events: list[dict[str, Any]] = []

    request_id = debug.get("request_id")
    if request_id is not None:
        events.append({"kind": "request", "msg": request_id})

    for trace in debug.get("trace") or []:
        if isinstance(trace, dict):
            events.append(_normalize_trace_event(trace))
            continue
        events.append({"kind": "trace", "step": "unknown", "value": trace})

    detail = debug.get("detail")
    if detail is not None:
        events.append({"kind": "detail", "msg": str(detail)})

    for key, value in debug.items():
        if key in {"request_id", "trace", "detail"}:
            continue
        events.append({"kind": "context", "key": key, "value": value})

    return events


def render_debug_lines(debug: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    for event in collect_debug_events(debug):
        facts = [f"{key}={_encode_value(value)}" for key, value in event.items()]
        lines.append("debug " + " ".join(facts))
    return lines
