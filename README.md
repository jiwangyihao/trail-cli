# Trail CLI

[![Release](https://img.shields.io/github/v/release/jiwangyihao/trail-cli?include_prereleases)](https://github.com/jiwangyihao/trail-cli/releases)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)](#安装)
[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)

> **当前重点：货币战争**
>
> - 让 AI Agent 接管《崩坏：星穹铁道》的本地工具。
> - 安装 `trail` 命令和完整 Trail skills bundle。
> - 支持截图、OCR、窗口操作和明确输入动作。
> - 当前重点支持“货币战争”流程编排。

Trail 包含 Windows 命令行程序和配套 skills。安装后，Agent 可以调用 `trail` 截图、OCR、操作窗口，并按 Trail skills 推进流程。

## 功能一览

- **本地接管** — 启动或接管游戏窗口。
- **截图与 OCR** — 读取截图和 OCR 文本，让 Agent 先观察再行动。
- **明确动作** — 执行点击、拖拽、按键等明确动作。
- **玩法编排** — 通过 Trail skills 编排玩法流程。
- **货币战争支持** — 在“货币战争”里选择攻略、识别投资环境、读取商店和阵容，并推进战斗流程。

---

## 安装

<details open>
<summary><b>面向普通用户</b></summary>

1. 打开 [发布页](https://github.com/jiwangyihao/trail-cli/releases)，选择最新版本。
2. 下载 Assets 里的 `trail-cli-windows-x64-vX.Y.Z.zip`。
3. 解压到一个固定文件夹。
4. 双击 `安装 Trail.cmd`。

安装向导会同时安装 `trail` 命令和完整 Trail skills bundle。普通安装不要求本机已有 Python、uv 或 Node。

</details>

<details>
<summary><b>让 Agent 帮你安装</b></summary>

把下面这段话交给任意支持本地命令的 Agent：

```text
请打开 https://github.com/jiwangyihao/trail-cli/releases ，选择最新版本，下载 trail-cli-windows-x64-vX.Y.Z.zip，解压后按 AGENT_INSTALL.md 安装 Trail CLI 和完整 Trail skills bundle。安装后运行 trail version 验证。
```

</details>

<details>
<summary><b>面向 Agent 的安装说明</b></summary>

如果需要可重复安装、升级、dry run、校验失败排查或自定义 skills 目录，请阅读 [`AGENT_INSTALL.md`](AGENT_INSTALL.md)。

支持的目标包括 OpenCode、OpenClaw、Claude Code、GitHub Copilot、Cursor、Gemini 和自定义 skills 目录。

</details>

---

## 使用方式

安装完成后，打开你的 AI 工具，对 Agent 说：

```text
使用 trail-hsr 接管星铁
```

如果你要直接进入当前重点流程，也可以说：

```text
使用 trail-hsr 帮我玩货币战争
```

## 使用边界

Trail CLI 提供本地命令和 skills，让 Agent 基于真实截图和命令结果推进游戏；它不是单独运行的“一键全自动挂机脚本”。

## 致谢

Trail CLI 早期实现参考并使用了 [StarRailAssistant](https://github.com/Shasnow/StarRailAssistant) 的部分代码与思路。

## 许可证

Trail CLI 使用 [GPL-3.0-only](LICENSE) 开源。第三方声明见 [`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt)。
