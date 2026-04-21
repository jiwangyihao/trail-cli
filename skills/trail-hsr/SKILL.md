---
name: trail-hsr
description: Use when an agent needs the simple-first Trail HSR entry: run `trail start`, inspect with `trail ocr read`, then act with `trail input ...`.
---

# Skill: trail-hsr

## 职责

- 默认先用 simple 层拿到可用 `session`
- 用 `trail ocr read` 观察当前界面
- 用 `trail input ...` 执行显式动作
- 需要进入具体场景 skill 时切换到 `trail-cw`

## 推荐输入

- 场景名，例如 `cw`
- 可选窗口标题，默认使用 `崩坏：星穹铁道`

## 标准流程

1. 运行 `trail start`
2. 从首行记录 `session=<id>`
3. 需要补充当前画面文字与截图时，运行 `trail ocr read`
4. 需要点击、拖拽或按键时，运行 `trail input ...`
5. 需要进入货币战争时，切换到 `trail-cw` 并显式带上 `--session <id>`

## 执行规则

- 默认 simple 层只教 `trail start`、`trail ocr read`、`trail input ...`
- 通用场景判断继续走 `trail start` / `trail ocr read` / `trail input ...`，不要把 `trail cw stage` 当成登录页、大世界等非 CW 场景检测器
- `trail start` 成功后优先复用返回的 `session=<id>`，后续场景命令都显式带上它
- 如果 `trail ocr read` 已返回 OCR 结果和 `shot path=...`，不要马上再跑额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；当你怀疑快档漏字、需要更稳的 box，或要做高精度对照时，再显式加 `--ocr-mode high`。如需强制做一次快档后高精度补跑，可再加 `--retry-high always`；正常情况下保持默认 `--retry-high auto`
- 如果 `trail start` 失败、simple 层不足以定位问题，或你需要手工拆解启动/恢复链路，加载 advanced skill `trail-hsr-advanced`
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本；不要跳过原始图直接行动
- 这个 skill 不负责货币战争具体策略
