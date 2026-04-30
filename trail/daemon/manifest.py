from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import sleep

from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest


_REPLACE_PERMISSION_RETRY_DELAYS = (0.02, 0.05, 0.1)


def manifest_path_for_user(daemon_home: Path) -> Path:
    return Path(daemon_home) / "traild" / "manifest.json"


def load_manifest(path: Path) -> TrailDaemonManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TrailDaemonManifest(
        install=InstallRecord(**payload["install"]),
        runtime=RuntimeRecord(**payload["runtime"]),
    )


def _replace_manifest(temp_path: Path, manifest_path: Path) -> None:
    for delay in (*_REPLACE_PERMISSION_RETRY_DELAYS, None):
        try:
            temp_path.replace(manifest_path)
            return
        except PermissionError:
            if delay is None:
                raise
            sleep(delay)


def save_manifest(path: Path, manifest: TrailDaemonManifest) -> None:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(manifest), ensure_ascii=False, indent=2)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            delete=False,
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)

        _replace_manifest(temp_path, manifest_path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
