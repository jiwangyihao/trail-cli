# Command Surface

- `trail cw stage detect --session <id>` / `trail cw stage wait --session <id>`：只用于已进入货币战争后的阶段确认。
- `trail cw slots read --session <id> --slot front:1 --slot hand:4`：读取指定槽位快照；Agent 可见槽位编号从 1 开始，截图优先。
- `trail cw slots read --session <id>`：读取完整槽位快照；只作为完整兜底，不表示默认每次都要全量读取。
- `trail cw slots place --session <id> --action hand:1,front:1 --action hand:2,back:3` / `trail cw slots swap --session <id> ...`：显式改变槽位；本 skill 不决定何时执行。
- `trail cw shop scan --session <id>` / `trail cw shop status --session <id>`：读取商店和经济事实；`shop.scan` 有截图，`cw.shop.status` 不产出截图，也不输出 `info read_image_first=1`。
- `trail cw shop buy-slot --session <id> --slot <n> --expect <name>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop buy-exp --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop refresh --session <id>` / `trail cw shop close --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw crystals collect --session <id>`：晶矿 mutation；本 skill 不决定何时执行。
- `trail cw hand sell-plan --session <id>` / `trail cw hand sell --session <id> --slot 1 --slot 3`：卖牌建议和显式卖牌；本 skill 不决定何时执行。
- `slots.read` 和 `shop.scan` 会按 CW config canonicalize 角色名；低置信度结果会带 `raw_name`、`score`、`match_kind`，必须先读截图确认。
- `item traits=...` 是商品角色 canonicalization 结果，可在 slots 不 fresh 时仍出现；`field trait_summary` 不是默认文本 `item traits`，当前默认文本 renderer 不渲染 shop `trait_summary`，只在 fresh slots 的结构化/RPC 投影中可用。
- `trail cw battle run --session <id>`：战斗链；本 skill 不决定何时出战。
