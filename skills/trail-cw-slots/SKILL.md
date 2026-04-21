---
name: trail-cw-slots
description: Use when an agent needs to inspect or mutate Currency Wars field, reserve, or hand slots with explicit slot commands.
---

# Skill: trail-cw-slots

## 输入

- `session_id`

## 职责

- 读取前台、后台、手牌快照
- 执行 `swap`、`place`、`collect`、`sell`
- 不决定整体阵容策略，只执行显式动作
- `slots read` 的结果是对当前界面的辅助结构化快照，agent 仍应结合截图本身判断阵容与站位
- 默认假设主 skill 已经完成 `trail cw start` 和 `trail cw portal.select`，当前已经进入局内编队/备战阶段

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 先看当前阶段已有 screenshot，判断哪些槽位是“看见有角色但名字不确定”
3. 需要定向确认时：`trail cw slots read --session <id> --slot front:0 --slot hand:3`
4. 只有需要完整快照兜底时，才运行：`trail cw slots read --session <id>`
5. 如需收水晶：`trail cw crystals collect --session <id>`
6. 如需换位：`trail cw slots swap --session <id> --source <src> --target <dst>`
7. 如需从手牌批量上场：`trail cw slots place --session <id> --action <src,dst> ...`
8. 如需卖牌，先看建议：`trail cw hand sell-plan --session <id>`
9. 真正出售时显式执行：`trail cw hand sell --session <id> --slot <n> --slot <m>`

## 执行规则

- `swap` 必须显式给出源位置与目标位置；`place` 用重复 `--action <src,dst>` 显式给出动作列表
- `sell-plan` 只提供建议，不会直接出售
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本；不要跳过原始图直接按结构化快照行动
- `place` / `sell` 都严格保序、遇错即停；只要中途失败且前面动作可能已生效，先重新执行 `trail cw slots read --session <id>`
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本；不要跳过原始图直接按结构化快照行动
- `place` / `sell` 都严格保序、遇错即停；只要中途失败且前面动作可能已生效，先重新执行 `trail cw slots read --session <id>`
- `sell-plan` 和 `slots read` 都是辅助快照，Agent 应优先看截图确认当前站位和手牌，再决定显式动作
- 槽位名字确认前先看当前 screenshot；若只需确认个别槽位，优先显式传 `--slot` 做定向读取，不要默认全量扫
- 不传 `--slot` 时，`trail cw slots read --session <id>` 仍是完整快照兜底，不是已废弃路径；若局部读取结果带 `stale=1`，不要把它当成新的完整 fresh 快照
- `trail ocr read` 只用于槽位外的通用 OCR 场景，不再作为槽位名确认的默认入口
- 任何修改槽位的动作后，如果主 skill 还需要最新布局，先看该动作返回的 screenshot，再按需执行 `trail cw slots read --session <id> --slot ...`；只有确实需要完整 fresh 快照时，才执行不带 `--slot` 的全量读取
- 这个 skill 只执行局部操作，不负责决定整局阵容路线
- 如果 `slots read` 的结构化结果与截图观感冲突，以截图为准，再决定是否重读或直接发显式动作
- 如果换位、上场、卖牌或收水晶后结果未知，或当前 session 不可继续使用，切到 `trail-hsr-advanced`
