from __future__ import annotations

from copy import deepcopy

from trail.output.capture import with_auto_capture


def run_session_command(*, store, session_id: str, runtime, command_name: str, action):
    session = store.load(session_id)
    working_session = deepcopy(session)
    result = with_auto_capture(runtime, lambda: action(working_session))
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
