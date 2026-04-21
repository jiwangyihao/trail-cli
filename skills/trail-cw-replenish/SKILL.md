---
name: trail-cw-replenish
description: Use when an agent needs to read or choose replenish, invest, encounter, or fortune options for a Currency Wars session.
---

# Skill: trail-cw-replenish

## 输入

- `session_id`

## 职责

- 读取并选择补给、投资、遭遇、命运卜者选项
- 一次只处理当前阶段
- 不负责阶段切换和整局循环
- `read` 命令返回的是辅助信息，不应替代 agent 对截图本身的判断
- `trail cw invest read|choose` 仅是兼容/粗粒度入口，只表示普通局内 invest 事件，不是开局投资环境页命令

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 优先查看上一条命令返回的 `screenshot`，必要时再调用读取命令补充辅助信息：
   - 补给：`trail cw replenish read --session <id>`
   - 投资：`trail cw invest read --session <id>`
   - 遭遇：`trail cw encounter read --session <id>`
   - 命运卜者：`trail cw fortune read --session <id>`
3. 根据显式策略选择一个选项：
   - `trail cw replenish choose --session <id> --option <n>`
   - `trail cw invest choose --session <id> --option <n>`
   - `trail cw encounter choose --session <id> --option <n>`
   - `trail cw fortune choose --session <id> --option <n>`
4. 动作完成后，把控制权交回主 skill 重新识别阶段

## 执行规则

- 优先看截图，再决定是否调用 `read`；不要把 `read` 的返回当成唯一事实来源
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；当补给/投资/遭遇页的文字或编号读得不稳时，再显式加 `--ocr-mode high`。如需固定做高精度补跑对照，可加 `--retry-high always`；平时保持默认 `--retry-high auto`
- `read` 返回的编号只是辅助输入，真正该选哪个仍应结合截图、攻略细节和当前局内目标判断
- 如果当前页面是“请选择投资策略”，不要在本 skill 内继续 choose；应交回主 skill，改走 `trail cw strategy detect|refresh|select`
- `--option` 必须显式给出
- `choose` 之后把 `stage` 视为失效，回到 `trail-cw` 重新 `stage detect`
- 这个 skill 不处理商店、编队和战斗逻辑
- 如果选择动作结果未知，或当前 session 不可继续使用，交回主 skill 并切到 `trail-hsr-advanced`
