# 货币战争 batch_locate 与共享 OCR 优化设计

## 目标

在不改 battle.run 协议文本语义、不扩成全仓库运行时重构的前提下，降低货币战争 `stage.detect` 与 `battle.run` 的单轮 wall time，优先缓解这两类 live 问题：

1. `--timeout 15` 这类短 budget 下，battle.run 已经进入结算页却仍来不及推进 `继续挑战`。
2. CLI 侧偶发 `DAEMON_UNAVAILABLE/TimeoutError`，但 daemon 内部其实已经正常完成并写入 `state.dump`。

## 背景

当前实现里，性能瓶颈更像是“同一轮里重复截图、重复模板匹配、重复整页 OCR”，而不是点击动作本身：

1. `build_cw_stage_detector()` 通过多次 `runtime.locate(template)` 串行探测阶段；而 `runtime.locate()` 每次都会重新截图，所以一次 stage detect 最多会做 10 轮独立截图与匹配。
2. `battle.run` 每轮先调用一次 `build_cw_stage_detector()`，随后 `classify_cw_battle_page()` 又会多次整页 OCR / headline OCR 重新判断 `battle_start`、`settle_entry`、`settle_followup`、`game_over`、`battle_progress`。
3. 当页面已经进入结算页时，单轮 wall time 主要消耗在“确认当前页属于哪种状态”上，而不是耗在实际点击 `继续挑战`。

从最新 live 证据看：

1. `cw.stage.detect` 已能把当前页判成 `stage=settle`。
2. 高精度 `ocr.read` 已能稳定读到 `挑战成功/挑战结束`、`继续挑战`、轮次和血量。
3. battle.run 仍可能在 `status=in_progress stage=settle` 下返回，trace 中却没有 `click_point`，说明当前主矛盾是“在 budget 内太晚才推进到结算动作”。

## 方案

### 总体方向

本次采用“**通用 helper + CW stage-family 消费方优先接入**”的收口方式：

1. 新增通用 `batch_locate` helper，接口设计对齐现有 `batch_ocr`，但这次不把 `runtime.locate()` 全局改成自动缓存层。
2. 在 `battle.run` 中引入单轮 observation / scan 层，把同一轮里会被多个判断分支重复消费的截图、模板匹配结果和 OCR 文本收口成共享输入。
3. 消费范围优先覆盖：
   - `cw.stage.detect`
   - `cw.stage.wait`
   - `trail/scenes/cw/battle.py` 内的 classifier / settle parser / `run_cw_battle` observation
   - `battle.run` 在 settle 页会委托到的 continue/next action helper 所需输入边界
4. 不在这次顺手改非 CW scene，也不把 locate/ocr 缓存机制自动扩散到全仓库。

这里的消费范围是**硬 allowlist**，不是开放式“看到相关 OCR 就顺手改”。本轮明确不纳入：

- `cw.strategy.*`
- `cw.portal.*`
- `cw.guide.*`
- `cw.entry.*`
- `cw.shop.*` 与 `cw.slots.*` 现有 `batch_ocr` 流程

### batch_locate helper

建议新增 runtime 层通用 helper，例如：

- `trail/runtime/batch_locate.py`

helper 职责只做三件事：

1. 接受一张共享截图和一组模板。
2. 返回每个模板的命中结果与必要的位置信息。
3. 输出稳定的 trace / debug 事实，便于后续判断是模板 miss、截图本身问题，还是上层逻辑问题。

第一版接口要允许两种实现策略：

1. **单图顺序匹配**：先 `screenshot()` 一次，再对这张图依次跑所有模板匹配。
2. **可切换的并行匹配实现位**：接口允许后续切到真正并行 matcher，但这次不强行引入线程池或更重的基础设施。

也就是说，本轮真正的性能收益首先来自“**同一张图复用**”，而不是来自“并行”这个形式本身；但接口设计要为并行版本留位置，避免后续再拆 API。

接口语义要先冻结清楚，避免实现者在 helper 内又回退到重复截图：

1. helper 允许接收一张显式传入的 `image`；传了 `image` 就**不得再次截图**。
2. 若未传 `image`，helper 内部最多截图一次。
3. helper 内部不得调用 `runtime.locate()`，因为 `runtime.locate()` 自带二次截图与 sleep retry；匹配必须直接对共享图调用 matcher。
4. helper 返回的 box 坐标空间、capture offset 与 trace/debug 形状需要固定，避免后续测试与调用方各自猜测。

### stage.detect 的新数据流

`build_cw_stage_detector()` 不再自己 10 次 `runtime.locate()`；而是改成：

1. 先截一张图。
2. 在这张图上做一次 `batch_locate`，同时探测：
   - `preparation`
   - `shop`
   - `replenish`
   - `encounter`
   - `invest`
   - `boss_preview`
   - `fortune`
   - `event`
   - `settle`
   - `game_over`
3. 若模板命中，则按现有优先顺序返回对应 stage。
4. 若模板全 miss，再做一次整页 OCR fallback，用于 settle OCR 关键词判定。

这样 `cw.stage.detect` 的单轮成本会收成：

1. 一次 screenshot
2. 一次 batch locate
3. 必要时一次整页 OCR

### battle.run 的 observation / 共享 OCR

`battle.run` 新增一个单轮 observation / scan helper，作用是把一轮 loop 内会被多处重复消费的信息共享出来。分成两层：

1. **base observation**：每轮固定只做一次
   - stage locate 结果
   - 一次整页 OCR 文本
2. **lazy settle detail**：只有明确进入 settle 解析或 continue-only fallback 时才 materialize
   - headline OCR
   - round OCR
   - stats OCR

