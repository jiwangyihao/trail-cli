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

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 拉取攻略内容：`trail guide fetch cw <lineup_url|lineup_id>`
3. 由 Agent 读取返回内容，确认这是要执行的攻略
4. 在游戏内应用：`trail cw guide apply --session <id> --lineup-id <lineup_id>`
5. 如需回顾当前攻略：`trail cw guide current --session <id>`
6. 确认返回的 `data` 已写入攻略引用、购买限制和约束快照

## 执行规则

- `trail guide fetch cw` 直接返回攻略内容，不自动修改 session，也不把 artifact 当主要产出
- `trail cw guide apply` 必须显式传入 `--lineup-id`（兼容旧 `--guide` 别名时，也应优先把它理解成 lineup_id）
- artifact 记录发生在 apply 成功后，便于后续通过 `trail cw guide current` 回顾当前实际应用的攻略
- `support_hard`、`has_change_equip`、`has_expert` 这类字段主要用于“选攻略”阶段，不能替代实际截图判断
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；当攻略页或 lineup 文字你觉得读得不稳、需要更稳的 box，或要做结果对照时，再显式加 `--ocr-mode high`。如需固定补跑高精度，可加 `--retry-high always`；常规情况下保持默认 `--retry-high auto`
- 攻略应用后，把旧的 `slots`、`shop`、`stage` 快照视为无效，交回主 skill 继续下一步
- 如果攻略 apply 后结果未知，或当前 session 不可继续使用，交回主 skill 并切到 `trail-hsr-advanced`
