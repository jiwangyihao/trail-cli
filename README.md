# Trail CLI

让 AI Agent 接管《崩坏：星穹铁道》的本地工具。

Trail 包含 Windows 命令行程序和配套 skills。安装后，Agent 可以调用 `trail` 截图、OCR、操作窗口，并按 Trail skills 推进流程；当前重点支持“货币战争”。

## 安装

普通 Windows 用户：

1. 打开 [最新版下载](https://github.com/trail-cli/trail-cli/releases/latest)。
2. 下载 Assets 里的 `trail-cli-windows-x64-vX.Y.Z.zip`。
3. 解压到一个固定文件夹。
4. 双击 `安装 Trail.cmd`。

安装向导会同时安装 `trail` 命令和完整 Trail skills bundle。普通安装不要求本机已有 Python、uv 或 Node。

## 开始使用

安装完成后，打开你的 AI 工具，对 Agent 说：

```text
使用 trail-hsr 接管星铁
```

如果你要直接进入当前重点流程，也可以说：

```text
使用 trail-hsr 帮我玩货币战争
```

## Agent 自动安装

如果你希望让 Agent 自己安装，给它看 [`AGENT_INSTALL.md`](AGENT_INSTALL.md)。

支持的目标包括 OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini 和自定义 skills 目录。

## 能做什么

- 启动或接管游戏窗口。
- 读取截图和 OCR 文本。
- 执行点击、拖拽、按键等明确动作。
- 通过 Trail skills 编排玩法流程。
- 在“货币战争”里选择攻略、识别投资环境、读取商店和阵容，并推进战斗流程。

## 许可证

Trail CLI 使用 [MPL-2.0](LICENSE) 开源。第三方声明见 [`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt)。
