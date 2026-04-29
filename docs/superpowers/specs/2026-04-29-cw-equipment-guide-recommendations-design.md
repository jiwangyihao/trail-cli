# CW 装备读取攻略推荐设计

## 背景

`cw.equipment.read` 目前已经能在货币战争备战页识别装备背包图标，并把最近一次背包快照写入 `cw_state.equipment`。输出协议固定为列表读取 renderer：首行 `ok cw.equipment.read count=<n> uncertain=<n> empty=<n>`，截图后先输出 `item pos=equipment:<idx> center=x,y name=...`，再输出 `info backend=... layout=...`、低置信 `warn` 和 `ref`。

当前缺口是 Agent 只能看到“背包里有什么”，看不到“当前攻略真正要优先合成什么、合成会消耗哪些基础装备、哪些角色还缺哪些推荐装备”。这会导致 Agent 可能为了较低优先级装备过早消耗基础装备。项目中还没有真实合成装备的 UI 命令，因此第一版只新增一个写 session 的壳命令，用来记录“某个角色已经持有某件进阶装备”。

## 目标

1. `cw.equipment.read` 在保持现有背包识别输出不破坏的前提下，追加基于当前攻略的装备推荐分块。
2. 第一块展示当前攻略明确列出的 `order_compose` 进阶装备优先度，按攻略顺序排序，并展示合成所需基础装备的当前持有情况。
3. 第一块同时展示该进阶装备在攻略角色装备需求中的需求角色、已获取角色、未获取角色。
4. 第二块展示当前 session 已有角色中，攻略推荐但尚未记录为已持有的装备需求。
5. 新增 `cw.equipment.compose` 壳命令，只写 session，把装备持有情况持久化到角色上，并阻止每个角色超过 3 件装备。
6. 多块信息必须使用 `trail/output/rendering.py` 现有 `_append_section` 分块 helper，不手写随意标题格式。

## 非目标

1. 不实现真实 UI 合成、拖拽、点击、装备到角色动作。
2. 不改变装备图标识别算法、阈值、网格裁切或图标缓存策略。
3. 不让 `cw.equipment.compose` 截图，也不把它加入 YAML allowlist。
4. 不把手牌区重复角色错误计入待合成列表；重复角色只展示一个 canonical 角色槽位。

## 现有项目约束

1. `cw.equipment.read` 已在 `trail/scenes/cw/equipment.py` 中通过 `apply_cw_equipment_read()` 写入 `cw_state.equipment`。
2. 当前攻略通过 `cw_state.guide` 保存，完整性由 `require_complete_cw_guide()` 校验。
3. 当前角色槽位通过 `cw_state.slots` 保存，`cw.slots.read` 会写入 `front`、`back`、`hand` 和 `stale`。
4. `guide.fetch.cw` 已把攻略的 `order_compose`、`role_stages[*].front_roles/back_roles[*].first_equipments/second_equipments` 归一化进当前攻略 payload。
5. raw config 的 `equipment_list[*].compose_list[*].childrens` 是进阶装备到基础装备的来源。
6. 新增输出标题必须同步更新 `AGENTS.md`、skills 与 renderer 测试；根目录 `README.md` 只在影响普通安装、用户入口或公开定位时更新。

## 状态模型

不新增独立 `equipment_records`。装备持有事实持久化到角色对象本身：

```python
cw_state["slots"] = {
    "front": [
        {"name": "希儿", "star": 3, "equipments": ["高周波电锯", "战场进化手册"]},
    ],
    "back": [...],
    "hand": [...],
    "stale": False,
}
```

如果目标槽位当前是字符串，例如 `"希儿"`，`cw.equipment.compose` 写入时把它转换为 `{"name": "希儿", "equipments": [装备名]}`。如果原本是 dict，则保留 `star`、`rarity`、`traits`、匹配诊断等已有字段，只追加或更新 `equipments`。

`cw.slots.read` 后续刷新槽位时，需要尽量保留旧角色对象上的 `equipments`，但只能在 canonical 角色之间迁移：先按本设计的 canonical 规则从旧 `cw_state.slots` 建立“角色名 -> 装备列表”映射，再把装备列表合并到新 `cw_state.slots` 中同名角色的 canonical 槽位。非 canonical 的重复槽位不得携带 `equipments`，即使旧数据里已有该字段也要在刷新时清理，避免把场上角色装备复制到重复手牌。

## 重复角色规则

当前角色候选按 `front[0..] -> back[0..] -> hand[0..]` 顺序生成 canonical 角色槽位。相同角色名只保留第一个：

