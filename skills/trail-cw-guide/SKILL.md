---
name: trail-cw-guide
description: Use when an agent needs to fetch or apply a Currency Wars guide artifact for an existing Trail session.
---

# Skill: trail-cw-guide

## 职责

- 拉取货币战争攻略内容，直接提供给 Agent
- 在游戏内应用显式指定的 lineup 攻略，并在成功后记录本地 artifact
- 支持回顾当前 session 已应用的攻略细节
- 不负责进入对局，也不负责整局循环

## 输入

- `session_id`
- `lineup_id` 或完整 lineup URL

## 标准流程

1. 首次使用先确认当前用户已执行 `trail daemon install`
2. 开始前查看常驻服务状态：`trail daemon status`
3. 拉取攻略内容：`trail guide fetch cw <lineup_url|lineup_id>`
4. 由 Agent 读取返回内容，确认这是要执行的攻略
5. 在游戏内应用：`trail cw guide apply --session <id> --lineup-id <lineup_id>`
6. 如需回顾当前攻略：`trail cw guide current --session <id>`
7. 确认返回的 `data` 已写入攻略引用、购买限制和约束快照

## 执行规则

- `trail guide fetch cw` 直接返回攻略内容，不自动修改 session，也不把 artifact 当主要产出
- `trail cw guide apply` 必须显式传入 `--lineup-id`（兼容旧 `--guide` 别名时，也应优先把它理解成 lineup_id）
- artifact 记录发生在 apply 成功后，便于后续通过 `trail cw guide current` 回顾当前实际应用的攻略
- `support_hard`、`has_change_equip`、`has_expert` 这类字段主要用于“选攻略”阶段，不能替代实际截图判断
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补一条 `trail screen shot`
- 攻略应用后，把旧的 `slots`、`shop`、`stage` 快照视为无效，交回主 skill 继续下一步
- 如需排查 apply 是否已经落地，优先读取默认文本里的 `request id=<id>`；只有 transport/control-plane 失败或显式 `--verbose` 调试时，再看 `debug.request_id`，随后执行 `trail daemon request-status --request-id <id>`
- 如果攻略 apply 后 session 被标记为 `tainted`，先执行 `trail daemon reconcile-session --session <id>`，再回到主 skill
