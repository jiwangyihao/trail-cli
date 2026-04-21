# 货币战争战斗/结算按钮收口设计

> Note: 若本文旧示例与当前 screenshot-first guidance 冲突，以 `2026-04-20-screenshot-first-guidance-design.md` 为准。
> 当前带截图 success 路径固定为 `shot path=...` -> `info read_image_first=1` -> 实体行，README / AGENTS / skills 也必须同步更新。

## 目标

收口 `cw.battle.start`、`cw.battle.continue`、`cw.settle.next` 这一组动作命令，使它们：

1. 不再完全依赖单一固定点位作为主路径。
2. 在成功返回时稳定带上动作后的截图输出。
3. 用测试把“点击策略”和“`shot path=...` 协议”一起锁住。

## 背景

用户当前观察到的稳定现象有两类：

1. `trail cw battle start --session <id>` 像是没有点中目标按钮。
2. 动作执行后没有自动截图输出。

当前 `trail` 的实现位于 `trail/scenes/cw/events.py`：

- `BATTLE_START_POINT = _point(0.5, 0.824)`
- `BATTLE_CONTINUE_POINT = _point(0.5, 0.824)`
- `SETTLE_NEXT_POINT = _point(0.5, 0.82)`

然后 `build_cw_battle_starter()` / `build_cw_battle_continuer()` / `build_cw_settle_continuer()` 都直接点击这些固定点。

这里要明确一个边界：

- 在本项目里，把点位写成窗口比例再换算，和先统一到 `1920x1080` 坐标系再点击，在语义上不是核心差异。
- 真正的问题不是“比例坐标 vs 像素坐标”，而是**按钮获取策略本身**：当前实现把这三类按钮都当成“纯固定点动作”，没有先做按钮识别。

## 与 SRA 的确切对照

用户要求对照的是 SRA 里这三个按钮的确切代码，而不是同类交互的泛化风格。对照结果如下。

### `battle start`

`StarRailAssistant/tasks/currency_wars/CurrencyWars.py:641-653` 的 `battle()`：

1. 先 `wait_img(CWIMG.BATTLE, timeout=3, interval=0.5)`。
2. 命中后 `click_box(battle_box, after_sleep=1.5)`。

也就是说，SRA 的“开始战斗”不是固定点，而是先识别 `battle.png` 对应按钮，再点按钮中心。

### `battle continue`

同一段 `battle()` 中，在等待 `[CWIMG.SETTLE, CWIMG.CONTINUE]` 成功后：

- 执行 `click_point(0.5, 0.824, after_sleep=1, tag="点击继续按钮")`

所以 SRA 的“战斗结束继续”本身仍然保留了固定点，但它是在前面已有状态图像识别的前提下触发，不是孤立裸点。

### `settle next`

`StarRailAssistant/tasks/currency_wars/CurrencyWars.py:766-776` 的 `handle_game_over()`：

1. 先 `click_img(CWIMG.NEXT_STEP)`。
2. 再 `move_to(0.5, 0.5)` 避免遮挡。
3. 再 `wait_img(CWIMG.NEXT_PAGE)`。
4. 识别不到下一页按钮时，才回退到 `click_point(0.5, 0.82)`。
5. 无论 `NEXT_PAGE` 是否命中，最后都会再点一次 `click_point(0.5, 0.82)` 返回货币战争。

也就是说，SRA 的 `handle_game_over()` 实际上是一段多步结算链处理；本轮 `trail` 不复制整段链路，只借鉴其中“按钮识别优先、固定点兜底”的按钮处理策略。

## 结论

基于上面的确切对照，可以得到两个结论：

1. `cw.battle.start` 是这次最可疑的点位问题，因为它和 SRA 的主路径差异最大。SRA 用的是按钮图像命中；我们当前是纯固定点。
2. `cw.battle.continue` 和 `cw.settle.next` 虽然不是完全错误地使用固定点，但缺少 SRA 那种“先识别，再回退”的收口层，因此也值得一起统一处理。

## 设计方案

### 1. 收口一层统一的动作按钮解析 helper

在 `trail/scenes/cw/events.py` 内增加一层统一 helper，用于“先找按钮 box，再点击中心，找不到再回退固定点”。

这层 helper 只服务本文件内这组事件动作，不扩散到 daemon 层。

理由：

- 识别策略和点位 fallback 都是场景逻辑，应留在 `scene` 层。
- `trail/scenes/cw/entry.py`、`portal.py`、`slots.py` 已经有用 `wait_img` / `locate` / `_box_center()` 的先例。

### 2. 三个命令分别的主路径