1. `front/back` 上的角色优先于 `hand`。
2. 多张同名手牌只保留第一张。
3. `# 角色装备需求` 只展示 canonical 角色槽位。
4. `# 装备优先级` 的已获取/未获取角色也只按 canonical 角色集合判断，不把重复手牌算成额外未获取需求。
5. `cw.equipment.compose --slot hand:N --role X` 如果指向非 canonical 的重复角色，返回失败，并提示使用 canonical slot。
6. 所有推荐计算、compose 校验、slots 刷新保留装备都必须复用同一个 canonical helper，避免不同模块对重复角色的判断分叉。

这条规则避免把手牌区重复卡误判成独立可装备角色。

## 推荐数据派生

在 `trail/scenes/cw/equipment.py` 增加推荐构建逻辑，输入为当前 session、刚识别的背包 snapshot、raw config。

### 装备目录与合成需求

从 raw config 构建一个显式 recipe helper，例如 `build_cw_equipment_recipes(raw_config)`。推荐算法必须消费该 helper，不能从现有 `build_cw_equipment_catalog()` 的扁平图标目录反推配方，因为扁平目录已经丢失 advanced -> basic 的父子关系。

该 helper 从 raw config 构建：

1. 进阶装备名称集合：`equipment_list[*].name`。
2. 进阶装备合成需求：`equipment_list[*].compose_list[*].childrens[*]`。
3. 基础装备需求按 `kind=basic`、`id/cache_key/name` 保留身份；重复 child 需要累计 `need`。
4. 如果某个进阶装备没有可用 child，`基础装备` 字段省略，但推荐行仍可输出角色需求事实。

当前持有基础装备数量来自刚识别的 `snapshot["items"]`。统计时优先按 `cache_key` 或 `kind+id` 匹配 recipe child；只有缺少稳定身份时才降级按 `name` 匹配。这样避免 raw config 中进阶装备和基础装备同名或同 id 时误计数量。默认文本仍按名称展示，`have=0` 必须在 compact 值里保留，例如 `基础装备=基础装甲:0/1|光能电池:2/1`。

### 攻略装备需求

从当前攻略 `role_stages` 中收集角色装备需求：

1. `first_equipments` 视为 `分类=优选`。
2. `second_equipments` 视为 `分类=次选`。
3. 同一角色同一装备去重；若同时出现在优选和次选，保留优选。
4. 角色名和装备名兼容字符串与 `{name: ...}` 两种历史形态。
5. 同一个 name extractor 也必须用于 `order_compose`，因为当前 selected guide 的历史测试夹具可能保留 `[{"name": "风暴"}]`，并不总是 fetch 后的字符串列表。

### 第一块：装备优先级

按当前攻略 `order_compose` 顺序输出。每个进阶装备推荐项包含：

1. `idx`：1-based 优先级序号。
2. `装备`：进阶装备名。
3. `基础装备`：`基础名:have/need` 列表，按 raw config child 顺序聚合。
4. `需求角色`：攻略中推荐该装备的角色集合。
5. `已获取数` / `已获取角色`：canonical 角色中已在 `equipments` 记录该装备的角色。
6. `未获取数` / `未获取角色`：canonical 角色中需要该装备但尚未记录的角色。

`需求角色` 表示攻略中所有需要该装备的角色；`已获取角色` 和 `未获取角色` 只在当前 canonical 角色集合内计算。攻略里需要该装备、但当前 front/back/hand 去重后不存在的角色，不进入当前未获取数，避免把尚未拥有的未来角色误当作立即需要合成的缺口。

如果攻略 `order_compose` 中出现 raw config 未识别为进阶装备的名称，仍保留推荐行，但不输出 `基础装备`，并在结构化数据里保留 `known=0` 供诊断。默认文本不新增 warn，避免攻略数据与 config 小幅差异导致噪音。

这类未知 `order_compose` 装备仍允许 `cw.equipment.compose` 记录，因为它来自当前攻略的明确进阶装备优先级；只是没有 recipe，因此不能展示基础装备持有情况。

### 第二块：角色装备需求

只展示 canonical 角色槽位中已有角色的未获取装备需求。每行表示一个角色还缺一件攻略推荐装备：

```text
slot pos=front:1 name=希儿 装备=高周波电锯 分类=优选
```

若当前 slots 缺失或 `stale != False`，不猜测当前已有角色，输出 `info todo=slots`，提示先执行 `cw.slots.read`。同一状态下，`# 装备优先级` 仍可展示攻略顺序、基础装备持有情况和攻略需求角色，但不得输出依赖当前角色集合的 `已获取数`、`已获取角色`、`未获取数`、`未获取角色`。

## 默认文本输出协议

`cw.equipment.read` 保持现有首行与背包 item 顺序：

