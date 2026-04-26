# Trail CLI

Trail 是面向《崩坏：星穹铁道》的独立命令行工具，默认输出 Agent 友好的紧凑文本协议，并在命令产生截图时显式返回截图路径，供多模态 agent 直接消费。

## Simple / Advanced 分层

- `trail` CLI 现在是非管理员薄壳，负责参数解析、workspace 解析、RPC 请求发送，以及把结果渲染为默认文本协议、显式 `--format yaml` 兜底或 `--verbose` 调试层
- 常驻 `traild` daemon 持有 runtime、截图、OCR、找图、输入、guide、`cw` 场景执行和 session 热状态
- 默认 simple 层只教 3 个起手能力：`trail start`、`trail ocr read`、`trail input ...`
- `trail start` 会自动收口 daemon、游戏、窗口与 session，并返回可继续使用的 `session=<id>`
- 如果 `trail start` 失败，或 simple 层不能满足定位需求，再由上层 skill 内部升级到 `trail-hsr-advanced` 处理 daemon / window / session / screen / image / state 等进阶命令

## Quick Start

推荐入口（simple-first）：

- 启动并拿到可用 session：`trail start`
- 观察当前画面：`trail ocr read`
- 执行明确动作：`trail input click ...`、`trail input drag ...`、`trail input key ...`
- 需要进入具体场景 skill 时，先由 `trail-hsr` 接管，再按 registry 交给当前已上线的 scene entry
- 部分已配置命令在 success 后会直接输出 handoff `info`，提示 Agent 切到对应 scene entry 或内部阶段 skill；当前包括 `cw.enter -> trail-cw-entry` 与 `cw.portal.select -> trail-cw-prep`

## Skill 拓扑

- `trail-hsr` 是对外总入口，用于接管并继续推进《崩坏：星穹铁道》常规游玩。
- `trail-<scene>-entry` 是对外场景入口；只有 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前入口。
- `trail-cw-entry` 是货币战争当前入口 skill / scene entry；它只负责该玩法入口后的编排，不是整局 owner。
- `trail-cw-guide` 是可直接进入的 public 攻略选择 skill；用户明确要先选攻略时可以直接切到它。
- `trail-cw-guide` 不是 scene entry、不是默认 owner、也不是整局 owner；真正进入开局流程仍要回到 `trail-cw-entry`。
- `trail-cw-portal` 是 internal portal-page skill；主要在 `trail cw start` 或 `trail cw portal refresh` 成功停留在投资环境页后切入，不是 direct-user 公共入口、不是 scene entry、也不是 owner。
- `trail-cw-portal` 在投资环境页负责 `portal detect/refresh/restart/select` 与环境优先逻辑；如果攻略还没定，就切到 `trail-cw-guide` 的无人值守模式按当前环境定攻略，再回到当前投资环境页流程。
- `trail-cw-prep` 是 internal 普通备战阶段 skill；只在 `cw.portal.select` 成功后的 post-portal handoff 中切入，不是 public scene entry、不是 direct-user、不是 owner。
- `trail-hsr-advanced` 是内部恢复层，用于启动失败、窗口接管异常、daemon / session 恢复等底层问题。
- `trail-hsr-advanced` 不作为用户入口；只有 `trail-hsr` 或当前 active 的 scene entry 需要恢复链路时才会内部升级到它。
- 旧货币战争 archive skill 已归档，不再作为 active owner 或推荐入口。

手工 CLI 冒烟顺序：

- `trail start`
- `trail ocr read`
- `trail input ...`

## Advanced 启动与排障

- simple 层失败或不够用时，由上层 active skill 内部升级到 `trail-hsr-advanced`
- 进阶原子命令包括：`trail daemon install`、`trail daemon status`、`trail daemon start`
- 结果未知或需要恢复时，使用：`trail daemon request-status --request-id <id>`、`trail daemon reconcile-session --session <id>`
- 需要手工控制游戏与窗口时，使用：`trail window launch --channel official|bilibili|global`、`trail window attach --window-title "崩坏：星穹铁道"`
- 需要手工建 session 时，使用：`trail session create`
- 需要额外截图、模板识别或状态转储时，使用：`trail screen shot`、`trail image ...`、`trail state dump`

