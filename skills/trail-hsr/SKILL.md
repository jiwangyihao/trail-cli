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

1. 如果游戏尚未启动，先运行 `trail window launch --game-path <StarRail.exe>`；必要时显式传 `--channel official|bilibili|global`
2. 如果还不确定窗口是否可操作，运行 `trail window attach --window-title "崩坏：星穹铁道"`
3. 运行 `trail session create`
4. 从返回的 `data.session_id` 记录本局会话 ID
5. 把后续所有场景命令都显式带上 `--session <id>`
6. 需要进入货币战争时，切换到 `trail-cw`

## 执行规则

- `session create` 成功前，不要开始场景命令
- 如果窗口检查失败，先解决窗口焦点或绑定问题，再继续
- 每次命令后优先阅读返回的 `screenshot` 与 `data`
- 如果需要调试窗口绑定、前台状态或复杂场景动作，可给 CLI 加顶层 `--verbose`
- 这个 skill 不负责货币战争具体策略
