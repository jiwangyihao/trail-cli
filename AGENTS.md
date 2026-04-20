# 项目输出协议约束

## renderer 家族

- 新命令必须先归类到已有 renderer 家族，再决定首行事实与正文前缀；不要把每个命令都做成独立的随意格式。
- 常见 renderer 家族包括：检测/状态摘要、列表读取、攻略结果、control-plane 状态与恢复路径。
- 如果确实需要新增 renderer 家族，先在 `trail/output/rendering.py` 固定首行字段顺序、正文前缀和 must-keep 事实，再补对应测试。

## canonical command 与前缀冻结

- 默认模式一律使用内部 canonical command 名，必须是点号形式，例如 `cw.shop.buy_slot`、`daemon.request_status`、`screen.shot`；不要写成 `cw.shop.buy-slot`、`daemon request-status`。
- 默认正文前缀只允许使用 `request`、`shot`、`item`、`guide`、`text`、`slot`、`opt`、`why`、`warn`、`ref`、`recover`、`info`；`debug` 仅用于 `--verbose` 追加层。
- 如果某个命令需要新增正文前缀，先更新 `trail/output/rendering.py` 的 renderer、README 示例和对应测试，再写文档。

## 默认模式必出事实

- 默认模式的第一行必须是 `<ok|fail> <canonical_command> <核心事实...>`。
- 只要当前命令产出截图，就必须输出 `shot path=...`。
- 失败结果只要带 `request_id`，就必须输出 `request id=<id>` 供恢复或排障使用。
- 只有结果未知或当前失败显式可恢复时，才输出 `recover action=daemon.request_status request=<id>`。
- 会影响下一步决策的 `0`、`false`、`count`、`more`、`tainted` 不能因为“看起来为空”而省略。
- `guide.fetch.cw` 默认首行至少保留 `攻略标题`、`攻略码`、`版本`、`最低金币`、`最低等级`、`中期等级`；`适用超频博弈`、`星徽攻略`、`专家顾问` 改以 `#标签` 形式并入 `guide 攻略标签=...` 同一行。

## 正文顺序约束

- success 路径必须先输出首行，再按需要输出 `shot`，然后才是 `item`、`guide`、`text`、`slot`、`opt`、`info` 这类实体行，最后才是 `warn`、`ref`。
- failure 路径正文顺序固定为：`request` -> `shot` -> `why` -> `warn` -> `ref` -> `recover`；`debug` 只能在 `--verbose` 时追加在最后。
- 不要为了单个命令“更自然”而重排 failure 行顺序；恢复链路必须稳定可扫读。

## 冻结字段与编码规则

- 高频冻结字段至少包括：`session_id` -> `session`、`next_page_token` -> `next`、`similarity` -> `sim`、`confidence/score` -> `score`、`bbox/rect` -> `box`、条目序号 -> `idx`、人类消息 -> `msg`。
- 默认文本统一使用 `key=value`；除首行的 `ok|fail` 和 `<command>` 外，不再新增位置参数。
- 布尔值统一编码为 `0/1`；含空格、引号、反斜杠、换行、等号或逗号歧义的值必须使用双引号。
- `box` 统一压成 `left,top,width,height`，不要回退成嵌套对象。
- 只省略语义缺失值，不能省略会影响下一步动作的 `0`、`false`、`count`、`more`、`tainted`。

## verbose 事件约束

- `--verbose` 只追加开发/排障层，不改变默认文本协议的事实集合和顺序。
- `--verbose` 事件必须统一经过 `trail.output.debug.collect_debug_events` 和 `trail.output.debug.render_debug_lines` 写入。
- 禁止各命令直接拼接 `debug ...` stdout，也不要把临时调试信息混入默认模式。
- 默认模式不得泄漏 verbose 事件；需要排障时才通过 `--verbose` 查看 `debug kind=...` 行。

## YAML allowlist 约束

