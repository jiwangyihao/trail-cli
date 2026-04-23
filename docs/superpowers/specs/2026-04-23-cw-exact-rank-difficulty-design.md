# `cw start --difficulty AX-X` 精确职级设计说明

## 背景

当前 `trail cw start --difficulty` 只支持 `lowest/current/highest` 三种粗粒度语义。

现有实现已经能在进入链里处理：

1. `current`：保持当前 UI 选中的难度。
2. `highest`：尝试点击 `返回最高职级`。
3. `lowest`：反复点击下箭头直到按钮消失。

但用户实际需要的是更细粒度的“精确职级”控制，例如 `A7-3`。这类需求不能只靠 `lowest/current/highest` 表达，因为：

1. 同一个职级内存在多层，例如 `A7-1..A7-9`。
2. 是否适合粗调要看“层级差值”，而不是 OCR 出来的“敌人难度差值”。
3. UI 当前已经存在可作为真值来源的固定区域，能够读出当前 `敌人难度` 数值。

本设计在不改变 `trail cw start` 总体职责的前提下，把 `--difficulty` 扩展为既支持旧三态，也支持 `AX-X` 精确职级。

## 目标

1. `trail cw start --difficulty` 同时支持 `lowest/current/highest/AX-X`。
2. `AX-X` 语义固定为“某个精确职级层”，例如 `A7-3`。
3. 通过固定 OCR 区域读取当前 `敌人难度`，作为精确调级与 `lowest` 粗调的统一真值来源。
4. 当层级差值大于 10 时，支持先做一次或多次粗调滑动，再进入细调点击。
5. `lowest` 也纳入同一套“读当前层级 + 粗调/细调”的机制。
6. 无法稳定拿到当前难度真值时，显式报错并提示 Agent 进入错误恢复，而不是继续盲点。
7. 同步更新 README、帮助文本与测试。

## 非目标

1. 不新增新的 `cw` 子命令；本轮只扩展 `trail cw start --difficulty`。
2. 不改变 `mode` / `battle_mode` 的对外语义。
3. 不把“精确职级”独立成单独 renderer 家族或新的输出协议。
4. 不让 `cw.portal.*`、`cw.strategy.*`、`cw.stage.*` 直接消费 `AX-X`。
5. 不在 help 中展开“每个 AX-X 对应哪个难度值”；help 只展示职级和层数列表。

## 采纳方案

采纳“方案 2：小型抽离”。

核心思路：

1. CLI 继续复用 `--difficulty`。
2. 在 `trail/scenes/cw/entry.py` 中新增一组固定 helper，负责解析、映射、区域 OCR、粗调/细调与统一错误。
3. `_select_difficulty()` 保留为总编排入口，但不再把全部细节塞进单个 if/else 分支。

这样既能保持对现有 `cw start` 链路的最小扰动，也能让精确职级和 `lowest` 的新增复杂度被隔离在可测试的小单元里。

## 职级模型

### 1. 外部输入语义

- `lowest`
- `current`
- `highest`
- `AX-X`

其中 `AX-X` 使用固定格式：

- `A0-1` 到 `A8-40`
- `A` 后面只允许 `0..8`
- `-` 后面是该职级内的层数

CLI 层将 `difficulty` 改为字符串参数，但职责只保留两件事：

1. 透传原始公开输入串。
2. 输出更新后的帮助文本。

公共输入校验职责固定如下：

1. CLI 不再做 `StrEnum` 级别的合法集合裁剪。
2. daemon 是唯一公共输入校验层，统一负责判断 `lowest/current/highest/AX-X` 是否合法。
3. daemon 对外只使用 `CW_START_DIFFICULTY_INVALID` 作为非法输入错误码。
4. scene 仍可保留防御性 invariant 检查，但这些内部错误不得成为 `cw.start` 的公共失败面。

### 2. 职级表

实现中维护一份固定表，至少包含：

