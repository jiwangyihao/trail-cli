# Trail CLI

Trail 是面向《崩坏：星穹铁道》的独立命令行工具，默认输出 Agent 友好的紧凑文本协议，并在命令产生截图时显式返回截图路径，供多模态 agent 直接消费。

## Simple / Advanced 分层

- `trail` CLI 现在是非管理员薄壳，负责参数解析、workspace 解析、RPC 请求发送，以及把结果渲染为默认文本协议、显式 `--format yaml` 兜底或 `--verbose` 调试层
- 常驻 `traild` daemon 持有 runtime、截图、OCR、找图、输入、guide、`cw` 场景执行和 session 热状态
- 默认 simple 层只教 3 个起手能力：`trail start`、`trail ocr read`、`trail input ...`
- `trail start` 会自动收口 daemon、游戏、窗口与 session，并返回可继续使用的 `session=<id>`
- 如果 `trail start` 失败，或 simple 层不能满足定位需求，再切到 `skills/trail-hsr-advanced` 处理 daemon / window / session / screen / image / state 等进阶命令

## Quick Start

推荐入口（simple-first）：

- 启动并拿到可用 session：`trail start`
- 观察当前画面：`trail ocr read`
- 执行明确动作：`trail input click ...`、`trail input drag ...`、`trail input key ...`
- 需要进入具体场景 skill 时，先用 `trail-hsr` 跑 simple 层，再切到 `trail-cw`

手工 CLI 冒烟顺序：

- `trail start`
- `trail ocr read`
- `trail input ...`

## Advanced 启动与排障

- simple 层失败或不够用时，加载 `skills/trail-hsr-advanced`
- 进阶原子命令包括：`trail daemon install`、`trail daemon status`、`trail daemon start`
- 结果未知或需要恢复时，使用：`trail daemon request-status --request-id <id>`、`trail daemon reconcile-session --session <id>`
- 需要手工控制游戏与窗口时，使用：`trail window launch --channel official|bilibili|global`、`trail window attach --window-title "崩坏：星穹铁道"`
- 需要手工建 session 时，使用：`trail session create`
- 需要额外截图、模板识别或状态转储时，使用：`trail screen shot`、`trail image ...`、`trail state dump`

## 货币战争流程

- 先通过 `trail start` 或 `trail session create` 拿到可用 `session`
- `trail cw enter --session <id>` 只负责把页面带到货币战争首页
- 到首页后先确认本局偏好：
  - `攻略优先` / `环境优先`
  - `standard` / `overclock`
  - 是否接受刷开局（后续是否允许 `trail cw portal.refresh` / `trail cw portal.restart`）
- `trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest --battle-mode standard|overclock` 负责把首页推进到投资环境页
- 如果先按环境选攻略，再用：`trail guide list cw --portal <title>` 或 `trail guide list cw --portal-id <id>`
- 如需免查 config 直接筛攻略，也可以在 list 阶段使用：`trail guide list cw --trait <name>`、`trail guide list cw --role <name>`；需要脚本固化或精确复现时，再切到 `--trait-id` / `--role-id`
- `guide list cw` 列表结果现在会直接返回 `version`，list 阶段就应把版本兼容性纳入筛选判断
- 查看返回的三卡摘要后，根据需要执行：
  - `trail cw portal.select --session <id> --card-idx <n>`
  - `trail cw portal.refresh --session <id>`
  - `trail cw portal.restart --session <id>`
- 在进入游戏并完成投资环境选择后，再执行：`trail cw guide apply --session <id> --lineup-id <lineup_id>`
- 如需回顾当前已应用攻略：`trail cw guide current --session <id>`
- `trail cw guide` 只负责当前对局攻略的 apply/current；筛攻略和拉攻略继续使用顶层 `trail guide ... cw`
- `trail cw invest.read|choose` 继续只表示局内 invest 事件，不是开局投资环境页命令

编队槽位读取建议：