## 货币战争流程

- 先通过 `trail start` 或 `trail session create` 拿到可用 `session`
- `trail cw enter --session <id>` 只负责把页面带到货币战争首页
- `trail cw enter --session <id>` success 尾行会返回 `info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered`，表示下一步应优先切到 `trail-cw-entry`
- `trail cw portal select --session <id> --card-idx <n>` success 尾行会返回 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`，表示进入普通备战阶段后应切到 `trail-cw-prep`
- 当用户明确要先选攻略，或 `trail-cw-entry` 走到“攻略优先 / 先定攻略”时，应切到 `trail-cw-guide`；真正进入开局流程仍要回到 `trail-cw-entry`
- 到首页后先确认本局偏好：
  - `攻略优先` / `环境优先`
  - `standard` / `overclock`
  - 是否接受刷开局（后续是否允许 `trail cw portal refresh` / `trail cw portal restart`）
- `trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest|AX-X --battle-mode standard|overclock` 负责把首页推进到投资环境页
- `trail cw start --session <id> ...` 成功进入投资环境页后，上层编排应切到 internal 的 `trail-cw-portal`，而不是把它当成新的 direct-user 入口
- `AX-X` 使用公开职级层级表示法，范围 `A0-1..A8-40`；例如 `A7-3`
- 如果已经手动进入投资环境页，但 `cw start` 中途失败或 session 没有 fresh portal snapshot，使用 `trail cw portal detect --session <id>`；不要重复执行 `trail cw start`
- `detect = 重识别当前三张卡，不点击`
- detect 后可直接 `select`
- `refresh = 点击刷新后生成新的三张卡`
- `restart` 依旧要求已有开局真值；detect 不会补录 `mode/difficulty/battle_mode`
- 如果投资环境页里还没定攻略，由 `trail-cw-portal` 切到 `trail-cw-guide` 的无人值守模式，按当前环境、`待收集=1` 与版本自动定攻略
- 如果先按环境选攻略，再用：`trail guide list cw --portal <title>` 或 `trail guide list cw --portal-id <id>`
- 如需免查 config 直接筛攻略，也可以在 list 阶段使用：`trail guide list cw --trait <name>`、`trail guide list cw --role <name>`；需要脚本固化或精确复现时，再切到 `--trait-id` / `--role-id`
- `guide list cw` 列表结果现在会直接返回 `version`，list 阶段就应把版本兼容性纳入筛选判断
- 查看返回的三卡摘要后，根据需要执行：
  - `trail cw portal select --session <id> --card-idx <n>`
  - `trail cw portal detect --session <id>`
  - `trail cw portal refresh --session <id>`
  - `trail cw portal restart --session <id>`
- 先用：`trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入当前 session；这一步不执行 UI 应用，也不创建当前攻略快照或额外追踪产物
- 回到开局链路后，`trail cw portal select --session <id> --card-idx <n>` 成功后会自动应用当前已选攻略
- `cw.portal.select` 成功进入普通备战后会 handoff 到 internal 的 `trail-cw-prep`，由它先读截图和收集普通备战事实
- `trail cw guide apply --session <id>` 只作为手动兜底；如需回顾当前已选攻略：`trail cw guide current --session <id>`
- `trail cw guide` 只负责当前对局已选攻略的 current/apply；筛攻略和拉攻略继续使用顶层 `trail guide ... cw`
- `stage=invest` 时，不要默认走 `trail cw invest.*`
- 如果 screenshot 或页面标题显示“请选择投资策略”，局内投资策略页应优先使用 `trail cw strategy detect|refresh|select`
- 开局投资环境页仍是 `trail cw portal.*`；普通局内 invest 事件的兼容/粗粒度入口才是 `trail cw invest.*`
- `trail cw invest read|choose` 继续只表示局内 invest 事件，不是开局投资环境页命令
- 策略页显式流程示例：`trail cw stage detect --session <id>` -> `trail cw strategy detect --session <id>` -> `trail cw strategy refresh --session <id> --card-idx <n>` -> `trail cw strategy select --session <id> --card-idx <n>`
- 如需批量从手牌上场：`trail cw slots place --session <id> --action hand:0,front:0 --action hand:1,back:2`
- 如需卖牌，先看建议：`trail cw hand sell-plan --session <id>`；真正出售时执行：`trail cw hand sell --session <id> --slot 0 --slot 2`
- 这两类命令都严格保序、遇错即停；只要中途失败且前面动作可能已生效，就应重新执行 `trail cw slots read`
- 常规 battle / settle 流程默认执行：`trail cw battle run --session <id> --timeout 570`
- `trail cw battle run` 可能耗时接近 10 分钟，命令行工具的外部 timeout 至少调到 11 分钟
- 如果 `trail cw battle run` 已在 daemon 内成功收口但当前 stdout 丢失，立刻执行：`trail state dump --session <id> --format yaml`
- `trail cw battle start` / `trail cw battle continue` / `trail cw settle next` 仍保留为 CLI 兼容命令，但只建议在内部 fallback 流程中手工拆链使用