```text
ok cw.equipment.read count=2 uncertain=0 empty=58
shot path=.trail/shots/req-equipment-read.png
info read_image_first=1
item pos=equipment:1 center=1855,275 name=基础装甲 score=0.93 uncertain=0
info backend=vector layout=default
# 装备优先级
guide idx=1 装备=高周波电锯 基础装备=基础装甲:1/1|光能电池:0/1 需求角色=希儿 已获取数=0 未获取数=1 未获取角色=希儿
# 角色装备需求
slot pos=front:1 name=希儿 装备=高周波电锯 分类=优选
```

顺序固定为：首行 -> `shot` -> `info read_image_first=1` -> 背包 `item` -> `info backend/layout` -> `# 装备优先级` -> `guide` 推荐行 -> `# 角色装备需求` -> `slot` 缺口行或 `info todo=slots` -> `warn` -> `ref`。

新增标题 `# 装备优先级`、`# 角色装备需求` 必须加入项目输出协议文档与测试。标题不承载 must-keep 事实，不新增正文前缀。

## `cw.equipment.compose` 命令

新增 CLI：

```text
trail cw equipment compose --session <id> --name <进阶装备名> --slot <front|back|hand:1-based> --role <角色名>
```

canonical command：`cw.equipment.compose`。

`cw.equipment.compose` 归入检测/状态摘要 renderer 家族。它成功不截图、不输出正文实体行；success 首行固定为：

```text
ok cw.equipment.compose pos=<agent-visible-slot> name=<角色名> 装备=<装备名> count=<n>
```

首行字段顺序固定为 `pos`、`name`、`装备`、`count`。`count` 表示该角色写入本次装备后已记录的装备数量，是用于阻止超过 3 件装备的 must-keep 事实，即使为 `0` 也不得省略。`cw.equipment.compose` 不加入 YAML allowlist；`--format yaml` 必须返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。

CLI 参数转换：

1. `--slot` 使用 Agent 可见 1-based，CLI 复用 `_parse_agent_slot_ref()` 转成内部 0-based。
2. `--name` 原样传给 payload 的 `name`。
3. `--role` 原样传给 payload 的 `role`。

服务端处理：

1. 路由为有 request journal 和 taint gate 的 session-only CW mutation：不走 UI、不截图，但必须像 `guide.fetch.cw --select` 一样记录 request journal、支持重复 request replay、执行 session taint 检查，并在成功后保存 session。
2. 读取当前攻略并校验完整。
3. 校验 `cw_state.slots.stale is False`。
4. 校验目标 slot 存在角色，且 slot 中角色名与 `--role` 一致。
5. 计算同名角色的 canonical slot；如果传入 slot 不是 canonical slot，失败并提示应使用的 Agent 可见 slot。
6. 校验装备名是 raw config 进阶装备、当前攻略 `order_compose`、或当前攻略角色装备推荐中的至少一种。
7. 规范化该角色已有 `equipments`：只保留非空字符串并去重；若原字段不是 list，返回明确失败。
8. 校验该角色未重复持有该装备。
9. 校验该角色当前装备数小于 3。
10. 写回该角色对象的 `equipments` 列表并保存 session。
11. 成功后标记最近 `cw_state.equipment` 背包快照 stale，或确保其中不持久化旧 `recommendations`，避免 state dump/YAML 暴露 compose 前的推荐投影。

成功默认文本示例：

```text
ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=1
```

失败示例：

```text
fail cw.equipment.compose code=CW_EQUIPMENT_ROLE_SLOT_STALE
why msg="当前角色槽位已过期，请先执行 cw.slots.read"
```

建议错误码：

1. `CW_EQUIPMENT_ROLE_SLOT_STALE`：slots 缺失或 stale。
2. `CW_EQUIPMENT_ROLE_SLOT_EMPTY`：目标 slot 没有角色。
3. `CW_EQUIPMENT_ROLE_SLOT_MISMATCH`：slot 中角色名与 `--role` 不一致。
4. `CW_EQUIPMENT_ROLE_DUPLICATE_SLOT`：传入 slot 不是该角色 canonical slot。
5. `CW_EQUIPMENT_NAME_INVALID`：装备名既不是已知进阶装备，也不在当前攻略 `order_compose` 或角色装备推荐中。
6. `CW_EQUIPMENT_ALREADY_HELD`：该角色已记录同名装备。
7. `CW_EQUIPMENT_ROLE_EQUIPMENT_FULL`：该角色已记录 3 件装备。
8. `CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID`：角色对象上的既有 `equipments` 不是 list 或包含无法规范化的数据。

其他输入校验规则：缺失或不完整当前攻略沿用 `CW_GUIDE_STATE_INVALID`；空 `--name` 使用 `CW_EQUIPMENT_NAME_INVALID`；空 `--role` 使用 `CW_EQUIPMENT_ROLE_SLOT_MISMATCH`；RPC 直接传入非法内部 slot 时，服务端必须复用 slots 解析规则校验 area 与 0-based index 范围，并在错误信息里返回 Agent 可见 1-based slot。实现时应优先把现有 `_parse_slot_reference()` 抽成可复用 helper，而不是复制私有逻辑。

