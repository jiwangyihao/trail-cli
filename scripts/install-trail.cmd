@echo off
setlocal

set "ENTRY_DIR=%~dp0"
set "INSTALLER="
set "PACKAGE_ROOT="

if exist "%ENTRY_DIR%scripts\agent-install.ps1" (
  set "INSTALLER=%ENTRY_DIR%scripts\agent-install.ps1"
  set "PACKAGE_ROOT=%ENTRY_DIR%"
) else if exist "%ENTRY_DIR%agent-install.ps1" (
  set "INSTALLER=%ENTRY_DIR%agent-install.ps1"
  for %%I in ("%ENTRY_DIR%..") do set "PACKAGE_ROOT=%%~fI"
) else (
  set "LOG_DIR=%LOCALAPPDATA%\TrailCLI\logs"
  if not defined LOCALAPPDATA set "LOG_DIR=%TEMP%\TrailCLI\logs"
  if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
  for /f %%T in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "LOG_STAMP=%%T"
  set "LOG_PATH=%LOG_DIR%\install-%LOG_STAMP%.log"
  > "%LOG_PATH%" echo code=INSTALLER_NOT_FOUND message=agent-install.ps1 not found entry_dir=%ENTRY_DIR%
  echo error_code=INSTALLER_NOT_FOUND log_path=%LOG_PATH%
  echo Trail install failed. scripts\agent-install.ps1 was not found.
  echo Copy the error_code and log_path above to Agent.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -PackageRoot "%PACKAGE_ROOT%"
if errorlevel 1 (
  echo.
  echo Trail install failed. Copy the error_code and log_path above to Agent.
  pause
  exit /b 1
)

echo.
echo Trail install complete. Open your AI tool and tell Agent: use trail-hsr to take over Star Rail
pause
