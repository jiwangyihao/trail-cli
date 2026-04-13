# Trail CLI

Trail 是面向《崩坏：星穹铁道》的独立命令行工具，默认输出结构化 envelope，供 agent 直接消费。

## Quick Start

推荐入口：

- 先用 `trail-hsr` 创建 session、检查窗口，并把流程切到 `trail-cw`
- 再由 `trail-cw` 负责编排完整一局货币战争

手工 CLI 冒烟顺序：

- `trail session create`
- `trail guide fetch cw <url>`
- `trail cw enter --session <id> --mode new`
- `trail cw guide apply --session <id> --guide <artifact>`

## 命令面概览

- `session`：创建并持久化 session
- `guide`：拉取场景攻略 artifact
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

## Skill 边界

- CLI 负责显式动作命令、固定 UI 流程命令与确定性防御动作
- skill 负责整局编排、阶段切换、策略判断与失败恢复
- `skills/trail-hsr` 负责 session、窗口检查与场景切换
- `skills/trail-cw` 负责整局货币战争循环
- `skills/trail-cw-*` 负责攻略、商店、补给、编队、事件等子流程
- README 里的 CLI 序列只用于手工检查命令面，不是推荐的整局自动化入口

## 项目边界

- `trail-cli` 是独立项目，不在运行时依赖 `StarRailAssistant` 的场景模块
- CLI 不提供“一键自动跑完整局”的单命令，完整对局由 `trail-cw` skill 编排
