---
name: trail-hsr-advanced
description: 当上层 Trail 技能在自动游玩过程中遇到启动失败、环境异常、窗口接管异常或需要恢复运行链路时使用。
---

# Skill: trail-hsr-advanced

## Role

- `trail-hsr-advanced` 是内部恢复 skill，只处理上层 owner 已经无法继续推进时的底层恢复链路。
- 它不作为用户直达入口，也不承担任何具体玩法或 scene 的编排流程。
- 只允许 `root_entry` 或 `scene_entry` 按 `skills/shared/escalation-contract.md` 升级调用。
- 即使上层 caller 是某个 scene entry，advanced 也只做恢复，不接管成新的长期 owner。

## When To Use

- 启动失败、环境异常、窗口接管异常、request 结果未知，或当前上下文已经断裂到上层无法安全继续推进时使用。
- 调用方必须同时携带失败症状、最近动作、可复用上下文，以及 `expected_return_owner`；在内部恢复记录里还要带上 `expected_return_owner_skill`，用来标明具体 skill owner。
- advanced 负责恢复可继续推进的底层状态；恢复完成后，控制权必须交回 `expected_return_owner`，并且必须按 `expected_return_owner_skill` 交回具体 skill owner，而不是只回到抽象角色类别。

## Recovery Ladder

1. 先确认调用方带来的失败症状、最近动作、可复用上下文、`expected_return_owner` 与 `expected_return_owner_skill` 是否完整。
2. 判断当前问题属于启动链路、窗口链路、request-status / taint，还是 state 回读问题，再选择对应恢复面。
3. 只使用 internal 恢复能力处理 daemon、window、session、screen、image、state 边界，不重新承担用户编排。
4. 一旦恢复出可继续推进的稳定状态，立即把控制权交回 `expected_return_owner`，并按 `expected_return_owner_skill` 交回具体 skill。
5. 如果结果仍 unknown、上下文仍 tainted，或还缺少关键路径 / 窗口信息，则停止自动重试并把阻塞事实返回上层 owner。

## Command Families

- `daemon`：控制面状态、request-status、session 协调与恢复。
- `window`：启动、附着、窗口接管，以及 channel / 路径相关恢复。
- `session`：补建或修复当前会话上下文。
- `screen` / `image`：需要截图或图像线索确认窗口状态、场景状态时使用。
- `state`：当 stdout 丢失、request 结果未知或需要回读当前 scene / session 状态时使用。
- 需要用 `screen` / `image` / `state` 做高级排障时，如果显式开启 `--verbose`，可以把 stdout 里的 major action trace 当作 shared helper 执行证据；finalized 事件至少会带 `ts=<UTC RFC3339 毫秒时间戳>` 与 `ok=0|1`。
- 这些 `debug kind=trace ...` 行只属于排障层，不改变默认文本协议；没有显式开启 `--verbose` 时，不能假定 stdout 含这些行。

## Stop Conditions

- 缺少 `expected_return_owner`、`expected_return_owner_skill`、最近动作或可复用上下文，无法建立可靠恢复链路时停止。
- `tainted` 状态在 request-status 或 reconcile 之后仍无法清理时停止。
- 启动路径、channel、窗口目标等关键输入缺失，且不能从现有上下文安全推断时停止。
- 一旦已经恢复到可由上层继续编排的状态，就必须立即返回上层 owner，而不是继续停留在 advanced；scene entry caller 必须回到对应的具体 skill owner，而不是只回到 `scene_entry` 这个抽象角色。

## Reference Map

- `references/advanced-command-surface.md`：daemon / window / session / screen / image / state 的边界与恢复职责。
- `references/recovery-ladder.md`：request 未知、恢复顺序、return owner 与停止条件。
- `references/request-status-and-taint.md`：`request id`、`tainted`、unknown result 与 reconcile 处理。
- `references/window-launch.md`：`channel`、`game path`、启动错误与窗口接管恢复语义。
