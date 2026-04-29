# Trail CLI Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Trail CLI 具备面向普通 skill 用户、Agent 自动安装和 GitHub Release 二进制分发的发布基础。

**Architecture:** 发布能力分成四个边界：文档与许可证、Agent/用户安装器、frozen runtime 兼容、构建发布流水线。安装器只负责编排下载、校验、复制 CLI 与完整 skill bundle；Python runtime 只暴露 frozen 路径解析与 daemon 启动兼容；CI 负责测试、构建、hash 与 release artifacts。

**Tech Stack:** Python 3.12、Typer、pytest、PowerShell 5+、Windows cmd、PyInstaller、hatchling/build、GitHub Actions、MPL-2.0。

---

## 文件结构

- Create `LICENSE`：Mozilla Public License 2.0 正文。
- Create `THIRD_PARTY_NOTICES.txt`：首版第三方许可证占位清单，后续构建任务可生成/扩充。
- Create `scripts/generate-third-party-notices.py`：从 installed distributions 生成第三方 notices。
- Modify `pyproject.toml`：加入 `license`、`license-files`、构建依赖。
- Modify `uv.lock`：运行 `uv lock` 后提交锁文件。
- Create `AGENT_INSTALL.md`：Agent 可读安装说明、参数矩阵、错误码、验证、更新、卸载。
- Modify `README.md`：普通用户首屏、Agent 安装、许可证、开发者说明、输出协议下沉。
- Modify `skills/trail-hsr/SKILL.md` and scene entry docs：补充发布后安装/使用入口，保持 README 与 skills 同步。
- Create `scripts/agent-install.ps1`：无交互/菜单安装器，支持 dry-run、目标 agent、scope、OpenClaw、hash 校验。
- Create `scripts/install-trail.cmd`：普通用户双击入口，调用 PowerShell 安装器。
- Create `scripts/build-windows.ps1`：本地/CI Windows 构建脚本，输出 versioned zip、hash、独立 installer asset。
- Create `trail.spec` or `packaging/trail.spec`：PyInstaller spec，收集 CLI、daemon、skills、assets、DLL、metadata。
- Modify `trail/daemon/bootstrap.py`：frozen 环境下 scheduled task 直接启动 `traild.exe`。
- Modify `trail/output/rendering.py`：handoff registry 支持 `TRAIL_SKILLS_ROOT` 与 exe root fallback。
- Modify `trail/cli.py`：新增隐藏诊断命令 `trail selfcheck release`，仅供 release smoke 验证 frozen artifact 中的 assets 与 handoff registry。
- Create/Modify tests: `tests/test_release_installer.py`、`tests/test_release_runtime.py`、`tests/test_release_metadata.py`、existing renderer/daemon tests as needed。
- Create `.github/workflows/release.yml`：测试、wheel/sdist、Windows zip、独立 installer、hash artifact。

## Task 1: 许可证与 package 元数据

**Files:**
- Create: `LICENSE`
- Create: `THIRD_PARTY_NOTICES.txt`
- Create: `scripts/generate-third-party-notices.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_release_metadata.py`

- [ ] **Step 1: 写元数据红灯测试**

Create `tests/test_release_metadata.py`:

```python
from __future__ import annotations

from pathlib import Path
import tomllib


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
    generator = (ROOT / "scripts" / "generate-third-party-notices.py").read_text(encoding="utf-8")
    assert "importlib.metadata" in generator
    assert "THIRD_PARTY_NOTICES.txt" in generator
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_metadata.py -q --basetemp .trail/pytest-tmp/release-metadata-red -p no:cacheprovider`

Expected: FAIL，缺少 `LICENSE`、`THIRD_PARTY_NOTICES.txt` 或 `project.license`。

- [ ] **Step 3: 添加许可证与元数据**

Add `LICENSE` with the official MPL-2.0 text from Mozilla. Add `THIRD_PARTY_NOTICES.txt`:

```text
Trail CLI third-party notices

This file lists third-party packages bundled or redistributed by Trail CLI release artifacts.
The release build must keep this file in the root of the Windows zip and include it in wheel/sdist license files.

Package: typer
Version: generated during release build
License: generated during release build
Notice: generated during release build

Package: pillow
Version: generated during release build
License: generated during release build
Notice: generated during release build
```

Create `scripts/generate-third-party-notices.py`:

```python
from __future__ import annotations

from importlib import metadata as importlib_metadata
from pathlib import Path

RUNTIME_DISTS = {
    "typer", "click", "rich", "pillow", "pyautogui", "pygetwindow", "pyscreeze", "pyyaml",
    "pywin32", "onnxruntime-directml", "onnxruntime", "rapidocr-onnxruntime",
    "windows-capture", "rectangle-packer", "numpy", "opencv-python", "protobuf", "flatbuffers", "sympy",
    "markdown-it-py", "mdurl", "pygments", "shellingham", "typing-extensions", "colorama",
}


def main() -> None:
    lines = ["Trail CLI third-party notices", ""]
    # Release builds run this after dependency sync and before PyInstaller; implementation
    # must keep the package set aligned with PyInstaller collected distributions.
    for dist in sorted(importlib_metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        package = dist.metadata.get("Name") or "UNKNOWN"
        if normalize_dist_name(package) not in {normalize_dist_name(name) for name in RUNTIME_DISTS}:
            continue
        meta = dist.metadata
        license_value = meta.get('License-Expression') or meta.get('License') or ', '.join(meta.get_all('Classifier') or [])
        if not license_value or license_value == 'UNKNOWN':
            raise SystemExit(f"missing license metadata for notices: {package}")
        files = [str(file) for file in (dist.files or []) if any(token in str(file).upper() for token in ['LICENSE', 'NOTICE', 'COPYING'])]
        if not files:
            raise SystemExit(f"missing license file for notices: {package}")
        lines.extend([
            f"Package: {package}",
            f"Version: {dist.version}",
            f"License: {license_value}",
            f"License-Files: {'; '.join(files)}",
            f"Notice: {meta.get('Summary', 'UNKNOWN')}",
            "",
        ])
    Path("THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")


def normalize_dist_name(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


if __name__ == "__main__":
    main()
```