编队槽位读取建议：

- 先看当前阶段已有 screenshot，再决定是否真的需要读取槽位名字。
- `trail cw slots read --session <id> --slot front:0 --slot hand:3` 是首选定向确认路径，只读“看见有角色但名字不确定”的槽位。
- 不传 `--slot` 时，`trail cw slots read --session <id>` 仍保留现有全量读取语义，只作为完整快照兜底，不表示默认行为已经改变。
- 如果局部读取结果带 `stale=1`，它不等于新的完整 fresh 快照；后续判断仍要结合已有截图和基线来源。
- `slots.read` 的 `slot ...` 行现在可能附带 `star=<n>`；没有稳定数出星级时不会强行输出 `star=`。

## 命令面概览

- `start`：simple-first 启动入口，自动收口 daemon、游戏、窗口与 session
- `daemon`：进阶安装、启动、停止、查看常驻服务，并提供 `request-status` / `reconcile-session` 管理查询面
- `session`：进阶创建并持久化 session
- `guide`：拉取攻略内容、返回筛选枚举与攻略列表
- `window`：进阶做窗口绑定检查或手工启动游戏
- `window launch`：支持按 channel 自动解析《崩坏：星穹铁道》启动路径，必要时仍可显式提供 `--game-path`
- `screen`：进阶截图
- `ocr`：OCR 读取
- `image`：进阶模板识别与等待
- `input`：点击、拖拽、按键
- `state`：进阶读取 session 与 scene state
- `cw`：货币战争固定流程命令；`enter` 到首页，`start` 从首页进入投资环境页；`portal` 负责开局投资环境页的识别/选择/刷新/重开；`strategy` 负责局内“请选择投资策略”页的识别/单卡刷新/选择；常规 battle / settle 默认入口是 `trail cw battle run --session <id> --timeout 570`；`battle` / `settle` 分组仍保留兼容原子命令，但只建议在内部 fallback 流程使用；`stage` 只用于已进入货币战争后的内部阶段快速检测/等待；`invest` 只保留普通局内 invest 事件的兼容/粗粒度入口；其余分组处理局内阶段与资源，包含 `portal`、`strategy`、`guide`、`stage`、`slots`、`shop`、`crystals`、`hand`、`replenish`、`invest`、`encounter`、`fortune`、`boss-preview`、`battle`、`settle`、`event`

## Window Launch

- 入口命令：`trail window launch --channel official|bilibili|global`
- 显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退
- 无显式路径时固定顺序：历史成功路径 -> 默认路径 -> 直接问用户
- 默认路径只覆盖 `official`，冻结值为 `C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`
- `bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`
- 显式路径不存在时返回 `GAME_PATH_NOT_FOUND`
- 显式路径存在但启动失败时返回 `GAME_LAUNCH_FAILED`
- Agent 不应默认乱搜路径；收到 `GAME_PATH_REQUIRED` 表示现在该直接问用户提供路径
- 如果游戏已成功启动但历史路径写回失败，仍返回 success，并追加 `warn code=GAME_PATH_PERSIST_FAILED`
- 上述 success warning 的稳定码是 `GAME_PATH_PERSIST_FAILED`

## OCR 选项

`trail ocr read` 当前冻结以下稳定参数与默认值入口：

