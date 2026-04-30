from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest


pytestmark = pytest.mark.slow


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent-install.ps1"


def run_installer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def parse_plan(stdout: str) -> dict[str, object]:
    return json.loads(stdout.splitlines()[-1])


def get_user_environment_variable(name: str) -> str | None:
    script = (
        f"$value = [Environment]::GetEnvironmentVariable('{name}', 'User'); "
        "if ($null -eq $value) { [Console]::Out.Write('__NULL__') } "
        "else { [Console]::Out.Write([Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($value))) }"
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            script,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    if result.stdout == "__NULL__":
        return None
    return base64.b64decode(result.stdout).decode("utf-8")


def set_user_environment_variable(name: str, value: str | None) -> None:
    encoded = "__NULL__" if value is None else base64.b64encode(value.encode("utf-8")).decode("ascii")
    script = (
        f"$encoded = '{encoded}'; "
        "$value = if ($encoded -eq '__NULL__') { $null } "
        "else { [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)) }; "
        f"[Environment]::SetEnvironmentVariable('{name}', $value, 'User')"
    )
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            script,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="module", autouse=True)
def restore_user_environment_after_installer_tests():
    original = {
        "Path": get_user_environment_variable("Path"),
        "TRAIL_SKILLS_ROOT": get_user_environment_variable("TRAIL_SKILLS_ROOT"),
    }
    yield
    for name, value in original.items():
        set_user_environment_variable(name, value)


def test_agent_install_dry_run_openclaw_user_scope(tmp_path):
    target_root = tmp_path / "targets"
    (target_root / "openclaw" / "skills").mkdir(parents=True)
    result = run_installer("-DryRun", "-Agent", "openclaw", "-Scope", "user", "-TargetRoot", str(target_root), "-Yes")
    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["agent"] == "openclaw"
    assert plan["scope"] == "user"
    assert plan["install_cli"] is True
    assert plan["install_skills"] is True
    assert "trail-hsr" in plan["skills"]
    assert "trail-cw-prep" in plan["skills"]
    assert str(plan["skill_dir"]).endswith("openclaw\\skills")
    assert plan["path_dir"].endswith("trail\\bin")
    assert plan["path_scope"] == "user"


def test_agent_install_project_yes_requires_project_path():
    result = run_installer("-DryRun", "-Agent", "opencode", "-Scope", "project", "-Yes")
    assert result.returncode != 0
    assert "PROJECT_PATH_REQUIRED" in result.stderr


def test_agent_install_project_path_not_found_has_stable_error(tmp_path):
    missing_project = tmp_path / "missing-project"

    result = run_installer(
        "-DryRun",
        "-Agent",
        "opencode",
        "-Scope",
        "project",
        "-ProjectPath",
        str(missing_project),
        "-Yes",
    )

    assert result.returncode != 0
    assert "PROJECT_PATH_NOT_FOUND" in result.stderr


