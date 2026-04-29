# Trail CLI 发布整理设计

## 背景

当前仓库已经具备 Python package 元数据、`trail` / `traild` console scripts、`skills/` 目录和较完整的测试，但发布资料仍偏向内部协议与开发者视角：

- `README.md` 首屏主要解释 CLI 分层、输出协议和货币战争流程，不适合作为普通 skill 用户的安装入口。
- 仓库缺少 `LICENSE` 文件；本项目确认使用 Mozilla Public License 2.0，SPDX 标识为 `MPL-2.0`。
- `pyproject.toml` 只配置了 hatchling wheel 构建，没有二进制分发、release workflow、校验和或安装器。
- 没有面向 Agent 的无交互安装说明，也没有面向非程序员用户的双击安装入口。

外部参考显示，主流 AI 工具和 skill 项目通常同时提供人类安装说明与 Agent 可执行安装面：Claude Code 提供官方脚本与 plugin 命令；GitHub Copilot Skills 提供 `gh skill install`；Vercel `skills` 提供 `npx skills add`；superpowers / superpowers-zh 强调自动检测并安装到目标 AI 环境。

## 目标

- 让 Agent 能全自动安装 Trail CLI 和本仓库 skills，不依赖用户本地 Python 或 uv。
- 让普通 Windows 用户能在不懂编程的前提下完成安装。
- 让 README 首屏面向“使用 skill 的用户”，而不是只面向 CLI 协议维护者。
- 使用 MPL-2.0 开源，并在仓库元数据中保持一致。
- 建立可重复构建、可发布 GitHub Release、可校验的构建体系。

## 非目标

- 首版不承诺代码签名、MSIX、winget、Scoop、Chocolatey 或 npm package 正式发布。
- 首版不把所有 AI 工具做成官方 marketplace plugin；只保证仓库结构和安装说明为后续兼容留接口。
- 首版不改变 CLI 命令输出协议、renderer 家族或货币战争业务行为。

## 发布入口设计

### Agent 自动安装入口

