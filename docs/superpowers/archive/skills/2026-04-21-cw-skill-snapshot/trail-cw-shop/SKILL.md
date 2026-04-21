---
name: trail-cw-shop
description: Use when an agent is in the Currency Wars shop stage and needs to scan, buy, refresh, or close the shop with explicit commands.
---

# Skill: trail-cw-shop

## 输入

- `session_id`

## 职责

- 调用 `trail cw shop *` 命令
- 执行显式购买、刷新、开关商店
- 不做整局调度，也不决定长期阵容策略
- 默认假设主 skill 已经完成 `trail cw start` 和 `trail cw portal.select`，当前已经进入局内商店阶段

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 扫描当前商店：`trail cw shop scan --session <id>`
3. `scan` 会先退回无弹窗备战页读取队伍容量，再自动重新打开商店读取商品、价格、金币和等级；调用者不需要先手动 `trail cw shop open`
4. 如需购买，使用显式槽位和角色名：`trail cw shop buy-slot --session <id> --slot <n> --expect <角色>`
5. 如需刷新，只调用：`trail cw shop refresh --session <id>`
6. 只有需要显式保持商店打开时，才单独调用：`trail cw shop open --session <id>`
7. 离开前可执行：`trail cw shop close --session <id>`

## 执行规则

- 先读 `scan` 返回的金币、等级、备战位状态，再决定是否继续动作
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本；不要跳过商店原图只看压缩摘要
- 不要为了 `scan` 额外先调一次 `trail cw shop open`；`scan` 已内置“收回弹窗 -> 读队伍容量 -> 重开商店”的两段式扫描路径
- `buy-slot` 必须显式指定 `--slot` 和 `--expect`
- 如果返回 `reserve_full` 或 `needs_reserve_clear`，把控制权交回主 skill 或 `trail-cw-slots` 处理卖牌，不在这里私自决定卖谁
- 每次购买或刷新后，优先使用最新返回结果，必要时重新 `scan`
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；如果商店页文本、价格或槽位读得不稳，或你要复核高精度结果，再显式加 `--ocr-mode high`。如需固定做一次高精度补跑，可加 `--retry-high always`；常规情况下保持默认 `--retry-high auto`
- 如果 `scan`、购买或刷新后结果未知，不要把它当成可安全重试的纯读取命令；停止重复点击，并切到 `trail-hsr-advanced` 走恢复路径。当前 session 不可继续使用时也按同样规则处理
