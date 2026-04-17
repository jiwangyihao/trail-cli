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
- 当前 YAML allowlist 是 `daemon.status`、`state.dump`、`guide.config.cw`。
- 非 allowlist 命令不要回退到旧式结构化 envelope；保持默认文本协议，并在不支持时显式返回格式不支持错误。

## 文档与测试同步要求

- 新命令或现有命令输出发生变化时，必须同步更新 `README.md` 示例与说明。
- 新命令至少要补 renderer 单测，以及受影响的 CLI stdout 测试或 RPC/契约测试增量。
- 如果新增前缀词、冻结字段、恢复语义或 `--verbose` 事件类型，必须同步更新本文件与 README。
- 评审输出变更时，优先检查：renderer 家族是否明确、默认模式必出事实是否稳定、YAML allowlist 是否合理、README 与测试是否已同步。
