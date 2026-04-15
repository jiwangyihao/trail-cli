from __future__ import annotations

import inspect
from pathlib import Path


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
        game_path = resolved.get("game_path")
        if game_path is not None:
            resolved["game_path"] = Path(game_path)
        return launch_game(**resolved)
