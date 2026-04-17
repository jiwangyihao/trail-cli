from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4

from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest
from trail.daemon.protocol import PROTOCOL_VERSION


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_daemon_home() -> Path:
    return Path.home() / ".trail-daemon"


def launcher_script_path(daemon_home: Path) -> Path:
    return Path(daemon_home) / "traild-launch.pyw"


def write_launcher_script(daemon_home: Path) -> tuple[Path, Path]:
    launcher = launcher_script_path(daemon_home)
    python_executable = Path(sys.executable)
    pythonw_executable = python_executable.with_name("pythonw.exe")
    launcher_python = pythonw_executable if pythonw_executable.exists() else python_executable
    launcher.write_text(
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n"
        "from trail.daemon.server import main\n"
        f'os.chdir(r"{Path.cwd()}")\n'
        f'sys.path.insert(0, r"{Path.cwd()}")\n'
        "main()\n",
        encoding="utf-8",
    )
    return launcher, launcher_python


def register_scheduled_task(*, bootstrap_id: str, launcher: Path, launcher_python: Path) -> None:
    action = subprocess.list2cmdline([str(launcher_python), str(launcher)])
    schtasks_args = (
        f"/Create /TN {bootstrap_id} /TR \"{action}\" /SC ONCE /ST 00:00 /RL HIGHEST /F /IT"
    )
    command = (
        "$process = Start-Process -FilePath 'schtasks.exe' -Verb RunAs "
        f"-WindowStyle Hidden -ArgumentList '{schtasks_args}' "
        "-Wait -PassThru; "
        "exit $process.ExitCode"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )


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
            launcher, launcher_python = write_launcher_script(daemon_home)
            register_scheduled_task(
                bootstrap_id=manifest.install.bootstrap_id,
                launcher=launcher,
                launcher_python=launcher_python,
            )
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
    launcher, launcher_python = write_launcher_script(daemon_home)
    register_scheduled_task(
        bootstrap_id=manifest.install.bootstrap_id,
        launcher=launcher,
        launcher_python=launcher_python,
    )
    return path


def start_bootstrap(daemon_home: Path) -> bool:
    manifest_path = manifest_path_for_user(daemon_home)
    if not manifest_path.exists():
        return False

    manifest = load_manifest(manifest_path)
    manifest.runtime.state = "starting"
    manifest.runtime.endpoint = None
    manifest.runtime.pid = None
    manifest.runtime.last_transition_at = _utc_now()
    manifest.runtime.last_start_error = None

    try:
        launcher, launcher_python = write_launcher_script(daemon_home)
        command = (
            "$process = Start-Process "
            f"-FilePath '{launcher_python}' "
            f"-Verb RunAs -WindowStyle Hidden -ArgumentList '{launcher}' "
            "-PassThru; exit 0"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as error:
        manifest.runtime.state = "degraded"
        manifest.runtime.last_start_error = f"{type(error).__name__}: {error}"
        save_manifest(manifest_path, manifest)
        return False

    manifest.runtime.updated_at = _utc_now()
    save_manifest(manifest_path, manifest)
    return True


def stop_bootstrap(pid: int) -> bool:
    command = (
        "$process = Start-Process "
        "-FilePath 'powershell' "
        f"-Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -Command Stop-Process -Id {pid} -Force' "
        "-PassThru; exit 0"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
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
        try:
            manifest = load_manifest(manifest_path)
            runtime = manifest.runtime
            token_path = Path(manifest.install.token_file)
            token_value = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
        except (OSError, json.JSONDecodeError, ValueError):
            sleep(interval_seconds)
            continue
        if runtime.state in {"ready", "degraded"} and runtime.endpoint and runtime.pid and runtime.token_generation and token_value:
            return asdict(runtime)
        sleep(interval_seconds)

    raise RuntimeError("daemon did not become ready in time")
