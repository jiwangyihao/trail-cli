# Command Surface

- `trail cw stage detect --session <id>` / `trail cw stage wait --session <id>`：只用于已进入货币战争后的阶段确认。
- `trail cw slots read --session <id> --slot front:1 --slot hand:4`：读取指定槽位快照；默认使用角色图标识别，不再逐槽位点击详情读取姓名；Agent 可见槽位编号从 1 开始，截图优先。
- `trail cw slots read --session <id>`：读取完整槽位快照；只作为完整兜底，不表示默认每次都要全量读取。带截图 success 后必须先读本次 `shot path` 指向的截图，再消费 `slot`、羁绊和装备/商店事实；若出现 `CW_ROLE_MATCH_LOW_CONFIDENCE` 或 `SLOTS_RECOGNITION_UNCERTAIN`，先核对截图再做换位、出售或购买决策。
- `trail cw slots place --session <id> --action hand:1,front:1 --action hand:2,back:3` / `trail cw slots swap --session <id> ...`：显式改变槽位；本 skill 不决定何时执行。
- `trail cw shop scan --session <id>` / `trail cw shop status --session <id>`：读取商店和经济事实；`shop.scan` 有截图，`cw.shop.status` 不产出截图，也不输出 `info read_image_first=1`。
- `trail cw equipment prepare --session <id> [--refresh]`：默认只验证/汇总 bundle 装备资源，不下载图标；只有 `--refresh` 会写 workspace equipment override 并刷新图标/特征；canonical 为 `cw.equipment.prepare`。
- `trail cw equipment read --session <id>`：读取当前装备背包图标；daemon 默认热路径使用 bundle recognizer，不调用 prepare/download/load icon cache；截图优先，默认 `item` 使用 `pos=equipment:<idx>` 和 `center=x,y`，确定项隐藏 `gap/alt/alt_score`，低置信 `uncertain=1` 才看这些诊断字段；有当前攻略时可在背包 `item` 与 `info backend/layout` 后输出 `# 装备优先级` 的 `guide` 行和 `# 角色装备需求` 的 `slot` 行或 `info todo=slots`，这些分块位于 `warn`、`ref` 之前；看到 `info todo=slots` 时先刷新 `cw.slots.read`，不要用 stale slots 推断角色缺口；需要 `row/col` 诊断时用 `trail --format yaml cw equipment read --session <id>` 或 `trail --format yaml state dump --session <id>`；canonical 为 `cw.equipment.read`。
- `cw.equipment.read` 的 `# 角色装备需求` 当前只推荐攻略 `优选装备` / `first_equipments`；`次选装备` / `second_equipments` 暂不作为合成目标。TODO：最后一层 BOSS 战前的备战阶段再考虑次选装备。
- `trail cw equipment compose --session <id> --name <进阶装备名> --slot <front|back|hand>:<1-based> --role <角色名>`：canonical command 为 `cw.equipment.compose`；只写 session，记录某个 canonical 角色已持有一件进阶装备，不截图，不执行真实 UI 合成，不支持 YAML；slot 使用 Agent 可见 1-based，例如 `front:1`；成功首行固定为 `ok cw.equipment.compose pos=<slot> name=<角色名> 装备=<装备名> count=<角色装备数>`。
- `trail cw shop buy-slot --session <id> --slot <n> --expect <name>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop buy-exp --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw shop refresh --session <id>` / `trail cw shop close --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw crystals collect --session <id>`：晶矿 mutation；本 skill 不决定何时执行。
- `trail cw hand sell-plan --session <id>` / `trail cw hand sell --session <id> --slot 1 --slot 3`：卖牌建议和显式卖牌；本 skill 不决定何时执行。
- `slots.read` 和 `shop.scan` 会按 CW config canonicalize 角色名；低置信度结果会带 `raw_name`、`score`、`match_kind`，必须先读截图确认。
- `item traits=...` 是商品角色 canonicalization 结果，可在 slots 不 fresh 时仍出现；`field trait_summary` 不是默认文本 `item traits`，当前默认文本 renderer 不渲染 shop `trait_summary`，只在 fresh slots 的结构化/RPC 投影中可用。
- 接收 `cw.portal.select` handoff 时，`trail-cw-prep` 优先复用同次 stage/slots/equipment/shop facts；带截图 success 先读原始截图，`# ` 行只是板块标题，不是 action/prefix/fact，Agent 只消费实体行：`# 综合信息`、`# 攻略提示`、`# 角色信息`、`# 羁绊信息`、`# 装备信息`、`# 装备优先级`、`# 角色装备需求`、`# 商店信息`。优先复用同次 equipment facts；缺失/stale/page changed 时才重跑 `cw.equipment.read`，不要在 handoff 后立刻重复扫描 slots/equipment/shop。
- `trail cw battle run --session <id>`：战斗链；本 skill 不决定何时出战。
