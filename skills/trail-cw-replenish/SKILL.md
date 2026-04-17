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

## 标准流程

1. 首次使用先确认当前用户已执行 `trail daemon install`
2. 开始前查看常驻服务状态：`trail daemon status`
3. 优先查看上一条命令返回的 `screenshot`，必要时再调用读取命令补充辅助信息：
   - 补给：`trail cw replenish read --session <id>`
   - 投资：`trail cw invest read --session <id>`
   - 遭遇：`trail cw encounter read --session <id>`
   - 命运卜者：`trail cw fortune read --session <id>`
4. 根据显式策略选择一个选项：
   - `trail cw replenish choose --session <id> --option <n>`
   - `trail cw invest choose --session <id> --option <n>`
   - `trail cw encounter choose --session <id> --option <n>`
   - `trail cw fortune choose --session <id> --option <n>`
5. 动作完成后，把控制权交回主 skill 重新识别阶段

## 执行规则

- 优先看截图，再决定是否调用 `read`；不要把 `read` 的返回当成唯一事实来源
- `read` 返回的编号只是辅助输入，真正该选哪个仍应结合截图、攻略细节和当前局内目标判断
- `--option` 必须显式给出
- `choose` 之后把 `stage` 视为失效，回到 `trail-cw` 重新 `stage detect`
- 这个 skill 不处理商店、编队和战斗逻辑
- 如果选择动作返回未知结果，优先读取默认文本里的 `request id=<id>`；只有 transport/control-plane 失败或显式 `--verbose` 调试时，再看 `debug.request_id`，随后执行 `trail daemon request-status --request-id <id>`
- 如果 session 进入 `tainted`，先执行 `trail daemon reconcile-session --session <id>`，再回到主 skill
