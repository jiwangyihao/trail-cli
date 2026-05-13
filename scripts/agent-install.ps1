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
  [string]$ReleaseDir,
  [string]$PackageRoot,
  [string]$RepoSlug,
  [switch]$SkipCliVerifyForTest,
  [switch]$SkipUserEnvironmentForTest
)

$ErrorActionPreference = 'Stop'

$SupportedAgents = @('opencode','openclaw','claude-code','copilot','cursor','gemini')
$BundleSkills = @(
  'trail-hsr',
  'trail-hsr-advanced',
  'trail-cw-entry',
  'trail-cw-guide',
  'trail-cw-portal',
  'trail-cw-prep'
)
$BundleSupportEntries = @('registry', 'shared')

function Fail([string]$Code, [string]$Message) {
  $logDir = Join-Path (Get-LocalAppDataRoot) 'TrailCLI\logs'
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
  "code=$Code message=$Message" | Set-Content -LiteralPath $logPath -Encoding utf8
  [Console]::Error.WriteLine("error_code=$Code log_path=$logPath")
  [Console]::Error.WriteLine("$Code $Message")
  exit 2
}

function Is-Blank([string]$Value) {
  return [string]::IsNullOrWhiteSpace($Value)
}

function Get-UserRoot() {
  if (-not (Is-Blank $env:USERPROFILE)) { return $env:USERPROFILE }
  return [Environment]::GetFolderPath('UserProfile')
}

function Get-LocalAppDataRoot() {
  if (-not (Is-Blank $env:LOCALAPPDATA)) { return $env:LOCALAPPDATA }
  return (Join-Path (Get-UserRoot) 'AppData\Local')
}

function Get-ProjectRoot([string]$Value) {
  if (-not (Is-Blank $Value)) {
    if (-not (Test-Path -LiteralPath $Value)) { Fail 'PROJECT_PATH_NOT_FOUND' '-ProjectPath does not exist.' }
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Get-Location).Path
}

function Get-DefaultSkillDir([string]$ScopeName, [string]$ProjectRoot) {
  if ($ScopeName -eq 'project') { return (Join-Path $ProjectRoot '.agents\skills') }
  return (Join-Path (Get-UserRoot) '.agents\skills')
}

function Get-RootAgentSkillDir([string]$Root, [string]$AgentName) {
  if (Is-Blank $Root) { return $null }
  return (Join-Path $Root (Join-Path $AgentName 'skills'))
}

