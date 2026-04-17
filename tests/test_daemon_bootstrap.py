from __future__ import annotations

import json
import socket
import subprocess
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.cli import app
from trail.daemon.client import send_daemon_request
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from tests.support.fake_daemon import write_installed_manifest, write_ready_manifest


def test_daemon_status_renders_summary_and_yaml(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    text_result = cli_runner.invoke(app, ["daemon", "status"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "daemon", "status"])

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert text_result.stdout.splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1"
    ]
    assert yaml_result.stdout.splitlines()[0] == "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1"
    assert "runtime:" in yaml_result.stdout


def test_daemon_status_surfaces_last_start_error_summary(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    manifest_path = write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    manifest = load_manifest(manifest_path)
    manifest.runtime.last_start_error = "port already in use"
    save_manifest(manifest_path, manifest)
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        'why msg="port already in use"',
    ]


def test_daemon_status_omits_missing_runtime_fields(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok daemon.status state=installed protocol=1"]
    assert "pid=null" not in result.stdout
    assert "endpoint=null" not in result.stdout


def test_daemon_status_returns_structured_failure_when_manifest_invalid(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    manifest_path = write_installed_manifest(daemon_home, runtime_state="installed")
    manifest_path.write_text("{invalid-json", encoding="utf-8")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "status"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail daemon.status code=DAEMON_MANIFEST_INVALID",
        "request id=local-status",
        'why msg="daemon manifest invalid"',
    ]


@pytest.mark.parametrize("command", ["start", "stop", "logs"])
def test_daemon_control_commands_return_structured_failure_when_manifest_invalid(cli_runner, monkeypatch, tmp_path: Path, command: str):
    daemon_home = tmp_path / "daemon-home"
    manifest_path = write_installed_manifest(daemon_home, runtime_state="installed")
    manifest_path.write_text("{invalid-json", encoding="utf-8")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", command])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        f"fail daemon.{command} code=DAEMON_MANIFEST_INVALID",
        f"request id=local-{command}",
        'why msg="daemon manifest invalid"',
    ]


def test_daemon_install_writes_manifest(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result = cli_runner.invoke(app, ["daemon", "install"])
    manifest = load_manifest(manifest_path_for_user(daemon_home))

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        f"ok daemon.install manifest_path={json.dumps(str(manifest_path_for_user(daemon_home)), ensure_ascii=False)}"
    ]
    assert manifest.runtime.state == "installed"


def test_install_bootstrap_registers_scheduled_task(monkeypatch, tmp_path: Path):
    import trail.daemon.bootstrap as bootstrap_module

    daemon_home = tmp_path / "daemon-home"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    monkeypatch.chdir(worktree_root)
    python_exe = tmp_path / "venv" / "Scripts" / "python.exe"
    pythonw_exe = tmp_path / "venv" / "Scripts" / "pythonw.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")
    pythonw_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(bootstrap_module.sys, "executable", str(python_exe))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap_module.subprocess, "run", fake_run)

    manifest_path = bootstrap_module.install_bootstrap(daemon_home)
    launcher = daemon_home / "traild-launch.pyw"
    manifest = load_manifest(manifest_path)

    assert launcher.exists()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert str(worktree_root) in launcher_text
    assert calls[0][0:3] == ["powershell", "-NoProfile", "-Command"]
    assert "Start-Process" in calls[0][3]
    assert "-Verb RunAs" in calls[0][3]
    assert "schtasks.exe" in calls[0][3]
    assert "/Create" in calls[0][3]
    assert "traild-user" in calls[0][3]
    assert subprocess.list2cmdline([str(pythonw_exe), str(launcher)]) in calls[0][3]
    assert manifest.install.bootstrap_type == "scheduled_task"


def test_install_bootstrap_uses_hidden_pythonw_launcher(monkeypatch, tmp_path: Path):
    import trail.daemon.bootstrap as bootstrap_module

    daemon_home = tmp_path / "daemon-home"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    monkeypatch.chdir(worktree_root)
    python_exe = tmp_path / "venv" / "Scripts" / "python.exe"
    pythonw_exe = tmp_path / "venv" / "Scripts" / "pythonw.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")
    pythonw_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(bootstrap_module.sys, "executable", str(python_exe))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap_module.subprocess, "run", fake_run)

    bootstrap_module.install_bootstrap(daemon_home)

    launcher = daemon_home / "traild-launch.pyw"
    assert launcher.exists()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert str(worktree_root) in launcher_text
    assert subprocess.list2cmdline([str(pythonw_exe), str(launcher)]) in calls[0][3]
    assert "from trail.daemon.server import main" in launcher_text
    assert "main()" in launcher_text


