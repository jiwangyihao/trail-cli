---
name: trail-hsr
description: Use when an agent needs to create a Trail session, verify the HSR window, and hand control to a scene-specific Trail skill.
---

# Skill: trail-hsr

## 职责

- 创建 `session`
- 检查《崩坏：星穹铁道》窗口是否可绑定
- 把流程切换到具体场景 skill，例如 `trail-cw`

## 推荐输入

- 场景名，例如 `cw`
- 可选窗口标题，默认使用 `崩坏：星穹铁道`

## 标准流程

1. 首次使用先运行 `trail daemon install`
2. 开始前确认常驻服务状态：`trail daemon status`；如果需要主动预热，运行 `trail daemon start`
3. 如果游戏尚未启动，先运行 `trail window launch --game-path <StarRail.exe>`；必要时显式传 `--channel official|bilibili|global`
4. 如果还不确定窗口是否可操作，运行 `trail window attach --window-title "崩坏：星穹铁道"`
5. 运行 `trail session create`
6. 从返回的 `data.session_id` 记录本局会话 ID
7. 把后续所有场景命令都显式带上 `--session <id>`
8. 需要进入货币战争时，切换到 `trail-cw`

## 执行规则

- `session create` 成功前，不要开始场景命令
- 如果窗口检查失败，先解决窗口焦点或绑定问题，再继续
- 每次命令后优先阅读返回的 `screenshot` 与 `data`
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补一条 `trail screen shot`
- `trail ocr read` 现在默认走 `ocr_mode=fast`（`1280x720`）；当你怀疑快档漏字、需要更稳的 box，或要做高精度对照时，再显式加 `--ocr-mode high`。如需强制做一次快档后高精度补跑，可再加 `--retry-high always`；正常情况下保持默认 `--retry-high auto`
- 如果需要调试窗口绑定、前台状态或复杂场景动作，可给 CLI 加顶层 `--verbose`
- 如果命令结果未知，优先读取默认文本里的 `request id=<id>`；只有 transport/control-plane 失败或显式 `--verbose` 调试时，再看 `debug.request_id`，随后执行 `trail daemon request-status --request-id <id>`
- 如果 session 被标记为 `tainted`，先查清请求终态，再执行 `trail daemon reconcile-session --session <id>`
- 这个 skill 不负责货币战争具体策略