- 先看当前阶段已有 screenshot，再决定是否真的需要读取槽位名字。
- `trail cw slots read --session <id> --slot front:0 --slot hand:3` 是首选定向确认路径，只读“看见有角色但名字不确定”的槽位。
- 不传 `--slot` 时，`trail cw slots read --session <id>` 仍保留现有全量读取语义，只作为完整快照兜底，不表示默认行为已经改变。
- 如果局部读取结果带 `stale=1`，它不等于新的完整 fresh 快照；后续判断仍要结合已有截图和基线来源。

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
- `cw`：货币战争固定流程命令；`enter` 到首页，`start` 从首页进入投资环境页；`stage` 只用于已进入货币战争后的内部阶段快速检测/等待；其余分组处理局内阶段与资源，包含 `portal`、`guide`、`stage`、`slots`、`shop`、`crystals`、`hand`、`replenish`、`invest`、`encounter`、`fortune`、`boss-preview`、`battle`、`settle`、`event`

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
- 默认成功输出协议保持不变：仍然是 `ok ocr.read hits=<n>`，有截图时先输出 `shot`，再输出 `text`

DirectML 安装与环境 profile 说明：

- DirectML 目前只作为 Windows 定向的可选加速 profile，不承诺为通用跨平台 GPU 方案
- 需要使用项目明确支持的 DirectML 环境 profile，而不是在任意现有 OCR 环境上直接叠加依赖
- 默认安装仍以 CPU 基线依赖为准；只有需要 DirectML 时，才切换到单独准备好的 Windows DirectML profile
- 不要在同一环境里模糊共存 `onnxruntime` 与 `onnxruntime-directml`；应确认当前环境最终只保留预期的 ONNX Runtime 变体
- 排障时先确认当前 profile、已安装的 ORT 变体和 `trail ocr read --provider dml` 的实际 failure/success 结果，再判断是否属于环境不满足或运行期 DML 失败

## 输出约定

- 默认输出是紧凑文本协议，统一首行为 `<ok|fail> <command> <核心事实...>`，例如 `ok cw.shop.status count=2`
- 默认模式是常规消费层；`--format yaml` 是结构化兜底，`--verbose` 是开发/排障层，不应作为终端 Agent 的常规依赖
- 默认模式绝不输出 YAML；只有显式指定 `--format yaml` 且命令进入 allowlist 时，才会在首行摘要后追加结构化块
- 常见正文前缀包括 `shot`、`item`、`guide`、`text`、`info`、`why`、`warn`、`ref`、`request`、`recover`；`debug` 仅在 `--verbose` 下追加
- `shot path=...` 表示当前命令结果对应的截图路径；只要当前命令有截图，就会输出 `shot path=...`，且位于实体行之前
- 默认失败路径只要当前结果携带 `request_id`，就会保留 `request id=<id>`，用于恢复与排障
- 只有结果未知或当前失败显式可恢复时，才会出现 `recover action=daemon.request_status request=<id>`；仅有 `request id=<id>` 不等于当前失败一定可恢复
- `trail daemon request-status --request-id <id>` 用于回查某个请求的终态、关联 session、最近可见阶段与污染状态，典型输出是 `ok daemon.request_status request=req-42 session=sess-1 final_state=completed last_visible_stage=responded tainted=0`
- `tainted=1` 表示当前 failure 或状态带有运行态污染风险；继续执行前，先确认请求终态，再决定是否执行 `trail daemon reconcile-session --session <id>`
- `--format yaml` 仍然保留同一条首行摘要，但只在允许的命令上提供结构化视图；当前更适合 `daemon status`、`state dump`、`guide fetch cw`、`guide config cw` 这类结果体量更大或层级更深的命令
- `--verbose` 只追加 `debug kind=...` 调试行，不改变默认文本协议里的事实集合与顺序
- 对多模态 agent 来说，截图仍是第一手事实来源；默认文本里的 `detect/read/status` 结果是压缩后的动作信号，而不是替代截图的唯一真相

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
guide id=abc title=购物阵容 version=3.2 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45
guide id=def title=事件阵容 version=3.2 idx=2 hard=0 change_equip=1 expert=0 like=22 favour=9
```

```text
ok ocr.read hits=2
shot path=.trail/shots/req-ocr.png
text value=点击进入 box=122,88,74,20 center=159,98
text value=开始挑战 box=410,502,120,36 center=470,520
```

```text
ok cw.shop.status count=2
shot path=.trail/shots/req-shop.png
item idx=1 slot=1 name=希儿 cost=2
item idx=2 slot=2 name=停云 cost=1
info coins=40 level=7 reserve_full=0 max_team_size=8
```

- 商店快照里的 `coins` / `level` / `reserve_full` / `max_team_size` 当前只在 `trail cw shop scan` 与 `trail cw shop status` 暴露；`open` / `refresh` / `close` 不重复输出旧快照事实

```text
fail input.click code=INPUT_BACKEND_MISSING tainted=1
request id=req-42
why msg="input backend missing"
recover action=daemon.request_status request=req-42
```

## Guide 字段语义

- `trail guide fetch cw` 默认文本会直接返回攻略标题、攻略标签、投资环境、投资策略、装备优先度与阶段阵容等完整关键信息，方便在 apply 前确认是否就是目标攻略
- `攻略标签`：除了原始标签外，还会把布尔类攻略特征折叠成 `#标签`，例如 `#适用超频博弈`、`#星徽攻略`、`#专家顾问`；值为 false 时省略
- `羁绊列表`：按当前攻略各阶段阵容里出现过的羁绊去重汇总，并尽量保留层数，形如 `6贝洛伯格`，便于 Agent 直接对照攻略核心体系
- `适用超频博弈`：是否适用于超频博弈，不表示“更适合高压环境”
- `最低金币`：这套攻略默认要求保留的最低金币阈值；后续 shop 决策应把它当成约束，而不是可随意花完的预算
- `优选投资策略` / `次选投资策略`：分别对应页面里的 primary / secondary investment strategy，不是战斗增益名的技术字段
- `简易装备优先度` / `进阶装备优先度`：分别对应页面里的 base / advanced equip priority，不是泛化的“基础顺序 / 成型顺序”
- `优选装备` / `次选装备`：按角色展开的推荐装备列表；当前只在该角色确实配置过对应装备时才输出
- `运营思路`：取自攻略详情原始 `description` 文本，会保留换行，适合直接作为局内运营参考
- `星徽攻略`：是否需要转阵营道具/星徽，这对选攻略非常关键
- `专家顾问`：是否包含专家顾问角色；这类角色通常不能在商店中直接购买
- `support_hard`：是否适用于超频博弈，不表示“更适合高压环境”
- `has_change_equip`：是否需要转阵营道具/星徽，这对选攻略非常关键
- `has_expert`：是否包含专家顾问角色；这类角色通常不能在商店中直接购买
- `version`：攻略适用版本；`guide list cw` 已在列表阶段直接返回，选攻略时先判断是否适配当前版本
- `final_role_cards`：最终阵容角色摘要，包含 `name / star / rarity / is_carry`，适合在 list 阶段判断“是否存在 X 星 X 费角色”
- `--role <name>` 在名称不精确或存在高相似角色时，可能返回 `info role_query=...` / `opt ...` 候选块，并在末尾追加 `warn code=GUIDE_ROLE_*` 提醒 Agent 复核目标角色
- 对语义尚不明确、或当前 fetch 响应里没有直接来源的字段，不默认重新发明英文别名，避免误导 Agent