| 职级 | 中文名 | 层数上限 | 起始难度 | 结束难度 | 全局层级序号 |
| --- | --- | ---: | ---: | ---: | --- |
| A0 | 黑铁 | 3 | 1 | 3 | 1..3 |
| A1 | 青铜 | 3 | 6 | 8 | 4..6 |
| A2 | 翠钢 | 3 | 11 | 13 | 7..9 |
| A3 | 钴银 | 5 | 16 | 20 | 10..14 |
| A4 | 冰钛 | 5 | 23 | 27 | 15..19 |
| A5 | 紫金 | 7 | 30 | 36 | 20..26 |
| A6 | 投资大师 | 7 | 39 | 45 | 27..33 |
| A7 | 资本帝王 | 9 | 49 | 57 | 34..42 |
| A8 | 财富造物主 | 40 | 61 | 108 | 43..82 |

其中 `A8` 的精确难度值按用户后续澄清固定为四段离散映射，而不是连续 `61..108`：

1. `A8-1..A8-10 -> 61..70`
2. `A8-11..A8-20 -> 74..83`
3. `A8-21..A8-30 -> 87..96`
4. `A8-31..A8-40 -> 99..108`

因此 `71..73`、`84..86`、`97..98` 也属于映射空洞值，和 `4/5/9/10/58/59/60` 一样不能被反解到合法 `AX-X`。

由此可推导：

1. `AX-X -> target_enemy_difficulty`
2. `enemy_difficulty -> 当前 AX-X`
3. `AX-X -> global_layer_ordinal`
4. `enemy_difficulty -> global_layer_ordinal`

例如：

- `A7-3 -> 51`
- `39 -> A6-1`
- `51 -> A7-3`

### 3. 解析结果对象

`AX-X` 解析成功后，至少要得到以下字段：

1. `rank_code`
2. `rank_name`
3. `layer`
4. `target_enemy_difficulty`
5. `global_layer_ordinal`

该对象只在 scene 层内部使用，不直接下沉到默认文本协议。

## 运行时真值来源

### 1. 固定 OCR 区域

当前 `敌人难度` 使用固定像素框读取：

- `from_x=480`
- `from_y=940`
- `to_x=590`
- `to_y=1005`

这个区域已经通过 live 试跑验证，可以读到例如 `39` 与 `1` 这类当前敌人难度值。

### 2. OCR 读取规则

新增专用 helper `read_entry_enemy_difficulty(runtime)`，职责固定为：

1. 只 OCR 上述固定区域。
2. 一律使用高精度 OCR。
3. 只接受带几何框的 OCR 片段；无 box 片段直接丢弃。
4. 从每个片段里抽取十进制数字 run；不包含数字的片段直接丢弃。
5. 按 `(left, top, original_order)` 排序后拼接数字。
6. 拼接结果必须满足 `1..108` 且能唯一反解到合法 `AX-X`；否则直接视为“当前敌人难度真值缺失”。
7. 最终返回整数形式的当前敌人难度。

这样可以覆盖两类场景：

1. OCR 直接返回 `39`
2. OCR 分裂成 `3` + `9`

若最终出现以下任一情况，也统一视为“当前敌人难度真值缺失”：

1. 数字片段为空。
2. 数字片段可拼接，但结果不在 `1..108`。
3. 结果落在职级映射空洞区间，例如 `4/5/9/10/58/59/60`。
4. OCR 同时给出互相冲突的多组数字，无法唯一确定当前值。

## 方案设计

### 1. CLI 与帮助文本

- `trail/commands/cw.py` 中，`cw_start(... difficulty=...)` 改为字符串参数。
- `--help` 明确写出：`lowest/current/highest/AX-X`。
- 帮助文本追加完整职级层数列表，例如：
  - `A0-1..A0-3`
  - `A1-1..A1-3`
  - `A2-1..A2-3`
  - `A3-1..A3-5`
  - `A4-1..A4-5`
  - `A5-1..A5-7`
  - `A6-1..A6-7`
  - `A7-1..A7-9`
  - `A8-1..A8-40`

帮助文本不展示“AX-X 对应难度值”，避免把用户面暴露成难度表背诵问题。

README 与 active skill 的公开口径也必须和 help 完全一致：

