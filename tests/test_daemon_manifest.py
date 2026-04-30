from pathlib import Path

import pytest

from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest
from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest
from tests.support.fake_daemon import write_ready_manifest


def _sample_manifest() -> TrailDaemonManifest:
    return TrailDaemonManifest(
        install=InstallRecord(
            bootstrap_type="scheduled_task",
            bootstrap_id="traild-user",
            daemon_entrypoint="trail.daemon.server:main",
            manifest_version=1,
            protocol_version=1,
            token_file="C:/daemon/token.txt",
            log_dir="C:/daemon/logs",
            workspace_strategy="per-request",
        ),
        runtime=RuntimeRecord(
            instance_id="inst-1",
            pid=4321,
            state="ready",
            endpoint="npipe://traild",
            token_generation=1,
            updated_at="2026-04-15T00:00:00+00:00",
            last_transition_at="2026-04-15T00:00:00+00:00",
            last_start_error=None,
        ),
    )


def test_manifest_path_for_user_is_global_not_workspace_scoped(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    workspace_root = tmp_path / "workspace-root"

    path = manifest_path_for_user(daemon_home)

    assert path == daemon_home / "traild" / "manifest.json"
    assert str(workspace_root) not in str(path)


def test_save_and_load_manifest_round_trip(tmp_path: Path):
    path = manifest_path_for_user(tmp_path / "daemon-home")
    manifest = _sample_manifest()

    save_manifest(path, manifest)

    assert load_manifest(path) == manifest


def test_save_manifest_leaves_existing_file_untouched_when_replace_fails(tmp_path: Path, monkeypatch):
    path = manifest_path_for_user(tmp_path / "daemon-home")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"install":{"sentinel":true}}'
    path.write_text(original, encoding="utf-8")

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_manifest(path, _sample_manifest())

    assert path.read_text(encoding="utf-8") == original


def test_save_manifest_retries_transient_permission_error_on_replace(tmp_path: Path, monkeypatch):
    path = manifest_path_for_user(tmp_path / "daemon-home")
    real_replace = Path.replace
    attempts: list[Path] = []

    def fail_once(self: Path, target: Path) -> Path:
        attempts.append(self)
        if len(attempts) == 1:
            raise PermissionError(5, "拒绝访问")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_once)

    manifest = _sample_manifest()
    save_manifest(path, manifest)

    assert len(attempts) == 2
    assert load_manifest(path) == manifest


def test_write_ready_manifest_publishes_runtime_endpoint_and_token_generation(tmp_path: Path):
    manifest_path = write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="npipe://traild",
        token_value="token-1",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.install.protocol_version == 1
    assert Path(manifest.install.token_file).read_text(encoding="utf-8") == "token-1"
    assert manifest.runtime.state == "ready"
    assert manifest.runtime.endpoint == "npipe://traild"
    assert manifest.runtime.token_generation == 1
