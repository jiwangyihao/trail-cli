---
name: trail-cw-events
description: Use when an agent needs to handle Currency Wars boss preview or special events after the main run has already entered those scene-local stages.
---

# Skill: trail-cw-events

## 输入

- `session_id`

## 职责

- 处理 Boss 预览与特殊事件
- 不负责 battle 主流程、settle paging 或整局循环
- 不负责商店、补给和整局循环
- 依赖主 skill 结合截图判断当前是不是该调用这里的命令，不假设 CLI 已内建穷尽识别
- 默认假设首页与投资环境选择已经完成；这里不负责 `trail cw start` 或 `trail cw portal.*`

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 强敌预览阶段：`trail cw boss-preview confirm --session <id>`
3. 特殊事件阶段：`trail cw event handle --session <id>`
4. 如果主 skill 判断当前已经进入 `battle` / `settle` 链，交回 `trail-cw` 默认执行 `trail cw battle run --session <id> --timeout 570`
5. 只有主 skill 明确要求 scene-local 手工拆链时，才切到 `trail-cw-battle-advanced`
6. 如果主 skill 判断当前是 `game_over`，把结果交回主 skill 结束整局，不在这个子 skill 内部自行控制循环

## 执行规则

- 每次事件命令后都把 `stage` 视为失效，交回主 skill 重新识别
- `trail cw event handle` 的返回至少要看 `event_type` 和 `handled_action`
- 这个 skill 负责固定事件处理，不负责决定何时购物、何时补给、何时换阵容，也不负责决定何时退出整局
- 不要在这里回退到旧的 battle / settle 原子命令；常规 battle / settle 主入口固定是 `trail cw battle run --session <id> --timeout 570`
- 如果事件截图和 `event_type` / `handled_action` 不一致，以截图为准，并由主 skill 决定下一步是否重试或改走别的命令
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本；不要只看 `event_type` / `handled_action` 就直接行动
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；如果事件页文字密、快档结果可疑，或你要对照高精度结果，再显式加 `--ocr-mode high`。如需固定做快档后高精度补跑，可加 `--retry-high always`；平时保持默认 `--retry-high auto`
- 即使 CLI 提供了 `event_type`，也不要假设它已经穷尽所有事件分支；必要时直接根据截图做多模态判断
- 如果事件命令结果未知，或当前 session 不可继续使用，交回主 skill 并切到 `trail-hsr-advanced`