def test_agent_install_project_scope_resolves_project_skill_dir(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    result = run_installer(
        "-DryRun",
        "-Agent",
        "opencode",
        "-Scope",
        "project",
        "-ProjectPath",
        str(project),
        "-Yes",
    )

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    expected = str(project / ".agents" / "skills")
    assert plan["skill_dir"] == expected
    assert plan["targets"][0]["skill_dir"] == expected


def test_agent_install_auto_yes_multiple_targets_fails(tmp_path):
    target_root = tmp_path / "targets"
    (target_root / "opencode" / "skills").mkdir(parents=True)
    (target_root / "openclaw" / "skills").mkdir(parents=True)
    result = run_installer("-DryRun", "-Agent", "auto", "-TargetRoot", str(target_root), "-Yes")
    assert result.returncode != 0
    assert "MULTIPLE_AGENT_TARGETS" in result.stderr


def test_agent_install_rejects_conflicting_modes():
    for args in [
        ("-DryRun", "-CliOnly", "-SkillsOnly", "-Yes"),
        ("-DryRun", "-CliOnly", "-InstallSkills", "-Yes"),
        ("-DryRun", "-SkillsOnly", "-InstallCli", "-Yes"),
    ]:
        result = run_installer(*args)
        assert result.returncode != 0
        assert "INSTALL_MODE_CONFLICT" in result.stderr


def test_agent_install_auto_yes_single_target_uses_detected_target(tmp_path):
    target_root = tmp_path / "targets"
    (target_root / "gemini" / "skills").mkdir(parents=True)

    result = run_installer("-DryRun", "-Agent", "auto", "-TargetRoot", str(target_root), "-Yes")

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["agent"] == "gemini"
    assert str(plan["skill_dir"]).endswith("gemini\\skills")
    assert plan["targets"] == [{"agent": "gemini", "skill_dir": plan["skill_dir"]}]


def test_agent_install_all_returns_all_detected_targets(tmp_path):
    target_root = tmp_path / "targets"
    (target_root / "opencode" / "skills").mkdir(parents=True)
    (target_root / "openclaw" / "skills").mkdir(parents=True)

    result = run_installer("-DryRun", "-Agent", "all", "-TargetRoot", str(target_root), "-Yes")

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["agent"] == "all"
    assert plan["skill_dir"] is None
    assert plan["targets"] == [
        {"agent": "opencode", "skill_dir": str(target_root / "opencode" / "skills")},
        {"agent": "openclaw", "skill_dir": str(target_root / "openclaw" / "skills")},
    ]


def test_agent_install_skill_dir_overrides_targets(tmp_path):
    skill_dir = tmp_path / "custom-skills"

    result = run_installer("-DryRun", "-Agent", "all", "-SkillDir", str(skill_dir), "-Yes")

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["skill_dir"] == str(skill_dir)
    assert plan["targets"] == [{"agent": "all", "skill_dir": str(skill_dir)}]


def test_agent_install_non_dry_run_is_not_implemented():
    result = run_installer("-Agent", "opencode", "-Yes", "-SkipUserEnvironmentForTest")

    assert result.returncode != 0
    assert "REPO_SLUG_REQUIRED" in result.stderr


def make_release_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    release = tmp_path / "release"
    package = tmp_path / "package"
    bin_dir = package / "trail" / "bin"
    skills_dir = package / "skills"
    bin_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    (bin_dir / "trail.exe").write_bytes(Path(os.environ["COMSPEC"]).read_bytes())
    (bin_dir / "traild.exe").write_bytes(Path(os.environ["COMSPEC"]).read_bytes())
    for name in [
        "trail-hsr",
        "trail-hsr-advanced",
        "trail-cw-entry",
        "trail-cw-guide",
        "trail-cw-portal",
        "trail-cw-prep",
    ]:
        skill = skills_dir / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (skills_dir / "registry").mkdir()
    (skills_dir / "registry" / "workflow-handoffs.yaml").write_text("commands: {}\n", encoding="utf-8")
    (skills_dir / "shared").mkdir()
    (skills_dir / "shared" / "escalation-contract.md").write_text("shared\n", encoding="utf-8")
    release.mkdir()
    zip_path = release / "trail-cli-windows-x64-v0.1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    return release, package, "0.1.0"


def test_agent_install_non_dry_run_installs_cli_and_full_bundle(tmp_path):
    release, _package, version = make_release_fixture(tmp_path)
    install_dir = tmp_path / "TrailCLI"
    skill_dir = tmp_path / "agent-skills"

    result = run_installer(
        "-Agent",
        "openclaw",
        "-Scope",
        "user",
        "-CliVersion",
        version,
        "-ReleaseDir",
        str(release),
        "-InstallDir",
        str(install_dir),
        "-SkillDir",
        str(skill_dir),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["ok"] is True
    assert plan["install_dir"] == str(install_dir)
    assert plan["skill_dir"] == str(skill_dir)
    assert plan["trail_skills_root"] == str(install_dir / "skills")
    assert "trail-hsr" in plan["skills"]
    assert (install_dir / "trail" / "bin" / "trail.exe").exists()
    assert (install_dir / "trail" / "bin" / "traild.exe").exists()
    assert (install_dir / "skills" / "trail-hsr" / "SKILL.md").exists()
    assert (install_dir / "skills" / "trail-cw-prep" / "SKILL.md").exists()
    assert (install_dir / "skills" / "registry" / "workflow-handoffs.yaml").exists()
    assert (install_dir / "skills" / "shared" / "escalation-contract.md").exists()
    assert (skill_dir / "trail-hsr" / "SKILL.md").exists()
    assert (skill_dir / "trail-cw-prep" / "SKILL.md").exists()
    assert (skill_dir / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()
    assert "打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁" in result.stdout


def test_agent_install_cli_only_success_page_does_not_claim_agent_handoff(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)

    result = run_installer(
        "-CliOnly",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "TrailCLI"),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert "仅安装 CLI，未安装 skills；如需 Agent 接管，请再运行 skills 安装或使用 AGENT_INSTALL.md" in result.stdout
    assert "打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁" not in result.stdout


def test_agent_install_skills_only_success_page_warns_about_cli_path(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)

    result = run_installer(
        "-SkillsOnly",
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "TrailCLI"),
        "-SkillDir",
        str(tmp_path / "agent-skills"),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert "仅安装 skills，请确认 trail.exe 已在 PATH 或让 Agent 使用完整路径" in result.stdout
    assert "打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁" not in result.stdout


def test_agent_install_skip_user_environment_for_test_keeps_user_env(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    original_path = get_user_environment_variable("Path")
    original_skills_root = get_user_environment_variable("TRAIL_SKILLS_ROOT")
    install_dir = tmp_path / "TrailCLI"

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(install_dir),
        "-SkillDir",
        str(tmp_path / "agent-skills"),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["trail_skills_root"] == str(install_dir / "skills")
    assert get_user_environment_variable("Path") == original_path
    assert get_user_environment_variable("TRAIL_SKILLS_ROOT") == original_skills_root


def test_agent_install_stops_on_checksum_mismatch(tmp_path):
    release, _package, version = make_release_fixture(tmp_path)
    (release / "SHA256SUMS.txt").write_text(
        "0" * 64 + "  trail-cli-windows-x64-v0.1.0.zip\n",
        encoding="ascii",
    )

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-ReleaseDir",
        str(release),
        "-InstallDir",
        str(tmp_path / "install"),
        "-SkillDir",
        str(tmp_path / "skills"),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode != 0
    assert "CHECKSUM_MISMATCH" in result.stderr
    assert not (tmp_path / "install" / "trail" / "bin" / "trail.exe").exists()


def test_agent_install_package_root_uses_current_extract_without_zip(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    install_dir = tmp_path / "TrailCLI"
    skill_dir = tmp_path / "agent-skills"

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(install_dir),
        "-SkillDir",
        str(skill_dir),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "trail" / "bin" / "trail.exe").exists()
    assert (install_dir / "skills" / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "trail-cw-guide" / "SKILL.md").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()


def test_agent_install_rejects_non_bundled_skills_ref(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "install"),
        "-SkillDir",
        str(tmp_path / "skills"),
        "-SkillsRef",
        "main",
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode != 0
    assert "SKILLS_REF_UNSUPPORTED" in result.stderr


def test_agent_install_rejects_existing_trail_owned_target_without_force(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    install_dir = tmp_path / "TrailCLI"
    skill_dir = tmp_path / "agent-skills"
    existing = skill_dir / "trail-hsr" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("user content\n", encoding="utf-8")

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(install_dir),
        "-SkillDir",
        str(skill_dir),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode != 0
    assert "TARGET_EXISTS" in result.stderr
    assert existing.read_text(encoding="utf-8") == "user content\n"
    assert not (install_dir / "skills" / "trail-hsr" / "SKILL.md").exists()


def test_agent_install_merges_existing_support_dirs_without_force(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    skill_dir = tmp_path / "agent-skills"
    registry_custom = skill_dir / "registry" / "custom.yaml"
    shared_custom = skill_dir / "shared" / "custom.md"
    registry_custom.parent.mkdir(parents=True)
    shared_custom.parent.mkdir(parents=True)
    registry_custom.write_text("custom registry\n", encoding="utf-8")
    shared_custom.write_text("custom shared\n", encoding="utf-8")

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "TrailCLI"),
        "-SkillDir",
        str(skill_dir),
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert registry_custom.read_text(encoding="utf-8") == "custom registry\n"
    assert shared_custom.read_text(encoding="utf-8") == "custom shared\n"
    assert (skill_dir / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()


def test_agent_install_force_overwrites_existing_trail_owned_target(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    skill_dir = tmp_path / "agent-skills"
    existing = skill_dir / "trail-hsr" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old content\n", encoding="utf-8")

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "TrailCLI"),
        "-SkillDir",
        str(skill_dir),
        "-Force",
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert "name: trail-hsr" in existing.read_text(encoding="utf-8")
    assert (skill_dir / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()


def test_agent_install_force_merges_existing_support_dirs(tmp_path):
    _release, package, version = make_release_fixture(tmp_path)
    skill_dir = tmp_path / "agent-skills"
    registry_custom = skill_dir / "registry" / "custom.yaml"
    shared_custom = skill_dir / "shared" / "custom.md"
    registry_custom.parent.mkdir(parents=True)
    shared_custom.parent.mkdir(parents=True)
    registry_custom.write_text("custom registry\n", encoding="utf-8")
    shared_custom.write_text("custom shared\n", encoding="utf-8")

    result = run_installer(
        "-Agent",
        "opencode",
        "-CliVersion",
        version,
        "-PackageRoot",
        str(package),
        "-InstallDir",
        str(tmp_path / "TrailCLI"),
        "-SkillDir",
        str(skill_dir),
        "-Force",
        "-Yes",
        "-SkipCliVerifyForTest",
        "-SkipUserEnvironmentForTest",
    )

    assert result.returncode == 0, result.stderr
    assert registry_custom.read_text(encoding="utf-8") == "custom registry\n"
    assert shared_custom.read_text(encoding="utf-8") == "custom shared\n"
    assert (skill_dir / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()


def test_user_installer_agent_docs_and_skill_install_notes_exist():
    cmd_text = (ROOT / "scripts" / "install-trail.cmd").read_text(encoding="utf-8")
    agent_doc = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    hsr_skill = (ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    cw_entry = (ROOT / "skills" / "trail-cw-entry" / "SKILL.md").read_text(encoding="utf-8")
    installer = SCRIPT.read_text(encoding="utf-8")

    assert "scripts\\agent-install.ps1" in cmd_text
    assert "PackageRoot" in cmd_text
    assert "Trail 安装失败" in cmd_text
    assert "Trail 安装完成" in cmd_text
    assert "INSTALLER_NOT_FOUND" in cmd_text
    assert "错误码=INSTALLER_NOT_FOUND" in cmd_text
    assert "日志路径=" in cmd_text
    assert "%LOCALAPPDATA%" in cmd_text
    assert "TrailCLI\\logs" in cmd_text
    assert "install-" in cmd_text
    assert "echo code=INSTALLER_NOT_FOUND" in cmd_text

    for expected in [
        "OpenClaw",
        "-Agent openclaw",
        "CHECKSUM_MISMATCH",
        "-DryRun",
        "PROJECT_PATH_REQUIRED",
        "MULTIPLE_AGENT_TARGETS",
        "-PackageRoot",
        "-SkillDir",
    ]:
        assert expected in agent_doc
    assert "CliOnly" in agent_doc and "SkillsOnly" in agent_doc
    assert "风险" in agent_doc

    assert "最新版下载" in readme
    assert "https://github.com/" in readme and "releases/latest" in readme
    assert "trail-cli-windows-x64-vX.Y.Z.zip" in readme
    assert "AGENT_INSTALL.md" in readme

    assert "complete Trail skill bundle" in hsr_skill
    assert "complete Trail skill bundle" in cw_entry

    for expected in [
        "自动检测并安装到已发现的 AI 工具",
        "OpenClaw",
        "自定义 skills 目录",
        "仅安装 CLI",
        "仅安装 skills",
        "输入 yes 确认",
    ]:
        assert expected in installer

    install_docs = "\n".join([agent_doc, readme, hsr_skill, cw_entry])
    for placeholder in ["owner/repo", "actual-owner", "发布仓库owner"]:
        assert placeholder not in install_docs
