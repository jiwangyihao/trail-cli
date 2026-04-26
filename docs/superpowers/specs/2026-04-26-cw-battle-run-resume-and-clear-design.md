# 货币战争 battle.run 续跑与清标记设计

## 目标

在现有 `cw.battle.run` 已能覆盖“开战 -> 等自动战斗 -> 处理结算 -> 回到下一稳定阶段”的基础上，进一步把它收成一个可安全重跑的 battle-flow 命令：

1. 默认业务 timeout 下调到 `90s`，避免天然超过外层常见 `120s` shell timeout。
2. 命令在 `in_progress` 返回时，直接告诉 Agent 下一步应继续运行 `cw.battle.run`。
3. 命令内部利用一个**只服务于 `in_battle=1`** 的持久化提示位，避免续跑一上来撞进无 UI 大招动画就 `CW_BATTLE_STATE_UNKNOWN`。
4. 提供一个显式命令清除这个内部续跑提示位，便于 Agent 在手动恢复后消除 battle-run 的内部续跑假设。
5. battle.run 每次结束时，把本轮 battle 的阶段号（例如 `1-4`）持久化进 session，供后续命令使用。
6. 规划一份 battle in-progress 场景/命令说明，供后续专门打磨 skill 的智能体使用；本设计不直接实现那个 skill。

## 背景与问题

经过当前 live 验证，battle.run 已经先后修到了以下几类问题：

1. 备战页 `出战` 起手不再“无动作 success 返回”。
2. `战斗流程 -> 下一轮备战页` 不再无条件再次点击 `出战`。
3. `挑战结束 + 继续挑战` 这类结算变体不再失败为 `CW_SETTLEMENT_UNREADABLE`。

但用户随后提出了一个更关键的续跑问题：

- 若 battle.run 在一条调用中已经返回 `status=in_progress in_battle=1`，下一条 battle.run 若一上来正好撞进“会隐藏所有 UI 的大招动画”，当前这次调用自己还没看到任何 battle/settle 证据，就可能直接 `CW_BATTLE_STATE_UNKNOWN`。

这说明 battle.run 的**可重跑语义**还不完整：

1. Agent 需要被明确告知：`in_progress` 时应检查截图，并在仍处于 battle flow 时继续运行 `battle.run`。
2. 命令自身也需要一个窄的内部续跑提示位，帮助它在下一次调用开场容忍无信号动画，而不是把这类帧误当未知错误。

## 设计边界

### Agent 与命令的职责分离

1. Agent 的决策依据只来自：
   - 当前命令返回的截图
   - 默认文本里的显式提示
   - 后续 battle in-progress 场景/命令说明
2. Agent **不读取也不依赖**命令内部持久化提示位。
3. 命令内部持久化提示位只服务于 battle.run 自己的续跑容错，不对外暴露为业务事实。

### battle flow 的 Agent 语义

对 Agent 来说，battle.run 覆盖的不只是“明确仍在战斗中”，还包括“已经到结算页/结算翻页，但还没回到下一稳定阶段”的整段 battle flow。

因此文档与后续 skill 说明必须明确：

1. 还在战斗中，应继续运行 `trail cw battle run --session <id>`。
2. 已到结算页，但尚未收口到下一稳定阶段，也仍然继续运行 `trail cw battle run --session <id>`。
3. 不要因为到了 settle 就切回旧的 `trail cw settle next`。

## Timeout 设计更新

### 新默认值

- `cw.battle.run --timeout` 的默认业务 timeout 从 `570s` 下调到 `90s`。

选择 `90s` 的原因：

1. 小于当前环境里常见的 `120s` shell/tool 默认 timeout。
2. 仍能覆盖一段正常战斗和部分结算收口。
3. 强制命令默认走“短等待 + `in_progress` + 重跑”的续跑语义，而不是依赖长时间单次阻塞。

### transport timeout

CLI <-> daemon 的 response timeout 继续保持“业务 timeout + 缓冲”的模式，但默认值随业务 timeout 收口到新的短预算，不再天然高于外层 `120s`。

## 默认文本协议更新

### `in_progress` 时新增显式提示

battle.run 在所有 success + `status=in_progress` 路径下，都要额外追加面向 Agent 的显式提示行：

```text
info next_action=cw.battle.run why=battle_flow_not_finished
```

语义：

1. Agent 必须先看当前截图。
2. 若 Agent 认为当前仍在 battle flow 中，就继续重跑 `cw.battle.run`。
3. 这条提示同时适用于：
   - `in_battle=1`
   - `stage=settle in_battle=0`

### battle flow 提示与内部提示位分离

`info next_action=cw.battle.run why=battle_flow_not_finished` 是给 Agent 的业务提示；内部持久化提示位不进入默认文本，不作为 Agent 决策依据。

## 内部持久化提示位

### 作用范围

新增一个窄的 battle-run 内部续跑提示位，约束如下：

1. 只在上一条 `cw.battle.run` 成功返回：
   - `status=in_progress`
   - `in_battle=1`
   时置上。
2. `stage=settle` 的 `in_progress` **不会**置上这个提示位。
3. 这个提示位只影响 battle.run 自身下一次调用开场时对“无 UI / 无文字帧”的容忍策略。

