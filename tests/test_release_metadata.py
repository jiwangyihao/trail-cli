from __future__ import annotations

from pathlib import Path
import tomllib

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def test_project_declares_mpl_license_and_license_files():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]

    assert project["license"] == "MPL-2.0"
    assert "LICENSE" in project["license-files"]
    assert "THIRD_PARTY_NOTICES*" in project["license-files"]


def test_license_and_notices_exist_for_release_assets():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    notices_text = (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")

    assert "Mozilla Public License Version 2.0" in license_text
    assert "Trail CLI third-party notices" in notices_text
    assert "Package:" in notices_text


def test_third_party_notice_generator_exists():
    generator = (ROOT / "scripts" / "generate-third-party-notices.py").read_text(
        encoding="utf-8"
    )

    assert "importlib.metadata" in generator
    assert "THIRD_PARTY_NOTICES.txt" in generator
    assert "normalize_dist_name" in generator


def test_third_party_notices_cover_runtime_transitive_dependencies():
    generator = (ROOT / "scripts" / "generate-third-party-notices.py").read_text(
        encoding="utf-8"
    ).lower()
    notices = (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8").lower()

    for package in [
        "mouseinfo",
        "pymsgbox",
        "pytweening",
        "pyclipper",
        "shapely",
        "six",
        "tqdm",
        "annotated-doc",
        "packaging",
        "pyperclip",
        "mpmath",
        "pyrect",
    ]:
        assert package in generator
        assert f"package: {package}" in notices


def test_release_build_files_define_required_assets():
    build_script = (ROOT / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "trail.spec").read_text(encoding="utf-8")

    assert "trail-cli-windows-x64-v$Version.zip" in build_script
    assert "$Version = $Version.TrimStart('v')" in build_script
    assert "trail\\bin" in build_script
    assert "SHA256SUMS.txt" in build_script
    assert "agent-install.ps1" in build_script
    assert "generate-third-party-notices.py" in build_script
    assert "$normalized -eq 'trail-cli'" in build_script
    assert "continue" in build_script
    assert "uv sync --locked --all-groups" in workflow
    assert "windows-latest" in workflow
    assert "trail.exe selfcheck release" in workflow
    assert "body_path" in workflow
    assert "project.version" in workflow
    assert "onnxruntime" in spec
    assert "windows_capture" in spec
    assert "packaging/trail_cli_entry.py" in spec
    assert "packaging/traild_entry.py" in spec


def test_release_selfcheck_command_runs_and_stays_hidden(monkeypatch):
    from trail.cli import _selfcheck_release, app

    monkeypatch.setenv("TRAIL_SKILLS_ROOT", str(ROOT / "skills"))
    runner = CliRunner()

    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "selfcheck" not in help_result.stdout

    result = runner.invoke(app, ["selfcheck", "release"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ok release selfcheck assets=1 handoff=1"
    assert _selfcheck_release() == (True, True, True)


def test_release_selfcheck_requires_portal_select_handoff(tmp_path, monkeypatch):
    from trail.cli import _selfcheck_release
    from trail.output import rendering

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
    monkeypatch.setenv("TRAIL_SKILLS_ROOT", str(tmp_path / "skills"))
    rendering._load_workflow_handoffs.cache_clear()

    assert _selfcheck_release() == (True, True, False)