- `trail ocr read --provider auto|cpu|dml`
- `trail ocr read --lang ch`
- `trail ocr read --use-cls/--no-use-cls`
- `trail ocr read --text-score <float>`
- `trail ocr read --ocr-mode fast|high`
- `trail ocr read --retry-high auto|never|always`
- `TRAIL_OCR_PROVIDER`、`TRAIL_OCR_LANG`、`TRAIL_OCR_USE_CLS`、`TRAIL_OCR_TEXT_SCORE` 用于设置低优先级默认值
- `TRAIL_OCR_MODE`、`TRAIL_OCR_RETRY_HIGH` 用于设置低优先级默认值

OCR 首版语义：

- `lang` 首版仅支持 `ch`；其他值返回 `OCR_LANG_UNSUPPORTED`
- `provider=auto` 会优先尝试 DirectML；如果当前环境不可用或本次 DML 推理失败，会自动回退 CPU
- `provider=cpu` 强制走 CPU
- `provider=dml` 会把 DirectML 视为硬约束；环境不可用或推理期 DML 失败都会返回 `OCR_PROVIDER_UNAVAILABLE`
- 默认 `ocr_mode=fast`
- 默认 `retry_high=auto`
- `fast = 1280x720`
- `high = native`
- `retry_high=auto` 只在 `hits==0`、平均分过低、或出现 `OCR_LOW_CONFIDENCE` 时触发
- `retry_high=always` 在 `ocr_mode=fast` 下会先跑 `fast`，再无条件补跑一次 `high`
- `ocr_mode=high` 下 `retry_high` 为 no-op
- 模式与重试事实只在 `--verbose` 下出现
- 默认成功输出协议保持不变：仍然是 `ok ocr.read hits=<n>`；有截图时固定按 `shot` -> `info read_image_first=1` -> `text` 输出

DirectML 安装与环境 profile 说明：

- Windows 默认安装就是 DML 基线；`provider=auto` 的默认行为与默认依赖保持一致
- CPU 现在只作为显式兼容/排障路径；需要 CPU 时，使用 `provider=cpu` 强制回落
- DirectML 仍然是 Windows 定向方案，不承诺为通用跨平台 GPU 方案
- 不要在同一环境里模糊共存 `onnxruntime` 与 `onnxruntime-directml`；应确认当前环境最终只保留预期的 ONNX Runtime 变体
- 排障时先确认当前 profile、已安装的 ORT 变体和 `trail ocr read --provider dml` 的实际 failure/success 结果，再判断是否属于环境不满足或运行期 DML 失败

## 输出约定

- 默认输出是紧凑文本协议，统一首行为 `<ok|fail> <command> <核心事实...>`，例如 `ok cw.shop.status count=2`
- 默认模式是常规消费层；`--format yaml` 是结构化兜底，`--verbose` 是开发/排障层，不应作为终端 Agent 的常规依赖
- 默认模式绝不输出 YAML；只有显式指定 `--format yaml` 且命令进入 allowlist 时，才会在首行摘要后追加结构化块
- 常见正文前缀包括 `shot`、`item`、`guide`、`text`、`info`、`why`、`warn`、`ref`、`request`、`recover`；`debug` 仅在 `--verbose` 下追加
- `shot path=...` 表示当前命令结果对应的截图路径；带截图的 success 结果会先输出 `shot path=...`，再输出 `info read_image_first=1`，然后才是实体行
- `info read_image_first=1` 只出现在带截图的 success 文本路径，表示 Agent 必须先阅读本次命令返回的原始截图，再参考后续压缩文本
- 已配置 workflow handoff 的 success 结果会在正常 success 内容、`warn`、`ref` 之后，额外追加一行尾行强提示：`info handoff_skill=... handoff_strength=... handoff_reason=...`；它始终是 success 输出最后一行
- 默认失败路径只要当前结果携带 `request_id`，就会保留 `request id=<id>`，用于恢复与排障
- 只有结果未知或当前失败显式可恢复时，才会出现 `recover action=daemon.request_status request=<id>`；仅有 `request id=<id>` 不等于当前失败一定可恢复
- `trail daemon request-status --request-id <id>` 用于回查某个请求的终态、关联 session、最近可见阶段与污染状态，典型输出是 `ok daemon.request_status request=req-42 session=sess-1 final_state=completed last_visible_stage=responded tainted=0`
- `tainted=1` 表示当前 failure 或状态带有运行态污染风险；继续执行前，先确认请求终态，再决定是否执行 `trail daemon reconcile-session --session <id>`
- `--format yaml` 仍然保留同一条首行摘要，但只在允许的命令上提供结构化视图；当前更适合 `daemon status`、`state dump`、`guide fetch cw`、`guide config cw` 这类结果体量更大或层级更深的命令
- `--verbose` 只追加 `debug kind=...` 调试行，不改变默认文本协议里的事实集合与顺序；shared helper 的 major action trace 会固定追加为 `debug kind=trace ...` 行
- finalized helper 动作 trace 至少携带 `step=<helper>`、`ts=<UTC RFC3339 毫秒时间戳>` 与 `ok=0|1`
- `trace/context` 边界固定：`trace` 只承载 finalized helper 动作事件；`context` 只承载跨动作请求级事实，不能再拿来补某次 helper 的动作结果
- OCR 的 mode/retry 事实不再作为 top-level debug context 暴露，而是通过 `debug kind=trace step=ocr ...` 出现；legacy trace 仍按现有 key/value 规则兼容渲染
- 对多模态 agent 来说，截图仍是第一手事实来源；看到 `shot path=...` 且紧随 `info read_image_first=1` 时，必须先读这张原始图，再参考后续 `detect/read/status` 压缩文本

