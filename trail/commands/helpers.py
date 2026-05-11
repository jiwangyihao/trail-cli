from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.core.jsonable import to_jsonable
from trail.output.capture import resolve_capture_verbose, with_auto_capture
from trail.output.rendering import print_output
from trail.runtime.operator import build_runtime
from trail.session.store import SessionStore


_DAEMON_CONTROL_OPTIONS = {"request_id": None, "wait_timeout": None, "no_wait": False}


def set_daemon_control_options(*, request_id: str | None, wait_timeout: float | None, no_wait: bool) -> None:
    _DAEMON_CONTROL_OPTIONS.update(
        {"request_id": request_id, "wait_timeout": wait_timeout, "no_wait": bool(no_wait)}
    )


def current_daemon_control_options() -> dict[str, object]:
    return dict(_DAEMON_CONTROL_OPTIONS)

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


def build_default_daemon_client():
    from trail.daemon.client import TrailDaemonClient, send_daemon_request
    from trail.daemon.paths import resolve_daemon_home

    return TrailDaemonClient(
        workspace_root=Path.cwd(),
        daemon_home=resolve_daemon_home(),
        transport=send_daemon_request,
    )


def print_json(payload: dict) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False))


def _normalize_workspace_path(path_value, *, workspace_root: Path):
    if path_value is None:
        return None
    if isinstance(path_value, str):
        candidate = Path(path_value)
        if not candidate.is_absolute():
            return path_value
    path = Path(path_value)
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.resolve().relative_to(workspace_root.resolve()))
    except ValueError:
        return str(path)


def _normalize_daemon_response_paths(payload: dict, *, workspace_root: Path) -> dict:
    normalized = deepcopy(to_jsonable(payload))
    normalized["screenshot"] = _normalize_workspace_path(normalized.get("screenshot"), workspace_root=workspace_root)

    references = normalized.get("references")
    if isinstance(references, list):
        normalized["references"] = [
            {
                **reference,
                "path": _normalize_workspace_path(reference.get("path"), workspace_root=workspace_root),
            }
            if isinstance(reference, dict) and "path" in reference
            else reference
            for reference in references
        ]

    return normalized


def call_daemon(
    method: str,
    payload: dict,
    *,
    session_id: str | None = None,
    verbose: bool | None = None,
    daemon_client=None,
    request_id: str | None = None,
    wait_timeout: float | None = None,
    no_wait: bool = False,
):
    workspace_root = Path.cwd()
    client = build_default_daemon_client() if daemon_client is None else daemon_client
    options = current_daemon_control_options()
    effective_request_id = request_id or options["request_id"]
    effective_wait_timeout = wait_timeout if wait_timeout is not None else options["wait_timeout"]
    effective_no_wait = bool(no_wait or options["no_wait"])
    response = client.call(
        method,
        to_jsonable(payload),
        session_id=session_id,
        verbose=resolve_capture_verbose(verbose),
        job_id=effective_request_id,
        wait_timeout=effective_wait_timeout,
        no_wait=effective_no_wait,
    )
    return _normalize_daemon_response_paths(response, workspace_root=workspace_root)


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


def _should_persist_failure(*, working_session, result: dict, failure_persistence=None) -> bool:
    if result["ok"]:
        return True
    if failure_persistence is None:
        return False
    return bool(failure_persistence(working_session, result))


def run_session_command(
    *,
    store,
    session_id: str,
    command_name: str,
    action,
    runtime=None,
    runtime_factory=None,
    failure_persistence=None,
):
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
    session_to_save = (
        working_session
        if _should_persist_failure(
            working_session=working_session,
            result=result,
            failure_persistence=failure_persistence,
        )
        else session
    )
    session_to_save.last_result = {
        "command": command_name,
        "ok": result["ok"],
        "data": deepcopy(result["data"]),
        "error": deepcopy(result["error"]),
    }
    session_to_save.last_screenshot = result["screenshot"]
    store.save(session_to_save)
    return result
