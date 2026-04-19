from __future__ import annotations

from pathlib import Path


def resolve_daemon_home() -> Path:
    return Path.home() / ".trail-daemon"


def game_paths_path_for_user(daemon_home: Path | None = None) -> Path:
    home = resolve_daemon_home() if daemon_home is None else Path(daemon_home)
    return home / "game-paths.json"