文本协议示例：

```text
ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8
guide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问
guide 羁绊列表=1智识|1巡猎|2量子
guide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携
guide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云
guide 阶段=前期阵容 前台=黑塔/star:1/rarity:1 后台=艾丝妲/star:1/rarity:1 羁绊=1智识
guide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 后台=佩拉/star:2/rarity:2 羁绊=1巡猎|2量子
guide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯|战场进化手册 次选装备=胜利之旗
guide 运营思路="前期：过渡\n中期：D牌\n后期：补强"
```

- `trail guide fetch cw` 默认文本会直接返回你选中的完整攻略字段，字段名尽量使用货币战争页面里的中文文案；现在还会补充 `羁绊列表`、`运营思路`，以及按角色展开的 `优选装备` / `次选装备`。当默认文本不够时，`guide.fetch.cw` 现在也进入 YAML allowlist

```text
ok guide.list.cw count=2 more=1 next=token-2
guide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45
guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3
```

```text
ok guide.list.cw groups=2 count=1 more=1
guide 投资环境=购物区 count=1 more=1 next=group-token
guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45
guide 投资环境=购物区 idx=1 最终阵容=希儿/carry:1/star:5/rarity:3
guide 投资环境=事件区 count=0 more=1 next=group-token
```

- 多 `--portal` 请求时，`guide.list.cw` 会按 `投资环境` 分组；分页信息只挂在各组 `guide 投资环境=...` 行上，不会回到顶层首行

```text
ok cw.start cards=2
shot path=.trail/shots/req-start.png
info read_image_first=1
opt idx=1 投资环境="购物区" score=0.99 待收集=1
opt idx=1 说明="花金币买角色和升级"
guide idx=1 gid=1 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45
guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3
```

```text
ok cw.portal.select idx=1 投资环境=击破概念股
shot path=.trail/shots/req-portal-select.png
info read_image_first=1
info skill_info=运营思路 text="前期：先收集事实，再按后续策略处理"
info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered
```

- `cw.strategy.detect|refresh` 的 `info 已加载攻略=0|1` 固定在所有 `opt` 行之后

```text
ok cw.strategy.detect cards=2
shot path=.trail/shots/req-strategy-detect.png
info read_image_first=1
opt idx=1 投资策略=回蓝 攻略推荐=优选 刷新次数=0
opt idx=1 说明=启动回转
opt idx=2 投资策略=暴击 攻略推荐=否 刷新次数=2
opt idx=2 说明=爆发增伤
info 已加载攻略=1
```

