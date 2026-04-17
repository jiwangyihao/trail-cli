# Trail CLI

Trail 是面向《崩坏：星穹铁道》的独立命令行工具，默认输出 Agent 友好的紧凑文本协议，并在命令产生截图时显式返回截图路径，供多模态 agent 直接消费。

## Daemon 模式

- `trail` CLI 现在是非管理员薄壳，负责参数解析、workspace 解析、RPC 请求发送，以及把结果渲染为默认文本协议、显式 `--format yaml` 兜底或 `--verbose` 调试层
- 常驻 `traild` daemon 持有 runtime、截图、OCR、找图、输入、guide、`cw` 场景执行和 session 热状态
- 首次使用前先运行：`trail daemon install`
- 查看常驻服务状态：`trail daemon status`
- 如需显式预热或重启常驻服务：`trail daemon start`
- 如果命令返回“结果未知”或需要排障，使用：`trail daemon request-status --request-id <id>`
- 如果 session 被标记为 `tainted`，清理前先确认请求终态，再运行：`trail daemon reconcile-session --session <id>`

## Quick Start

推荐入口：

- 首次在当前用户环境启用常驻服务：`trail daemon install`
- 开始前先确认 daemon 可用：`trail daemon status`
- 先用 `trail-hsr` 创建 session、检查窗口，并把流程切到 `trail-cw`
- 再由 `trail-cw` 负责编排完整一局货币战争

手工 CLI 冒烟顺序：

- 首次机器准备：`trail daemon install`
- 确认或预热常驻服务：`trail daemon status`，必要时 `trail daemon start`
- 如果游戏还没开：`trail window launch --game-path <StarRail.exe>`
- `trail session create`
- `trail guide fetch cw <lineup_url|lineup_id>`
- `trail cw enter --session <id> --mode new`
- 在进入游戏并完成投资环境选择后，再执行：`trail cw guide apply --session <id> --lineup-id <lineup_id>`
- 如需回顾当前已应用攻略：`trail cw guide current --session <id>`

## 命令面概览

- `daemon`：安装、启动、停止、查看常驻服务，并提供 `request-status` / `reconcile-session` 管理查询面
- `session`：创建并持久化 session
- `guide`：拉取攻略内容、返回筛选枚举与攻略列表
- `window`：做窗口绑定检查
- `window launch`：按显式路径启动《崩坏：星穹铁道》客户端，支持渠道切换
- `screen`：截图
- `ocr`：OCR 读取
- `image`：模板识别与等待
- `input`：点击、拖拽、按键
- `state`：读取 session 与 scene state
- `cw`：货币战争固定流程命令，包含 `enter`、`guide`、`stage`、`slots`、`shop`、`crystals`、`hand`、`replenish`、`invest`、`encounter`、`fortune`、`boss-preview`、`battle`、`settle`、`event`

## 输出约定

- 默认输出是紧凑文本协议，统一首行为 `<ok|fail> <command> <核心事实...>`，例如 `ok cw.shop.status count=2`
- 默认模式是常规消费层；`--format yaml` 是结构化兜底，`--verbose` 是开发/排障层，不应作为终端 Agent 的常规依赖
- 默认模式绝不输出 YAML；只有显式指定 `--format yaml` 且命令进入 allowlist 时，才会在首行摘要后追加结构化块
- 常见正文前缀包括 `shot`、`item`、`guide`、`text`、`why`、`warn`、`ref`、`request`、`recover`；`debug` 仅在 `--verbose` 下追加
- `shot path=...` 表示当前命令结果对应的截图路径；只要当前命令有截图，就会输出 `shot path=...`，且位于实体行之前
- 默认失败路径只要当前结果携带 `request_id`，就会保留 `request id=<id>`，用于恢复与排障
- 只有结果未知或当前失败显式可恢复时，才会出现 `recover action=daemon.request_status request=<id>`；仅有 `request id=<id>` 不等于当前失败一定可恢复
- `trail daemon request-status --request-id <id>` 用于回查某个请求的终态、最近可见阶段与污染状态，典型输出是 `ok daemon.request_status request=req-42 final_state=completed last_visible_stage=responded tainted=0`
- `tainted=1` 表示当前 failure 或状态带有运行态污染风险；继续执行前，先确认请求终态，再决定是否执行 `trail daemon reconcile-session --session <id>`
- `--format yaml` 仍然保留同一条首行摘要，但只在允许的命令上提供结构化视图；当前更适合 `daemon status`、`state dump`、`guide config cw` 这类结果体量更大或层级更深的命令
- `--verbose` 只追加 `debug kind=...` 调试行，不改变默认文本协议里的事实集合与顺序
- 对多模态 agent 来说，截图仍是第一手事实来源；默认文本里的 `detect/read/status` 结果是压缩后的动作信号，而不是替代截图的唯一真相

文本协议示例：

```text
ok guide.list.cw count=2 more=1 next=token-2
guide id=abc idx=1 carry=希儿 hard=1 change_equip=0 expert=1
guide id=def idx=2 hard=0 change_equip=1 expert=0
```

```text
ok ocr.read hits=2
shot path=.trail/shots/req-ocr.png
text rank=1 value=点击进入 score=0.98 box=122,88,74,20
text rank=2 value=开始挑战 score=0.93 box=410,502,120,36
```

```text
ok cw.shop.status count=2
shot path=.trail/shots/req-shop.png
item idx=1 slot=1 name=希儿 cost=2
item idx=2 slot=2 name=停云 cost=1
```

```text
fail input.click code=INPUT_BACKEND_MISSING tainted=1
request id=req-42
why msg="input backend missing"
recover action=daemon.request_status request=req-42
```

## Guide 字段语义

- `support_hard`：是否适用于超频博弈，不表示“更适合高压环境”
- `has_change_equip`：是否需要转阵营道具/星徽，这对选攻略非常关键
- `has_expert`：是否包含专家顾问角色；这类角色通常不能在商店中直接购买
- `final_role_cards`：最终阵容角色摘要，包含 `name / star / rarity / is_carry`，适合在 list 阶段判断“是否存在 X 星 X 费角色”
- 对语义尚不明确的字段，不默认展示，避免误导 Agent

## Skill 边界

- CLI 负责显式动作命令、固定 UI 流程命令与确定性防御动作
- skill 负责整局编排、阶段切换、策略判断与失败恢复
- 本项目不追求“内建识别穷尽所有状态”，而是优先把真实动作链和最小可靠检测做出来，把复杂画面判断留给 agent 的多模态能力
- 货币战争里，攻略应用应放在“进入游戏并完成投资环境选择之后”执行，不建议在更早的入口阶段导入攻略
- `skills/trail-hsr` 负责 session、窗口检查与场景切换
- `skills/trail-cw` 负责整局货币战争循环
- `skills/trail-cw-*` 负责攻略、商店、补给、编队、事件等子流程
- README 里的 CLI 序列只用于手工检查命令面，不是推荐的整局自动化入口

## 项目边界

- `trail-cli` 是独立项目，不在运行时依赖 `StarRailAssistant` 的场景模块
- CLI 不提供“一键自动跑完整局”的单命令，完整对局由 `trail-cw` skill 编排