1. 只展示 `lowest/current/highest/AX-X` 与 `A0-1..A8-40` 范围。
2. 不展示 `AX-X -> enemy_difficulty` 数值映射。
3. 不引入中文名别名、纯数值别名或额外 `--rank` 参数。

### 2. 输入校验

保留现有 `CW_START_DIFFICULTY_INVALID`，但把合法集合扩展为：

1. `lowest`
2. `current`
3. `highest`
4. 满足 `AX-X` 格式且层数合法的输入

以下输入必须在统一校验层失败：

1. `A3-6`
2. `A8-41`
3. `A9-1`
4. `A7_3`
5. `a7-3`

### 3. 页面职责矩阵与持久化格式

这次改动不能改变现有 `cw.start` 页面分支职责。spec 固定如下：

| 页面 | 是否允许执行 UI 选级 | `difficulty` 真值来源 | 说明 |
| --- | --- | --- | --- |
| `home` | 否 | 请求参数原样保留 | 仅负责点击首页 `start` 进入后续链路 |
| `entry.new` | 是 | 请求参数原样保留 | 只有这里允许执行 `current/highest/lowest/AX-X` 的真实 UI 选级 |
| `settlement.entry/followup/return` | 否 | 请求参数原样保留 | `mode=continue` 时先走结算恢复链，真正选级仍发生在后续 `entry.new` |
| `entry.continue` | 否 | 已记录的 `entry.difficulty` | 继续沿用现有 recorded truth 语义，不重新选级 |
| `stage.boss_preview` | 否 | 已记录的 `entry.difficulty` | 继续沿用现有 recorded truth 语义，不重新选级 |
| `invest` | 否 | 已记录值或请求值 | 继续沿用现有 no-op/backfill/conflict guard 语义，不重新选级 |

同时冻结持久化格式：

1. `scene_state["cw"]["entry"]["difficulty"]` 始终保存公开输入 token，即 `lowest/current/highest/AX-X`。
2. `scene_state["cw"]["portal"]["difficulty"]` 也始终保存同样的公开输入 token。
3. 任何内部解析结果，例如 `target_enemy_difficulty`、`global_layer_ordinal`、`rank_name`，都不得写回 session state，也不得作为成功响应里的 `difficulty` 对外暴露。
4. `cw.portal.restart` 继续复用已经记录下来的 `entry.difficulty` 原始 token；这不代表 `portal.*` 新增用户可传的 `--difficulty`，只是 replay 已记录真值。

### 4. 精确 `AX-X` 选择流程

`_select_difficulty()` 在收到 `AX-X` 时，走以下链路：

1. 该逻辑只允许在 `entry.new` 页面执行；`entry.continue`、`stage.boss_preview`、`invest` 保持现有 recorded truth 语义不变。
2. 读取当前敌人难度，并反解出当前 `AX-X` 与 `global_layer_ordinal`。
3. 若当前层级已等于目标层级，直接返回。
4. 否则进入循环式状态机：每一轮都必须重新 OCR 当前值，再决定下一步动作。
5. 若当前层级小于目标层级：
   - 必须先识别 `返回最高职级` 按钮；
   - 识别不到就进入统一恢复错误，`reason=highest_reset_unavailable`；
   - 识别到后点击一次，等待固定 settle，再重新 OCR 当前难度。
6. 若当前层级大于目标层级且层级差 `> 10`：
   - 执行一次粗调滑动；
   - 等待固定 settle，再重新 OCR 当前难度；
   - 若 OCR 结果与动作前完全一致，则进入统一恢复错误，`reason=coarse_no_progress`。
7. 若当前层级大于目标层级且层级差 `<= 10`：
   - 执行一次细调点击；
   - 等待固定 settle，再重新 OCR 当前难度；
   - 若 OCR 结果与动作前完全一致，则进入统一恢复错误，`reason=step_no_progress`。
8. 若任意一次 OCR 无法反解为合法职级，则进入统一恢复错误，`reason=ocr_missing` 或 `reason=ocr_unmapped`。
9. 若循环预算耗尽仍未命中目标，则进入统一恢复错误，`reason=iteration_budget_exhausted`。
10. 当 OCR 到的当前难度与目标 `target_enemy_difficulty` 相等时结束。

