from __future__ import annotations

import json
from pathlib import Path
import threading
from uuid import uuid4

from trail.daemon.paths import game_paths_path_for_user


_LAUNCH_PATHS_WRITE_LOCK = threading.Lock()


def _resolve_launch_paths_path(path: Path | None = None) -> Path:
    return game_paths_path_for_user() if path is None else Path(path)


def _normalize_launch_paths(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for channel, bucket in raw.items():
        if not isinstance(channel, str) or not isinstance(bucket, dict):
            return {}
        last_success_game_path = bucket.get("last_success_game_path")
        if not isinstance(last_success_game_path, str):
            return {}
        normalized[channel] = {"last_success_game_path": last_success_game_path}
    return normalized


def _write_launch_paths_atomically(target: Path, data: dict[str, dict[str, str]]) -> None:
    temp_path = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(target)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def read_launch_paths(path: Path | None = None) -> dict[str, dict[str, str]]:
    target = _resolve_launch_paths_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return _normalize_launch_paths(raw)


def write_launch_path(channel: str, game_path: str, path: Path | None = None) -> None:
    target = _resolve_launch_paths_path(path)
    with _LAUNCH_PATHS_WRITE_LOCK:
        data = read_launch_paths(target)
        data[channel] = {"last_success_game_path": game_path}
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_launch_paths_atomically(target, data)
