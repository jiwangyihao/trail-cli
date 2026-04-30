param([string]$Version = '0.1.0')

$ErrorActionPreference = 'Stop'
$Version = $Version.TrimStart('v')

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dist = Join-Path $Root 'dist'
$Package = Join-Path $Dist "trail-cli-windows-x64-v$Version"
$Zip = Join-Path $Dist "trail-cli-windows-x64-v$Version.zip"

Push-Location $Root
try {
  New-Item -ItemType Directory -Force -Path $Dist | Out-Null

  uv run python scripts\generate-third-party-notices.py
  uv run python scripts\build-cw-resource-bundle.py
  uv run python -m build
  uv run pyinstaller packaging\trail.spec --noconfirm

  $noticeNames = Select-String -Path (Join-Path $Root 'THIRD_PARTY_NOTICES.txt') -Pattern '^Package: ' | ForEach-Object {
    $_.Line.Substring(9).ToLowerInvariant().Replace('_', '-').Replace('.', '-')
  }
  Get-ChildItem (Join-Path $Root 'dist\trail') -Recurse -Directory -Filter '*.dist-info' | ForEach-Object {
    $pkg = $_.Name -replace '-[0-9].*\.dist-info$', ''
    $normalized = $pkg.ToLowerInvariant().Replace('_', '-').Replace('.', '-')
    if ($normalized -eq 'trail-cli') { continue }
    if ($noticeNames -notcontains $normalized) {
      throw "missing third-party notice for bundled package $pkg"
    }
  }

  if (Test-Path $Package) { Remove-Item $Package -Recurse -Force }
  if (Test-Path $Zip) { Remove-Item $Zip -Force }

  $Bin = Join-Path $Package 'trail\bin'
  New-Item -ItemType Directory -Force -Path $Bin | Out-Null
  Copy-Item (Join-Path $Root 'dist\trail\*') $Bin -Recurse -Force
  Copy-Item (Join-Path $Root 'skills') (Join-Path $Package 'skills') -Recurse -Force
  New-Item -ItemType Directory -Force -Path (Join-Path $Package 'scripts') | Out-Null
  Copy-Item (Join-Path $Root 'scripts\agent-install.ps1') (Join-Path $Package 'scripts\agent-install.ps1') -Force
  Copy-Item (Join-Path $Root 'scripts\install-trail.cmd') (Join-Path $Package '安装 Trail.cmd') -Force
  Copy-Item (Join-Path $Root 'LICENSE') (Join-Path $Package 'LICENSE') -Force
  Copy-Item (Join-Path $Root 'THIRD_PARTY_NOTICES.txt') (Join-Path $Package 'THIRD_PARTY_NOTICES.txt') -Force

  Compress-Archive -Path (Join-Path $Package '*') -DestinationPath $Zip -Force
  Copy-Item (Join-Path $Root 'scripts\agent-install.ps1') (Join-Path $Dist 'agent-install.ps1') -Force
  uv run python scripts\verify-cw-resource-bundle-artifacts.py dist

  $artifacts = @()
  $artifacts += Get-Item $Zip
  $artifacts += Get-ChildItem $Dist -File -Filter '*.whl'
  $artifacts += Get-ChildItem $Dist -File -Filter '*.tar.gz'
  $artifacts += Get-Item (Join-Path $Dist 'agent-install.ps1')
  $artifacts | Sort-Object Name -Unique | ForEach-Object {
    $hash = Get-FileHash $_.FullName -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $($_.Name)"
  } | Set-Content (Join-Path $Dist 'SHA256SUMS.txt') -Encoding ascii
}
finally {
  Pop-Location
}
