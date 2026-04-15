from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from time import monotonic, sleep
from uuid import uuid4

from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest
from trail.daemon.protocol import PROTOCOL_VERSION


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_daemon_home() -> Path:
    return Path.home() / ".trail-daemon"


def install_bootstrap(daemon_home: Path) -> Path:
    daemon_home = Path(daemon_home)
    daemon_home.mkdir(parents=True, exist_ok=True)
    path = manifest_path_for_user(daemon_home)

    if path.exists():
        try:
            manifest = load_manifest(path)
        except Exception:
            manifest = None
        else:
            Path(manifest.install.log_dir).mkdir(parents=True, exist_ok=True)
            token_file = Path(manifest.install.token_file)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            if not token_file.exists():
                token_file.write_text(uuid4().hex, encoding="utf-8")
            return path

    log_dir = daemon_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    token_file = daemon_home / "daemon-token.txt"
    token_file.write_text(uuid4().hex, encoding="utf-8")

    manifest = TrailDaemonManifest(
        install=InstallRecord(
            bootstrap_type="scheduled_task",
            bootstrap_id="traild-user",
            daemon_entrypoint="trail.daemon.server:main",
            manifest_version=1,
            protocol_version=PROTOCOL_VERSION,
            token_file=str(token_file),
            log_dir=str(log_dir),
            workspace_strategy="per-request",
        ),
        runtime=RuntimeRecord(
            instance_id=None,
            pid=None,
            state="installed",
            endpoint=None,
            token_generation=None,
            updated_at=None,
            last_transition_at=None,
            last_start_error=None,
        ),
    )
    save_manifest(path, manifest)
    return path


def start_bootstrap(daemon_home: Path) -> bool:
    manifest_path = manifest_path_for_user(daemon_home)
    if not manifest_path.exists():
        return False

    manifest = load_manifest(manifest_path)
    manifest.runtime.state = "starting"
    manifest.runtime.last_transition_at = _utc_now()
    manifest.runtime.last_start_error = None

    try:
        process = subprocess.Popen(["traild"], cwd=str(Path.cwd()))
    except Exception as error:
        manifest.runtime.state = "degraded"
        manifest.runtime.last_start_error = f"{type(error).__name__}: {error}"
        save_manifest(manifest_path, manifest)
        return False

    manifest.runtime.pid = process.pid
    manifest.runtime.updated_at = _utc_now()
    save_manifest(manifest_path, manifest)
    return True


def wait_until_runtime_ready(
    daemon_home: Path,
    *,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
) -> dict[str, object]:
    manifest_path = manifest_path_for_user(daemon_home)
    deadline = monotonic() + timeout_seconds

    while monotonic() < deadline:
        manifest = load_manifest(manifest_path)
        runtime = manifest.runtime
        token_path = Path(manifest.install.token_file)
        token_value = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
        if runtime.state in {"ready", "degraded"} and runtime.endpoint and runtime.pid and runtime.token_generation and token_value:
            return asdict(runtime)
        sleep(interval_seconds)

    raise RuntimeError("daemon did not become ready in time")