```text
ok cw.strategy.refresh cards=2
shot path=.trail/shots/req-strategy-refresh.png
info read_image_first=1
opt idx=1 投资策略=连携 攻略推荐=否 刷新次数=1
opt idx=1 说明=补充连段
opt idx=2 投资策略=回蓝 攻略推荐=优选 刷新次数=0
opt idx=2 说明=启动回转
info 已加载攻略=1
```

```text
ok cw.strategy.select idx=2 投资策略=回蓝
shot path=.trail/shots/req-strategy-select.png
info read_image_first=1
```

```text
ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2
guide 攻略标签=#7级搜牌|#适用超频博弈
```

```text
ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2
info 搜牌档位=3 羁绊=42 角色=80 角色标签=11 投资环境=6
```

- `guide.fetch.cw` 看完整攻略
- `guide.list.cw` 看筛选摘要
- `cw.guide.current|apply` 看当前已选攻略摘要
- `guide.config.cw` 看筛选枚举和全局配置规模
- `guide.config.cw --format yaml` 会先输出中文摘要，再附原始英文 key 的结构化 YAML data

```text
ok ocr.read hits=2
shot path=.trail/shots/req-ocr.png
info read_image_first=1
text value=点击进入 box=122,88,74,20 center=159,98
text value=开始挑战 box=410,502,120,36 center=470,520
```

```text
ok cw.shop.scan opened=1 stale=0 count=2
shot path=.trail/shots/req-shop.png
info read_image_first=1
item idx=1 slot=1 name=希儿 cost=2
item idx=2 slot=2 name=停云 cost=1
info coins=40 level=7 exp=4/52 reserve_full=0 team_size=7/7
```

- 商店快照里的 `coins` / `level` / `exp` / `reserve_full` / `team_size` 会在 `trail cw shop scan`、`trail cw shop status` 与 `trail cw shop buy-exp` 暴露或可暴露；`open` / `refresh` / `close` 不重复输出旧快照事实
- `trail cw shop scan` 是当前画面读命令，所以会带 `shot path=...` 与 `info read_image_first=1`；`trail cw shop status` 仍是 session 汇总读，不默认带图；`trail cw shop buy-exp` 是 shop action renderer family，会输出买经验后的 fresh snapshot facts，且 `team_size=null` 是 must-keep null fact

```text
ok cw.shop.buy_exp opened=1 stale=0 count=1
shot path=.trail/shots/req-buy-exp.png
info read_image_first=1
item idx=1 slot=1 name=灵砂 cost=3
info coins=36 level=4 exp=0/8 reserve_full=0 team_size=4/4
```

```text
fail input.click code=INPUT_BACKEND_MISSING tainted=1
request id=req-42
why msg="input backend missing"
recover action=daemon.request_status request=req-42
```

## Guide 字段语义

- `trail guide fetch cw` 默认文本会直接返回攻略标题、攻略标签、投资环境、投资策略、装备优先度与阶段阵容等完整关键信息，方便在 apply 前确认是否就是目标攻略
- `trail guide list cw` 默认文本只保留选攻略最关键的摘要：`攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`，并在第二行补 `最终阵容=`
- `trail cw guide current|apply` 只返回当前已选攻略摘要：首行看 `攻略ID/攻略标题/攻略码/版本`，正文按需补 `guide 攻略标签=...`
- `trail guide fetch cw` 默认是完整攻略预览，不会建立当前已选攻略；确认后用 `trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入 session，不创建当前攻略快照或额外追踪产物
- `trail cw portal select --session <id> --card-idx <n>` 成功后会自动应用当前已选攻略；`trail cw guide apply` 只作为手动兜底
- `trail guide fetch cw` 负责看完整攻略；`trail cw guide current|apply` 负责回顾当前已选攻略；两者不要混用
- `攻略标签`：除了原始标签外，还会把布尔类攻略特征折叠成 `#标签`，例如 `#适用超频博弈`、`#星徽攻略`、`#专家顾问`；值为 false 时省略
- `羁绊列表`：按当前攻略各阶段阵容里出现过的羁绊去重汇总，并尽量保留层数，形如 `6贝洛伯格`，便于 Agent 直接对照攻略核心体系
- `最低金币`：这套攻略默认要求保留的最低金币阈值；后续 shop 决策应把它当成约束，而不是可随意花完的预算
- `优选投资策略` / `次选投资策略`：是局内 `cw.strategy.detect|refresh` 里 `攻略推荐=优选|次选|否` 的来源，不用于 `cw.portal.*`
- `简易装备优先度` / `进阶装备优先度`：分别对应页面里的 base / advanced equip priority，不是泛化的“基础顺序 / 成型顺序”
- `优选装备` / `次选装备`：按角色展开的推荐装备列表；当前只在该角色确实配置过对应装备时才输出
- `运营思路`：取自攻略详情原始 `description` 文本，会保留换行，适合直接作为局内运营参考
- `最终阵容`：用于 `guide.list.cw` 与 `cw.start/cw.portal.*` 的摘要第二行，保留角色、星级、费用和主C标记，适合快速判断这套阵容是否值得选
- `--role <name>` 在名称不精确或存在高相似角色时，可能返回 `info role_query=...` / `opt ...` 候选块，并在末尾追加 `warn code=GUIDE_ROLE_*` 提醒 Agent 复核目标角色
- 对语义尚不明确、或当前 fetch 响应里没有直接来源的字段，不默认重新发明英文别名，避免误导 Agent

