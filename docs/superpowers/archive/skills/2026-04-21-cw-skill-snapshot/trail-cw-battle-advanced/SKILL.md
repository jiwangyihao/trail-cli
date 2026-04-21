---
name: trail-cw-battle-advanced
description: Use when `trail cw battle run` fails, its result conflicts with the screenshot, or the user explicitly asks to manually split Currency Wars battle and settle steps.
---

# Skill: trail-cw-battle-advanced

## 何时使用

- `trail cw battle run --session <id> --timeout 570` 报错
- `battle.run` 返回的 `status/result/stage/in_battle` 与 screenshot 观感矛盾，需要 scene-local 手工拆链确认
- 用户明确要求手工拆 battle / settle 链
- 不要把这个 skill 当成常规主流程；常规 battle / settle 默认仍由 `trail-cw` 调 `trail cw battle run --session <id> --timeout 570`

## 输入

- `session_id`

## 职责

- 只负责 CW battle / settle 的 scene-local fallback
- 使用旧的兼容原子命令：`trail cw battle start --session <id>`、`trail cw battle continue --session <id>`、`trail cw settle next --session <id>`
- 不负责 daemon / request-status / reconcile-session / window / session / screen / image / state 这类 control-plane 恢复

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 默认假设主 skill 已经完成 `trail cw start` 和 `trail cw portal.select`，当前已经进入局内 battle / settle 相邻阶段
3. 准备进入战斗时：`trail cw battle start --session <id>`
4. 战斗结束继续时：`trail cw battle continue --session <id>`
5. 结算翻页时：`trail cw settle next --session <id>`
6. 每次原子动作后都把 `stage` 视为失效，交回主 skill 重新识别；如果现场已经恢复常规路径，回到 `trail-cw` 继续用 `trail cw battle run --session <id> --timeout 570`

## 执行规则

- 只在 `battle.run` 报错、结果与截图矛盾、或用户要求手工拆链时使用
- 每条命令后优先读取这次返回的 screenshot 与 `data`，不要假设旧状态仍然有效
- 如果 battle / settle 原子命令结果未知，但 `session=<id>` 还在，不要在这个 skill 里做 control-plane 恢复；切到 `trail-hsr-advanced`，由它接管 request-status / reconcile-session / state dump 这条恢复链路
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；如果 battle / settle 页文字密、快档结果可疑，或你要对照高精度结果，再显式加 `--ocr-mode high`。如需固定做快档后高精度补跑，可加 `--retry-high always`；平时保持默认 `--retry-high auto`
