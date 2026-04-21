---
name: trail-cw-guide
description: Use when an agent needs to fetch or apply a Currency Wars guide for an existing Trail session.
---

# Skill: trail-cw-guide

## 职责

- 拉取货币战争攻略内容，直接提供给 Agent
- 在游戏内应用显式指定的 lineup 攻略，并在成功后记录本地攻略快照ID
- 支持回顾当前 session 已应用的攻略摘要
- 不负责进入对局，也不负责整局循环

## 输入

- `session_id`
- `lineup_id` 或完整 lineup URL

## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 如需先筛攻略，可按环境 / 羁绊 / 角色使用：`trail guide list cw --portal <title>`、`trail guide list cw --portal-id <id>`、`trail guide list cw --trait <name>`、`trail guide list cw --role <name>`；需要脚本固化时再用 `--trait-id` / `--role-id`
3. 拉取攻略内容：`trail guide fetch cw <lineup_url|lineup_id>`
4. 由 Agent 读取返回内容，至少确认 `攻略标题`、`攻略标签`、`羁绊列表`、`攻略码`、`最低金币`、`投资环境`、`优选投资策略/次选投资策略`、`运营思路` 与当前目标一致，再决定是否执行 apply
5. `trail cw guide apply --session <id> --lineup-id <lineup_id>` 只应在 `trail cw portal.select` 已完成、真正进入游戏后执行
6. 如需回顾当前攻略：`trail cw guide current --session <id>`；默认文本只看 `攻略ID/攻略标题/攻略码/版本`、可选 `攻略标签` 与 `攻略快照ID`
7. 确认 `info 攻略快照ID=...` 已返回；它是 artifact id / 恢复追踪 id，不是 `shot path` 截图路径

## 执行规则

- `trail guide fetch cw` 直接返回完整攻略内容，不自动修改 session；用户面向的主术语是 `攻略标题/攻略标签/羁绊列表/投资环境/运营思路`，不是 artifact
- `trail guide fetch cw` 的默认文本会直接暴露中文字段名的完整攻略摘要；其中 `适用超频博弈` / `星徽攻略` / `专家顾问` 会折叠进 `攻略标签` 同一行，`羁绊列表`、`运营思路` 与按角色展开的推荐装备也会直接给出；不够时可直接用 `--format yaml` 获取完整结构化 `data`
- `优选投资策略` / `次选投资策略` 会在局内 strategy 页被消费，映射为 `攻略推荐=优选|次选|否`
- `trail guide list cw --portal ...` / `--portal-id ...` 适合在“环境优先”流程里，根据投资环境页三卡摘要反查更合适的攻略；默认摘要看 `攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`，下一行看 `最终阵容`
- 按羁绊或角色筛时，优先使用 `--trait <name>` / `--role <name>`
- `--role <name>` 在名称不精确或存在高相似角色时，可能先返回候选块与 warning；看到 `info role_query=...` / `opt ...` 时，先核对 resolved 角色是否符合当前计划
- `trail cw guide apply/current` 只面向当前对局已选攻略；攻略查询与拉取继续使用顶层 `trail guide ... cw`
- 需要完整攻略内容时回到 `trail guide fetch cw`；`current/apply` 只负责当前已应用攻略摘要
- 本 skill 负责提供攻略事实，不直接替 Agent 选择策略卡
- `trail cw guide apply` 必须显式传入 `--lineup-id`（兼容旧 `--guide` 别名时，也应优先把它理解成 lineup_id）
- 不要把 `trail cw enter` 视为可以立刻 apply 攻略的时机；`cw enter` 现在只到首页，真正进入游戏前还需要 `trail cw start` 和 `trail cw portal.select`
- 攻略快照ID记录发生在 apply 成功后，便于后续通过 `trail cw guide current` 回顾当前实际应用的攻略
- 不要继续依赖旧的 `support_hard`、`has_change_equip`、`has_expert` 字段名解释攻略元数据；面向决策时统一看 `攻略标签`
- `guide list cw` 已直接返回 `版本`，Agent 在 list 阶段就应把版本兼容性纳入筛选判断
- 如果同时需要 OCR 文字和对应截图，优先只运行一次 `trail ocr read`；它已经会返回 OCR 结果和 `shot path=...`，不要紧接着再补额外截图命令
- `trail ocr read` 默认走 `ocr_mode=fast`（`1280x720`）；当攻略页或 lineup 文字你觉得读得不稳、需要更稳的 box，或要做结果对照时，再显式加 `--ocr-mode high`。如需固定补跑高精度，可加 `--retry-high always`；常规情况下保持默认 `--retry-high auto`
- 攻略应用后，把旧的 `slots`、`shop`、`stage` 快照视为无效，交回主 skill 继续下一步
- 如果攻略 apply 后结果未知，或当前 session 不可继续使用，交回主 skill 并切到 `trail-hsr-advanced`
