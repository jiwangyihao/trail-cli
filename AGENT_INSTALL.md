# Trail CLI Agent 安装说明

本文件给 Agent 和高级用户使用。普通 Windows 用户优先下载 release zip，双击 `安装 Trail.cmd`，按中文菜单安装。

Trail 安装必须同时处理两部分：`trail` CLI 二进制，以及 complete Trail skill bundle。不要只复制 `trail-hsr` 单个 skill；货币战争 handoff 依赖 `trail-cw-entry`、`trail-cw-guide`、`trail-cw-portal`、`trail-cw-prep`、`trail-hsr-advanced`、`registry` 和 `shared`。

## 推荐：本地 release asset 安装

在已解压的 `trail-cli-windows-x64-vX.Y.Z.zip` 根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent opencode -Scope user -Yes
```

安装完成后，打开对应 AI 工具，对 Agent 说：使用 `trail-hsr` 接管星铁。

## OpenClaw 示例

OpenClaw 是安装器支持的目标环境。用户目录安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent openclaw -Scope user -Yes
```

如果 OpenClaw 使用自定义 skills 目录，显式指定 `-SkillDir`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent openclaw -SkillDir C:\Path\To\OpenClaw\skills -Yes
```

## Project Scope 示例

项目级安装必须提供 `-ProjectPath`，否则 `-Yes` 模式会返回 `PROJECT_PATH_REQUIRED`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent opencode -Scope project -ProjectPath C:\Path\To\Workspace -Yes
```

## Dry Run

`-DryRun` 只输出计划，不写文件、不改 PATH、不写环境变量：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -Agent openclaw -Scope user -DryRun -Yes
```

## 自定义 skills 目录

未知宿主或未验证宿主使用 `-SkillDir`。请填该 AI 工具实际加载 skills 的目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -SkillDir C:\Path\To\skills -Yes
```

交互菜单里的“自定义 skills 目录”会显示目标路径，并要求输入 `yes` 确认。

## 远程 Bootstrap

源码文档不写仓库占位符命令。GitHub release notes 会注入具体下载地址、`agent-install.ps1` 地址和可复制的远程 bootstrap 命令。

Agent 主路径仍建议先下载 release asset 到本地，校验后执行本地命令。远程安装必须校验 `SHA256SUMS.txt`，失败时停止。

## 生态安装器说明

如果目标工具已有生态安装器，例如 `gh skill install` 或 `npx --yes skills add`，只能在确认它会保留 complete Trail skill bundle、`registry` 和 `shared` 时使用。

生态安装器通常只安装 skills，不安装 `trail.exe`。需要 CLI 二进制时仍使用 `agent-install.ps1`。release notes 会给出针对当前发布仓库的具体生态命令。

## Portable Fallback

脚本不可用时，可以手动解压 release zip：

1. 保留 `trail\bin\trail.exe` 与 `trail\bin\traild.exe`。
2. 把 `skills` 下的完整 bundle 复制到目标 AI 工具的 skills 目录。
3. 把 `trail\bin` 加入 PATH；如果不改 PATH，让 Agent 使用完整路径，例如 `C:\Path\To\trail\bin\trail.exe start`。

仅安装 skills 的兜底命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -SkillsOnly -SkillDir C:\Path\To\skills -Yes
```

## 错误码

- `NO_AGENT_TARGET_DETECTED`：没有检测到支持的 Agent 目标目录。改用 `-SkillDir`。
- `MULTIPLE_AGENT_TARGETS`：自动检测到多个目标。改用 `-Agent all` 或指定单个 `-Agent`。
- `PROJECT_PATH_REQUIRED`：`-Scope project -Yes` 需要 `-ProjectPath`。
- `PROJECT_PATH_NOT_FOUND`：`-ProjectPath` 不存在。
- `INSTALL_MODE_CONFLICT`：`-CliOnly`、`-SkillsOnly`、`-InstallCli`、`-InstallSkills` 组合冲突。
- `CHECKSUM_MISMATCH`：release zip 与 `SHA256SUMS.txt` 不一致，安装器已停止。
- `CHECKSUM_NOT_FOUND`：找不到目标 asset 的校验和。
- `PACKAGE_ROOT_NOT_FOUND`：`-PackageRoot` 不存在。
- `RELEASE_ASSET_NOT_FOUND`：未找到版本化 `trail-cli-windows-x64-vX.Y.Z.zip`。
- `REPO_SLUG_REQUIRED`：远程安装缺少 release 仓库坐标；请使用 release notes 中的具体命令。
- `TARGET_EXISTS`：目标目录已有 Trail-owned 条目；确认覆盖时加 `-Force`。
- `USER_CANCELLED`：用户没有输入 `yes` 确认。

失败时安装器会输出 `错误码=... 日志路径=...`。请把这两个值复制给 Agent 排查。

## 更新

下载新版 `trail-cli-windows-x64-vX.Y.Z.zip`，解压后运行同一安装命令。覆盖已有 Trail bundle 时使用 `-Force`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -PackageRoot . -Agent openclaw -Scope user -Force -Yes
```

## 卸载

1. 删除安装器输出的 `Trail 安装位置` 目录。
2. 删除安装器输出的 `skills 安装位置` 中的 Trail-owned 条目：`trail-hsr`、`trail-hsr-advanced`、`trail-cw-entry`、`trail-cw-guide`、`trail-cw-portal`、`trail-cw-prep`、`registry`、`shared`。
3. 如已写入用户 PATH，移除对应 `trail\bin` 项；如已写入 `TRAIL_SKILLS_ROOT`，清理该用户环境变量。

## 模式风险

- `-CliOnly` / `CliOnly`：只安装 CLI，不安装 skills。Agent 可能无法发现 `trail-hsr`，也不会有 workflow handoff bundle。
- `-SkillsOnly` / `SkillsOnly`：只安装 skills，假设 `trail.exe` 已在 PATH 或 Agent 知道完整 exe 路径。
- `-InstallCli` 与 `-InstallSkills` 是显式包含开关；不要和 `-CliOnly`、`-SkillsOnly` 混用。

## 验证

安装后检查：

```powershell
trail version
```

并确认目标 skills 目录包含：`trail-hsr`、`trail-hsr-advanced`、`trail-cw-entry`、`trail-cw-guide`、`trail-cw-portal`、`trail-cw-prep`、`registry`、`shared`。
