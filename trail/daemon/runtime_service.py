from __future__ import annotations

import inspect
from pathlib import Path
from time import monotonic
from time import sleep

from trail.core.errors import TrailError


class RuntimeService:
    def __init__(self) -> None:
        self._runtimes: dict[tuple[str, str | None], object] = {}

    def get_runtime(self, *, workspace_root: str, window_binding: dict | None):
        from trail.runtime.operator import build_runtime

        workspace = Path(workspace_root) / ".trail" / "shots"
        binding_key = None if window_binding is None else repr(sorted(window_binding.items()))
        cache_key = (str(Path(workspace_root)), binding_key)
        if cache_key not in self._runtimes:
            window_title = "崩坏：星穹铁道"
            if isinstance(window_binding, dict):
                maybe_title = window_binding.get("title")
                if isinstance(maybe_title, str) and maybe_title:
                    window_title = maybe_title
            build_kwargs = {
                "workspace": workspace,
                "window_title": window_title,
                "window_binding": window_binding,
            }
            parameters = inspect.signature(build_runtime).parameters.values()
            if any(
                parameter.name == "reference_root" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            ):
                build_kwargs["reference_root"] = Path(workspace_root)
            self._runtimes[cache_key] = build_runtime(**build_kwargs)
        return self._runtimes[cache_key]

    def attach_window(self, *, window_title: str):
        from trail.runtime.window import attach_window

        return attach_window(window_title)

    def launch_game(self, **payload):
        from trail.runtime.window import launch_game

        resolved = dict(payload)
        if "game_path" in resolved:
            game_path = resolved["game_path"]
            resolved["game_path"] = None if game_path is None else Path(game_path)
        return launch_game(**resolved)

    def start_run(
        self,
        *,
        window_title: str,
        game_path: str | None = None,
        channel: str = "official",
        timeout_seconds: int = 30,
        interval_seconds: int = 1,
    ):
        try:
            return self.attach_window(window_title=window_title)
        except TrailError as error:
            if error.code != "WINDOW_NOT_FOUND":
                raise

        launch_result = self.launch_game(game_path=game_path, channel=channel)
        if not launch_result.get("started") and not launch_result.get("already_running"):
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")

        last_error: TrailError | None = None
        deadline = monotonic() + timeout_seconds
        while True:
            try:
                return self.attach_window(window_title=window_title)
            except TrailError as error:
                if error.code != "WINDOW_NOT_FOUND":
                    raise
                last_error = error
            if monotonic() >= deadline:
                break
            sleep(interval_seconds)

        if last_error is not None:
            raise last_error
        raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