After PyInstaller finishes, add a build check that every `*.dist-info` directory copied under `dist\trail` has a package name present in `THIRD_PARTY_NOTICES.txt`; fail the build if a bundled distribution is missing from notices. This keeps the explicit runtime allowlist honest without including dev-only packages.

Modify `pyproject.toml`:

```toml
[project]
name = "trail-cli"
version = "0.1.0"
description = "Agent-friendly HSR automation CLI"
readme = "README.md"
requires-python = ">=3.12"
license = "MPL-2.0"
license-files = ["LICENSE", "THIRD_PARTY_NOTICES*"]
```

Extend dev dependencies:

```toml
[dependency-groups]
dev = ["pytest>=8.0", "build>=1.2", "pyinstaller>=6.11"]
```

Run `uv lock` after editing `pyproject.toml` and include the resulting `uv.lock` change. This is required because CI uses `uv sync --locked --all-groups`.

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_release_metadata.py -q --basetemp .trail/pytest-tmp/release-metadata-green -p no:cacheprovider`

Expected: PASS。

## Task 2: Agent 安装器 dry-run 与目标解析

**Files:**
- Create: `scripts/agent-install.ps1`
- Create: `tests/test_release_installer.py`

- [ ] **Step 1: 写安装器 dry-run 红灯测试**

Create `tests/test_release_installer.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import subprocess


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


def test_agent_install_dry_run_openclaw_user_scope():
    result = run_installer("-DryRun", "-Agent", "openclaw", "-Scope", "user", "-Yes")

    assert result.returncode == 0, result.stderr
    plan = parse_plan(result.stdout)
    assert plan["agent"] == "openclaw"
    assert plan["scope"] == "user"
    assert plan["install_cli"] is True
    assert plan["install_skills"] is True
    assert "trail-hsr" in plan["skills"]
    assert "trail-cw-prep" in plan["skills"]
    assert str(plan["skill_dir"]).endswith(".agents\\skills")
    assert plan["path_dir"].endswith("trail\\bin")
    assert plan["path_scope"] == "user"


def test_agent_install_project_yes_requires_project_path():
    result = run_installer("-DryRun", "-Agent", "opencode", "-Scope", "project", "-Yes")

    assert result.returncode != 0
    assert "PROJECT_PATH_REQUIRED" in result.stderr


def test_agent_install_auto_yes_multiple_targets_fails(tmp_path):
    target_root = tmp_path / "targets"
    (target_root / "opencode" / "skills").mkdir(parents=True)
    (target_root / "openclaw" / "skills").mkdir(parents=True)
    result = run_installer("-DryRun", "-Agent", "auto", "-TargetRoot", str(target_root), "-Yes")

    assert result.returncode != 0
    assert "MULTIPLE_AGENT_TARGETS" in result.stderr


def test_agent_install_rejects_conflicting_modes():
    result = run_installer("-DryRun", "-CliOnly", "-SkillsOnly", "-Yes")

    assert result.returncode != 0
    assert "INSTALL_MODE_CONFLICT" in result.stderr

    result = run_installer("-DryRun", "-CliOnly", "-InstallSkills", "-Yes")
    assert result.returncode != 0
    assert "INSTALL_MODE_CONFLICT" in result.stderr

    result = run_installer("-DryRun", "-SkillsOnly", "-InstallCli", "-Yes")
    assert result.returncode != 0
    assert "INSTALL_MODE_CONFLICT" in result.stderr
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_installer.py -q --basetemp .trail/pytest-tmp/installer-red -p no:cacheprovider`

Expected: FAIL，`scripts/agent-install.ps1` 不存在。

- [ ] **Step 3: 实现最小 dry-run 安装器**

Create `scripts/agent-install.ps1`:

```powershell
param(
  [ValidateSet('opencode','openclaw','claude-code','copilot','cursor','gemini','all','auto')]
  [string]$Agent = 'auto',
  [ValidateSet('user','project')]
  [string]$Scope = 'user',
  [string]$ProjectPath,
  [string]$SkillDir,
  [string]$TargetRoot,
  [string]$CliVersion = 'latest',
  [string]$SkillsRef = 'bundled',
  [switch]$InstallCli,
  [switch]$InstallSkills,
  [switch]$CliOnly,
  [switch]$SkillsOnly,
  [string]$InstallDir,
  [switch]$Yes,
  [switch]$DryRun,
  [switch]$Force,
  [switch]$SkipCliVerifyForTest
)

$ErrorActionPreference = 'Stop'

function Fail($Code, $Message) {
  $logDir = Join-Path $env:LOCALAPPDATA 'TrailCLI\logs'
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
  "code=$Code message=$Message" | Set-Content $logPath -Encoding utf8
  [Console]::Error.WriteLine("错误码=$Code 日志路径=$logPath")
  [Console]::Error.WriteLine("$Code $Message")
  exit 2
}

if (($CliOnly -and $SkillsOnly) -or ($CliOnly -and $InstallSkills) -or ($SkillsOnly -and $InstallCli)) {
  Fail 'INSTALL_MODE_CONFLICT' 'Conflicting install mode flags.'
}

if ($Scope -eq 'project' -and $Yes -and [string]::IsNullOrWhiteSpace($ProjectPath)) {
  Fail 'PROJECT_PATH_REQUIRED' '-Scope project requires -ProjectPath when -Yes is set.'
}

$installCliValue = -not $SkillsOnly
$installSkillsValue = -not $CliOnly
if ($InstallCli) { $installCliValue = $true }
if ($InstallSkills) { $installSkillsValue = $true }

$bundleSkills = @(
  'trail-hsr',
  'trail-hsr-advanced',
  'trail-cw-entry',
  'trail-cw-guide',
  'trail-cw-portal',
  'trail-cw-prep'
)

function Resolve-SkillDir($AgentName, $ScopeName, $ProjectRoot, $Override) {
  if (-not [string]::IsNullOrWhiteSpace($Override)) { return $Override }
  if ($ScopeName -eq 'project') { return (Join-Path $ProjectRoot '.agents\skills') }
  return (Join-Path $env:USERPROFILE '.agents\skills')
}