也就是说，headline / round / stats 不是每轮预取项，而是 lazy/cached 字段；一旦同轮某个分支触发了 settle detail，后续同轮消费者继续复用，不能再次 OCR。

一轮 loop 内的数据流建议收成：

1. 一次 screenshot + stage-family batch locate
2. 一次整页 OCR snapshot
3. 用这份整页 OCR 同时判断：
   - `battle_start`
   - `settle_entry`
   - `settle_followup`
   - `game_over`
   - `battle_progress`
4. 只有在明确进入 settle parser 或 continue-only settlement fallback 时，再做 headline / round / stats 的局部 OCR

另外，settle 页的 continue/next action helper 也要纳入这次 observation 边界：

1. battle 层如果已经在同轮 observation 中拿到了按钮候选或足够的 settle 证据，action helper 不应再独立做整页 locate / OCR。
2. 只有 observation 没提供足够动作输入时，action helper 才允许 fallback 到既有 locate / OCR。
3. 否则就会出现“classifier / parser 虽复用了 observation，但真正 `continue/next` 前又重走一遍 locate/ocr”的半优化状态。

也就是说，classifier、settle parser、resume 逻辑都消费同一轮 observation，而不是各自再截图、再 OCR。

## 为什么这能缓解当前 live 问题

### 对 settle 页推进的收益

当前 settle 页的主要问题不是“完全认不出”，而是“认出来太晚”。

优化后：

1. stage-family 模板探测不再做 10 次独立截图。
2. battle.run 在 settle 页上不再重复跑多次整页 OCR。
3. 因此更容易在 budget 内更早进入 `settle_entry -> continue/next`，而不是耗完时间后才返回 `status=in_progress stage=settle`。

### 对 CLI 响应超时的收益

当前 CLI 偶发 `DAEMON_UNAVAILABLE/TimeoutError`，但 daemon 内其实已经完成并写入 state，说明 battle.run 的真实 wall time 明显大于业务 timeout 的直觉值。

这次优化后：

1. 单轮 classifier 的固定开销下降。
2. 相同 `--timeout 15` 下，更多时间会花在“等待 battle flow 本身推进”，而不是花在“重复识别当前页是什么”。
3. 这样 response timeout 被拖爆的概率会下降，即使不能一次性消灭，也能把问题压缩到更容易定位的范围。

但本次 spec **不改 timeout 契约本身**：

1. 不修改 `trail/daemon/command_timeouts.py`。
2. `cw.battle.run` 默认业务 timeout 仍保持 `90s`。
3. response timeout 仍保持“业务 timeout + 30s buffer”。
4. 无效 `--timeout` 的回退规则保持现状。

## 文件边界

### 新增文件

- `trail/runtime/batch_locate.py`

### 优先修改文件

- `trail/scenes/cw/stage.py`
- `trail/scenes/cw/battle.py`

### 本次明确不改

- `trail/output/rendering.py`
- `trail/daemon/command_timeouts.py`
- `trail/daemon/client.py`
- `README.md`
- `AGENTS.md`
- `docs/cw-stage-reference/README.md`
- `skills/trail-cw-entry/SKILL.md`
- `skills/trail-hsr/references/simple-command-surface.md`
- battle in-progress skill 本体
- 非 CW scene 的 locate / OCR 调用点
- `runtime.locate()` / `runtime.ocr()` 的全局自动缓存语义

## 测试策略

### runtime 层

新增 `batch_locate` 单测，至少锁住：

1. 同图多模板匹配结果正确。
2. 未命中模板的稳定返回形状。
3. trace / debug 事实包含每个模板的匹配结果。
4. 若以后切到并行实现，测试仍锁定结果语义而不是实现细节。
5. 传入共享 `image` 时不得再次截图；未传 `image` 时最多截图一次。

### stage 层

锁住：

1. `build_cw_stage_detector()` 的阶段结果不变。
2. 单轮 screenshot / OCR 调用次数有量化下降：
   - 模板命中路径：`screenshot` 最多 1 次、整页 OCR 0 次
   - 全 miss fallback 路径：`screenshot` 最多 1 次、整页 OCR 最多 1 次
3. settle OCR fallback 仍能保住当前 `继续挑战/挑战成功/挑战失败` 这条语义。

### battle 层

锁住：

1. `settle_entry -> continue/next` 在 detector miss / OCR 命中时仍能收口。
2. `challenge_end` / `challenge_success` 等结算变体不回归。
3. `resume` / `clear-in-progress` / `last_battle_round` 这些既有行为不被顺手改坏。
4. 单轮 OCR / screenshot 次数下降，而不是只看最终结果。
5. 要明确锁住一个短 budget 场景：用 fake clock / per-step cost 模拟 settle 页，证明 `detector miss + OCR 命中 settle` 时会在 deadline 前触发 `_continue_after_settlement` 或 `_advance_settlement_page`，而不是继续 timeout 成 `stage=settle`。
6. 多次续跑场景中，`in_battle_hint -> settle -> continue/next` 的推进不回归。

### 回归与 live 验证

1. 继续跑当前 battle.run focused 回归。
2. 跑现有 renderer / CLI / state.dump focused 回归，确保这次性能优化没有顺手改协议。
3. `tests/test_output_rendering.py` / README / AGENTS 文档契约本次只做防回归跑现有测试，不新增新的用户可见协议断言，除非实现真的改了用户可见语义。
3. live 重点看：
   - `--timeout 15` 首次 `in_battle=1`
   - 后续 `stage=settle`
   - 结算页继续推进的 wall time 是否改善
   - CLI 响应超时是否缓解

## 非目标

1. 不做全仓库 locate / ocr 自动缓存重构。
2. 不顺手改非 CW scene。
3. 不在这次顺手改 battle.run 的文本协议语义。
4. 不在这次顺手发明新的 battle state tree。