## Skill 边界

- CLI 负责显式动作命令、固定 UI 流程命令与确定性防御动作
- skill 负责整局编排、阶段切换、策略判断与失败恢复
- 本项目不追求“内建识别穷尽所有状态”，而是优先把真实动作链和最小可靠检测做出来，把复杂画面判断留给 agent 的多模态能力
- 货币战争里，攻略应用应放在“进入游戏并完成投资环境选择之后”执行，不建议在更早的入口阶段导入攻略
- `skills/trail-hsr` 负责 `trail start`、`trail ocr read`、`trail input ...` 的 simple-first 起手与场景切换
- `skills/trail-hsr-advanced` 负责 daemon / window / session / screen / image / state 等进阶命令
- `trail cw enter` 只负责把页面带到货币战争首页；真正进入投资环境页要用 `trail cw start`
- `trail cw stage` 只适用于已进入货币战争后的内部阶段快速检测/等待，不用于登录页、大世界等非 CW 场景判断
- `trail cw guide` 只负责当前对局攻略的 apply/current；筛攻略和拉攻略继续使用顶层 `trail guide ... cw`
- `trail cw portal.select|refresh|restart` 只用于首页之后的投资环境选择页
- `trail cw invest.read|choose` 继续表示局内 invest 事件，不是开局投资环境页命令
- `skills/trail-hsr` 负责 session、窗口检查与场景切换
- `skills/trail-cw` 负责整局货币战争循环
- `skills/trail-cw-*` 负责攻略、商店、补给、编队、事件等子流程
- README 里的 simple 层序列是默认入口；advanced 段落只在 simple 层失败或不够用时启用

## 项目边界

- `trail-cli` 是独立项目，不在运行时依赖 `StarRailAssistant` 的场景模块
- CLI 不提供“一键自动跑完整局”的单命令，完整对局由 `trail-cw` skill 编排
