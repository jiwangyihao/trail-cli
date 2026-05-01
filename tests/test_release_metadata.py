from __future__ import annotations

from pathlib import Path
import subprocess
import tomllib

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def extract_powershell_function(script_text: str, name: str) -> str:
    start = script_text.index(f"function {name}")
    body_start = script_text.index("{", start)
    depth = 0
    for index in range(body_start, len(script_text)):
        char = script_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script_text[start : index + 1]
    raise AssertionError(f"PowerShell function not closed: {name}")


def test_project_declares_gpl_license_and_license_files():
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(pyproject_text)
    project = data["project"]

    assert project["license"] == "GPL-3.0-only"
    assert "LICENSE" in project["license-files"]
    assert "THIRD_PARTY_NOTICES*" in project["license-files"]
    assert 'url = "https://pypi.org/simple"' in pyproject_text
    assert "default = true" in pyproject_text


def test_uv_lock_uses_official_pypi_index():
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert "https://pypi.org/simple" in lock_text
    assert "mirrors.tuna" not in lock_text


def test_license_and_notices_exist_for_release_assets():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    notices_text = (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "GNU GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 29 June 2007" in license_text
    assert "Trail CLI third-party notices" in notices_text
    assert "Package:" in notices_text
    assert "GPL-3.0-only" in readme
    assert "StarRailAssistant" in readme
    assert "https://github.com/Shasnow/StarRailAssistant" in readme


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
        "importlib_metadata",
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
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "trail-cli-windows-x64-v$Version.zip" in build_script
    assert "$Version = $Version.TrimStart('v')" in build_script
    assert "trail\\bin" in build_script
    assert "SHA256SUMS.txt" in build_script
    assert "agent-install.ps1" in build_script
    assert "generate-third-party-notices.py" in build_script
    assert "$normalized -eq 'trail-cli'" in build_script
    assert "if ($normalized -eq 'trail-cli') { return }" in build_script
    assert "if ($normalized -eq 'trail-cli') { continue }" not in build_script
    assert "uv sync --locked --all-groups" in workflow
    assert "windows-latest" in workflow
    assert "trail.exe selfcheck release" in workflow
    assert "body_path" in workflow
    assert "project.version" in workflow
    assert "prerelease: true" in workflow
    assert "make_latest: false" in workflow
    assert "overwrite_files: true" in workflow
    assert "fail_on_unmatched_files: true" in workflow
    assert "releases/download/v$project/agent-install.ps1" in workflow
    assert "-CliVersion $project" in workflow
    assert "releases/latest/download" not in workflow
    assert "onnxruntime" in spec
    assert "windows_capture" in spec
    assert "packaging/trail_cli_entry.py" in spec
    assert "packaging/traild_entry.py" in spec
    assert "Path.cwd().resolve()" in spec
    assert "pathex=[str(ROOT)]" in spec
    assert "uv.lock" not in gitignore.splitlines()


def test_agent_installer_latest_release_selection_and_pagination():
    installer = (ROOT / "scripts" / "agent-install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(
        extract_powershell_function(installer, name)
        for name in [
            "Is-Blank",
            "Resolve-VersionTag",
            "Get-AssetName",
            "Select-LatestReleaseTag",
            "Get-NextReleaseApiUrl",
            "Resolve-LatestTag",
        ]
    )
    command = functions + r'''
$releases = @(
  [pscustomobject]@{
    tag_name = 'v9.9.9'
    draft = $true
    published_at = '2030-01-01T00:00:00Z'
    assets = @([pscustomobject]@{ name = 'trail-cli-windows-x64-v9.9.9.zip' })
  },
  [pscustomobject]@{
    tag_name = 'v0.11.0'
    draft = $false
    published_at = '2026-02-01T00:00:00Z'
    assets = @([pscustomobject]@{ name = 'notes.txt' })
  },
  [pscustomobject]@{
    tag_name = 'v0.9.0'
    draft = $false
    prerelease = $false
    published_at = '2026-01-01T00:00:00Z'
    assets = @([pscustomobject]@{ name = 'trail-cli-windows-x64-v0.9.0.zip' })
  },
  [pscustomobject]@{
    tag_name = 'v0.10.0'
    draft = $false
    prerelease = $true
    published_at = '2026-01-15T00:00:00Z'
    assets = @([pscustomobject]@{ name = 'trail-cli-windows-x64-v0.10.0.zip' })
  }
)
$result = Select-LatestReleaseTag $releases
if ($result -ne 'v0.10.0') { throw "expected v0.10.0, got $result" }

$script:requestedUris = @()
function Invoke-WebRequest([string]$Uri, [switch]$UseBasicParsing) {
  $script:requestedUris += $Uri
  if ($script:requestedUris.Count -eq 1) {
    return [pscustomobject]@{
      Content = '[{"tag_name":"v0.11.0","draft":false,"published_at":"2026-02-01T00:00:00Z","assets":[{"name":"notes.txt"}]}]'
      Headers = @{ Link = '<https://api.github.com/repos/owner/repo/releases?page=2>; rel="next"' }
    }
  }
  return [pscustomobject]@{
    Content = '[{"tag_name":"v0.10.0","draft":false,"prerelease":true,"published_at":"2026-01-15T00:00:00Z","assets":[{"name":"trail-cli-windows-x64-v0.10.0.zip"}]}]'
    Headers = @{}
  }
}
function Fail([string]$Code, [string]$Message) { throw "$Code $Message" }

$result = Resolve-LatestTag 'owner/repo'
if ($result -ne 'v0.10.0') { throw "expected v0.10.0, got $result" }
if ($script:requestedUris.Count -ne 2) { throw "expected 2 requests, got $($script:requestedUris.Count)" }
if ($script:requestedUris[1] -ne 'https://api.github.com/repos/owner/repo/releases?page=2') {
  throw "unexpected next uri $($script:requestedUris[1])"
}
'''

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_release_selfcheck_command_runs_and_stays_hidden(monkeypatch):
    from trail.cli import _selfcheck_release, app

    monkeypatch.setenv("TRAIL_SKILLS_ROOT", str(ROOT / "skills"))
    runner = CliRunner()

    selfcheck_commands = [command for command in app.registered_commands if command.name == "selfcheck"]
    assert len(selfcheck_commands) == 1
    assert selfcheck_commands[0].hidden is True

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
