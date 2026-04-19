---
name: trail-cw-slots
description: Use when an agent needs to inspect or mutate Currency Wars field, reserve, or hand slots with explicit slot commands.
---

# Skill: trail-cw-slots

## 输入

- `session_id`

## 职责

- 读取前台、后台、手牌快照
- 执行 `swap`、`place-one`、`collect`、`sell`
- 不决定整体阵容策略，只执行显式动作
- `slots read` 的结果是对当前界面的辅助结构化快照，agent 仍应结合截图本身判断阵容与站位

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 读取槽位：`trail cw slots read --session <id>`
3. 如需收水晶：`trail cw crystals collect --session <id>`
4. 如需换位：`trail cw slots swap --session <id> --source <src> --target <dst>`
5. 如需从手牌上场：`trail cw slots place-one --session <id> --source <src> --target <dst>`
6. 如需卖牌，先看建议：`trail cw hand sell-plan --session <id>`
7. 真正出售时显式执行：`trail cw hand sell-one --session <id> --slot <n>`

## 执行规则

- `swap` 和 `place-one` 必须显式给出源位置与目标位置
- `sell-plan` 只提供建议，不会直接出售
- `sell-plan` 和 `slots read` 都是辅助快照，Agent 应优先看截图确认当前站位和手牌，再决定显式动作
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；如果编队/手牌页文字密、快档结果不稳，或你需要更稳的 box，再显式加 `--ocr-mode high`。如需固定做高精度补跑对照，可加 `--retry-high always`；平时保持默认 `--retry-high auto`
- 任何修改槽位的动作后，如果主 skill 还需要最新布局，应重新执行 `trail cw slots read --session <id>`
- 这个 skill 只执行局部操作，不负责决定整局阵容路线
- 如果 `slots read` 的结构化结果与截图观感冲突，以截图为准，再决定是否重读或直接发显式动作
- 如果换位、上场、卖牌或收水晶后结果未知，或当前 session 不可继续使用，切到 `trail-hsr-advanced`
