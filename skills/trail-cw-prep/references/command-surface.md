# Command Surface

- `trail cw stage detect --session <id>` / `trail cw stage wait --session <id>`：只用于已进入货币战争后的阶段确认。
- `trail cw slots read --session <id> [--slot area:n]`：读取槽位快照；截图优先。
- `trail cw slots place --session <id> ...` / `trail cw slots swap --session <id> ...`：显式改变槽位；本 skill 不决定何时执行。
- `trail cw shop scan --session <id>` / `trail cw shop status --session <id>`：读取商店和经济事实。
- `trail cw equipment prepare --session <id> [--refresh]`：准备装备图标缓存；普通读取会自动补齐缺失/损坏缓存，`--refresh` 只在确认同版本 URL 变化时使用；canonical 为 `cw.equipment.prepare`。
- `trail cw equipment read --session <id>`：读取当前装备背包图标；截图优先，低置信格子看 `uncertain/alt/alt_score`；canonical 为 `cw.equipment.read`。
- `trail cw shop buy-slot --session <id> --slot <n> --expect <name>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop buy-exp --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop refresh --session <id>` / `trail cw shop close --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw crystals collect --session <id>`：晶矿 mutation；本 skill 不决定何时执行。
- `trail cw hand sell-plan --session <id>` / `trail cw hand sell --session <id>`：卖牌建议和显式卖牌；本 skill 不决定何时执行。
- `trail cw battle run --session <id> --timeout 570`：战斗链；本 skill 不决定何时出战。