function Get-DetectedTargets($Root) {
  if ([string]::IsNullOrWhiteSpace($Root)) { $Root = $env:USERPROFILE }
  $items = @()
  foreach ($name in @('opencode','openclaw','claude-code','copilot','cursor','gemini')) {
    $path = Join-Path $Root "$name\skills"
    if (Test-Path $path) { $items += [ordered]@{ agent = $name; skill_dir = $path } }
  }
  $generic = Join-Path $Root ".agents\skills"
  if (Test-Path $generic) { $items += [ordered]@{ agent = 'agent-skills'; skill_dir = $generic } }
  $claude = Join-Path $Root ".claude\skills"
  if (Test-Path $claude) { $items += [ordered]@{ agent = 'claude-code'; skill_dir = $claude } }
  $seen = @{}
  $unique = @()
  foreach ($item in $items) { if (-not $seen.ContainsKey($item.skill_dir)) { $seen[$item.skill_dir] = $true; $unique += $item } }
  return $unique
}

function Resolve-Targets($AgentName, $ScopeName, $ProjectRoot, $Override, $Root) {
  if ($Override) { return @([ordered]@{ agent = $AgentName; skill_dir = $Override }) }
  if ($AgentName -eq 'all') {
    $targets = @(Get-DetectedTargets $Root)
    if ($targets.Count -eq 0) { Fail 'NO_AGENT_TARGET_DETECTED' 'No supported Agent target was detected.' }
    return $targets
  }
  return @([ordered]@{ agent = $AgentName; skill_dir = (Resolve-SkillDir $AgentName $ScopeName $ProjectRoot $Override) })
}

$projectRoot = if ($ProjectPath) { (Resolve-Path $ProjectPath).Path } else { (Get-Location).Path }
if ($Agent -eq 'auto' -and $Yes) {
  $detected = @(Get-DetectedTargets $TargetRoot)
  if ($detected.Count -eq 0) { Fail 'NO_AGENT_TARGET_DETECTED' 'No supported Agent target was detected.' }
  if ($detected.Count -gt 1) { Fail 'MULTIPLE_AGENT_TARGETS' 'Use -Agent all or select one explicit Agent.' }
  $Agent = [string]$detected[0].agent
  $SkillDir = [string]$detected[0].skill_dir
}
$resolvedSkillDir = Resolve-SkillDir $Agent $Scope $projectRoot $SkillDir
$resolvedTargets = @(Resolve-Targets $Agent $Scope $projectRoot $SkillDir $TargetRoot)
$resolvedInstallDir = if ($InstallDir) { $InstallDir } else { Join-Path $env:LOCALAPPDATA 'TrailCLI' }
$pathDir = Join-Path $resolvedInstallDir 'trail\bin'

$plan = [ordered]@{
  agent = $Agent
  scope = $Scope
  cli_version = $CliVersion
  skills_ref = $SkillsRef
  install_cli = $installCliValue
  install_skills = $installSkillsValue
  install_dir = $resolvedInstallDir
  skill_dir = $resolvedSkillDir
  targets = $resolvedTargets
  path_dir = $pathDir
  path_scope = 'user'
  skills = $bundleSkills
  dry_run = [bool]$DryRun
}

if ($DryRun) {
  $plan | ConvertTo-Json -Depth 4 -Compress
  exit 0
}

Fail 'NOT_IMPLEMENTED' 'Non-dry-run install is implemented in a later task.'
```

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_release_installer.py -q --basetemp .trail/pytest-tmp/installer-green -p no:cacheprovider`

Expected: PASS。

## Task 3: 实际安装、hash 校验与完整 bundle 复制

**Files:**
- Modify: `scripts/agent-install.ps1`
- Modify: `tests/test_release_installer.py`

- [ ] **Step 1: 写非 dry-run 安装红灯测试**

Append to `tests/test_release_installer.py`:

```python
import hashlib
import os
import zipfile


def make_release_fixture(tmp_path: Path) -> tuple[Path, str]:
    release = tmp_path / "release"
    package = tmp_path / "package"
    bin_dir = package / "trail" / "bin"
    skills_dir = package / "skills"
    scripts_dir = package / "scripts"
    bin_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    (bin_dir / "trail.exe").write_bytes(Path(os.environ["COMSPEC"]).read_bytes())
    (bin_dir / "traild.exe").write_bytes(Path(os.environ["COMSPEC"]).read_bytes())
    for name in ["trail-hsr", "trail-hsr-advanced", "trail-cw-entry", "trail-cw-guide", "trail-cw-portal", "trail-cw-prep"]:
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
    return release, "0.1.0"


def test_agent_install_non_dry_run_installs_cli_and_full_bundle(tmp_path):
    release, version = make_release_fixture(tmp_path)
    install_dir = tmp_path / "TrailCLI"
    skill_dir = tmp_path / "agent-skills"

    result = run_installer(
        "-Agent", "openclaw",
        "-Scope", "user",
        "-CliVersion", version,
        "-ReleaseDir", str(release),
        "-InstallDir", str(install_dir),
        "-SkillDir", str(skill_dir),
        "-Yes",
        "-SkipCliVerifyForTest",
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "trail" / "bin" / "trail.exe").exists()
    assert (skill_dir / "trail-hsr" / "SKILL.md").exists()
    assert (skill_dir / "trail-cw-prep" / "SKILL.md").exists()
    assert (skill_dir / "registry" / "workflow-handoffs.yaml").exists()
    assert (skill_dir / "shared" / "escalation-contract.md").exists()


def test_agent_install_stops_on_checksum_mismatch(tmp_path):
    release, version = make_release_fixture(tmp_path)
    (release / "SHA256SUMS.txt").write_text("0" * 64 + "  trail-cli-windows-x64-v0.1.0.zip\n", encoding="ascii")

    result = run_installer(
        "-Agent", "opencode",
        "-CliVersion", version,
        "-ReleaseDir", str(release),
        "-InstallDir", str(tmp_path / "install"),
        "-SkillDir", str(tmp_path / "skills"),
        "-Yes",
        "-SkipCliVerifyForTest",
    )

    assert result.returncode != 0
    assert "CHECKSUM_MISMATCH" in result.stderr
    assert not (tmp_path / "install" / "trail" / "bin" / "trail.exe").exists()
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_installer.py::test_agent_install_non_dry_run_installs_cli_and_full_bundle tests/test_release_installer.py::test_agent_install_stops_on_checksum_mismatch -q --basetemp .trail/pytest-tmp/installer-real-red -p no:cacheprovider`