def test_daemon_install_returns_structured_failure_when_bootstrap_registration_fails(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    def fail_run(*args, **kwargs):
        raise PermissionError("elevation denied")

    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", fail_run)

    result = cli_runner.invoke(app, ["daemon", "install"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail daemon.install code=DAEMON_INSTALL_FAILED",
        "request id=local-install",
        'why msg="daemon install failed"',
    ]


def test_daemon_install_recovers_from_invalid_manifest(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    manifest_path = manifest_path_for_user(daemon_home)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{invalid-json", encoding="utf-8")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result = cli_runner.invoke(app, ["daemon", "install"])

    assert result.exit_code == 0
    manifest = load_manifest(manifest_path)
    token_value = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
    assert result.stdout.splitlines() == [
        f"ok daemon.install manifest_path={json.dumps(str(manifest_path), ensure_ascii=False)}"
    ]
    assert manifest.runtime.state == "installed"
    assert manifest.runtime.endpoint is None
    assert token_value


def test_daemon_install_is_idempotent_for_existing_ready_runtime(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    manifest_path = manifest_path_for_user(daemon_home)
    before = load_manifest(manifest_path)
    token_before = Path(before.install.token_file).read_text(encoding="utf-8")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result = cli_runner.invoke(app, ["daemon", "install"])

    assert result.exit_code == 0
    after = load_manifest(manifest_path)
    token_after = Path(after.install.token_file).read_text(encoding="utf-8")
    assert result.stdout.splitlines() == [
        f"ok daemon.install manifest_path={json.dumps(str(manifest_path), ensure_ascii=False)}"
    ]
    assert after.runtime.state == before.runtime.state
    assert after.runtime.endpoint == before.runtime.endpoint
    assert after.runtime.pid == before.runtime.pid
    assert after.runtime.token_generation == before.runtime.token_generation
    assert token_after == token_before


def test_daemon_start_returns_bootstrap_required_when_not_installed(cli_runner, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: tmp_path / "daemon-home")

    result = cli_runner.invoke(app, ["daemon", "start"])

    assert result.stdout.splitlines() == [
        "fail daemon.start code=DAEMON_BOOTSTRAP_REQUIRED",
        "request id=local-start",
        'why msg="daemon bootstrap not installed"',
    ]


def test_daemon_start_is_idempotent_when_runtime_ready(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    manifest_path = manifest_path_for_user(daemon_home)
    before = load_manifest(manifest_path)
    token_before = Path(before.install.token_file).read_text(encoding="utf-8")
    started: list[Path] = []
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.commands.daemon.runtime_endpoint_is_traild", lambda **kwargs: True)
    monkeypatch.setattr("trail.commands.daemon.start_bootstrap", lambda home: started.append(home) or True)

    result = cli_runner.invoke(app, ["daemon", "start"])

    assert result.exit_code == 0
    after = load_manifest(manifest_path)
    token_after = Path(after.install.token_file).read_text(encoding="utf-8")
    assert result.stdout.splitlines() == ["ok daemon.start started=0 already_running=1"]
    assert started == []
    assert after.runtime.state == before.runtime.state
    assert after.runtime.endpoint == before.runtime.endpoint
    assert after.runtime.pid == before.runtime.pid
    assert token_after == token_before


def test_daemon_start_restarts_stale_ready_runtime(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    listener = socket.create_server(("127.0.0.1", 0))
    endpoint = f"127.0.0.1:{listener.getsockname()[1]}"
    try:
        write_ready_manifest(daemon_home, endpoint=endpoint, token_value="token-live")
        started: list[Path] = []
        monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
        monkeypatch.setattr("trail.commands.daemon.start_bootstrap", lambda home: started.append(home) or True)

        result = cli_runner.invoke(app, ["daemon", "start"])

        assert result.exit_code == 0
        assert result.stdout.splitlines() == ["ok daemon.start started=1 already_running=0"]
        assert started == [daemon_home]
    finally:
        listener.close()


def test_daemon_logs_returns_log_dir(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="ready")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "logs"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        f"ok daemon.logs log_dir={json.dumps(str(daemon_home / 'logs'), ensure_ascii=False)}"
    ]


def test_daemon_stop_returns_stopped_and_clears_runtime(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    terminated: list[int] = []
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.commands.daemon.runtime_endpoint_is_traild", lambda **kwargs: True)
    monkeypatch.setattr("trail.commands.daemon.terminate_daemon_process", lambda pid: terminated.append(pid))

    result = cli_runner.invoke(app, ["daemon", "stop"])
    manifest = load_manifest(manifest_path_for_user(daemon_home))

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok daemon.stop stopped=1"]
    assert terminated == [1234]
    assert manifest.runtime.state == "stopped"
    assert manifest.runtime.endpoint is None
    assert manifest.runtime.pid is None


def test_daemon_stop_returns_failure_when_process_termination_fails(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")
    token_path = daemon_home / "daemon-token.txt"
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
    monkeypatch.setattr("trail.commands.daemon.runtime_endpoint_is_traild", lambda **kwargs: True)

    def fail_terminate(pid: int) -> None:
        raise PermissionError(f"denied: {pid}")

    monkeypatch.setattr("trail.commands.daemon.terminate_daemon_process", fail_terminate)

    result = cli_runner.invoke(app, ["daemon", "stop"])

    assert result.exit_code == 0
    manifest = load_manifest(manifest_path_for_user(daemon_home))
    assert result.stdout.splitlines() == [
        "fail daemon.stop code=DAEMON_STOP_FAILED",
        "request id=local-stop",
        'why msg="daemon stop failed"',
    ]
    assert manifest.runtime.state == "ready"
    assert manifest.runtime.endpoint == "127.0.0.1:8765"
    assert manifest.runtime.pid == 1234
    assert token_path.read_text(encoding="utf-8") == "token-1"


def test_daemon_stop_clears_stale_runtime_without_killing_unverified_pid(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    listener = socket.create_server(("127.0.0.1", 0))
    endpoint = f"127.0.0.1:{listener.getsockname()[1]}"
    try:
        write_ready_manifest(daemon_home, endpoint=endpoint, token_value="token-stale")
        terminated: list[int] = []
        token_path = daemon_home / "daemon-token.txt"
        monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)
        monkeypatch.setattr("trail.commands.daemon.terminate_daemon_process", lambda pid: terminated.append(pid))

        result = cli_runner.invoke(app, ["daemon", "stop"])

        assert result.exit_code == 0
        manifest = load_manifest(manifest_path_for_user(daemon_home))
        assert result.stdout.splitlines() == ["ok daemon.stop stopped=1"]
        assert terminated == []
        assert manifest.runtime.state == "stopped"
        assert manifest.runtime.endpoint is None
        assert manifest.runtime.pid is None
        assert token_path.read_text(encoding="utf-8") == ""
    finally:
        listener.close()


def test_start_bootstrap_marks_runtime_starting_and_launches_elevated_pythonw(monkeypatch, tmp_path: Path):
    from trail.daemon.bootstrap import start_bootstrap
    import trail.daemon.bootstrap as bootstrap_module

    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")
    python_exe = tmp_path / "venv" / "Scripts" / "python.exe"
    pythonw_exe = tmp_path / "venv" / "Scripts" / "pythonw.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")
    pythonw_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(bootstrap_module.sys, "executable", str(python_exe))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", fake_run)

    started = start_bootstrap(daemon_home)
    manifest = load_manifest(manifest_path_for_user(daemon_home))
    launcher = daemon_home / "traild-launch.pyw"

    assert started is True
    assert calls[0][0:3] == ["powershell", "-NoProfile", "-Command"]
    assert "Start-Process" in calls[0][3]
    assert "-Verb RunAs" in calls[0][3]
    assert "-WindowStyle Hidden" in calls[0][3]
    assert str(pythonw_exe) in calls[0][3]
    assert str(launcher) in calls[0][3]
    assert manifest.runtime.state == "starting"


def test_start_bootstrap_returns_false_when_elevated_launch_fails(monkeypatch, tmp_path: Path):
    from trail.daemon.bootstrap import start_bootstrap

    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")

    def fail_run(command: list[str], **kwargs):
        raise OSError("schtasks failed")

    monkeypatch.setattr("trail.daemon.bootstrap.subprocess.run", fail_run)

    started = start_bootstrap(daemon_home)
    manifest = load_manifest(manifest_path_for_user(daemon_home))

    assert started is False
    assert manifest.runtime.state == "degraded"
    assert "schtasks failed" in (manifest.runtime.last_start_error or "")


def test_wait_until_runtime_ready_returns_runtime_snapshot(tmp_path: Path):
    from trail.daemon.bootstrap import wait_until_runtime_ready

    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="starting")

    def promote_runtime() -> None:
        write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-1")

    thread = threading.Timer(0.1, promote_runtime)
    thread.start()
    runtime = wait_until_runtime_ready(daemon_home, timeout_seconds=2.0, interval_seconds=0.05)
    thread.join()

    assert runtime["state"] == "ready"
    assert runtime["endpoint"] == "127.0.0.1:8765"
    assert runtime["pid"] == 1234


def test_runtime_service_caches_runtime_and_delegates_window_ops(monkeypatch, tmp_path: Path):
    from trail.daemon.runtime_service import RuntimeService

    build_calls: list[tuple[Path, dict | None]] = []
    attached: list[str] = []
    launched: list[dict[str, object]] = []

    class FakeRuntime:
        def __init__(self, name: str):
            self.name = name

    def fake_build_runtime(*, workspace: Path, window_title: str = "崩坏：星穹铁道", window_binding=None):
        build_calls.append((workspace, window_binding))
        return FakeRuntime(f"runtime-{len(build_calls)}")

    def fake_attach_window(window_title: str):
        attached.append(window_title)
        return {"title": window_title, "hwnd": 1}

    def fake_launch_game(**payload):
        launched.append(payload)
        return {"started": True}

    monkeypatch.setattr("trail.runtime.operator.build_runtime", fake_build_runtime)
    monkeypatch.setattr("trail.runtime.window.attach_window", fake_attach_window)
    monkeypatch.setattr("trail.runtime.window.launch_game", fake_launch_game)

    service = RuntimeService()
    runtime_a = service.get_runtime(workspace_root=str(tmp_path), window_binding={"title": "Demo", "hwnd": 1})
    runtime_b = service.get_runtime(workspace_root=str(tmp_path), window_binding={"title": "Demo", "hwnd": 1})
    runtime_c = service.get_runtime(workspace_root=str(tmp_path), window_binding={"title": "Other", "hwnd": 2})
    attached_window = service.attach_window(window_title="Demo Window")
    launched_game = service.launch_game(game_path=str(tmp_path / "StarRail.exe"), channel="official")

    assert runtime_a is runtime_b
    assert runtime_c is not runtime_a
    assert build_calls == [
        (tmp_path / ".trail" / "shots", {"title": "Demo", "hwnd": 1}),
        (tmp_path / ".trail" / "shots", {"title": "Other", "hwnd": 2}),
    ]
    assert attached == ["Demo Window"]
    assert attached_window == {"title": "Demo Window", "hwnd": 1}
    assert launched == [{"game_path": tmp_path / "StarRail.exe", "channel": "official"}]
    assert launched_game == {"started": True}


def test_pyproject_exposes_traild_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["traild"] == "trail.daemon.server:main"


def _start_server_in_thread(*, daemon_home: Path, monkeypatch):
    from trail.daemon.bootstrap import wait_until_runtime_ready
    from trail.daemon.command_service import CommandService
    from trail.daemon.server import TrailDaemonServer

    class StubRuntime:
        def ocr(self, **kwargs):
            return [{"text": f"ocr:{kwargs.get('lang', 'default')}"}]

    class StubRuntimeService:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def get_runtime(self, *, workspace_root: str, window_binding: dict | None):
            self.calls.append({"workspace_root": workspace_root, "window_binding": window_binding})
            return StubRuntime()

    write_installed_manifest(daemon_home, runtime_state="starting")
    runtime_service = StubRuntimeService()
    server = TrailDaemonServer(command_service=CommandService(runtime_service=runtime_service))
    monkeypatch.setattr("trail.daemon.server.resolve_daemon_home", lambda: daemon_home)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = wait_until_runtime_ready(daemon_home, timeout_seconds=2.0, interval_seconds=0.05)
    manifest = load_manifest(manifest_path_for_user(daemon_home))
    token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
    return server, thread, runtime_service, runtime["endpoint"], token


def test_traild_server_accepts_socket_requests(tmp_path: Path, monkeypatch):
    server, thread, runtime_service, endpoint, token = _start_server_in_thread(
        daemon_home=tmp_path / "daemon-home",
        monkeypatch=monkeypatch,
    )

    try:
        payload = send_daemon_request(
            DaemonRequest(
                request_id="req-server-1",
                protocol_version=PROTOCOL_VERSION,
                workspace_root=str(tmp_path),
                session_id=None,
                verbose=False,
                method="ocr.read",
                payload={"lang": "zh"},
            ),
            token=token,
            endpoint=endpoint,
        )
        manifest = load_manifest(manifest_path_for_user(tmp_path / "daemon-home"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert payload["ok"] is True
    assert payload["request_id"] == "req-server-1"
    assert payload["data"] == {"result": [{"text": "ocr:zh"}]}
    assert runtime_service.calls == [{"workspace_root": str(tmp_path), "window_binding": None}]
    assert manifest.runtime.state == "ready"
    assert manifest.runtime.endpoint == endpoint
    assert manifest.runtime.pid


def test_traild_server_responds_to_ping(tmp_path: Path, monkeypatch):
    server, thread, _runtime_service, endpoint, token = _start_server_in_thread(
        daemon_home=tmp_path / "daemon-home",
        monkeypatch=monkeypatch,
    )

    try:
        payload = send_daemon_request(
            DaemonRequest(
                request_id="req-ping-1",
                protocol_version=PROTOCOL_VERSION,
                workspace_root=str(tmp_path),
                session_id=None,
                verbose=False,
                method="daemon.ping",
                payload={},
            ),
            token=token,
            endpoint=endpoint,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert payload["ok"] is True
    assert payload["data"] == {"alive": True}


def test_runtime_endpoint_is_traild_returns_false_for_non_object_json():
    from trail.commands.daemon import runtime_endpoint_is_traild

    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(5)

    def handle_once() -> None:
        with listener:
            connection, _ = listener.accept()
            with connection:
                chunks = b""
                while not chunks.endswith(b"\n"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks += chunk
                connection.sendall(b"[]\n")

    thread = threading.Thread(target=handle_once)
    thread.start()
    endpoint = f"127.0.0.1:{listener.getsockname()[1]}"

    try:
        assert runtime_endpoint_is_traild(
            endpoint=endpoint,
            token="token-1",
            protocol_version=PROTOCOL_VERSION,
        ) is False
    finally:
        thread.join(timeout=5)


def test_traild_server_rejects_token_mismatch(tmp_path: Path, monkeypatch):
    server, thread, _runtime_service, endpoint, _token = _start_server_in_thread(
        daemon_home=tmp_path / "daemon-home",
        monkeypatch=monkeypatch,
    )

    try:
        payload = send_daemon_request(
            DaemonRequest(
                request_id="req-auth-1",
                protocol_version=PROTOCOL_VERSION,
                workspace_root=str(tmp_path),
                session_id=None,
                verbose=False,
                method="ocr.read",
                payload={},
            ),
            token="wrong-token",
            endpoint=endpoint,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_AUTH_FAILED",
        "message": "daemon token mismatch",
    }


def test_traild_server_rejects_protocol_mismatch(tmp_path: Path, monkeypatch):
    server, thread, _runtime_service, endpoint, token = _start_server_in_thread(
        daemon_home=tmp_path / "daemon-home",
        monkeypatch=monkeypatch,
    )

    try:
        payload = send_daemon_request(
            DaemonRequest(
                request_id="req-version-1",
                protocol_version=PROTOCOL_VERSION + 1,
                workspace_root=str(tmp_path),
                session_id=None,
                verbose=False,
                method="ocr.read",
                payload={},
            ),
            token=token,
            endpoint=endpoint,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_VERSION_MISMATCH",
        "message": "protocol version mismatch",
    }
