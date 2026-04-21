---
name: trail-cw
description: Use when an agent needs to orchestrate a full Currency Wars run by looping on internal Currency Wars stage detection and dispatching to Trail sub-skills.
---

# Skill: trail-cw

## 职责

- 编排一整局货币战争
- 在关键阶段切换到攻略、编队、商店、补给、事件子 skill
- `trail cw stage` 只适用于已进入货币战争后的内部阶段快速检测/等待，不用于登录页、大世界等非 CW 场景判断，也不代替分组动作执行
- 负责循环、阶段切换和失败恢复，不重写 CLI 原子动作
- 把每条命令返回的 `screenshot` 视为第一手事实来源；如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 `data` / `detect` / `read` / `status` 文本

## 输入

- `session_id`
- `guide_url` 或 `lineup_id`
- 开局模式：`new` 或 `continue`
- 首页偏好：`攻略优先` / `环境优先`
- 对局模式：`standard` / `overclock`
- 是否接受刷开局（允许使用 `trail cw portal refresh` / `trail cw portal restart`）

## 验收清单

1. 使用已有 session
2. `cw enter` 到首页并确认首页偏好
3. `cw start` 到投资环境页
4. 选择投资环境并进入游戏
5. 在合适时机拉取并应用攻略
6. 循环：stage detect -> 分发到 slots/shop/replenish/events 子 skill
7. 直到 settle next / game over

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 使用来自 `trail-hsr` 的既有 `session_id`
3. `trail cw enter --session <id>`，确认已经停在货币战争首页；若返回 `info already_home=1`，表示本次是 no-op 成功
4. 在首页先问清并记录：
   - 这局是 `攻略优先` 还是 `环境优先`
   - 使用 `standard` 还是 `overclock`
   - 是否接受刷开局（后续是否允许 `trail cw portal refresh` / `trail cw portal restart`）
5. 如果是 `攻略优先`：
    - 先切到 `trail-cw-guide` 选择或拉取攻略
    - 再运行 `trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest --battle-mode standard|overclock`
    - 如果已经手动进入投资环境页，但 `cw start` 中途失败或 session 没有 fresh portal snapshot，运行 `trail cw portal detect --session <id>`；不要回退到 `trail cw start`
    - detect 后可直接 `select`；`refresh` 仍是点击刷新后生成新的三张卡；`restart` 依旧要求已有开局真值，detect 不会补录 `mode/difficulty/battle_mode`
    - 根据三卡摘要判断是否直接 `trail cw portal select --session <id> --card-idx <n>`，或在用户允许时执行 `trail cw portal refresh --session <id>` / `trail cw portal restart --session <id>`；投资环境卡片会输出 `投资环境/说明/待收集/score`，下挂攻略摘要复用 `guide.list.cw` 的中文语义
6. 如果是 `环境优先`：
    - 先运行 `trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest --battle-mode standard|overclock`
    - 如果已经手动进入投资环境页，但 `cw start` 中途失败或 session 没有 fresh portal snapshot，运行 `trail cw portal detect --session <id>`；不要回退到 `trail cw start`
    - detect 后可直接 `select`；`refresh` 仍是点击刷新后生成新的三张卡；`restart` 依旧要求已有开局真值，detect 不会补录 `mode/difficulty/battle_mode`
    - 读取返回的三卡摘要，必要时在用户允许下执行 `trail cw portal refresh --session <id>` / `trail cw portal restart --session <id>`
    - 若需要按环境 / 羁绊 / 角色反查攻略，切到 `trail-cw-guide`，使用 `trail guide list cw --portal <title>`、`trail guide list cw --portal-id <id>`、`trail guide list cw --trait <name>` 或 `trail guide list cw --role <name>`
    - 再执行 `trail cw portal select --session <id> --card-idx <n>` 进入游戏；真正应用攻略要等进入游戏后再做
7. 进入游戏后，如果当前 session 还没有已加载的攻略，则切到 `trail-cw-guide`，执行：
     - `trail guide fetch cw <lineup_url|lineup_id>`
      返回后先核对 `攻略标题` / `攻略标签` / `羁绊列表` / `攻略码` / `最低金币` / `投资环境` / `优选投资策略` / `次选投资策略` / `运营思路`；其中布尔类攻略特征会作为 `#标签` 并入 `攻略标签`
     - `trail cw guide apply --session <id> --lineup-id <lineup_id>`
