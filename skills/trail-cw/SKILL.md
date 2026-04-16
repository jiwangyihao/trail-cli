---
name: trail-cw
description: Use when an agent needs to orchestrate a full Currency Wars run by looping on stage detection and dispatching to Trail sub-skills.
---

# Skill: trail-cw

## 职责

- 编排一整局货币战争
- 在关键阶段切换到攻略、编队、商店、补给、事件子 skill
- 负责循环、阶段切换和失败恢复，不重写 CLI 原子动作
- 把每条命令返回的 `screenshot` 视为第一手事实来源；CLI 自带的 `detect/read/status` 只作为辅助判断

## 输入

- `session_id`
- `guide_url` 或已存在的攻略 artifact
- 开局模式：`new` 或 `continue`

## 验收清单

1. 使用已有 session
2. 进入货币战争
3. 拉取并应用攻略
4. 循环：stage detect -> 分发到 slots/shop/replenish/events 子 skill
5. 直到 settle next / game over

## 标准流程

1. 确认当前用户已经完成 `trail daemon install`
2. 用 `trail daemon status` 确认常驻服务可用；如需显式预热，运行 `trail daemon start`
3. 使用来自 `trail-hsr` 的既有 `session_id`
4. 如果窗口尚未恢复，先回到 `trail-hsr`，必要时用 `trail window launch --game-path <StarRail.exe>` 重新启动游戏并重建可用 session
5. `trail cw enter --session <id> --mode new|continue`
6. 如果是 `new` 模式，先把入口链推进到“投资环境”页，并由 Agent 亲自完成投资环境选择
7. 进入游戏后，如果当前 session 还没有已加载的攻略，则切到 `trail-cw-guide`，执行：
   - `trail guide fetch cw <lineup_url|lineup_id>`
   - `trail cw guide apply --session <id> --lineup-id <lineup_id>`
8. 如果是 `continue` 模式，且 session 中已经有可用的 guide 状态，则跳过重新 apply
9. 循环执行：
    - `trail cw stage detect --session <id>`
    - 根据 `data.value` 分发：
      - `preparation` 或需要调整编队时，切到 `trail-cw-slots`
     - `shop` 时，切到 `trail-cw-shop`
     - `replenish`、`invest`、`encounter`、`fortune` 时，切到 `trail-cw-replenish`
      - `boss_preview`、`event`、`settle`、`game_over` 或战斗衔接时，切到 `trail-cw-events`
    - 每次动作后优先消费当前命令返回的 `screenshot` 与 `data`，不要假设旧状态仍然有效
    - 如果 `detect/read` 与截图观感冲突，以截图为准，再决定下一条显式动作命令
10. 在 `settle` 阶段执行 `trail cw settle next --session <id>` 后继续下一轮识别；在 `game_over` 后退出

## 执行规则

- 攻略拉取和攻略应用分两步，不能隐式复用“上一条 fetch 结果”
- `guide fetch` 主要是把攻略内容直接提供给 Agent；真正的本地 artifact 记录发生在 `cw guide apply` 成功之后
- 所有 `trail cw ...` 命令都必须显式传入 `--session <id>`
- 阶段切换由这个 skill 决定，子 skill 不负责整局调度
- 如果当前动作让 `stage` 失效，立刻回到 `trail cw stage detect --session <id>`
- `continue` 模式表示“继续当前 UI 进度”，不是重新创建 session；只有当 session 中缺少 guide 状态时，才重新走攻略子 skill
- 不要假设 `read_*` 命令已经穷尽了所有 UI 语义；必要时直接根据 screenshot 做多模态判断后，再调用显式动作命令
- 如果 `detect/read` 与截图观感冲突，以截图为准；guide 相关元数据（如 `support_hard`、`has_change_equip`、`has_expert`）主要用于选攻略，不用于替代实屏判断
- 开发期调试场景命令时，可给 CLI 加顶层 `--verbose` 查看中间 trace
- 如果命令返回未知结果，立即用 `debug.request_id` 执行 `trail daemon request-status --request-id <id>`，不要盲目重放 mutating 命令
- 如果 `request-status` 或 `state dump` 显示 session 已 tainted，先执行 `trail daemon reconcile-session --session <id>`，再继续整局编排
