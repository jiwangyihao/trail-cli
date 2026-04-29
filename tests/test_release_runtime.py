from __future__ import annotations

from pathlib import Path

from trail.daemon import bootstrap
from trail.output import rendering


def test_frozen_daemon_command_uses_traild_exe(tmp_path, monkeypatch):
    fake_exe = tmp_path / "trail" / "bin" / "trail.exe"
    fake_traild = fake_exe.with_name("traild.exe")
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_text("exe", encoding="utf-8")
    fake_traild.write_text("daemon", encoding="utf-8")
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(fake_exe))

    launcher, launcher_python = bootstrap.write_launcher_script(tmp_path / "daemon")

    assert launcher == fake_traild
    assert launcher_python == fake_traild


def test_frozen_scheduled_task_action_does_not_pass_traild_as_argument(tmp_path, monkeypatch):
    fake_traild = tmp_path / "trail" / "bin" / "traild.exe"
    fake_traild.parent.mkdir(parents=True)
    fake_traild.write_text("daemon", encoding="utf-8")
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)

    action = bootstrap._scheduled_task_action(launcher=fake_traild, launcher_python=fake_traild)

    assert str(fake_traild) in action
    assert action.count(str(fake_traild)) == 1


def test_install_bootstrap_writes_frozen_daemon_entrypoint(tmp_path, monkeypatch):
    fake_exe = tmp_path / "trail" / "bin" / "trail.exe"
    fake_traild = fake_exe.with_name("traild.exe")
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_text("exe", encoding="utf-8")
    fake_traild.write_text("daemon", encoding="utf-8")
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(fake_exe))
    monkeypatch.setattr(bootstrap, "register_scheduled_task", lambda **kwargs: None)

    manifest_path = bootstrap.install_bootstrap(tmp_path / "daemon")
    manifest = bootstrap.load_manifest(manifest_path)

    assert manifest.install.daemon_entrypoint == "traild.exe"


def test_workflow_handoffs_path_prefers_trail_skills_root(tmp_path, monkeypatch):
    registry = tmp_path / "skills" / "registry"
    registry.mkdir(parents=True)
    expected = registry / "workflow-handoffs.yaml"
    expected.write_text("commands: {}\n", encoding="utf-8")
    monkeypatch.setenv("TRAIL_SKILLS_ROOT", str(tmp_path / "skills"))

    assert rendering.workflow_handoffs_path() == expected


def test_workflow_handoffs_path_uses_release_layout_when_frozen(tmp_path, monkeypatch):
    root = tmp_path / "release"
    fake_exe = root / "trail" / "bin" / "trail.exe"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_text("exe", encoding="utf-8")
    monkeypatch.delenv("TRAIL_SKILLS_ROOT", raising=False)
    monkeypatch.setattr(rendering.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rendering.sys, "executable", str(fake_exe))

    expected = root / "skills" / "registry" / "workflow-handoffs.yaml"

    assert rendering.workflow_handoffs_path() == expected


def test_render_output_loads_workflow_handoffs_from_current_skills_root(tmp_path, monkeypatch):
    stale_registry = tmp_path / "stale" / "workflow-handoffs.yaml"
    stale_registry.parent.mkdir(parents=True)
    stale_registry.write_text("commands: {}\n", encoding="utf-8")
    registry = tmp_path / "skills" / "registry"
    registry.mkdir(parents=True)
    (registry / "workflow-handoffs.yaml").write_text(
        "commands:\n"
        "  cw.enter:\n"
        "    default:\n"
        "      handoff_skill: trail-cw-entry\n"
        "      handoff_strength: strong\n"
        "      handoff_reason: scene_entered\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rendering, "WORKFLOW_HANDOFFS_PATH", stale_registry)
    monkeypatch.setenv("TRAIL_SKILLS_ROOT", str(tmp_path / "skills"))
    rendering._load_workflow_handoffs.cache_clear()

    text = rendering.render_output("cw.enter", {"ok": True, "data": {}})

    assert "handoff_skill=trail-cw-entry" in text
