from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest


def manifest_path_for_user(daemon_home: Path) -> Path:
    return Path(daemon_home) / "traild" / "manifest.json"


def load_manifest(path: Path) -> TrailDaemonManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TrailDaemonManifest(
        install=InstallRecord(**payload["install"]),
        runtime=RuntimeRecord(**payload["runtime"]),
    )


def save_manifest(path: Path, manifest: TrailDaemonManifest) -> None:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