8. 如果是 `continue` 模式，且 session 中已经有可用的 guide 状态，则跳过重新 apply
9. 循环执行：
    - `trail cw stage detect --session <id>`
    - 根据 `data.value` 分发：
       - `preparation` 或需要调整编队时，切到 `trail-cw-slots`
      - `shop` 时，切到 `trail-cw-shop`
      - `replenish`、`encounter`、`fortune` 时，切到 `trail-cw-replenish`
      - `invest` 时，先根据 screenshot 或页面标题判断；如果 screenshot 或页面标题显示“请选择投资策略”，优先使用 `trail cw strategy detect|refresh|select`；只有普通局内 invest 事件才切到 `trail-cw-replenish`
      - `boss_preview`、`event`、`settle`、`game_over` 或战斗衔接时，切到 `trail-cw-events`
    - 每次动作后如果看到 `shot path=...` 和 `info read_image_first=1`，先读图，再看返回的 `data`，不要假设旧状态仍然有效
    - 如果 `detect/read` 与截图观感冲突，以截图为准，再决定下一条显式动作命令
10. 在 `settle` 阶段执行 `trail cw settle next --session <id>` 后继续下一轮识别；在 `game_over` 后退出

## 执行规则

- 攻略拉取和攻略应用分两步，不能隐式复用“上一条 fetch 结果”
- `guide fetch` 主要是把完整攻略内容直接提供给 Agent；`cw guide current/apply` 只看当前已应用攻略摘要，真正的本地攻略快照ID记录发生在 `cw guide apply` 成功之后
- `攻略快照ID` 只是 `cw guide current/apply` 的回顾/追踪事实，不是整局编排入口；整局编排仍以 `guide_url` 或 `lineup_id` 作为攻略输入
- 所有 `trail cw ...` 命令都必须显式传入 `--session <id>`
- 阶段切换由这个 skill 决定，子 skill 不负责整局调度
- `trail cw enter` 只到首页，不再直接推进到投资环境页；真正开局一律使用 `trail cw start`
- `trail cw start` 负责把首页推进到投资环境页，并把 `mode / difficulty / battle_mode` 固化到当前 session
- `trail cw guide` 只负责当前对局攻略的 apply/current；攻略查询与拉取继续使用 `trail guide ... cw`
- `trail cw portal select|detect|refresh|restart` 只在投资环境页可用；`detect` 只重建当前三张卡识别结果，不点击、不刷新、不重开，且 detect 后可直接 `select`；`refresh` 才会点击刷新后生成新的三张卡；`restart` 依旧要求已有开局真值，detect 不会补录 `mode/difficulty/battle_mode`；`refresh/restart` 是否允许，先看用户在首页给出的偏好
- `stage=invest` 时，不要默认走 `trail cw invest read|choose`；如果 screenshot 或页面标题显示“请选择投资策略”，优先使用 `trail cw strategy detect|refresh|select`
- 如果当前动作让 `stage` 失效，立刻回到 `trail cw stage detect --session <id>`
- `continue` 模式表示“继续当前 UI 进度”，不是重新创建 session；只有当 session 中缺少 guide 状态时，才重新走攻略子 skill
- 不要假设 `read_*` 命令已经穷尽了所有 UI 语义；必要时直接根据 screenshot 做多模态判断后，再调用显式动作命令
- 在 list 阶段选攻略时，同时读取 `版本` 与 `攻略标签` / `最终阵容`，不要回退到 portal / hard / change_equip / expert 这些旧字段名
- 槽位名字确认时，先看当前 screenshot，再优先使用 `trail cw slots read --session <id> --slot ...` 做定向确认；只有需要完整快照兜底时，才不传 `--slot`
- `trail ocr read` 只保留给非槽位特定文字或通用 OCR 场景；如果同时需要这类 OCR 文字和对应截图，优先只运行一次 `trail ocr read`，不要紧接着再补额外截图命令
- `trail ocr read` 默认是 `ocr_mode=fast`（`1280x720`）；当你怀疑快档漏字、需要更稳的 box，或要人工复核关键文字时，再显式加 `--ocr-mode high`。如需固定做高精度补跑对照，可加 `--retry-high always`；常规情况下保持默认 `--retry-high auto`
- 如果 `detect/read` 与截图观感冲突，以截图为准；guide 相关元数据优先看 `攻略标签/最终阵容/投资环境` 这些中文摘要，不要继续依赖旧字段名做判断
- `trail cw invest read|choose` 继续只表示局内 invest 事件，不要把它们当成开局投资环境页命令；如果 screenshot 或页面标题显示“请选择投资策略”，则改走 `trail cw strategy detect|refresh|select`
- 开发期调试场景命令时，可给 CLI 加顶层 `--verbose` 查看中间 trace
- 如果结果未知、当前 session 不可继续使用，或你需要手工恢复运行态，停止自动重放并切回 `trail-hsr-advanced`