function Normalize-PathKey([string]$PathValue) {
  return $PathValue.TrimEnd('\','/').ToLowerInvariant()
}

function Add-Target($Targets, $Seen, [string]$AgentName, [string]$SkillPath) {
  $key = Normalize-PathKey $SkillPath
  if ($Seen.ContainsKey($key)) { return }
  $Seen[$key] = $true
  [void]$Targets.Add([ordered]@{ agent = $AgentName; skill_dir = $SkillPath })
}

function Get-DetectedTargets([string]$Root) {
  if (Is-Blank $Root) { $Root = Get-UserRoot }

  $targets = [System.Collections.ArrayList]::new()
  $seen = @{}

  foreach ($name in $SupportedAgents) {
    $path = Get-RootAgentSkillDir $Root $name
    if ((-not (Is-Blank $path)) -and (Test-Path -LiteralPath $path)) {
      Add-Target $targets $seen $name $path
    }
  }

  $generic = Join-Path $Root '.agents\skills'
  if (Test-Path -LiteralPath $generic) {
    Add-Target $targets $seen 'agent-skills' $generic
  }

  $claude = Join-Path $Root '.claude\skills'
  if (Test-Path -LiteralPath $claude) {
    Add-Target $targets $seen 'claude-code' $claude
  }

  return @($targets)
}

function Resolve-ExplicitTarget([string]$AgentName, [string]$ScopeName, [string]$ProjectRoot, [string]$Root) {
  $rootPath = Get-RootAgentSkillDir $Root $AgentName
  if ((-not (Is-Blank $rootPath)) -and (Test-Path -LiteralPath $rootPath)) { return $rootPath }
  return (Get-DefaultSkillDir $ScopeName $ProjectRoot)
}

function Resolve-Targets([string]$AgentName, [string]$ScopeName, [string]$ProjectRoot, [string]$OverrideSkillDir, [string]$Root) {
  if (-not (Is-Blank $OverrideSkillDir)) {
    return @([ordered]@{ agent = $AgentName; skill_dir = $OverrideSkillDir })
  }

  if ($AgentName -eq 'all') {
    $detected = @(Get-DetectedTargets $Root)
    if ($detected.Count -eq 0) { Fail 'NO_AGENT_TARGET_DETECTED' 'No supported Agent target was detected.' }
    return $detected
  }

  $skillPath = Resolve-ExplicitTarget $AgentName $ScopeName $ProjectRoot $Root
  return @([ordered]@{ agent = $AgentName; skill_dir = $skillPath })
}

function Confirm-Yes([string]$Prompt) {
  $answer = Read-Host $Prompt
  return ($answer -eq 'yes')
}

function Prompt-ConfirmedSkillDir([string]$Prompt) {
  $path = Read-Host $Prompt
  if (Is-Blank $path) { Fail 'SKILL_DIR_REQUIRED' 'A skills directory path is required.' }
  Write-Host "Target skills directory: $path"
  if (-not (Confirm-Yes "Install to $path. Type yes to confirm")) {
    Fail 'USER_CANCELLED' 'User cancelled skill directory confirmation.'
  }
  return $path
}

function Write-DetectedTargets($Targets) {
  Write-Host 'Detected AI tool skills directories:'
  foreach ($target in $Targets) {
    Write-Host "- $($target.agent): $($target.skill_dir)"
  }
}

function Use-DetectedTargetsFromMenu() {
  $detected = @(Get-DetectedTargets $TargetRoot)
  if ($detected.Count -eq 0) {
    Write-Host 'No AI tool directory was detected. Select a custom skills directory.'
    $script:SkillDir = Prompt-ConfirmedSkillDir 'Enter custom skills directory path, for example C:\Users\you\.agents\skills'
    return
  }

  Write-DetectedTargets $detected
  if ($detected.Count -gt 1) {
    if (-not (Confirm-Yes 'Install to all directories above. Type yes to confirm')) {
      Fail 'USER_CANCELLED' 'User cancelled detected target installation.'
    }
    $script:Agent = 'all'
    return
  }

  if (-not (Confirm-Yes 'Install to the directory above. Type yes to confirm')) {
    Fail 'USER_CANCELLED' 'User cancelled detected target installation.'
  }
  $script:Agent = [string]$detected[0].agent
  $script:SkillDir = [string]$detected[0].skill_dir
}

function Use-UnverifiedHostSkillDir([string]$AgentName, [string]$DisplayName) {
  $script:Agent = $AgentName
  $script:SkillDir = Prompt-ConfirmedSkillDir "Enter $DisplayName skills directory path. This host path is not verified by this installer; use the directory actually loaded by that tool"
}

function Invoke-InteractiveInstallMenu() {
  Write-Host 'Trail installer'
  Write-Host '1. Auto-detect and install to discovered AI tools (recommended)'
  Write-Host '2. OpenCode'
  Write-Host '3. OpenClaw'
  Write-Host '4. Claude Code'
  Write-Host '5. GitHub Copilot'
  Write-Host '6. Cursor'
  Write-Host '7. Gemini'
  Write-Host '8. All detected environments'
  Write-Host '9. Custom skills directory, for example C:\Users\you\.agents\skills'
  Write-Host '10. Advanced: CLI only'
  Write-Host '11. Advanced: skills only'
  $choice = Read-Host 'Select an option'

  if ($choice -eq '1') { Use-DetectedTargetsFromMenu }
  elseif ($choice -eq '2') { $script:Agent = 'opencode' }
  elseif ($choice -eq '3') { $script:Agent = 'openclaw' }
  elseif ($choice -eq '4') { Use-UnverifiedHostSkillDir 'claude-code' 'Claude Code' }
  elseif ($choice -eq '5') { Use-UnverifiedHostSkillDir 'copilot' 'GitHub Copilot' }
  elseif ($choice -eq '6') { Use-UnverifiedHostSkillDir 'cursor' 'Cursor' }
  elseif ($choice -eq '7') { Use-UnverifiedHostSkillDir 'gemini' 'Gemini' }
  elseif ($choice -eq '8') { Use-DetectedTargetsFromMenu; if ((Is-Blank $script:SkillDir) -and $script:Agent -ne 'all') { $script:Agent = 'all' } }
  elseif ($choice -eq '9') { $script:SkillDir = Prompt-ConfirmedSkillDir 'Enter custom skills directory path, for example C:\Users\you\.agents\skills' }
  elseif ($choice -eq '10') {
    Write-Host 'Risk: CLI-only install does not install the complete Trail skill bundle. Agent may not be able to take over the game.'
    if (-not (Confirm-Yes 'Type yes to confirm')) { Fail 'USER_CANCELLED' 'User cancelled CLI-only installation.' }
    $script:CliOnly = $true
  }
  elseif ($choice -eq '11') {
    Write-Host 'Risk: skills-only install requires trail.exe to already be executable. Otherwise Agent cannot call Trail CLI.'
    if (-not (Confirm-Yes 'Type yes to confirm')) { Fail 'USER_CANCELLED' 'User cancelled skills-only installation.' }
    $script:SkillsOnly = $true
  }
  else { Fail 'MENU_CHOICE_INVALID' 'Invalid menu choice.' }
}

if (-not $Yes -and -not $DryRun) {
  Invoke-InteractiveInstallMenu
}

if (($CliOnly -and $SkillsOnly) -or ($CliOnly -and $InstallSkills) -or ($SkillsOnly -and $InstallCli)) {
  Fail 'INSTALL_MODE_CONFLICT' 'Conflicting install mode flags.'
}

if ($SkillsRef -ne 'bundled') {
  Fail 'SKILLS_REF_UNSUPPORTED' 'This installer only supports bundled skills in the release zip.'
}

if ($Scope -eq 'project' -and $Yes -and (Is-Blank $ProjectPath)) {
  Fail 'PROJECT_PATH_REQUIRED' '-Scope project requires -ProjectPath when -Yes is set.'
}

$installCliValue = $true
$installSkillsValue = $true
if ($CliOnly) { $installSkillsValue = $false }
if ($SkillsOnly) { $installCliValue = $false }
if ($InstallCli) { $installCliValue = $true }
if ($InstallSkills) { $installSkillsValue = $true }

$projectRoot = Get-ProjectRoot $ProjectPath

if ($installSkillsValue -and $Agent -eq 'auto' -and $Yes -and (Is-Blank $SkillDir)) {
  $detected = @(Get-DetectedTargets $TargetRoot)
  if ($detected.Count -eq 0) { Fail 'NO_AGENT_TARGET_DETECTED' 'No supported Agent target was detected.' }
  if ($detected.Count -gt 1) { Fail 'MULTIPLE_AGENT_TARGETS' 'Use -Agent all or select one explicit Agent.' }
  $Agent = [string]$detected[0].agent
  $SkillDir = [string]$detected[0].skill_dir
}

$resolvedTargets = @()
if ($installSkillsValue) {
  $resolvedTargets = @(Resolve-Targets $Agent $Scope $projectRoot $SkillDir $TargetRoot)
}
$resolvedSkillDir = $null
if ($resolvedTargets.Count -eq 1) { $resolvedSkillDir = [string]$resolvedTargets[0].skill_dir }

$resolvedInstallDir = $InstallDir
if (Is-Blank $resolvedInstallDir) { $resolvedInstallDir = Join-Path (Get-LocalAppDataRoot) 'TrailCLI' }
$pathDir = Join-Path $resolvedInstallDir 'trail\bin'

$plan = [ordered]@{
  agent = $Agent
  scope = $Scope
  cli_version = $CliVersion
  skills_ref = $SkillsRef
  install_cli = [bool]$installCliValue
  install_skills = [bool]$installSkillsValue
  install_dir = $resolvedInstallDir
  skill_dir = $resolvedSkillDir
  targets = @($resolvedTargets)
  path_dir = $pathDir
  path_scope = 'user'
  skills = $BundleSkills
  dry_run = [bool]$DryRun
}

if ($DryRun) {
  $plan | ConvertTo-Json -Depth 4 -Compress
  exit 0
}

function Resolve-VersionTag([string]$Value) {
  if ($Value -eq 'latest') { return 'latest' }
  if ($Value.StartsWith('v')) { return $Value }
  return "v$Value"
}

function Get-AssetName([string]$Value) {
  $tag = Resolve-VersionTag $Value
  if ($tag -eq 'latest') { return $null }
  return "trail-cli-windows-x64-$tag.zip"
}

function Select-LatestReleaseTag($Releases) {
  $candidates = @(
    $Releases |
      Where-Object { -not $_.draft } |
      Sort-Object -Property @{ Expression = {
        if (Is-Blank $_.published_at) { [datetimeoffset]::MinValue } else { [datetimeoffset]$_.published_at }
      } } -Descending
  )
  foreach ($release in $candidates) {
    $tag = [string]$release.tag_name
    if (Is-Blank $tag) { continue }
    $assetName = Get-AssetName $tag
    if (Is-Blank $assetName) { continue }
    $asset = @($release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1)
    if ($asset.Count -gt 0) { return $tag }
  }
  return $null
}

function Get-NextReleaseApiUrl($Response) {
  if ($null -eq $Response) { return $null }
  $headers = $Response.Headers
  if ($null -eq $headers) { return $null }
  $link = $null
  try {
    $link = [string]$headers['Link']
  } catch {
    $link = [string]$headers.Link
  }
  if (Is-Blank $link) { return $null }
  foreach ($entry in ($link -split ',')) {
    if ($entry -match '<([^>]+)>;\s*rel="next"') { return $Matches[1] }
  }
  return $null
}

function Resolve-LatestTag([string]$RepoSlugValue) {
  if (Is-Blank $RepoSlugValue) { Fail 'REPO_SLUG_REQUIRED' 'Remote install requires -RepoSlug.' }
  $api = "https://api.github.com/repos/$RepoSlugValue/releases?per_page=20"
  while (-not (Is-Blank $api)) {
    $response = Invoke-WebRequest -Uri $api -UseBasicParsing
    $releases = @($response.Content | ConvertFrom-Json)
    $tag = Select-LatestReleaseTag $releases
    if (-not (Is-Blank $tag)) { return $tag }
    $api = Get-NextReleaseApiUrl $response
  }
  Fail 'RELEASE_ASSET_NOT_FOUND' 'No published release with Trail zip asset found.'
}

function Assert-DirectoryExists([string]$PathValue, [string]$Code, [string]$Message) {
  if ((Is-Blank $PathValue) -or -not (Test-Path -LiteralPath $PathValue -PathType Container)) {
    Fail $Code $Message
  }
}

function Copy-DirectoryContents([string]$Source, [string]$Target) {
  Assert-DirectoryExists $Source 'PACKAGE_CONTENT_MISSING' "Missing package directory: $Source"
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force
  }
}