Expected: FAIL，`-ReleaseDir` 参数不存在或非 dry-run 返回 `NOT_IMPLEMENTED`。

- [ ] **Step 3: 实现本地 release 安装路径**

Update `scripts/agent-install.ps1` to add parameters:

```powershell
  [string]$ReleaseDir,
  [string]$PackageRoot,
[string]$RepoSlug
```

Replace the non-dry-run `NOT_IMPLEMENTED` block with:

```powershell
function Resolve-VersionTag($Value) {
  if ($Value -eq 'latest') { return 'latest' }
  if ($Value.StartsWith('v')) { return $Value }
  return "v$Value"
}

function Get-AssetName($Value) {
  $tag = Resolve-VersionTag $Value
  if ($tag -eq 'latest') { return $null }
  return "trail-cli-windows-x64-$tag.zip"
}

function Resolve-LatestTag($RepoSlugValue) {
  if ([string]::IsNullOrWhiteSpace($RepoSlugValue)) { Fail 'REPO_SLUG_REQUIRED' 'Remote install requires -RepoSlug.' }
  $api = "https://api.github.com/repos/$RepoSlugValue/releases/latest"
  $release = iwr $api -UseB | ConvertFrom-Json
  return [string]$release.tag_name
}

function Copy-Bundle($Source, $Target) {
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  Copy-Item (Join-Path $Source '*') $Target -Recurse -Force
}

function Assert-Checksum($ZipPath, $SumsPath) {
  $name = Split-Path $ZipPath -Leaf
  $line = Get-Content $SumsPath | Where-Object { $_ -match "\s+$([regex]::Escape($name))$" } | Select-Object -First 1
  if (-not $line) { Fail 'CHECKSUM_NOT_FOUND' "No checksum for $name" }
  $expected = ($line -split '\s+')[0].ToLowerInvariant()
  $actual = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $expected) { Fail 'CHECKSUM_MISMATCH' "Checksum mismatch for $name" }
}

$resolvedCliVersion = $CliVersion
if ($resolvedCliVersion -eq 'latest' -and -not $ReleaseDir -and -not $PackageRoot) {
  $resolvedCliVersion = Resolve-LatestTag $RepoSlug
}
if ($resolvedCliVersion -eq 'latest' -and $ReleaseDir) {
  $candidate = Get-ChildItem $ReleaseDir -Filter 'trail-cli-windows-x64-v*.zip' | Sort-Object Name -Descending | Select-Object -First 1
  if (-not $candidate) { Fail 'RELEASE_ASSET_NOT_FOUND' 'No versioned Trail zip found in ReleaseDir.' }
  $assetName = $candidate.Name
} else {
  $assetName = Get-AssetName $resolvedCliVersion
}
if (-not $PackageRoot -and [string]::IsNullOrWhiteSpace($assetName)) {
  Fail 'VERSION_ASSET_INVALID' "Cannot resolve asset for $CliVersion"
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("trail-install-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
if ($PackageRoot) {
  $extract = (Resolve-Path $PackageRoot).Path
} elseif ($ReleaseDir) {
  $zip = Join-Path $ReleaseDir $assetName
  $sums = Join-Path $ReleaseDir 'SHA256SUMS.txt'
} else {
  $tag = Resolve-VersionTag $resolvedCliVersion
  $base = if ($tag -eq 'latest') { "https://github.com/$RepoSlug/releases/latest/download" } else { "https://github.com/$RepoSlug/releases/download/$tag" }
  $zip = Join-Path $work $assetName
  $sums = Join-Path $work 'SHA256SUMS.txt'
  iwr "$base/$assetName" -UseB -OutFile $zip
  iwr "$base/SHA256SUMS.txt" -UseB -OutFile $sums
}
if (-not $PackageRoot) {
  Assert-Checksum $zip $sums
  $extract = Join-Path $work 'extract'
  Expand-Archive -Path $zip -DestinationPath $extract -Force
}
if ($SkillsRef -ne 'bundled') { Fail 'SKILLS_REF_UNSUPPORTED' 'This release plan only supports -SkillsRef bundled.' }
if ($installCliValue) { Copy-Bundle (Join-Path $extract 'trail') (Join-Path $resolvedInstallDir 'trail') }
if ($installSkillsValue) { Copy-Bundle (Join-Path $extract 'skills') (Join-Path $resolvedInstallDir 'skills') }
if ($installSkillsValue) { foreach ($target in $resolvedTargets) { Copy-Bundle (Join-Path $extract 'skills') ([string]$target.skill_dir) } }
$envRoot = Join-Path $resolvedInstallDir 'skills'
if ($installSkillsValue) { [Environment]::SetEnvironmentVariable('TRAIL_SKILLS_ROOT', $envRoot, 'User') }
$binPath = Join-Path $resolvedInstallDir 'trail\bin'
if ($installCliValue) {
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($userPath -notlike "*$binPath*") { [Environment]::SetEnvironmentVariable('Path', "$userPath;$binPath", 'User') }
}
$trailExe = Join-Path $binPath 'trail.exe'
if ($installCliValue -and -not (Test-Path $trailExe)) { Fail 'CLI_VERIFY_FAILED' 'trail.exe was not installed.' }
if ($installCliValue -and -not $SkipCliVerifyForTest) {
  $versionResult = & $trailExe version 2>&1
  if ($LASTEXITCODE -ne 0) { Fail 'CLI_VERIFY_FAILED' "trail.exe version failed: $versionResult" }
}
$result = [ordered]@{ ok = $true; install_dir = $resolvedInstallDir; skill_dir = $resolvedSkillDir; targets = $resolvedTargets; trail_skills_root = $envRoot; skills = $bundleSkills }
$result | ConvertTo-Json -Depth 4 -Compress
Write-Host "Trail 安装位置: $resolvedInstallDir"
Write-Host "skills 安装位置: $resolvedSkillDir"
Write-Host "下一步：打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁"
exit 0
```

- [ ] **Step 4: 运行绿灯测试**

Run: `uv run pytest tests/test_release_installer.py::test_agent_install_non_dry_run_installs_cli_and_full_bundle tests/test_release_installer.py::test_agent_install_stops_on_checksum_mismatch -q --basetemp .trail/pytest-tmp/installer-real-green -p no:cacheprovider`