Agent 安装是发布设计的一等入口。仓库新增 `scripts/agent-install.ps1`，支持无交互安装 CLI 二进制和完整 Trail skill bundle：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\agent-install.ps1 -Agent opencode -Scope user -CliVersion latest -Yes
```

远程 bootstrap 作为可选路径，但不得把裸 `main | iex` 作为 Agent 主路径。Agent 可复制命令应先下载到临时文件，再带完整参数执行：

```powershell
$env:TRAIL_REPO_SLUG = "发布仓库owner/trail-cli"
$script = Join-Path $env:TEMP "trail-agent-install.ps1"
iwr "https://github.com/$env:TRAIL_REPO_SLUG/releases/latest/download/agent-install.ps1" -UseB -OutFile $script
powershell -NoProfile -ExecutionPolicy Bypass -File $script -Agent opencode -Scope user -CliVersion latest -Yes
```

实现前必须先确定 GitHub 发布仓库坐标，并把 README 中的示例替换为真实 URL；当前本地仓库没有配置 git remote。固定版本安装时，URL 应使用对应 tag 的 release asset，而不是 `latest`。

脚本参数：

- `-Agent opencode|openclaw|claude-code|copilot|cursor|gemini|all|auto`
- `-Scope user|project`
- `-ProjectPath <path>`
- `-SkillDir <path>`
- `-CliVersion latest|<tag>`
- `-SkillsRef bundled|<tag>|<sha>|<branch>`
- `-InstallCli` / `-InstallSkills`
- `-CliOnly` / `-SkillsOnly`
- `-InstallDir <path>`
- `-Yes`
- `-DryRun`
- `-Force`

行为要求：

- `-Yes` 下不得进入交互菜单；缺少必要信息时输出明确错误和可复制的修正命令。
- `-DryRun` 只打印将下载的 release、安装目录、skills 目标目录和 PATH 变更，不写入文件。
- 默认行为是同时安装 CLI 和完整 Trail skill bundle。
- `-CliOnly` 与 `-SkillsOnly` 互斥；`-InstallCli` / `-InstallSkills` 是显式包含开关；任何互相矛盾组合在 `-Yes` 下必须失败并输出稳定错误码与修正命令。
- `-Force` 只允许覆盖同一安装目标下的旧 Trail 文件，不得删除用户目录中的非 Trail 内容。
- `-Agent auto -Yes` 的规则固定：检测到 0 个目标时报 `NO_AGENT_TARGET_DETECTED`；检测到 1 个目标则安装；检测到多个目标时报 `MULTIPLE_AGENT_TARGETS` 并列出候选，除非用户显式使用 `-Agent all` 或指定单个 agent。
- `-Scope project -Yes` 必须显式提供 `-ProjectPath`；只有在非 `-Yes` 模式下，脚本才允许从当前目录提示确认项目路径。
- `openclaw` 是一等目标，路径检测与自定义 `-SkillDir` 都必须支持。
- 安装结束后验证 `trail --version` 或等价命令可执行，并输出已安装 skills 列表。
- 安装脚本必须下载对应 release 的外部 `SHA256SUMS.txt`，校验版本化 release asset `trail-cli-windows-x64-vX.Y.Z.zip` 后再解压；校验失败输出稳定错误码并停止。
- CLI 二进制只从 GitHub Release asset 安装。`-CliVersion latest|<tag>` 负责选择 release；任意 git ref 不得触发源码构建。
- `-SkillsRef` 只影响 skills/source 内容；默认 `bundled` 表示使用 release zip 内随附的 skills。若 `-InstallCli` 与 `-SkillsRef <branch|sha>` 同时出现，脚本必须明确提示 CLI 与 skills 可能版本不一致。

Trail skill bundle 必须包含所有 active public 与 active internal 依赖，而不是只安装 `trail-hsr`：

- `trail-hsr`
- `trail-hsr-advanced`
- `trail-cw-entry`
- `trail-cw-guide`
- `trail-cw-portal`
- `trail-cw-prep`
- `skills/registry/*`
- `skills/shared/*`

如果目标宿主只接受单 skill 目录，安装器必须复制完整依赖集合，或在目标目录下生成可解析的 bundle 结构，保证 `cw.enter -> trail-cw-entry` 与 `cw.portal.select -> trail-cw-prep` handoff 不会指向缺失 skill。

目标环境矩阵首版不把未经 smoke 的宿主路径写死为最终事实。实现时先按宿主原生文档、通用 Agent Skills 路径和 `-SkillDir` 三类来源验证，再回写 README。设计基线如下：

| Agent | 宿主原生目录 | 通用 Agent Skills 目录 | 自动检测信号 | 验证方式 |
| --- | --- | --- | --- | --- |
| `opencode` | 实现时按 OpenCode 文档或本机 smoke 校验 | user `%USERPROFILE%\.agents\skills`；project `<project>\.agents\skills` | 已存在 `.agents\skills` 或 OpenCode 配置目录 | 安装后目录存在 `trail-hsr\SKILL.md` 与 bundle 依赖 |
| `openclaw` | 实现时按 OpenClaw 文档或本机 smoke 校验 personal/workspace/shared scope | user `%USERPROFILE%\.agents\skills`；project `<project>\.agents\skills` | 已存在 OpenClaw skill 或用户显式 `-Agent openclaw` | 安装后目录存在 `trail-hsr\SKILL.md`，并可被 OpenClaw skill loader 扫描 |
| `claude-code` | `%USERPROFILE%\.claude\skills` / `<project>\.claude\skills` | 可 fallback 到用户显式 `-SkillDir` | 已存在 `.claude` 目录或用户显式指定 | 目录存在入口与依赖 skill |
| `copilot` | 按 `gh skill` 当前版本验证 | user/project 可 fallback 到 `.agents\skills` 或生态工具实际路径 | 已安装 `gh skill` 或存在 `.github` | 目录存在入口与依赖 skill；生态安装器另行验证 |
| `cursor` | 实现时按 Cursor 文档或生态工具路径表验证 | user/project 可 fallback 到 `.agents\skills` | 已存在 `.cursor` | 目录存在入口与依赖 skill |
| `gemini` | 实现时按 Gemini 文档或生态工具路径表验证 | user/project 可 fallback 到 `.agents\skills` | 已存在 `.gemini` | 目录存在入口与依赖 skill |

`-SkillDir <path>` 覆盖上述矩阵，适合未知宿主或用户自定义 OpenClaw/OpenCode 目录。实现前必须用目标宿主文档或本机 smoke 校验这些路径；如果路径规则与宿主最新版本不一致，以实现时验证结果更新 README 与 spec 附录。任何未验证路径不得在 README 中写成“默认目录”。

### 生态兼容入口

README 的 Agent 安装段同时提供现有生态安装器命令，便于已经安装对应工具的 Agent 直接复用。生态入口只在已验证能保留完整 bundle、`registry` 与 `shared` 时才可作为完整安装路径；否则只能作为单 skill / 多 skill 示例，完整安装仍推荐 `agent-install.ps1`：

```bash
gh skill install 发布仓库owner/trail-cli trail-hsr --agent opencode --scope user
npx --yes skills add 发布仓库owner/trail-cli --skill '*' --agent opencode --global --yes
```

说明优先级：

- 如果目标环境已有 `gh skill` 或 `npx skills`，可优先生态安装器安装 skills；实现前必须验证当前工具版本是否支持目标 agent 与全量 skill bundle。
- `gh skill install` 若不能一次安装 bundle，README 必须提供多条非交互命令，或明确生态入口只适合安装单个入口 skill，完整安装仍用 `agent-install.ps1`。
- 如果还需要安装 Trail CLI 二进制，使用 `agent-install.ps1`；生态安装器只负责 skills，不负责 `trail.exe`。
- 如果某宿主在生态安装器当前版本不支持，fallback 到 `agent-install.ps1 -SkillDir <path>`。
- 文档必须说明如何固定版本或引用，例如 tag、SHA、`-CliVersion`、`-SkillsRef`、生态工具的 pin 参数。

### 普通用户入口

GitHub Release 附带版本化 asset `trail-cli-windows-x64-vX.Y.Z.zip`。zip 解压后包含中文双击入口：

- `安装 Trail.cmd`
- `trail/bin/trail.exe`
- `trail/bin/traild.exe`
- `skills/`
- `scripts/agent-install.ps1`
- `SHA256SUMS.txt`

普通用户 README 首屏只展示三步：

1. 打开 README 提供的“最新版下载”直达链接。
2. 下载 Assets 里的 `trail-cli-windows-x64-vX.Y.Z.zip`，并解压到一个固定文件夹。
3. 双击 `安装 Trail.cmd`，按中文菜单选择安装到 OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini 或自定义目录。

`安装 Trail.cmd` 是人类友好包装。它可以调用 `agent-install.ps1`，但用户不需要理解 PowerShell、PATH 或执行策略。PowerShell 一行命令、执行策略和手工 PATH 设置只放到高级安装与排障章节。

中文菜单验收要求：

- 首屏默认推荐“自动检测并安装到已发现的 AI 工具”。
- 普通默认路径只安装“CLI + 完整 Trail skill bundle”。
- 菜单项固定包含 OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini、全部检测到的环境、自定义 skills 目录。
- “仅安装 CLI”“仅安装 skills”只能放在高级/排障分组；选择前必须提示风险，成功页也必须按安装模式输出不同下一步。
- 自定义目录必须显示示例路径，并在写入前要求确认。
- 成功页必须显示 Trail 安装位置、skills 安装位置、下一步提示：“打开对应 AI 工具，对 Agent 说：使用 trail-hsr 接管星铁”。
- 失败页必须显示可复制给 Agent 的错误码和日志路径。

## README 结构设计

README 改为普通用户优先：

1. Trail 是什么：一句话说明它是面向《崩坏：星穹铁道》的 Agent skill + CLI 工具。
2. 谁需要它：想让 AI Agent 接管星铁、尤其是货币战争流程的用户。
3. 最快安装：普通用户三步，第一步必须是直达最新版下载页的链接，不以“GitHub Releases”作为唯一动作文案。Agent 自动安装命令放在紧随其后的独立小节，标题明确为“给 Agent / 高级用户”。
4. 安装到哪些 AI 环境：OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini、自定义 skills 目录。
5. 第一次使用：打开目标 AI 工具，请 Agent 使用 `trail-hsr` 接管星铁；首个 CLI 命令是 `trail start`。
6. 常见问题：Windows 拦截脚本、杀软误报、找不到 `trail`、skills 没被加载、如何更新。
7. Agent / 高级安装：`agent-install.ps1` 参数、生态安装器、手工校验 SHA256、更新、卸载、验证。
8. 开发者说明：源码安装、uv、pytest、wheel 构建。
9. 输出协议和命令面：保留现有协议说明，但下沉到用户安装之后。

项目规则要求 README 与 skills 文档同步。凡是影响普通用户或 Agent 使用方式的安装入口，都要同步更新相关 `skills/*/SKILL.md` 或引用文档，尤其是 `trail-hsr`、`trail-cw-entry` 和 registry 入口说明。

同时新增 Agent 可读安装说明 `AGENT_INSTALL.md`，内容只服务自动化安装：推荐命令、目标环境矩阵、错误码、dry-run、验证、更新、卸载、版本 pin、`CliOnly` / `SkillsOnly` 风险说明。README 首屏链接该文件，不把所有自动化细节塞进用户首屏。

## 许可证设计

- 新增 `LICENSE`，使用 Mozilla Public License 2.0 官方文本。
- `pyproject.toml` 新增 SPDX 元数据：`license = "MPL-2.0"`。
- `pyproject.toml` 新增 license files：`license-files = ["LICENSE", "THIRD_PARTY_NOTICES*"]`。
- README 增加许可证段，说明本项目使用 MPL-2.0。
- release zip 根目录、wheel、sdist 必须包含 `LICENSE`。
- 二进制 release 必须包含 `THIRD_PARTY_NOTICES.txt` 或等价文件，并覆盖 PyInstaller 捆绑的第三方依赖。CI 必须生成或校验 notices，避免只发布主项目许可证。

## 构建体系设计

保留现有 Python wheel 构建，同时新增 Windows 二进制构建：

- `pyproject.toml` 增加开发依赖：`pyinstaller`、`build`，必要时增加 packaging 辅助依赖。
- 新增 PyInstaller spec 或构建脚本，生成 `trail.exe` 和 `traild.exe`。
- 资源收集必须包含 `skills/`、`skills/registry`、`skills/shared`、CW assets、OCR/模板所需资源，以及运行期需要的 DLL / data files。
- PyInstaller spec 必须显式处理 `onnxruntime-directml`、`rapidocr-onnxruntime`、`windows-capture`、`pywin32`、包 metadata、CW assets、hidden imports 和 DLL/binary collection。
- `trail version` 在 frozen 环境中必须可用；构建需复制 package metadata 或内置版本文件，不能只依赖源码 `pyproject.toml` fallback。
- release zip 使用稳定目录结构，避免用户解压后不知道入口；release asset 文件名固定为 `trail-cli-windows-x64-vX.Y.Z.zip`，其中 `vX.Y.Z` 必须与 tag 一致。
- release 页面单独上传顶层 `SHA256SUMS.txt`，覆盖 zip、wheel、sdist、独立 `agent-install.ps1`；zip 内可另放内部 manifest，但不能把 zip 内校验文件当作外部完整性校验。
- release 页面必须单独上传 `agent-install.ps1`，供 Agent 远程 bootstrap 下载；CI/release smoke 必须检查该 asset 存在且被 `SHA256SUMS.txt` 覆盖。
- GitHub Actions workflow 至少包含：checkout、安装 Python、安装依赖、运行 pytest、构建 wheel/sdist、构建 Windows zip、上传 artifacts。
- CI 必须使用 Windows x64 runner、Python 3.12，并通过 `uv sync --locked --all-groups` 或等价锁定安装复现依赖；构建日志记录 Python、PyInstaller、依赖锁版本。
- tag release 时可进一步创建 GitHub Release 并上传 artifacts；首版如果不自动发布，也要能从 workflow artifact 验证产物。
- tag 发布规则：`vX.Y.Z` tag 必须与 `pyproject.toml` 的 `project.version` 一致；artifact 文件名包含版本号；维护 `CHANGELOG.md` 或自动生成 release notes。

### Frozen runtime 调整

当前 daemon bootstrap 会生成 `traild-launch.pyw`，并用 `sys.executable` 或旁边的 `pythonw.exe` 启动 Python 脚本。PyInstaller 下 `sys.executable` 会指向 `trail.exe`，不是 Python 解释器。发布设计必须包含 frozen/runtime 分支：

- 源码环境继续使用现有 launcher 脚本。
- frozen 环境下 scheduled task 直接启动 release zip 中的 `traild.exe`。
- manifest 记录 frozen daemon exe 路径，不依赖源码 `cwd` 或 `sys.path.insert`。
- CI 或 release smoke 至少验证解压后执行 `trail.exe daemon install` / `trail.exe daemon status` / daemon 启动路径不会调用源码 Python launcher。

当前 renderer 使用 `Path(__file__).resolve().parents[2] / "skills" / "registry" / "workflow-handoffs.yaml"` 查找 handoff registry。PyInstaller 后该路径可能指向 bundle 内部或临时解包目录。发布设计必须包含 registry 解析策略：

- 优先读取环境变量 `TRAIL_SKILLS_ROOT`。
- 其次读取 exe 根目录旁的 `skills/registry`。
- 最后回退到源码相对路径。
- release smoke 必须断言 `cw.enter` 与 `cw.portal.select` 的 handoff 行仍可渲染。

## 安全与执行策略

README 不能保证一行 PowerShell 在所有 Windows 环境中都不会被拦截。官方执行策略、企业组策略、AppLocker/WDAC、杀软、SmartScreen、网络代理都可能影响执行。

文档原则：

- 普通用户主路径使用 Release zip + 双击 `安装 Trail.cmd`。
- Agent 自动安装路径使用 `-ExecutionPolicy Bypass` 仅作用于当前进程，不要求用户修改全局执行策略。
- 如果用户选择本地 `.ps1` 方式并遇到脚本被阻止，文档提供 Windows 设置路径和命令行等价路径。
- Windows 设置路径使用用户语言描述：打开“设置 -> 系统 -> 开发者选项 -> PowerShell”，允许本地 PowerShell 脚本在未签名情况下运行、远程脚本需要签名。
- 命令行等价路径只建议当前用户作用域：`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`。
- 如果只有单个下载脚本被 Internet 标记阻止，文档建议先审阅脚本，再执行 `Unblock-File .\agent-install.ps1`。
- 手动 zip 安装作为不愿启用脚本运行用户的兜底：保留解压目录运行 `trail\bin\trail.exe`，手动把 `skills` bundle 复制到目标宿主 skills 目录，或用宿主界面导入自定义 skills 目录。
- 无脚本 portable 兜底必须说明 skill 如何找到 CLI：优先让用户把 `trail\bin` 加入 PATH；不改 PATH 时，README/`AGENT_INSTALL.md` 必须提供让 Agent 使用完整 exe 路径的说明，例如 `C:\path\to\trail\bin\trail.exe start`，并明确这不是普通推荐路径。

## 测试与验收

设计验收标准：

- README 首屏能让非程序员理解“下载什么、双击什么、安装到哪里、下一步怎么叫 Agent 使用”。
- Agent 安装说明可无交互执行，且能显式指定 `opencode`、`openclaw` 等目标。
- `LICENSE` 与 `pyproject.toml` 许可证元数据一致。
- CI 能运行测试并生成 wheel 与 Windows zip artifact。
- `scripts/agent-install.ps1 -DryRun -Agent opencode -Scope user -Yes` 不写文件且输出可检查计划。
- `scripts/agent-install.ps1 -DryRun -Agent openclaw -Scope user -Yes` 覆盖 OpenClaw 目标路径逻辑。
- `scripts/agent-install.ps1 -DryRun -Agent auto -Scope user -Yes` 在检测到多个目标时失败并列出候选。
- `scripts/agent-install.ps1 -DryRun -Agent opencode -Scope project -ProjectPath <tmp-project> -Yes` 输出 project scope 目标路径。
- `scripts/agent-install.ps1 -DryRun -CliOnly -SkillsOnly -Yes` 这类冲突组合必须失败并输出稳定错误码。
- release zip 的 `安装 Trail.cmd` 支持测试模式或等价非交互验证，覆盖菜单选项、OpenClaw、自定义 `-SkillDir`、成功页下一步提示。
- `agent-install.ps1` 自动下载外部 `SHA256SUMS.txt` 并校验 zip；校验失败 smoke 必须证明不会继续安装。
- 完整安装后，目标 skills 目录必须包含 Trail skill bundle 的所有 active 入口、internal 依赖、registry 和 shared references。
- 解压后的产物必须通过 `trail.exe version`、daemon frozen 启动路径 smoke、workflow handoff registry smoke、CW assets 解析 smoke。
- release zip 中存在 `安装 Trail.cmd`、CLI exe、skills、安装脚本和校验文件。
- release 页面存在外部 `SHA256SUMS.txt`，覆盖所有 release assets，包括独立 `agent-install.ps1`。

## 参考资料

- Anthropic skills 文档：https://docs.anthropic.com/en/docs/agents-and-tools/skills/overview
- GitHub awesome-copilot：https://github.com/github/awesome-copilot
- GitHub Copilot skill 文档：https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/add-skills
- Vercel skills：https://github.com/vercel-labs/skills
- superpowers：https://github.com/obra/superpowers
- superpowers-zh：https://github.com/jnMetaCode/superpowers-zh
- PyInstaller usage：https://pyinstaller.org/en/stable/usage.html
- GitHub upload-artifact：https://github.com/actions/upload-artifact
- Microsoft PowerShell execution policies：https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies
- Microsoft Set-ExecutionPolicy：https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-executionpolicy
- Mozilla Public License 2.0：https://www.mozilla.org/en-US/MPL/2.0/
