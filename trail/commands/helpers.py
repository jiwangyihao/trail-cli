from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.runtime.operator import build_runtime
from trail.session.store import SessionStore


DEFAULT_WINDOW_TITLE = "崩坏：星穹铁道"
TRAIL_WORKSPACE = ".trail"


def build_default_runtime(*, window_title: str = DEFAULT_WINDOW_TITLE, window_binding: dict | None = None):
    return build_runtime(
        workspace=Path(TRAIL_WORKSPACE) / "shots",
        window_title=window_title,
        window_binding=window_binding,
    )


def build_default_session_store() -> SessionStore:
    return SessionStore(Path(TRAIL_WORKSPACE) / "sessions")


def build_default_artifact_store() -> ArtifactStore:
    return ArtifactStore(Path(TRAIL_WORKSPACE) / "artifacts")


def to_jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return to_jsonable(value.to_dict())
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    return value


def print_json(payload: dict) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False))


def _resolve_runtime(*, runtime=None, runtime_factory=None, window_binding: dict | None = None):
    if runtime is not None:
        return runtime

    factory = build_default_runtime if runtime_factory is None else runtime_factory
    title = DEFAULT_WINDOW_TITLE
    if isinstance(window_binding, dict):
        maybe_title = window_binding.get("title")
        if isinstance(maybe_title, str) and maybe_title:
            title = maybe_title
    return factory(window_title=title, window_binding=window_binding)


def _load_session_result(*, store, session_id: str, runtime=None, runtime_factory=None):
    try:
        return store.load(session_id)
    except FileNotFoundError:
        return with_auto_capture(
            _resolve_runtime(runtime=runtime, runtime_factory=runtime_factory),
            lambda: (_ for _ in ()).throw(TrailError("SESSION_NOT_FOUND", f"session not found: {session_id}")),
        )
    except (json.JSONDecodeError, ValueError, OSError):
        return with_auto_capture(
            _resolve_runtime(runtime=runtime, runtime_factory=runtime_factory),
            lambda: (_ for _ in ()).throw(TrailError("SESSION_INVALID", f"session invalid: {session_id}")),
        )


def run_session_command(*, store, session_id: str, command_name: str, action, runtime=None, runtime_factory=None):
    session = _load_session_result(
        store=store,
        session_id=session_id,
        runtime=runtime,
        runtime_factory=runtime_factory,
    )
    if isinstance(session, dict):
        return session

    resolved_runtime = _resolve_runtime(
        runtime=runtime,
        runtime_factory=runtime_factory,
        window_binding=session.window_binding,
    )
    working_session = deepcopy(session)
    result = with_auto_capture(resolved_runtime, lambda: action(working_session))
    session_to_save = working_session if result["ok"] else session
    session_to_save.last_result = {
        "command": command_name,
        "ok": result["ok"],
        "data": deepcopy(result["data"]),
        "error": deepcopy(result["error"]),
    }
    session_to_save.last_screenshot = result["screenshot"]
    store.save(session_to_save)
    return result
