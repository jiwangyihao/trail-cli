# 货币战争首页与投资环境流程设计

## 目标

把当前语义过长的 `cw enter` 拆成更可控的两段流程，使 Agent 能在“货币战争首页”这个安全决策点先询问用户玩法偏好，再决定是否开局、按攻略优先还是投资环境优先推进，并支持刷开局。

## 背景

当前 `cw enter` 会从大世界一路推进到投资环境页。这有三个问题：

1. Agent 还没来得及问用户想玩 `standard` 还是 `overclock`。
2. Agent 还没来得及问用户是“攻略优先”还是“环境优先”。
3. 很多攻略要求特定投资环境，若不先定策略，直接推进到投资环境页会让后续决策变被动。

因此需要把“进入货币战争首页”和“真正开始一局”拆开。

## 核心命令语义

### `cw enter`

`cw enter` 的唯一职责是：

- 从任意外部状态推进到“货币战争首页 / 开局首页”
- 若已经在首页，则直接成功返回，不再重复走大世界入口链
- 不再接收 `mode / difficulty / battle_mode`

它不再负责：

- 直接点 `开始` / `新游戏` / `继续`
- 直接推进到投资环境页
- 直接开始一局

具体行为：

1. 若当前在大世界，则执行外层入口链：
   - `F4 -> 旷宇纷争 -> 货币战争 -> 前往参与`
2. 若当前已在货币战争首页，则 no-op 成功返回。
3. 若当前已经在更深层页面（例如投资环境页、补给/备战/商店等游戏内阶段），直接稳定报错，并带当前阶段信息；不负责继续推进到新局后阶段。

`cw enter` 的成功结果必须冻结成“已到首页”语义，不再保留旧的 `mode / difficulty / battle` 事实。第一版默认文本协议固定为：

- 首行：`ok cw.enter page=home`
- 若本次是 no-op，因为原本已在首页，可追加：`info already_home=1`

### `cw start`

新增 `cw start`，职责是：

- 从“货币战争首页”推进到“投资环境选择页”
- 自动 OCR 当前三张投资环境卡
- 返回结构化的环境摘要结果
- 接收并持久化本局的 `mode / difficulty / battle_mode`

具体行为：

1. 若当前在货币战争首页，则根据传入的 `mode / difficulty / battle_mode` 执行开局流程，推进到投资环境页。
2. 若当前已经在投资环境页，则 no-op 成功返回当前识别结果。
3. 若当前已经进入游戏内阶段（补给/备战/商店等），直接报错，不做回推。

`cw start` 的返回不再是“开局成功”，而是“当前投资环境页的三张卡摘要”。

`cw start` 是后续 `cw portal.restart` 的 canonical 参数来源，必须把 `mode / difficulty / battle_mode` 持久化到当前 session，作为“重开一局同模式同难度”的真相源。

若 `cw start` 在“已在投资环境页”的 no-op 分支被调用：

- 如果当前 session 尚未记录 `mode / difficulty / battle_mode`，则本次调用仍要写入这些值；
- 如果当前 session 已记录且与本次传参冲突，则直接稳定报错，不允许静默覆盖。

## 投资环境识别语义

`cw start` 和后续 `cw portal.refresh` 都需要返回投资环境识别结果。

每张卡只保留一个最可能 portal 候选，字段为：

- `card_idx`
- `portal_title`
- `portal_description`
- `score`

不返回：

- `portal_id`
- `ocr_text`
- `box`
- `center`

识别步骤：

1. 对投资环境页做 OCR。
2. 按三张卡固定的左右布局，把 OCR 片段按 `center.x` 分配到 3 个 card lane。
3. 对每个 lane 内的 OCR 片段，按 `top -> left` 排序，并把 y 距离足够近的片段合并成同一行，再拼成 card text。
4. 用 `guide config cw` 返回的 `portal_list` 作为 canonical 数据源。
5. 对每张卡，把合并后的 card text 与每个 portal 的 `title + description` 做归一化相似度计算，取分数最高的 1 个 portal 作为结果。

返回给 Agent 的结果是：

- 第 1/2/3 张卡分别最可能是什么 portal
- 该 portal 的官方标题与完整描述
- 以及匹配分数

## 投资环境操作命令

### `cw portal.select`

新增 `cw portal.select`。

职责：

- 在投资环境页选择某张卡
- 立刻点击确认按钮

输入：

- `card_idx`，有效值仅 `1/2/3`

错误语义：

- 不在投资环境页：稳定报错
- `card_idx` 非 `1/2/3`：稳定报错，并给出可选值

### `cw portal.refresh`

新增 `cw portal.refresh`。

职责：

- 点击投资环境页刷新按钮
- 等刷新完成
- 重新 OCR/合并/匹配
- 返回刷新后的三张卡摘要

错误语义：