先冻结一份公共约束：

- 公共 OCR 按钮区固定为屏幕底部中间区域：`from_x=0.30, from_y=0.72, to_x=0.70, to_y=0.92`。
- OCR 只在这个区域内找按钮 box，避免误点首页“继续进度”等非目标文案。
- 匹配规则固定为“命中文本白名单中的完整词面或其去空白等价形式”，不做模糊包含扩散。

#### `cw.battle.start`

主路径改成：

1. 先识别“开始战斗”按钮的 box。
2. 识别成功则点击 box 中心。
3. 识别失败才回退到现有 `BATTLE_START_POINT`。

这是本次最核心的行为变化。

实现顺序固定为：

1. 本项目自管模板图优先。
2. 模板未命中时，再尝试 OCR 文本框。
3. 两者都未命中时，才回退固定点。

这样既保持与 SRA `battle start` 主路径一致，也避免模板暂时失效时完全失去点击能力。

识别时序固定为：

1. 先 `wait_img` 模板，超时上限 `3s`。
2. 模板未命中时，执行一次按钮区 OCR。
3. OCR 仍未命中时，才回退固定点。

OCR 文本白名单固定为：

- `开始战斗`
- `开始挑战`

#### `cw.battle.continue`

主路径改成：

1. 先识别“继续挑战 / 继续”类按钮 box。
2. 命中后点中心。
3. 未命中时，保留 `BATTLE_CONTINUE_POINT` 作为 fallback。

这里也固定采用“模板图 -> OCR 文本框 -> 固定点”的顺序，但不强行改变我们现有命令的一次一动作语义。

识别时序固定为：

1. 先做一次单帧模板 `locate`。
2. 模板未命中时，再做一次按钮区 OCR。
3. OCR 未命中时回退固定点。

OCR 文本白名单固定为：

- `继续挑战`
- `继续`

#### `cw.settle.next`

主路径改成：

1. 先识别“下一步”按钮 box。
2. 命中后点中心。
3. 未命中时，保留 `SETTLE_NEXT_POINT` 作为 fallback。

注意：

- `trail` 当前的 `cw.settle.next` 仍保持“一次只点当前可见下一步按钮”的单步语义。
- 不在这次改动里把它扩展成 SRA `handle_game_over()` 那种整段多步结算链封装。

识别时序固定为：

1. 先做一次单帧模板 `locate`。
2. 模板未命中时，再做一次按钮区 OCR。
3. OCR 未命中时回退固定点。

OCR 文本白名单固定为：

- `下一步`
- `下一页`

### 3. 资源与别名边界

模板资源边界固定如下：

- `trail/scenes/cw/assets/`
- `trail/scenes/cw/resources.py`

并为这组按钮增加稳定 alias，供 `events.py` 通过 `resolve_scene_asset("cw", alias)` 读取。

冻结表如下：

| 命令 | 模板文件 | alias | 备注 |
| --- | --- | --- | --- |
| `cw.battle.start` | `battle.png` | `action.battle_start` | 新增本项目自管模板 |
| `cw.battle.continue` | `continue.png` | `action.battle_continue` | 新增本项目自管模板 |
| `cw.settle.next` | `next_step.png` | 复用 `stage.settle` | 已有模板，继续复用 |
| `cw.settle.next` | `next_page.png` | `action.settle_next_page` | 新增模板，仅用于“下一页”按钮单步点击 |

`cw.settle.next` 的模板匹配顺序固定为：`stage.settle` -> `action.settle_next_page` -> OCR -> 固定点。

同时需要同步更新 `trail/scenes/cw/assets/README.md`，把新增的 `battle.png`、`continue.png`、`next_page.png` 纳入版本化模板清单。

### 4. 错误与回退语义

第一版不新增新的硬失败分支。

也就是说：

- 找到按钮 box：点击识别结果。
- 找不到按钮 box：点击旧固定点。

这样做的目的不是掩盖问题，而是：

1. 先把主路径从“纯固定点”收口成“识别优先”。
2. 保留现有 fixed-point 兜底，避免一次性引入新的“按钮未识别就直接失败”行为变化。

## 自动截图语义

这次不重新设计一套新的截图机制。

当前 `cw` 变更链路本来就走 daemon mutation capture；这次的重点是把“这组命令成功后应带截图”补成明确契约，并用测试锁住。

因此本轮目标是：

- `cw.battle.start`
- `cw.battle.continue`
- `cw.settle.next`

在成功路径下都要稳定输出 `shot path=...`，并在 success 文本里紧跟 `info read_image_first=1`。

这里把截图约束明确收紧为：

