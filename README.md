# Trail CLI

Trail 是面向《崩坏：星穹铁道》的独立命令行工具，默认输出结构化 envelope 和执行后截图，供多模态 agent 直接消费。

## Quick Start

推荐入口：

- 先用 `trail-hsr` 创建 session、检查窗口，并把流程切到 `trail-cw`
- 再由 `trail-cw` 负责编排完整一局货币战争

手工 CLI 冒烟顺序：

- `trail session create`
- `trail guide fetch cw <lineup_url|lineup_id>`
- `trail cw enter --session <id> --mode new`
- 在进入游戏并完成投资环境选择后，再执行：`trail cw guide apply --session <id> --lineup-id <lineup_id>`
- 如需回顾当前已应用攻略：`trail cw guide current --session <id>`

## 命令面概览

- `session`：创建并持久化 session
- `guide`：拉取攻略内容、返回筛选枚举与攻略列表
- `window`：做窗口绑定检查
- `screen`：截图
- `ocr`：OCR 读取
- `image`：模板识别与等待
- `input`：点击、拖拽、按键
- `state`：读取 session 与 scene state
- `cw`：货币战争固定流程命令，包含 `enter`、`guide`、`stage`、`slots`、`shop`、`crystals`、`hand`、`replenish`、`invest`、`encounter`、`fortune`、`boss-preview`、`battle`、`settle`、`event`

## 输出约定

- 所有命令默认返回结构化 envelope
- 关键字段固定为 `ok`、`data`、`screenshot`、`timing`、`error`
- skill 应优先消费当前命令返回的 `screenshot` 与 `data`，不要沿用旧推断
- 对多模态 agent 来说，`screenshot` 是第一手事实来源；CLI 自带的 `detect/read/status` 更适合作为辅助输入，而不是唯一真相

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