### 5. 粗调动作

新增一个粗调 helper `coarse_reduce_entry_difficulty(runtime)`。

动作固定为：

1. 起点：屏幕 x 轴中央
2. 起点 y：屏幕高度的 75%
3. 终点：同一 x，向上滑到屏幕上边缘

粗调仅由“层级差值 > 10”触发。

这里冻结两条规则：

1. 判断阈值使用全局层级差值，不使用敌人难度差值。
2. 每次粗调后必须重新 OCR 当前难度，禁止盲滑固定次数。
3. `返回最高职级`、粗调滑动、细调点击三种动作都要在动作后等待同一个 `ENTRY_DIFFICULTY_SETTLE_SECONDS=0.5` 再重新 OCR。
4. 粗调循环预算固定为 20 轮。
5. 细调循环预算固定为 12 轮。

### 6. 细调动作

新增一个细调 helper `step_reduce_entry_difficulty(runtime)`。

职责固定为：

1. 识别当前下箭头。
2. 点击一次。
3. 等待 `ENTRY_DIFFICULTY_SETTLE_SECONDS=0.5`。
4. 重新 OCR 当前敌人难度。

精确 `AX-X` 选择在差值 `<= 10` 时只允许走这条路径，不再使用粗调。

细调的进入条件也固定为：

1. 必须已经能识别到下箭头。
2. 必须已经拿到当前敌人难度真值。
3. 任一条件不成立时，不允许盲点，直接进入统一恢复错误。

### 7. `lowest` 的新逻辑

`lowest` 不再只是“盲点直到下箭头消失”，而是纳入统一真值流程。

新规则如下：

1. 先同时获取 `arrow_present` 与当前敌人难度真值。
2. 只要 `arrow_present=1` 且当前敌人难度 OCR 缺失或无法反解，就立即进入统一恢复错误，`reason=ocr_missing` 或 `reason=ocr_unmapped`，不允许继续 coarse/fine。
3. 如果 `arrow_present=1` 且当前层级与 `A1-1` 的层级差值 `> 10`，先执行粗调循环，直到差值 `<= 10`。
4. 当差值 `<= 10` 且 `arrow_present=1` 时，进入细调循环：点下箭头、等待 `ENTRY_DIFFICULTY_SETTLE_SECONDS=0.5`、重新检测箭头和当前难度。
5. 只有当下箭头消失且当前层级已经是 `A0-1` 时，`lowest` 才算成功结束。
6. `lowest` 不会先点 `返回最高职级`。

这里保留用户明确要求的边界：

1. 粗调阈值仍是看层级差，不看难度差。
2. `lowest` 的粗调参考点是 `A1-1`，不是某个难度绝对值。

### 8. `lowest` 的箭头缺失兜底

当 `lowest` 一开始识别不到下箭头时：

1. 如果当前敌人难度可以识别且当前层级已经是 `A0-1`，直接 success/no-op。
2. 如果当前敌人难度可以识别且当前层级高于 `A0-1`：
   - 当它与 `A1-1` 的层级差值 `> 10` 时，允许先走粗调；
   - 当差值 `<= 10` 时，先执行一次 `wait` 再重试；
   - `wait` 后若仍无箭头且当前层级不是 `A0-1`，进入统一恢复错误，`reason=lowest_arrow_missing_non_bottom`。
3. 如果当前敌人难度也识别不出来，执行一次 `wait`。
4. `wait` 后仍无法识别当前敌人难度，则进入统一恢复错误，`reason=ocr_missing`。

这个兜底只用于保证“没有箭头时仍有一条安全真值链路”；它不允许在无箭头、无当前难度真值的情况下继续盲点。

## 错误语义

### 1. 输入非法

- 继续使用 `CW_START_DIFFICULTY_INVALID`
- 错误文案改为覆盖 `lowest/current/highest/AX-X`

### 2. 运行时恢复错误

新增统一的运行时恢复错误 `CW_START_DIFFICULTY_RECOVERY_REQUIRED`，覆盖所有“无法证明下一步动作仍然安全”的场景，包括但不限于：

