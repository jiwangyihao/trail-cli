from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any


def to_jsonable(value: Any, *, _seen: set[int] | None = None) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()

    seen = set() if _seen is None else _seen
    value_id = id(value)
    if value_id in seen:
        return safe_str(value)

    item = _callable_attr(value, "item")
    if item is not None:
        seen.add(value_id)
        try:
            return to_jsonable(item(), _seen=seen)
        except Exception:
            pass
        finally:
            seen.discard(value_id)

    if is_dataclass(value):
        seen.add(value_id)
        try:
            return {field.name: to_jsonable(getattr(value, field.name), _seen=seen) for field in fields(value)}
        finally:
            seen.discard(value_id)

    to_dict = _callable_attr(value, "to_dict")
    if to_dict is not None:
        seen.add(value_id)
        try:
            return to_jsonable(to_dict(), _seen=seen)
        except Exception:
            pass
        finally:
            seen.discard(value_id)

    model_dump = _callable_attr(value, "model_dump")
    if model_dump is not None:
        seen.add(value_id)
        try:
            return to_jsonable(model_dump(mode="json"), _seen=seen)
        except Exception:
            pass
        finally:
            seen.discard(value_id)

    if isinstance(value, Mapping):
        seen.add(value_id)
        try:
            items = list(value.items())
            return {str(to_jsonable(key, _seen=seen)): to_jsonable(item, _seen=seen) for key, item in items}
        except Exception:
            return safe_str(value)
        finally:
            seen.discard(value_id)

    if isinstance(value, set | frozenset):
        seen.add(value_id)
        try:
            return sorted((to_jsonable(item, _seen=seen) for item in value), key=_sort_key)
        finally:
            seen.discard(value_id)

    if isinstance(value, list | tuple):
        seen.add(value_id)
        try:
            return [to_jsonable(item, _seen=seen) for item in value]
        finally:
            seen.discard(value_id)

    return safe_str(value)


def _sort_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(value)


def _callable_attr(value: Any, name: str):
    try:
        attr = getattr(value, name)
    except Exception:
        return None
    if callable(attr):
        return attr
    return None


def safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception as error:
        message = format_exception_message(error)
        if message:
            return f"<unjsonable {type(value).__name__}: {type(error).__name__}: {message}>"
        return f"<unjsonable {type(value).__name__}: {type(error).__name__}>"


def format_exception_message(error: Exception) -> str:
    try:
        message = str(error)
    except Exception:
        return type(error).__name__
    return message or type(error).__name__


def format_exception_detail(error: Exception) -> str:
    try:
        message = str(error)
    except Exception as str_error:
        detail = format_exception_message(str_error)
        return f"{type(error).__name__}: <unprintable {type(str_error).__name__}: {detail}>"
    if not message:
        return type(error).__name__
    return f"{type(error).__name__}: {message}"