function Assert-TrailBundleTargetAvailable([string]$Target) {
  if ($Force) { return }
  foreach ($entry in $BundleSkills) {
    $targetEntry = Join-Path $Target $entry
    if (Test-Path -LiteralPath $targetEntry) {
      Fail 'TARGET_EXISTS' "Target already contains Trail-owned entry: $targetEntry. Use -Force to overwrite."
    }
  }
}

function Copy-TrailSkillsBundle([string]$Source, [string]$Target) {
  Assert-DirectoryExists $Source 'PACKAGE_CONTENT_MISSING' "Missing package directory: $Source"
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  foreach ($entry in $BundleSkills) {
    $sourceEntry = Join-Path $Source $entry
    if (-not (Test-Path -LiteralPath $sourceEntry)) {
      Fail 'PACKAGE_CONTENT_MISSING' "Missing bundled skills entry: $sourceEntry"
    }
    $targetEntry = Join-Path $Target $entry
    if (Test-Path -LiteralPath $targetEntry) {
      Remove-Item -LiteralPath $targetEntry -Recurse -Force
    }
    Copy-Item -LiteralPath $sourceEntry -Destination $Target -Recurse -Force
  }
  foreach ($entry in $BundleSupportEntries) {
    $sourceEntry = Join-Path $Source $entry
    $targetEntry = Join-Path $Target $entry
    Copy-DirectoryContents $sourceEntry $targetEntry
  }
}