1. 精确 `AX-X` 选择时无法稳定读出当前敌人难度。
2. OCR 结果无法反解为合法职级。
3. `返回最高职级` 按钮缺失。
4. 粗调后无进展。
5. 细调后无进展。
6. `lowest` 路径里箭头与当前难度真值都拿不到。
7. `lowest` 箭头缺失但当前层级并不在最低位。
8. 循环预算耗尽。

错误文案固定为：`cw start cannot safely continue difficulty selection; ask agent to enter error recovery`。

该错误的对外协议也固定如下：

1. 即使已经发生了点击或拖拽，这类“已知业务恢复失败”也继续对外保留 `CW_START_DIFFICULTY_RECOVERY_REQUIRED`，不再退化成通用 unknown-result 语义。
2. 如果请求已有 `request_id`，failure 文本仍遵守现有顺序：`request -> shot -> why -> warn -> ref -> recover`。
3. `recover` 继续使用现有 request-status 恢复链路，不新增新的 failure 文本前缀。
4. `requested_difficulty`、`target_enemy_difficulty`、`current_enemy_difficulty`、`reason`、`page` 只进入 envelope `data` 与 request-status，不直接进入默认文本 body。
5. 若恢复错误发生在任何点击/拖拽之前，`request_status.final_state=failed_before_side_effect` 且 `tainted=0`。
6. 若恢复错误发生在点击或拖拽之后，但当前业务失败已被明确识别，`request_status.final_state=completed` 且 `tainted=0`。

该错误固定带上以下上下文字段：

1. `requested_difficulty`
2. `target_enemy_difficulty`（若适用）
3. `current_enemy_difficulty`（若已读到）
4. `reason`
5. `page=entry.new`

## 数据流

`trail cw start --difficulty A7-3`

1. CLI 接收字符串 `A7-3`
2. daemon 校验其属于合法 `AX-X`
3. `start_cw()` 进入现有 entry 链路
4. `_select_difficulty()` 识别出这是精确职级请求
5. 只有在真正进入 `entry.new` 的难度选择 UI 后，scene helper 才读取固定区域 OCR，得到当前敌人难度
6. helper 把当前难度反解成当前 `AX-X` 和全局层级序号
7. 进入循环：`OCR -> 判断 -> 动作 -> settle -> OCR`
8. 根据层级差决定：
   - `返回最高职级`
   - 粗调滑动
   - 细调下箭头
9. 命中目标后继续现有 `start_game -> settle -> boss_preview -> invest` 链路
10. 最终写回 `entry.difficulty=A7-3` 与 `portal.difficulty=A7-3` 这类公开 token，而不是写回 `51`

## 测试计划

至少补齐以下测试：

### 1. 参数与映射测试

- `AX-X` 解析成功与失败
- `A7-3 -> 51`
- `39 -> A6-1`
- 非法输入边界：`A3-6`、`A8-41`、`A9-1`、`A7_3`、`a7-3`

### 2. OCR 读取测试

- 区域 OCR 返回单个 `39`
- 区域 OCR 返回 `3` + `9` 时能拼成 `39`
- OCR 片段无 box 时会被丢弃
- OCR 拼出空洞值例如 `4`、`10`、`58` 时返回统一恢复错误
- OCR 给出冲突数字组合时返回统一恢复错误
- 区域 OCR 无法解析数字时返回统一恢复错误

### 3. 精确职级动作测试

- 当前层级已经等于目标层级时 no-op
- 当前层级低于目标层级时会先点击 `返回最高职级`
- 层级差 `> 10` 时会执行粗调滑动
- 层级差 `<= 10` 时会执行细调点击
- 每次 `返回最高职级`、粗调、细调后都会先 settle 再 OCR
- 细调过程中每一步都会重新 OCR，而不是盲点固定次数
- `返回最高职级` 按钮缺失时进入统一恢复错误
- 粗调后无进展时进入统一恢复错误
- 细调后无进展时进入统一恢复错误
- 循环预算耗尽时进入统一恢复错误
- `entry.continue`、`stage.boss_preview`、`invest` 分支不会触发任何 OCR 读数、滑动或点击