Expected: PASS。

## Task 4: 普通用户 cmd 入口与 Agent 安装文档

**Files:**
- Create: `scripts/install-trail.cmd`
- Create: `AGENT_INSTALL.md`
- Modify: `README.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Test: `tests/test_release_installer.py`

- [ ] **Step 1: 写文档与 cmd 红灯测试**

Append to `tests/test_release_installer.py`:

```python
def test_user_installer_and_agent_doc_exist():
    cmd_text = (ROOT / "scripts" / "install-trail.cmd").read_text(encoding="utf-8")
    agent_doc = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    hsr_skill = (ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    cw_entry = (ROOT / "skills" / "trail-cw-entry" / "SKILL.md").read_text(encoding="utf-8")

    assert "scripts\\agent-install.ps1" in cmd_text
    assert "OpenClaw" in agent_doc
    assert "-Agent openclaw" in agent_doc
    assert "CHECKSUM_MISMATCH" in agent_doc
    assert "trail-cli-windows-x64-vX.Y.Z.zip" in readme
    assert "releases/latest" in readme
    assert "最新版下载" in readme
    assert "Trail skill bundle" in hsr_skill
    assert "Trail skill bundle" in cw_entry
    assert "自动检测并安装到已发现的 AI 工具" in agent_doc or "自动检测并安装到已发现的 AI 工具" in cmd_text
    assert "仅安装 CLI" in agent_doc or "仅安装 CLI" in cmd_text
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_installer.py::test_user_installer_and_agent_doc_exist -q --basetemp .trail/pytest-tmp/installer-docs-red -p no:cacheprovider`

Expected: FAIL，缺少 `scripts/install-trail.cmd` 或 `AGENT_INSTALL.md`。

- [ ] **Step 3: 创建 cmd 入口**

Create `scripts/install-trail.cmd`:

```bat
@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\agent-install.ps1" -PackageRoot "%SCRIPT_DIR%" 
if errorlevel 1 (
  echo.
  echo Trail 安装失败。请把上面的错误码和日志路径复制给 Agent。
  pause
  exit /b 1
)
echo.
echo Trail 安装完成。请打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁
pause
```

Update `scripts/agent-install.ps1` so no-`-Yes` mode shows a Chinese menu before installing:

```powershell
if (-not $Yes -and -not $DryRun) {
  Write-Host 'Trail 安装向导'
  Write-Host '1. 自动检测并安装到已发现的 AI 工具（推荐）'
  Write-Host '2. OpenCode'
  Write-Host '3. OpenClaw'
  Write-Host '4. Claude Code'
  Write-Host '5. GitHub Copilot'
  Write-Host '6. Cursor'
  Write-Host '7. Gemini'
  Write-Host '8. 全部检测到的环境'
  Write-Host '9. 自定义 skills 目录，例如 C:\Users\你\.agents\skills'
  Write-Host '10. 高级：仅安装 CLI'
  Write-Host '11. 高级：仅安装 skills'
  $choice = Read-Host '请选择'
  if ($choice -eq '2') { $Agent = 'opencode' }
  elseif ($choice -eq '3') { $Agent = 'openclaw' }
  elseif ($choice -eq '4') { $Agent = 'claude-code'; $SkillDir = Read-Host '请输入 Claude Code skills 目录完整路径'; $confirm = Read-Host "将安装到 $SkillDir，输入 yes 确认"; if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled custom skill dir.' } }
  elseif ($choice -eq '5') { $Agent = 'copilot'; $SkillDir = Read-Host '请输入 GitHub Copilot skills 目录完整路径'; $confirm = Read-Host "将安装到 $SkillDir，输入 yes 确认"; if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled custom skill dir.' } }
  elseif ($choice -eq '6') { $Agent = 'cursor'; $SkillDir = Read-Host '请输入 Cursor skills 目录完整路径'; $confirm = Read-Host "将安装到 $SkillDir，输入 yes 确认"; if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled custom skill dir.' } }
  elseif ($choice -eq '7') { $Agent = 'gemini'; $SkillDir = Read-Host '请输入 Gemini skills 目录完整路径'; $confirm = Read-Host "将安装到 $SkillDir，输入 yes 确认"; if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled custom skill dir.' } }
  elseif ($choice -eq '8') { $Agent = 'all' }
  elseif ($choice -eq '9') {
    $SkillDir = Read-Host '请输入 skills 目录完整路径'
    $confirm = Read-Host "将安装到 $SkillDir，输入 yes 确认"
    if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled custom skill dir.' }
  }
  elseif ($choice -eq '10') { Write-Host '风险：仅安装 CLI 不会安装 skills。'; $CliOnly = $true }
  elseif ($choice -eq '11') { Write-Host '风险：仅安装 skills 要求 trail.exe 已可执行。'; $SkillsOnly = $true }
  elseif ($choice -eq '1') {
    $detected = @(Get-DetectedTargets $TargetRoot)
    if ($detected.Count -eq 0) { Write-Host '未检测到 AI 工具目录，请选择自定义 skills 目录。'; Fail 'NO_AGENT_TARGET_DETECTED' 'No supported Agent target was detected.' }
    Write-Host '将安装到以下目录：'
    foreach ($target in $detected) { Write-Host "- $($target.agent): $($target.skill_dir)" }
    $confirm = Read-Host '输入 yes 确认'
    if ($confirm -ne 'yes') { Fail 'USER_CANCELLED' 'User cancelled detected targets.' }
    if ($detected.Count -eq 1) { $Agent = [string]$detected[0].agent; $SkillDir = [string]$detected[0].skill_dir }
    else { $Agent = 'all' }
  }
}
```

The implementation may use helper functions for menu handling, but the visible menu text and advanced risk prompts must stay testable.
The success page must print `Trail 安装位置: C:\Users\Alice\AppData\Local\TrailCLI`, `skills 安装位置: C:\Users\Alice\.agents\skills`, and the next step. The failure page must print the stable error code and a log path under `%LOCALAPPDATA%\TrailCLI\logs`.
Place the menu handling block before `$resolvedTargets = @(Resolve-Targets $Agent $Scope $projectRoot $SkillDir $TargetRoot)`; if implementation chooses to keep parsing earlier, it must recompute `$resolvedTargets`, `$resolvedSkillDir`, and `$pathDir` after menu selection.

- [ ] **Step 4: 创建 Agent 安装说明**

Create `AGENT_INSTALL.md` with these sections:

```markdown
# Trail CLI Agent Install

## Recommended non-interactive install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent opencode -Scope user -Yes
```

Use `-Agent openclaw` for OpenClaw. Use `-Scope project -ProjectPath <workspace>` for project-local installs. Remote release notes provide the concrete `-RepoSlug` bootstrap command for GitHub Release installs.

## Remote bootstrap

The release workflow writes the concrete repository URL into release notes. Agent instructions in source form should prefer the local release asset command above; release notes must not contain repository placeholder text.

## Dry run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -Agent openclaw -Scope user -DryRun -Yes
```

## Ecosystem installers

Use ecosystem installers only after verifying the tool preserves the complete Trail bundle, including `registry` and `shared`.

Release notes must include concrete `gh skill install` and `npx --yes skills add` commands derived from the release repository. Source docs must not contain placeholder repository names. Fallback command: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -SkillsOnly -SkillDir C:\Path\To\skills -Yes`.

## Errors

- `NO_AGENT_TARGET_DETECTED`: no supported target directory was found.
- `MULTIPLE_AGENT_TARGETS`: use `-Agent all` or a single explicit agent.
- `PROJECT_PATH_REQUIRED`: project scope requires `-ProjectPath` when `-Yes` is used.
- `INSTALL_MODE_CONFLICT`: incompatible install mode flags were provided.
- `CHECKSUM_MISMATCH`: downloaded release asset did not match `SHA256SUMS.txt`.

## Update

Run the same install command with a newer `-CliVersion` and `-Force`.

## Uninstall

Remove the Trail install directory and the Trail skill bundle directories listed by the installer output.

## Mode risks

`-CliOnly` installs no skills. `-SkillsOnly` assumes a working `trail` binary is already on PATH or that the Agent will use a full executable path.

## Supported agents

- `opencode`
- `openclaw`
- `claude-code`
- `copilot`
- `cursor`
- `gemini`
- `all`
- `auto`

## Verification

Run `trail version` after CLI install. Verify the target skill directory contains `trail-hsr`, `trail-cw-entry`, `trail-cw-guide`, `trail-cw-portal`, `trail-cw-prep`, `trail-hsr-advanced`, `registry`, and `shared`.

## Portable fallback

If PATH is not changed, tell the Agent to call the full executable path, for example `C:\path\to\trail\bin\trail.exe start`.
```
```

- [ ] **Step 5: 重写 README 首屏**

Move the existing protocol-heavy content below a new user-first introduction. The first sections should be:

```markdown
# Trail CLI

Trail CLI 是给 AI Agent 使用的《崩坏：星穹铁道》自动化工具，包含 `trail` 命令行和一组 Trail skills。

## 普通用户快速安装

1. 打开 [最新版下载](https://github.com/trail-cli/trail-cli/releases/latest)。
2. 下载 Assets 里的 `trail-cli-windows-x64-vX.Y.Z.zip`，解压到固定文件夹。
3. 双击 `安装 Trail.cmd`，按中文菜单选择 OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini 或自定义目录。

安装完成后，打开你的 AI 工具，对 Agent 说：使用 `trail-hsr` 接管星铁。

## 给 Agent / 高级用户

详见 `AGENT_INSTALL.md`。
```

Keep the existing output protocol and CW command details later in README instead of deleting them.

- [ ] **Step 6: 同步 skill 文档**

In `skills/trail-hsr/SKILL.md` and `skills/trail-cw-entry/SKILL.md`, add a short install note near the top:

```markdown
## Installation Note

- Trail CLI and the complete Trail skill bundle should be installed through the release installer or `AGENT_INSTALL.md`.
- OpenClaw is a supported target environment for the installer.
- Do not install only this single skill for normal play; handoff depends on the bundle's active public/internal skills, `registry`, and `shared` references.
```

- [ ] **Step 7: 运行绿灯测试**

Run: `uv run pytest tests/test_release_installer.py::test_user_installer_and_agent_doc_exist -q --basetemp .trail/pytest-tmp/installer-docs-green -p no:cacheprovider`

Expected: PASS。

## Task 5: Frozen daemon 与 handoff registry 路径

**Files:**
- Modify: `trail/daemon/bootstrap.py`
- Modify: `trail/output/rendering.py`
- Test: `tests/test_release_runtime.py`

- [ ] **Step 1: 写 runtime 红灯测试**

Create `tests/test_release_runtime.py`:

```python
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


def test_frozen_manifest_records_traild_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)

    manifest = bootstrap.build_manifest_payload(daemon_entrypoint=Path("traild.exe"))

    assert manifest["daemon_entrypoint"] == "traild.exe"


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
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_runtime.py -q --basetemp .trail/pytest-tmp/release-runtime-red -p no:cacheprovider`

Expected: FAIL，`workflow_handoffs_path` 不存在或 frozen launcher 仍是 Python script。

- [ ] **Step 3: 实现 frozen launcher 分支**

Modify `trail/daemon/bootstrap.py`:

```python
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _frozen_traild_executable() -> Path:
    return Path(sys.executable).with_name("traild.exe")


def write_launcher_script(daemon_home: Path) -> tuple[Path, Path]:
    if _is_frozen():
        traild = _frozen_traild_executable()
        return traild, traild
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
```

Keep the existing launcher body unchanged for non-frozen mode.

Also update `register_scheduled_task()` so frozen mode does not pass `traild.exe` as its own argument:

```python
def build_manifest_payload(*, daemon_entrypoint: Path | str) -> dict[str, str]:
    return {"daemon_entrypoint": str(daemon_entrypoint)}


def _scheduled_task_action(*, launcher: Path, launcher_python: Path) -> str:
    if _is_frozen() and launcher == launcher_python:
        return subprocess.list2cmdline([str(launcher_python)])
    return subprocess.list2cmdline([str(launcher_python), str(launcher)])


def register_scheduled_task(*, bootstrap_id: str, launcher: Path, launcher_python: Path) -> None:
    action = _scheduled_task_action(launcher=launcher, launcher_python=launcher_python)
    schtasks_args = f"/Create /TN {bootstrap_id} /TR \"{action}\" /SC ONCE /ST 00:00 /RL HIGHEST /F /IT"
    # keep the existing Start-Process schtasks.exe command body
```

The manifest should keep `daemon_entrypoint="traild.exe"` in frozen installs and `trail.daemon.server:main` in source installs, so diagnostics reveal which runtime path is active.
Update `install_bootstrap()` to pass `daemon_entrypoint="traild.exe"` when `_is_frozen()` is true and `daemon_entrypoint="trail.daemon.server:main"` otherwise.

- [ ] **Step 4: 实现 registry path helper**

Modify `trail/output/rendering.py`:

```python
import os
import sys


def workflow_handoffs_path() -> Path:
    skills_root = os.environ.get("TRAIL_SKILLS_ROOT")
    if skills_root:
        return Path(skills_root) / "registry" / "workflow-handoffs.yaml"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2] / "skills" / "registry" / "workflow-handoffs.yaml"
    return Path(__file__).resolve().parents[2] / "skills" / "registry" / "workflow-handoffs.yaml"


WORKFLOW_HANDOFFS_PATH = workflow_handoffs_path()
```

- [ ] **Step 5: 运行绿灯测试**

Run: `uv run pytest tests/test_release_runtime.py -q --basetemp .trail/pytest-tmp/release-runtime-green -p no:cacheprovider`

Expected: PASS。

## Task 6: Windows 构建脚本与 CI workflow

**Files:**
- Create: `scripts/build-windows.ps1`
- Create: `packaging/trail_cli_entry.py`
- Create: `packaging/traild_entry.py`
- Create: `packaging/trail.spec`
- Modify: `trail/cli.py`
- Create: `.github/workflows/release.yml`
- Test: `tests/test_release_metadata.py`

- [ ] **Step 1: 写构建文件红灯测试**

Append to `tests/test_release_metadata.py`:

```python
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
    assert "uv sync --locked --all-groups" in workflow
    assert "project.version" in workflow
    assert "windows-latest" in workflow
    assert "trail.exe version" in workflow
    assert "SHA256SUMS.txt" in workflow
    assert "onnxruntime" in spec
    assert "windows_capture" in spec
    assert "packaging/trail_cli_entry.py" in spec
    assert "packaging/traild_entry.py" in spec
```

- [ ] **Step 2: 运行红灯测试**

Run: `uv run pytest tests/test_release_metadata.py::test_release_build_files_define_required_assets -q --basetemp .trail/pytest-tmp/release-build-files-red -p no:cacheprovider`

Expected: FAIL，构建文件不存在。

- [ ] **Step 3: 创建 build script**

Create `scripts/build-windows.ps1`:

```powershell
param([string]$Version = '0.1.0')
$ErrorActionPreference = 'Stop'
$Version = $Version.TrimStart('v')
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$Dist = Join-Path $Root 'dist'
$Package = Join-Path $Dist "trail-cli-windows-x64-v$Version"
New-Item -ItemType Directory -Force -Path $Package | Out-Null
uv run python scripts\generate-third-party-notices.py
uv run python -m build
uv run pyinstaller (Join-Path $Root 'packaging\trail.spec') --noconfirm
Get-ChildItem (Join-Path $Root 'dist\trail') -Recurse -Directory -Filter '*.dist-info' | ForEach-Object {
  $pkg = $_.Name -replace '-[0-9].*\.dist-info$', ''
  $normalized = $pkg.ToLowerInvariant().Replace('_','-').Replace('.','-')
  $noticeNames = Select-String -Path (Join-Path $Root 'THIRD_PARTY_NOTICES.txt') -Pattern '^Package: ' | ForEach-Object { $_.Line.Substring(9).ToLowerInvariant().Replace('_','-').Replace('.','-') }
  if ($noticeNames -notcontains $normalized) { throw "missing third-party notice for bundled package $pkg" }
}
$Bin = Join-Path $Package 'trail\bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item (Join-Path $Root 'dist\trail\*') $Bin -Recurse -Force
Copy-Item (Join-Path $Root 'skills') (Join-Path $Package 'skills') -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Package 'scripts') | Out-Null
Copy-Item (Join-Path $Root 'scripts\agent-install.ps1') (Join-Path $Package 'scripts\agent-install.ps1') -Force
Copy-Item (Join-Path $Root 'scripts\install-trail.cmd') (Join-Path $Package '安装 Trail.cmd') -Force
Copy-Item (Join-Path $Root 'LICENSE') (Join-Path $Package 'LICENSE') -Force
Copy-Item (Join-Path $Root 'THIRD_PARTY_NOTICES.txt') (Join-Path $Package 'THIRD_PARTY_NOTICES.txt') -Force
$Zip = Join-Path $Dist "trail-cli-windows-x64-v$Version.zip"
Compress-Archive -Path (Join-Path $Package '*') -DestinationPath $Zip -Force
Copy-Item (Join-Path $Root 'scripts\agent-install.ps1') (Join-Path $Dist 'agent-install.ps1') -Force
Get-ChildItem $Dist -File | Where-Object { $_.Name -match 'trail-cli-windows-x64|\.whl$|\.tar\.gz$|agent-install\.ps1$' } | ForEach-Object {
  $hash = Get-FileHash $_.FullName -Algorithm SHA256
  "$($hash.Hash.ToLower())  $($_.Name)"
} | Set-Content (Join-Path $Dist 'SHA256SUMS.txt') -Encoding ascii
```

- [ ] **Step 4: 添加隐藏 release selfcheck 命令**

In `trail/cli.py`, add a hidden diagnostic Typer group/command:

```python
@app.command("selfcheck", hidden=True)
def selfcheck_release(kind: str = typer.Argument(...)) -> None:
    if kind != "release":
        raise typer.BadParameter("expected release")
    from trail.output.rendering import render_output
    from trail.runtime.resources import resolve_scene_asset

    asset = resolve_scene_asset("cw", "guide.strategy")
    if not asset.exists():
        raise typer.Exit(2)
    text = render_output("cw.enter", {"ok": True, "data": {}})
    if "handoff_skill=trail-cw-entry" not in text:
        raise typer.Exit(2)
    typer.echo("ok release selfcheck assets=1 handoff=1")
```

This command is hidden from normal help and exists only to make release artifact smoke executable without importing source modules outside the frozen exe.

- [ ] **Step 5: 创建 PyInstaller entry wrappers 与 spec skeleton**

Create `packaging/trail_cli_entry.py`:

```python
from trail.cli import app

if __name__ == "__main__":
    app()
```

Create `packaging/traild_entry.py`:

```python
from trail.daemon.server import main

if __name__ == "__main__":
    main()
```

Create `packaging/trail.spec` with explicit collection comments and real entry points:

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, copy_metadata

datas = []
binaries = []
hiddenimports = []
for package in ["onnxruntime", "rapidocr_onnxruntime", "windows_capture"]:
    collected = collect_all(package)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]
hiddenimports += ["win32api", "win32gui", "win32ui", "pythoncom", "pywintypes"]
datas += copy_metadata("trail-cli")
datas += collect_data_files("trail")

cli_analysis = Analysis(["packaging/trail_cli_entry.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
daemon_analysis = Analysis(["packaging/traild_entry.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
cli_pyz = PYZ(cli_analysis.pure)
daemon_pyz = PYZ(daemon_analysis.pure)
trail = EXE(cli_pyz, cli_analysis.scripts, [], exclude_binaries=True, name="trail")
traild = EXE(daemon_pyz, daemon_analysis.scripts, [], exclude_binaries=True, name="traild")
coll = COLLECT(trail, traild, cli_analysis.binaries, daemon_analysis.binaries, cli_analysis.datas, daemon_analysis.datas, strip=False, upx=False, name="trail")
```

- [ ] **Step 6: 创建 release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: release

on:
  workflow_dispatch:
  push:
    tags:
      - 'v*'

jobs:
  windows:
    runs-on: windows-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: uv sync --locked --all-groups
      - run: uv run pytest -q
      - shell: pwsh
        run: |
          $project = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
          if ('${{ github.ref_type }}' -eq 'tag' -and '${{ github.ref_name }}' -ne "v$project") { throw "tag does not match project.version" }
          .\scripts\build-windows.ps1 -Version $project
      - shell: pwsh
        run: |
          $zip = Get-ChildItem dist\trail-cli-windows-x64-*.zip | Select-Object -First 1
          Expand-Archive $zip.FullName -DestinationPath dist\smoke -Force
          if (-not (Test-Path dist\smoke\trail\bin\trail.exe)) { throw 'trail.exe missing' }
          if (-not (Test-Path dist\smoke\trail\bin\traild.exe)) { throw 'traild.exe missing' }
          dist\smoke\trail\bin\trail.exe version
          $sums = Get-Content dist\SHA256SUMS.txt
          foreach ($required in @($zip.Name, 'agent-install.ps1')) { if (-not ($sums -match [regex]::Escape($required))) { throw "missing checksum for $required" } }
          foreach ($asset in Get-ChildItem dist -File | Where-Object { $_.Name -match '\.(whl|gz)$' }) { if (-not ($sums -match [regex]::Escape($asset.Name))) { throw "missing checksum for $($asset.Name)" } }
          $env:TRAIL_SKILLS_ROOT = (Resolve-Path dist\smoke\skills).Path
          dist\smoke\trail\bin\trail.exe cw battle clear-in-progress --help
          dist\smoke\trail\bin\trail.exe cw enter --help
          dist\smoke\trail\bin\trail.exe daemon status --help
          dist\smoke\trail\bin\trail.exe guide config cw --help
          dist\smoke\trail\bin\trail.exe selfcheck release
          @"
          ## Agent install

          ````powershell
          `$script = Join-Path `$env:TEMP 'trail-agent-install.ps1'
          iwr 'https://github.com/${{ github.repository }}/releases/latest/download/agent-install.ps1' -UseB -OutFile `$script
          powershell -NoProfile -ExecutionPolicy Bypass -File `$script -Agent openclaw -Scope user -CliVersion latest -RepoSlug '${{ github.repository }}' -Yes
          ````
          "@ | Set-Content dist\RELEASE_NOTES.md -Encoding utf8
          Get-Content dist\SHA256SUMS.txt
      - uses: actions/upload-artifact@v4
        with:
          name: trail-cli-windows-release
          path: |
            dist\trail-cli-windows-x64-*.zip
            dist\agent-install.ps1
            dist\SHA256SUMS.txt
            dist\*.whl
            dist\*.tar.gz
      - uses: softprops/action-gh-release@v2
        if: github.ref_type == 'tag'
        with:
          files: |
            dist\trail-cli-windows-x64-*.zip
            dist\agent-install.ps1
            dist\SHA256SUMS.txt
            dist\*.whl
            dist\*.tar.gz
          generate_release_notes: true
          body_path: dist\RELEASE_NOTES.md
```

- [ ] **Step 7: 运行绿灯测试**

Run: `uv run pytest tests/test_release_metadata.py::test_release_build_files_define_required_assets -q --basetemp .trail/pytest-tmp/release-build-files-green -p no:cacheprovider`

Expected: PASS。

## Task 7: 集成验证与文档同步

**Files:**
- Modify: `README.md`
- Modify: `AGENT_INSTALL.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Test: all touched tests

- [ ] **Step 1: 运行目标测试集合**

Run: `uv run pytest tests/test_release_metadata.py tests/test_release_installer.py tests/test_release_runtime.py -q --basetemp .trail/pytest-tmp/release-targeted -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 2: 运行完整测试**

Run: `uv run pytest -q --basetemp .trail/pytest-tmp/release-full -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 3: 检查工作树**

Run: `git status --short`

Expected: 只包含本发布整理相关文件；不要修改无关用户文件。

- [ ] **Step 4: 请求 review**

Use `requesting-code-review` and dispatch 3 read-only reviewers for documentation/install, runtime/packaging, and CI/license. Fix all blocking/important findings before continuing.

- [ ] **Step 5: 提交（仅在用户明确要求时执行）**

If the user explicitly asks to commit, use Conventional Commits:

```bash
git add LICENSE THIRD_PARTY_NOTICES.txt AGENT_INSTALL.md README.md pyproject.toml uv.lock scripts packaging .github trail tests skills docs/superpowers/specs/2026-04-29-release-readiness-design.md docs/superpowers/plans/2026-04-29-release-readiness.md
git commit -m "feat(release): 完善发布安装与构建体系"
```