- `--format yaml` 是结构化兜底，不是默认主通道。
- 只有进入 YAML allowlist 的命令才允许输出 YAML；新增命令前先确认是否真的存在结构化兜底需求。
- 当前 YAML allowlist 是 `daemon.status`、`state.dump`、`guide.fetch.cw`、`guide.config.cw`。
- 非 allowlist 命令不要回退到旧式结构化 envelope；保持默认文本协议，并在不支持时显式返回格式不支持错误。

## 文档与测试同步要求

- 新命令或现有命令输出发生变化时，必须同步更新 `README.md` 示例与说明。
- 所有会影响普通用户或 Agent 使用方式的变更，都必须同步更新相关 `skills/*/SKILL.md`，不要只改 README 或 spec/plan。
- 新命令至少要补 renderer 单测，以及受影响的 CLI stdout 测试或 RPC/契约测试增量。
- 如果新增前缀词、冻结字段、恢复语义或 `--verbose` 事件类型，必须同步更新本文件与 README。
- 评审输出变更时，优先检查：renderer 家族是否明确、默认模式必出事实是否稳定、YAML allowlist 是否合理、README 与测试是否已同步。

## guide.fetch.cw 约束

- `guide.fetch.cw` 默认首行按中文字段返回攻略核心事实，至少保留：`攻略标题`、`攻略码`、`版本`、`最低金币`、`最低等级`、`中期等级`。
- `guide.fetch.cw` 正文继续使用 `guide` 行补充：`攻略标签`、`羁绊列表`、`投资环境`、`优选投资策略`、`次选投资策略`、`简易装备优先度`、`进阶装备优先度`、`运营思路`，以及按阶段展开的阵容摘要与角色推荐装备；`适用超频博弈`、`星徽攻略`、`专家顾问` 折叠成 `#标签` 并入 `攻略标签` 同一行，`羁绊列表` 与阶段 `羁绊` 在上游提供层数时要保留成 `6贝洛伯格` 这类形式。
- `guide.fetch.cw` 允许 `--format yaml`，用于在默认文本之外回落到完整结构化 `data`。

## guide / cw 攻略摘要约束

- guide.list.cw 的默认文本改用 攻略ID/攻略标题/版本/主C/攻略标签/最终阵容，并继续保留 `count`、`more`、`next`、`idx` 这些冻结控制字段。
- `guide.list.cw` 非分组条目字段冻结为 `攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`；第二行固定使用 `guide ... 最终阵容=...`，不能回退成 `final_role_cards` 或布尔技术位直出。
- `guide.list.cw` 分组视图固定使用 `guide 投资环境=... count=... more=...`；只有分组行允许携带 `next=...`，顶层 grouped 首行不带 `next`。
- `cw.start` / `cw.portal.select|refresh|restart` 的 portal 卡片字段使用 `投资环境/说明/待收集`；`待收集` 必须统一编码为 `0/1`，即使为 `0` 也不能省略。
- `cw.portal.select` success 首行固定为 `ok cw.portal.select idx=... 投资环境=...`；`cw.start` / `cw.portal.*` 下挂攻略摘要继续复用 `guide.list.cw` 的中文条目与 `最终阵容` 语义。
- `cw.guide.current|apply` 使用 `攻略ID/攻略标题/攻略码/版本`，并以 `info 攻略快照ID=...` 表示 artifact id。
- `cw.guide.current|apply` 的 `攻略快照ID` 是 artifact id / 恢复追踪 id，不是 `shot path` 截图路径；`current/apply` 只看当前已应用攻略摘要，完整攻略仍由 `guide.fetch.cw` 提供。
- `guide.config.cw` 使用 `赛季/子赛季/大版本/搜牌档位/羁绊/角色/角色标签/投资环境`；这五个统计项即使为 `0` 也必须保留。
- `guide.config.cw --format yaml` 仍然先输出中文摘要，再追加原英文 key 的 YAML shape，不得回退成纯英文首屏。