## 结构化数据

`cw.equipment.read` 已在 YAML allowlist 中。结构化 `data` 应追加：

```yaml
recommendations:
  priority:
    - idx: 1
      name: 高周波电锯
      known: true
      basics:
        - name: 基础装甲
          have: 1
          need: 1
      required_roles: [希儿]
      acquired_roles: []
      missing_roles: [希儿]
  role_missing:
    - pos: front:1
      role: 希儿
      equipment: 高周波电锯
      category: 优选
  todos: []
```

默认文本只消费这些结构化字段，不把 `known`、内部 slot index 或 raw compose object 泄漏为默认正文。

## 文档与测试

需要同步更新：

1. `trail/output/rendering.py`：新增分块 renderer helper，更新 `cw.equipment.read`，新增 `cw.equipment.compose` renderer。
2. `trail/scenes/cw/equipment.py`：新增推荐构建、compose session 写入、角色装备上限与重复角色校验。
3. `trail/scenes/cw/slots.py`：刷新 slots 时只在 canonical 同名角色之间保留 `equipments`，并清理重复槽位上的装备字段。
4. `trail/daemon/command_service.py`：为 `cw.equipment.compose` 接入有 request journal、taint gate、无截图的 session-only CW mutation 路由。
5. `trail/daemon/cw_service.py`：接入 compose handler。
6. `trail/commands/cw.py`：新增 CLI 子命令与 1-based slot 转换。
7. `AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`：同步命令、分块、字段与使用建议；本次不更新根目录 `README.md`。
8. `tests/test_cw_equipment.py`：覆盖推荐构建、recipe helper、基础装备 `have=0`、同名 advanced/basic 不误计、dict/string name extractor、角色装备 3 件上限、重复角色 canonical 规则。
9. `tests/test_output_rendering.py`：覆盖无 guide 时 `cw.equipment.read` 保持旧输出、有 guide/fresh slots 时新分块顺序、slots stale 时 `info todo=slots` 且不输出已获取/未获取字段、warn/ref 仍在新分块之后、`cw.equipment.compose` 首行。
10. `tests/test_daemon_protocol.py`：覆盖 compose session 写入、request journal/taint 路由、错误路径和 session 保存。
11. `tests/test_cw_rpc_contracts.py`：覆盖 CLI 参数映射、1-based slot 转换、slot 0 拒绝、默认文本输出、`--format yaml` 对 compose 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
12. `tests/test_atomic_commands.py`：覆盖 `trail cw equipment --help` / `CW_EQUIPMENT_HELP` 更新，以及 `trail cw equipment compose --help`；帮助文本必须说明 `compose` 是只写 session 的装备记录命令、不执行真实 UI 合成，并覆盖 `--name`、`--slot`、`--role`、1-based slot。
13. `tests/test_output_debug.py`：覆盖 AGENTS.md 输出协议新增标题与命令说明。
14. `tests/test_cw_slots.py`：覆盖 `cw.slots.read` 刷新时只把旧 canonical 角色的 `equipments` 合并到新 canonical 角色，并清理重复 hand 角色上的装备字段。

## 验收标准

1. 无当前攻略时，`cw.equipment.read` 保持现有背包输出，不失败。
2. 有当前攻略与 fresh slots 时，`cw.equipment.read` 输出 `# 装备优先级` 和 `# 角色装备需求`。
3. `# 装备优先级` 按 `order_compose` 排序，基础装备持有数中 `0` 不丢失。
4. `# 角色装备需求` 只展示 front/back/hand 去重后的 canonical 角色，不重复展示同名手牌。
5. `cw.equipment.compose` 成功后，目标角色对象出现 `equipments`，再次 `cw.equipment.read` 会把对应装备从该角色的缺口中移除。
6. `cw.equipment.compose` 对角色装备数超过 3、重复装备、slot/role 不匹配、重复 slot 都返回明确失败。
7. `cw.equipment.compose` 对 stale slots、empty slot、missing guide、invalid equipment name、脏 `equipments` 状态都返回明确失败。
8. `cw.slots.read` 刷新同名角色时只保留 canonical 角色装备，不把装备复制到重复手牌。
9. 新增输出标题、字段、命令、help 与 skill 文档保持同步。
10. 相关单测与 CLI contract 测试通过。

## 后续实现执行约束

实施计划必须明确使用项目内 worktree，并按可独立验证的任务拆分给子代理并发开发。主代理负责整合子代理结果、处理冲突、运行最终验证，并确保不修改用户或其他代理留下的无关变更。