- 不在投资环境页：稳定报错
- 没有刷新机会 / 刷新按钮不可用：稳定报错

### `cw portal.restart`

新增 `cw portal.restart`。

职责：

- 用于刷开局投资环境
- 选择第一张卡并确认
- 进入对局后立即退出
- 按当前 session 里记住的 `mode / difficulty / battle_mode` 重开一局
- 再次停在投资环境页，并返回新的一组三卡摘要

实现上允许组合前面的场景命令，但必须显式引入一个“从局内退出回首页”的内部 helper；不能把这一步留给实现者临场发挥。

错误语义：

- 当前 session 中没有已知 `mode / difficulty / battle_mode`：稳定报错
- 不在投资环境页：稳定报错

## 攻略过滤语义

`guide list cw` 需要新增本地投资环境过滤能力。

新增输入：

- `--portal`
- 或 `--portal-id`

处理流程：

1. `guide config cw` 需要新增 `portal_list`，并冻结每项至少包含：
   - `portal_id`
   - `title`
   - `description`
2. 使用 `guide config cw` 的 `portal_list` 做 canonical 校验。
3. 若用户/Agent 传入的 portal 不存在：
   - 返回稳定错误
   - 同时给出最接近的 3 个候选
4. 若存在：
   - 在本地做**有界**过滤：第一版最多过取 60 条原始 list 结果，再按 portal 过滤，返回最多 `limit` 条
   - 第一版 portal 过滤模式下，不承诺复用官方 `next_page_token` 继续翻页；`more=0`、`next` 省略，统一视为单次有界本地过滤结果
5. 若同时传入 `--portal` 和 `--portal-id`：
   - 第一版直接稳定报错，不允许双重指定

因为官方接口本身不支持按投资环境筛攻略，所以这里的 portal 过滤属于命令层补充能力。

## Agent 交互语义

不设默认偏好。Skill 必须在“货币战争首页”这个安全决策点先问用户：

- `攻略优先` 还是 `环境优先`
- `standard` 还是 `overclock`
- 是否接受刷开局

推荐交互顺序：

1. `cw enter` 到首页
2. skill 询问用户偏好
3. 根据偏好分流：

### 攻略优先

1. 先定 `guide / lineup`
2. 再调用 `cw start`
3. 看当前三张环境是否符合攻略需求
4. 不符合则决定：
   - `cw portal.refresh`
   - 或 `cw portal.restart`

### 环境优先

1. 先调用 `cw start`
2. 拿当前 portal 摘要去执行 `guide list cw --portal ...`
3. 再由用户/Agent 决定最终攻略

## 错误与幂等边界

- `cw enter`：已在首页时必须 no-op 成功。
- `cw start`：已在投资环境页时必须 no-op 成功返回当前三卡摘要。
- `cw start`：已进局时必须稳定报错。
- `cw portal.select` / `cw portal.refresh` / `cw portal.restart`：只有在投资环境页才允许执行。

## 测试边界

### daemon-side

新增/更新：

- `cw start`
- `cw portal.select`
- `cw portal.refresh`
- `cw portal.restart`
- `guide list` 的 portal 过滤与候选错误

### CLI-side

新增对应 wrapper 的 RPC 契约测试。

### 文档与技能

需要同步更新：

- `README.md`
- `skills/trail-cw/SKILL.md`
- 相关 `trail-cw-*` skill 文档

核心心智必须改成：

- `cw enter` 到首页
- 在首页先问用户偏好
- 再走 `cw start` / `cw portal.*` / `guide list --portal ...` 的后续流程

## 默认文本协议约束

新命令需要进入现有 renderer 家族，并冻结稳定文本输出：

- `cw.start`
  - 首行：`ok cw.start cards=<n> matched=<n>`
  - 正文：每张卡使用 `opt` 行输出 `idx / title / score`，再用第二条 `opt` 行输出 `idx / desc`
- `cw.enter`
  - 首行：`ok cw.enter page=home`
  - 若是 no-op，可追加 `info already_home=1`
- `cw.portal.refresh`
  - 与 `cw.start` 同一家族，输出同样的三卡摘要
- `cw.portal.restart`
  - 与 `cw.start` 同一家族，输出同样的三卡摘要
- `cw.portal.select`
  - 首行：`ok cw.portal.select idx=<card_idx> title=<portal_title>`
  - 若后续立即进入其他稳定阶段，可继续附 `info` 行说明结果
- `guide.list.cw --portal...`
  - 继续沿用 `guide.list.cw` renderer 家族，但结果集已是 portal 过滤后的本地结果
  - 第一版 portal 过滤模式下固定 `more=0`，不输出 `next`

所有失败仍然遵守现有统一失败协议：

- `fail <command> code=...`
- 之后按顺序输出 `request` / `shot` / `why` / `warn` / `ref` / `recover`