1. 这三条命令继续走现有 `_render_cw_stage` renderer，不新增 renderer 家族、前缀或 YAML 行为。
2. 成功文本首行继续沿用 `_render_cw_stage` 的现有摘要逻辑：`stale=<0|1>` 必须保留；如果 payload 带 `value`，则继续输出 `stage=<value>`。
3. `shot path=...` 固定作为第二行出现，`info read_image_first=1` 固定作为第三行出现，并位于任何实体行之前。
4. daemon 返回的 `screenshot` 必须是非空值，且经现有 normalize 后写成 workspace-relative 的 `.trail/shots/...` 路径。
5. 对这三条命令来说，“success 但没有 `screenshot` / `shot path`”视为回归，不再把它当作可接受的 best-effort 结果。

如果测试暴露现有 capture 链路对这组命令并没有真正产出截图，则再做最小修补；但设计目标本身是“补齐契约并验证它”，不是额外发明一套平行截图流程。本轮也不额外为这三条 mutation 发明新的 `stage.value` 生成链；真实 daemon integration 以 `stale` + `screenshot` 契约为准。

## 测试与验收

### 1. 动作层测试

在 `tests/test_cw_events.py` 增加回归，覆盖：

1. 模板命中时优先点击模板 box 中心。
2. 模板 miss 但 OCR hit 时点击 OCR box 中心。
3. 模板 miss 且 OCR miss 时才回退旧固定点。
4. `start` / `continue` / `settle` 三者分别都走到各自策略。

这里重点保护“识别优先、点位兜底”的分支顺序。

### 2. daemon/capture 契约测试

在 `tests/test_daemon_protocol.py` 补这组 `cw` mutation 的 capture 断言，至少验证：

1. 成功响应里 `screenshot` 非空。
2. capture 使用了 request-scoped path。
3. `screenshot` 已被归一化成 workspace-relative 的 `.trail/shots/...`。
4. action mutation 在集成层仍至少保留 `stale` 更新，不要求这轮额外合成新的 `stage.value`。

### 3. CLI stdout 契约测试

在 `tests/test_cw_rpc_contracts.py` 补或改这组命令的 stdout 断言，明确要求：

- success 首行后先出现 `shot path=...`，再出现 `info read_image_first=1`

尤其是 `cw.battle.start` 与 `cw.settle.next`，当前契约测试没有把 `shot` 锁住，这次需要补齐；`cw.battle.continue` 也要继续保持显式覆盖。

这里的 CLI/rendering 用例仍可继续用带 `value` 的 fake payload 覆盖 stage-family renderer 形状；但它们不替代 daemon integration 对真实 action mutation 返回数据的约束。

同时在 `tests/test_output_rendering.py` 补一个 stage-family renderer 回归，显式覆盖：

- `cw.battle.start`
- `cw.battle.continue`
- `cw.settle.next`

确认它们仍然走 `_render_cw_stage` 家族，且 `shot path=...` 与 `info read_image_first=1` 排在首行之后、实体行之前；另外补一条 stale-only payload 回归，显式锁住“当 payload 不带 `value` 时，success 首行只保留 `stale=<0|1>`，不会凭空生成 `stage=`”。

## 文档同步

这次文档同步决议固定为：

1. `trail/scenes/cw/assets/README.md` 必须更新，因为会新增本项目自管模板。
2. `README.md` 必须同步更新，明确带截图 success 统一按 `shot path=...` -> `info read_image_first=1` -> 实体行输出。
3. `skills/trail-cw-events/SKILL.md` 也必须同步更新，把“优先看图”升级成看到新 guidance 词面时必须先读图的硬规则。

## 非目标

这次不做：

1. 改写 `cw.battle.continue` / `cw.settle.next` 为整段多步结算链命令。
2. 在 daemon 层引入新的事件专用 capture 分支，除非测试证明现有 capture 链路确实漏掉了这组命令。
3. 把货币战争所有事件按钮都一起改成语义识别；本轮只覆盖 `battle.start` / `battle.continue` / `settle.next`。
4. 复刻 SRA `handle_game_over()` 的整段“下一步 -> 下一页 -> 返回货币战争”链式处理。
5. 为这三条 mutation 额外引入 action 之后的 `stage.value` 再探测链路。

## 预期结果

完成后，这一组命令的行为应收口为：

1. `cw.battle.start` 不再把纯固定点当作唯一主路径。
2. `cw.battle.continue` / `cw.settle.next` 从“裸固定点”提升为“识别优先、点位兜底”。
3. 成功结果稳定带上动作后截图，并由测试保护。