### 命令内部语义

若上一条 battle.run 已置上这个提示位，则下一条 battle.run 开场时：

1. 即便当前帧没有 OCR 文本、没有稳定阶段、没有 settle 特征，也不能立刻 `CW_BATTLE_STATE_UNKNOWN`。
2. 命令应把这次调用视为一次合法的 battle-flow 续跑，允许先等待下一轮可识别帧出现。
3. 一旦当前调用明确看到 `battle_start`、`battle_progress`、`settle_entry`、`settle_followup`、稳定阶段或 `game_over`，就按本次新观察到的事实刷新/覆盖这个提示位。
4. 一旦当前调用成功或失败收口，也按当前结果刷新/清理提示位，不保留过期 battle 假设。

## 清标记命令

### 命令面

- CLI：`trail cw battle clear-in-progress --session <id>`
- canonical command：`cw.battle.clear_in_progress`

### 语义

1. 只清 battle.run 的内部续跑提示位。
2. 执行后 `session.last_result` 保持原值；不能让清标记命令把上一条 battle 摘要覆盖成自己的结果。
3. 执行后 `session.last_screenshot` 保持原值。
4. 执行后 `scene_state["cw"]["stage"]` / `last_stage` 保持原值。
5. 这是给 Agent 在手动恢复 battle 流程后取消内部续跑假设用的，不是通用 session reset。

### 默认文本

success 首行固定：

```text
ok cw.battle.clear_in_progress cleared=0|1
```

其中：

1. `cleared=1` 表示本来有提示位，现已清掉。
2. `cleared=0` 表示本来就没有提示位。

### renderer / format / 同步要求

`cw.battle.clear_in_progress` 归入现有“检测/状态摘要”success renderer 家族，约束如下：

1. 默认文本首行固定为 `ok cw.battle.clear_in_progress cleared=0|1`。
2. 该命令不产出 screenshot，不加入 YAML allowlist。
3. 必须同步更新：
   - `trail/output/rendering.py`
   - CLI / RPC 契约测试
   - renderer golden
   - README
   - 相关 active `skills/*/SKILL.md`
   - 若命令协议冻结面发生扩张，相关 `AGENTS.md`

## 本轮 battle 阶段持久化

battle.run 每次结束时，除已有 `result/stage/stale/in_battle/...` 外，还要把**本轮 battle 阶段号**持久化进 session，至少覆盖类似 `1-4` 这种事实，供后续命令使用。

持久化位置固定为一个窄字段，例如：

- `scene_state["cw"]["metrics"]["last_battle_round"] = "1-4"`

约束：

1. 这个字段只承载 battle 轮次事实，不写进 `scene_state["cw"]["stage"]["value"]`。
2. 不把 battle 轮次写进 `last_stage`。
3. 不把“当前能不能继续续跑”寄托在 `last_result.data.round`，因为 `last_result` 会被后续命令自然覆盖。
4. 允许后续按需要补充 `last_battle_result` 这类并列事实，但当前最低要求是先把 `last_battle_round` 冻结下来。

当前需求只要求“至少有 battle 阶段号可用”，不要求现在就扩成一整棵重 battle state tree。

## 文档与场景说明

README / 场景说明 / 规划中的 battle in-progress skill 说明，需要新增并冻结以下事实：

1. `battle.run` 的默认业务 timeout 已改为 `90s`。
2. 这条命令设计上依赖 `in_progress + 重跑 battle.run` 的续跑语义。
3. 结算页也属于 battle flow，仍然应继续运行 `battle.run`。
4. battle in-progress 场景说明只提供场景判断与命令建议，不直接在这次设计里实现 skill 本体。

## 非目标

1. 不把 battle.run 的内部续跑提示位暴露成 Agent 的业务事实。
2. 不把 `stage=settle` 并入内部 `in_battle` 提示位。
3. 不顺手清理 `last_result/last_screenshot/stage` 这些自然会被后续命令覆盖的摘要。
4. 不在这次设计里引入完整 battle-flow 子树状态机。
5. 不在这次设计里直接实现 battle in-progress skill。

## 测试重点

1. 默认业务 timeout 已从 `570s` 下调到 `90s`，transport timeout 也同步收口。
2. `status=in_progress` 的文本协议会稳定追加 `info next_action=cw.battle.run why=battle_flow_not_finished`。
3. 上一条 battle.run 返回 `in_progress in_battle=1` 后，下一条一上来撞进无 UI 帧不再直接 `CW_BATTLE_STATE_UNKNOWN`。
4. `stage=settle` 的 `in_progress` 不会误设置内部续跑提示位。
5. `clear-in-progress` 只清内部提示位，且执行前后的 `session.last_result`、`session.last_screenshot`、`scene_state["cw"]["stage"]`、`last_stage` 保持不变。
6. battle.run 结束后，本轮阶段号（例如 `1-4`）已持久化到 `scene_state["cw"]["metrics"]["last_battle_round"]`。
7. `cw.battle.clear_in_progress` 的 renderer/CLI/RPC/README/skills/AGENTS 同步要求都被测试锁住。