function Assert-Checksum([string]$ZipPath, [string]$SumsPath) {
  if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) { Fail 'RELEASE_ASSET_NOT_FOUND' "Missing release asset: $ZipPath" }
  if (-not (Test-Path -LiteralPath $SumsPath -PathType Leaf)) { Fail 'CHECKSUM_NOT_FOUND' "Missing checksum file: $SumsPath" }

  $name = Split-Path $ZipPath -Leaf
  $escapedName = [regex]::Escape($name)
  $line = Get-Content -LiteralPath $SumsPath | Where-Object { $_ -match "^\s*([a-fA-F0-9]{64})\s+$escapedName\s*$" } | Select-Object -First 1
  if (-not $line) { Fail 'CHECKSUM_NOT_FOUND' "No checksum for $name" }

  $expected = (($line -split '\s+')[0]).ToLowerInvariant()
  $actual = Get-Sha256Hex $ZipPath
  if ($actual -ne $expected) { Fail 'CHECKSUM_MISMATCH' "Checksum mismatch for $name" }
}

function Get-Sha256Hex([string]$PathValue) {
  $stream = [System.IO.File]::OpenRead($PathValue)
  try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
      $sha.Dispose()
    }
  } finally {
    $stream.Dispose()
  }
}

function Ensure-UserPathEntry([string]$PathEntry) {
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  $parts = @()
  if (-not (Is-Blank $userPath)) {
    $parts = @($userPath -split ';' | Where-Object { -not (Is-Blank $_) })
  }
  foreach ($part in $parts) {
    if ((Normalize-PathKey $part) -eq (Normalize-PathKey $PathEntry)) { return }
  }
  $parts += $PathEntry
  [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

function Get-ReleasePackageRoot() {
  if (-not (Is-Blank $PackageRoot)) {
    Assert-DirectoryExists $PackageRoot 'PACKAGE_ROOT_NOT_FOUND' '-PackageRoot does not exist.'
    return (Resolve-Path -LiteralPath $PackageRoot).Path
  }

  $resolvedCliVersion = $CliVersion
  if ($resolvedCliVersion -eq 'latest' -and (Is-Blank $ReleaseDir)) {
    $resolvedCliVersion = Resolve-LatestTag $RepoSlug
  }

  if (-not (Is-Blank $ReleaseDir)) {
    Assert-DirectoryExists $ReleaseDir 'RELEASE_DIR_NOT_FOUND' '-ReleaseDir does not exist.'
  }

  if ($resolvedCliVersion -eq 'latest' -and -not (Is-Blank $ReleaseDir)) {
    $candidate = Get-ChildItem -LiteralPath $ReleaseDir -Filter 'trail-cli-windows-x64-v*.zip' | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $candidate) { Fail 'RELEASE_ASSET_NOT_FOUND' 'No versioned Trail zip found in -ReleaseDir.' }
    $assetName = $candidate.Name
  } else {
    $assetName = Get-AssetName $resolvedCliVersion
  }

  if (Is-Blank $assetName) { Fail 'VERSION_ASSET_INVALID' "Cannot resolve release asset for $CliVersion." }

  $work = Join-Path ([System.IO.Path]::GetTempPath()) ("trail-install-" + [guid]::NewGuid().ToString('n'))
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $extract = Join-Path $work 'extract'

  if (-not (Is-Blank $ReleaseDir)) {
    $zip = Join-Path $ReleaseDir $assetName
    $sums = Join-Path $ReleaseDir 'SHA256SUMS.txt'
  } else {
    if (Is-Blank $RepoSlug) { Fail 'REPO_SLUG_REQUIRED' 'Remote install requires -RepoSlug.' }
    $tag = Resolve-VersionTag $resolvedCliVersion
    $base = "https://github.com/$RepoSlug/releases/download/$tag"
    $zip = Join-Path $work $assetName
    $sums = Join-Path $work 'SHA256SUMS.txt'
    Invoke-WebRequest -Uri "$base/$assetName" -UseBasicParsing -OutFile $zip
    Invoke-WebRequest -Uri "$base/SHA256SUMS.txt" -UseBasicParsing -OutFile $sums
  }

  Assert-Checksum $zip $sums
  Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
  return $extract
}

$sourceRoot = Get-ReleasePackageRoot
$sourceTrail = Join-Path $sourceRoot 'trail'
$sourceSkills = Join-Path $sourceRoot 'skills'
$installSkillsRoot = Join-Path $resolvedInstallDir 'skills'
$skillDestinations = [System.Collections.ArrayList]::new()
$seenSkillDestinations = @{}

if ($installSkillsValue) {
  foreach ($destination in @($installSkillsRoot)) {
    $key = Normalize-PathKey $destination
    if (-not $seenSkillDestinations.ContainsKey($key)) {
      $seenSkillDestinations[$key] = $true
      [void]$skillDestinations.Add($destination)
    }
  }
  foreach ($target in $resolvedTargets) {
    $destination = [string]$target.skill_dir
    $key = Normalize-PathKey $destination
    if (-not $seenSkillDestinations.ContainsKey($key)) {
      $seenSkillDestinations[$key] = $true
      [void]$skillDestinations.Add($destination)
    }
  }
  foreach ($destination in $skillDestinations) {
    Assert-TrailBundleTargetAvailable $destination
  }
}

if ($installCliValue) {
  Copy-DirectoryContents $sourceTrail (Join-Path $resolvedInstallDir 'trail')
}

if ($installSkillsValue) {
  foreach ($destination in $skillDestinations) {
    Copy-TrailSkillsBundle $sourceSkills $destination
  }
  if (-not $SkipUserEnvironmentForTest) {
    [Environment]::SetEnvironmentVariable('TRAIL_SKILLS_ROOT', $installSkillsRoot, 'User')
  }
}

$trailExe = Join-Path $pathDir 'trail.exe'
if ($installCliValue -and -not (Test-Path -LiteralPath $trailExe -PathType Leaf)) {
  Fail 'CLI_VERIFY_FAILED' 'trail.exe was not installed.'
}

if ($installCliValue) {
  if (-not $SkipUserEnvironmentForTest) {
    Ensure-UserPathEntry $pathDir
  }
  if (-not $SkipCliVerifyForTest) {
    $versionResult = & $trailExe version 2>&1
    if ($LASTEXITCODE -ne 0) { Fail 'CLI_VERIFY_FAILED' "trail.exe version failed: $versionResult" }
  }
}

$result = [ordered]@{
  ok = $true
  install_dir = $resolvedInstallDir
  skill_dir = $resolvedSkillDir
  targets = @($resolvedTargets)
  trail_skills_root = if ($installSkillsValue) { (Join-Path $resolvedInstallDir 'skills') } else { $null }
  skills = $BundleSkills
}

Write-Host "Trail install dir: $resolvedInstallDir"
if ($installSkillsValue) {
  $skillsDisplay = $resolvedSkillDir
  if (Is-Blank $skillsDisplay) {
    $skillsDisplay = (@($resolvedTargets) | ForEach-Object { [string]$_.skill_dir }) -join '; '
  }
  Write-Host "skills install dir: $skillsDisplay"
} else {
  Write-Host 'skills install dir: not installed (CLI only)'
}
if ($installCliValue -and $installSkillsValue) {
  Write-Host 'Next: open your AI tool and tell Agent: use trail-hsr to take over Star Rail'
} elseif ($installCliValue) {
  Write-Host 'Next: CLI only, skills were not installed. To let Agent take over, run skills install or use AGENT_INSTALL.md'
} elseif ($installSkillsValue) {
  Write-Host 'Next: skills only. Confirm trail.exe is in PATH, or let Agent use the full path'
}
$result | ConvertTo-Json -Depth 4 -Compress
exit 0
