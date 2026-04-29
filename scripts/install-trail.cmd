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
  echo 错误码=INSTALLER_NOT_FOUND 日志路径=%LOG_PATH%
  echo Trail 安装失败。找不到 scripts\agent-install.ps1。
  echo 请把上面的错误码和日志路径复制给 Agent。
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -PackageRoot "%PACKAGE_ROOT%"
if errorlevel 1 (
  echo.
  echo Trail 安装失败。请把上面的错误码和日志路径复制给 Agent。
  pause
  exit /b 1
)

echo.
echo Trail 安装完成。请打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁
pause