## Skill 边界

- CLI 负责显式动作命令、固定 UI 流程命令与确定性防御动作
- skill 负责整局编排、阶段切换、策略判断与失败恢复
- 本项目不追求“内建识别穷尽所有状态”，而是优先把真实动作链和最小可靠检测做出来，把复杂画面判断留给 agent 的多模态能力
- 货币战争里，先用 `trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入 session；回到开局链路后由 `trail cw portal select` 成功时自动应用，`trail cw guide apply` 只作为手动兜底
- `trail-hsr` 是对外总入口，负责 `trail start`、`trail ocr read`、`trail input ...` 的 simple-first 起手、总入口接管与 scene 路由判断
- `trail-<scene>-entry` 是对外场景入口；只有 registry 中 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前入口
- `trail cw enter` 只负责把页面带到货币战争首页；真正进入投资环境页要用 `trail cw start`
- `trail cw stage` 只适用于已进入货币战争后的内部阶段快速检测/等待，不用于登录页、大世界等非 CW 场景判断
- `trail cw guide` 只负责当前对局已选攻略的 current/apply；筛攻略和拉攻略继续使用顶层 `trail guide ... cw`
- `trail cw portal select|detect|refresh|restart` 只用于首页之后的投资环境选择页；`detect = 重识别当前三张卡，不点击`，`refresh = 点击刷新后生成新的三张卡`
- `trail cw invest read|choose` 继续表示局内 invest 事件，不是开局投资环境页命令
- `trail cw slots place` 用重复 `--action <source,target>` 显式批量上场；`trail cw hand sell` 用重复 `--slot <n>` 显式批量卖牌
- 这两类批量命令都严格保序、遇错即停；如果中途失败且前面动作可能已生效，先重新执行 `trail cw slots read --session <id>` 再继续后续判断
- `trail-hsr` 负责 session、窗口检查与场景切换，并在没有已上线 scene entry 时继续承担总入口 owner
- 当前 scene entry 一旦命中并接管某个具体场景，该 scene entry 就成为该场景内的唯一编排 owner；常规 battle / settle 链默认执行 `trail cw battle run --session <id> --timeout 570`
- `trail-hsr-advanced` 是内部恢复层，继续负责 daemon / request-status / reconcile-session / window / session / screen / image / state 这类 control-plane 与恢复链路；如果 `trail cw battle run` 的 stdout 丢失但 `session=<id>` 还在，立刻执行 `trail state dump --session <id> --format yaml`
- `trail-hsr-advanced` 不作为用户入口；它完成恢复后必须把控制权交回调用它的上层 active skill
- 归档 skill 不再作为 active owner 或推荐入口
- README 里的 simple 层序列是默认入口；advanced 段落只在 simple 层失败或不够用时启用

## 项目边界

- `trail-cli` 是独立项目，不在运行时依赖 `StarRailAssistant` 的场景模块
- CLI 不提供“一键自动跑完整局”的单命令，完整对局由 `trail-hsr` 或当前 active 的 scene entry skill 编排
