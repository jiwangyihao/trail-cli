---
name: trail-cw-guide
description: Use when an agent needs to fetch or apply a Currency Wars guide artifact for an existing Trail session.
---

# Skill: trail-cw-guide

## 职责

- 拉取货币战争攻略 artifact
- 把显式传入的攻略应用到当前 session
- 不负责进入对局，也不负责整局循环

## 输入

- `session_id`
- 攻略 URL，或已存在的攻略 `artifact_id` / 文件路径

## 标准流程

1. 拉取攻略：`trail guide fetch cw <url>`
2. 记录返回中的 `artifact_id` 或 `path`
3. 应用攻略：`trail cw guide apply --session <id> --guide <artifact|path>`
4. 确认返回的 `data` 已写入攻略引用、购买限制和约束快照

## 执行规则

- `trail guide fetch cw <url>` 只负责生成 artifact，不会自动修改 session
- `trail cw guide apply` 必须显式传入 `--guide`
- 攻略应用后，把旧的 `slots`、`shop`、`stage` 快照视为无效，交回主 skill 继续下一步