### 4. `lowest` 回归与增强测试

- `lowest` 的原有“点到下箭头消失”行为不回归
- 当前层级距离 `A1-1` 大于 10 时会先走粗调
- `arrow_present=1` 且当前难度 OCR 缺失或无法反解时，立即进入统一恢复错误，且后续不再发生 coarse/fine 动作
- 一开始无箭头但能读到当前难度时，仍能依赖当前层级继续流程
- 一开始无箭头且当前层级是 `A0-1` 时直接 success/no-op
- 一开始无箭头且当前层级不是 `A0-1`、wait 后仍异常时进入统一恢复错误
- 无箭头且当前难度 OCR 失败时，只 `wait` 一次；仍失败则进入统一恢复错误

### 5. CLI、RPC 与协议测试

- `tests/test_atomic_commands.py`：`trail cw start --help` 展示 `lowest/current/highest/AX-X` 与完整 `A0-1..A8-40` 范围
- `tests/test_atomic_commands.py`：`trail cw start --difficulty <非法值>` 不再由 CLI 本地枚举报错，而是进入 daemon 并返回 `CW_START_DIFFICULTY_INVALID`
- `tests/test_cw_rpc_contracts.py`：`A7-3` 会被原样透传为 `cw.start` 的 `difficulty` 参数
- `tests/test_daemon_protocol.py`：daemon 统一使用 `CW_START_DIFFICULTY_INVALID` 处理 `A3-6`、`A8-41`、`A9-1`、`A7_3`、`a7-3`
- `tests/test_daemon_protocol.py`：`CW_START_DIFFICULTY_RECOVERY_REQUIRED` 在无 side effect 时固定为 `final_state=failed_before_side_effect`、`tainted=0`
- `tests/test_daemon_protocol.py`：`CW_START_DIFFICULTY_RECOVERY_REQUIRED` 在已发生 side effect 但业务失败可判定时固定为 `final_state=completed`、`tainted=0`
- `tests/test_output_rendering.py`：`CW_START_DIFFICULTY_RECOVERY_REQUIRED` 失败时仍遵守既有 failure 顺序，且新增上下文字段只留在 envelope data
- `tests/test_output_rendering.py`：README 示例与 help/skill 使用同一公开口径，不展示 `AX-X -> enemy_difficulty` 数值映射
- `tests/test_cw_portal.py`：`scene_state["cw"]["entry"]["difficulty"]` 与 `portal.difficulty` 原样保存 `A7-3` 这类公开 token
- `tests/test_cw_portal.py`：`cw.portal.restart` 会重放已记录的 `A7-3`
- `tests/test_skill_structure.py`：active skill 对 `trail cw start --difficulty` 的说明更新为 `lowest/current/highest/AX-X`

## 文档同步要求

实现本设计时，除代码与测试外，还必须同步更新：

1. `README.md`
2. `trail/commands/cw.py` 对应的 `trail cw start --help`
3. `skills/trail-cw-entry/SKILL.md`
4. `skills/trail-cw-entry/references/player-language-mapping.md`
5. `skills/trail-cw-entry/references/confirmation-checklist.md`
6. 受影响的 CLI/help/renderer/RPC/skill 断言测试
7. 如果最终实现需要扩展 `cw.start` 的 mutation failure 协议，使 `CW_START_DIFFICULTY_RECOVERY_REQUIRED` 在 side effect 之后仍作为稳定公共错误码对外可见，则同步更新 `AGENTS.md`、README 示例和对应协议测试

## 结论

本设计在不新增命令的前提下，把 `trail cw start --difficulty` 从三态粗粒度选择扩展为“粗粒度 + 精确职级”混合输入。

其中最关键的新增约束有三条：

1. `AX-X` 的真值不是 UI 文案，而是固定区域 OCR 到的 `敌人难度` 数值。
2. 粗调阈值看的是全局层级差值，不是难度差值。
3. 一旦当前难度真值不可得，必须显式进入错误恢复，而不是继续盲点。
