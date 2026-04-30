# cw.portal.select 自动收集装备设计

## 背景

`cw.portal.select` 已经在成功选择投资环境并应用当前攻略后，自动收集普通备战阶段需要的基础事实：收水晶、读取 slots、扫描 shop，并把这些事实附加到本次响应。`cw.equipment.read` 现在也已经能读取装备背包、写入 `cw_state.equipment`，并基于当前攻略与 fresh slots 输出装备优先级和角色装备需求。

当前缺口是 Agent 进入备战后仍需要再单独执行一次 `cw.equipment.read`，才能看到装备背包与攻略推荐。用户希望像之前整合 `cw.slots.read` 和 `cw.shop.scan` 一样，把装备读取也纳入 `cw.portal.select` 的自动收集链路。

## 目标

1. `cw.portal.select` 成功进入备战并应用攻略后，自动读取当前装备背包。
2. 装备读取发生在 slots fresh 之后、打开商店之前，使装备推荐能使用当前角色集合，同时不受商店打开/关闭页面状态影响。
3. 成功时把完整装备 snapshot 写入 `cw_state.equipment`，并附加到 `cw.portal.select` 响应的 `data.equipment`。
4. `cw.portal.select` 默认文本新增 `# 装备信息` 板块，并复用 `cw.equipment.read` 的装备 item、backend/layout、`# 装备优先级`、`# 角色装备需求` 输出语义。
5. 装备自动读取失败时不让 `cw.portal.select` 整体失败；用 warn 暴露失败，并继续执行商店自动收集与 handoff。

## 非目标

1. 不做截图复用改造；本设计继续使用现有 runtime/capture 行为。
2. 不改变 `cw.equipment.read` 的识别算法、网格布局、图标缓存策略或推荐算法。
3. 不新增 public 命令。
4. 不把 `cw.portal.select` 加入 YAML allowlist。
5. 不改变 `cw.equipment.compose` 行为，也不执行真实 UI 合成。

## 当前代码边界

1. `trail/daemon/cw_service.py` 的 `_select_portal_and_apply_selected_guide(...)` 是 `cw.portal.select` 自动收集编排点。
2. `collect_cw_crystals(...)`、`read_cw_slots(...)`、`scan_cw_shop(...)` 已作为 scene 层状态写入函数被 portal select 复用。
3. `apply_cw_equipment_read(session, runtime, workspace_root=...)` 已负责读取装备、构建推荐、写入 `cw_state.equipment`，并返回完整 snapshot。
4. `trail/output/rendering.py` 的 `_render_cw_equipment_read(...)` 已实现装备 item 和攻略推荐分块；当前这些 helper 只由 `cw.equipment.read` 使用。
5. 项目输出协议要求新增固定标题时必须同步 renderer、`AGENTS.md`、skills 与测试。

## 设计

### 自动收集顺序

`cw.portal.select` 成功路径按以下顺序组合：

1. `select_cw_portal(...)`
2. `wait_cw_portal_preparation(...)`
3. `_apply_selected_guide_via_ui(...)`
4. `collect_cw_crystals(session, collector=crystal_collector_factory(runtime))`
5. `dismiss_cw_slots_overlay(runtime)`
6. `read_cw_slots(session, reader=build_cw_slots_reader(runtime, dismiss_initial_overlay=False), guide_config=fetch_cw_guide_config(...))`
7. `apply_cw_equipment_read(session, runtime, workspace_root=workspace_root)`，成功后立即把返回值 `deepcopy` 到局部 `equipment_snapshot`
8. `open_cw_shop(session, opener=shop_opener_factory(runtime))`
9. `sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)`
10. `scan_cw_shop(session, scanner=build_cw_shop_page_snapshot_reader(runtime))`
11. 缓存 `slots`、局部 `equipment_snapshot`、`project_cw_shop_snapshot(session)` 到响应数据
12. `close_cw_shop(session, closer=shop_closer_factory(runtime))`

装备读取放在 slots 之后，因为装备推荐需要 fresh slots 判断当前角色的已获取/未获取装备；放在 open shop 之前，因为装备背包读取面向普通备战页，避免商店浮层改变截图布局。

### 装备软失败

装备自动读取使用局部 try/except 包裹，只捕获装备自动收集阶段的异常：

```python
equipment_snapshot = None
try:
    equipment_snapshot = deepcopy(
        apply_cw_equipment_read(session, runtime, workspace_root=workspace_root)
    )
except Exception as exc:
    equipment_warning = _cw_equipment_auto_collect_warning(exc)
```

失败时：

1. 不写 `data.equipment`。
2. 不输出 `# 装备信息`、`# 装备优先级` 或 `# 角色装备需求` 空标题。
3. 追加 warning：`warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED msg=...`。
4. 继续执行 shop open/scan/close。
5. 仍保留 `cw.portal.select` success 与最后的 `info handoff_skill=trail-cw-prep ...`。

warning message 使用异常的稳定用户可读信息；如果异常是 `TrailError`，保留其 `code` 到结构化 warning，例如 `detail_code=<code>`。默认文本不新增 `why` 行，因为整体命令仍成功。

### 响应数据

`cw.portal.select` 的 `data` 在现有字段基础上追加：

```python
{
    "equipment": {
        "count": 2,
        "uncertain": 0,
        "empty": 58,
        "items": [...],
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
        "recommendations": {...},
    },
}
```

该 shape 与 `cw.equipment.read` 返回值一致，避免 renderer 和 Agent 需要理解两套装备数据。响应构造必须使用 `apply_cw_equipment_read(...)` 返回后立即保存的局部 `equipment_snapshot`：成功时写入 `selected_data["equipment"] = deepcopy(equipment_snapshot)`；不得在 `close_cw_shop(...)` 或 mutation finalizer 之后再从 `cw_state.equipment` 重新读取来投影响应。

### 文本输出

`cw.portal.select` success 默认文本顺序固定为：

1. 首行：`ok cw.portal.select idx=... 投资环境=...`
2. `shot path=...`
3. `info read_image_first=1`
4. `# 综合信息`
5. stage/status `info` 行
6. `# 攻略提示`
7. `info skill_info=运营思路 text=...`
8. `# 角色信息`
9. slots `slot` 行
10. `# 羁绊信息`
11. traits `info` 行
12. `# 装备信息`
13. equipment `item` 行和 `info count=... uncertain=... empty=... backend=... layout=...`
14. `# 装备优先级`
15. equipment recommendation `guide` 行
16. `# 角色装备需求`
17. equipment recommendation `slot` 行或 `info todo=slots`
18. `# 商店信息`
19. shop `item` 行和 `info coins=... reserve_full=...`
20. `warn` 行
21. `ref` 行
22. 最后一行继续由 workflow handoff 追加：`info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`

新增固定标题：`# 装备信息`。该标题只用于分组，不承载 must-keep 事实，不是正文前缀。`# 装备优先级` 与 `# 角色装备需求` 已存在，portal select 复用其语义。

如果 equipment snapshot 存在但 `items` 为空，仍可输出 `# 装备信息` 和 `info count=0 uncertain=0 empty=60 backend=... layout=...`，因为 `count/uncertain/empty` 是影响下一步判断的 must-keep 事实；但不输出空 item 行。如果 recommendations 不存在，则不输出推荐标题。

### Renderer 复用

从 `_render_cw_equipment_read(...)` 中抽出小 helper，供 `cw.equipment.read` 和 `cw.portal.select` 共用：

1. `_append_cw_equipment_lines(lines, data, include_summary=False)`：追加装备 `item` 行与 backend/layout 信息，不输出首行、截图、warn/ref。`cw.equipment.read` 使用 `include_summary=False`，因为 `count/uncertain/empty` 已在首行；`cw.portal.select` 使用 `include_summary=True`，在 `# 装备信息` 下输出 `info count=... uncertain=... empty=... backend=... layout=...`。
2. `_append_cw_equipment_recommendation_lines(lines, data)`：继续复用现有推荐分块 helper。
3. `_render_cw_equipment_read(...)` 继续负责首行、截图、低置信 warning、warn/ref。
4. `_render_cw_portal_select(...)` 在 `data.equipment` 非空时，先输出 `# 装备信息`，再调用装备 helper，然后调用推荐分块 helper。

低置信装备仍在 portal select 中输出 warning。为了避免重复逻辑，可新增 `_append_cw_equipment_low_confidence_warning(lines, data)` 并由两个 renderer 复用。

### 状态与 stale 语义

成功读取 equipment 后，`apply_cw_equipment_read(...)` 已写入 `cw_state.equipment.stale=False`。

同一个 `cw.portal.select` 自动收集链路内，刚读到的 `cw_state.equipment` 不得被后续内部 `open_cw_shop`、`scan_cw_shop`、`close_cw_shop` 或 mutation finalizer 标记 stale。若当前 `handle_mutation` 成功后会统一调用 `_mark_cw_equipment_stale(session)`，需要增加明确例外：当本次 command 是 `cw.portal.select` 且本次响应 `data.equipment` 存在并且 `stale=False` 时，本次 finalizer 跳过 `_mark_cw_equipment_stale(session)`。

如果 equipment 自动收集失败或本次响应没有 `data.equipment`，通用 finalizer 仍按现有规则把旧 equipment snapshot 标记 stale，避免把历史装备误判为当前 fresh 事实。

独立的后续 CW mutation 仍应按现有规则把最近装备快照标记 stale。

## 文档与 skill 同步

需要同步更新：

1. `AGENTS.md`：新增固定标题 `# 装备信息`，更新 `cw.portal.select` 自动收集顺序与板块顺序，说明装备自动收集失败是 success warning，并为 mutation stale 规则补充 `cw.portal.select` 同次 fresh `data.equipment.stale=False` 的例外。
2. `README.md`：更新 `cw.portal.select` 示例，展示装备板块和装备推荐板块。
3. `skills/trail-cw-prep/SKILL.md` 和 `skills/trail-cw-prep/references/command-surface.md`：handoff 后先读截图，再消费 stage/slots/equipment/shop facts；只有缺失、stale 或页面变化时才重跑 `cw.slots.read`、`cw.equipment.read` 或 `cw.shop.scan`。
4. `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-portal/references/portal-command-surface.md`、`skills/trail-cw-guide/SKILL.md`、`skills/trail-cw-guide/references/command-surface.md`、`skills/trail-hsr/references/scene-entry-index.md`：同步 portal select handoff 现在携带装备预备事实；其它 guide references 若描述 portal select 后续语义，也要同步。
5. 所有会消费或描述 `cw.portal.select` 输出、handoff 或后续语义的 active skill、相关 references 和 scene-entry 文档必须明确：`# 综合信息`、`# 攻略提示`、`# 角色信息`、`# 羁绊信息`、`# 装备信息`、`# 装备优先级`、`# 角色装备需求`、`# 商店信息` 都只是标题行，不是 action、prefix 或事实行；Agent 只消费 `item`、`guide`、`slot`、`info`、`warn`、`ref` 等实体行。
6. `cw portal select --help` 必须同步装备自动收集与 soft warning；命令 surface 文档凡是描述 select 后续语义，也需要同步。
7. `tests/test_output_debug.py`、`tests/test_skill_structure.py`、`tests/test_output_rendering.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_atomic_commands.py`、`tests/test_skill_routing_contracts.py`：按各自职责锁定协议、active skill、help、CLI/RPC contract 和 routing/handoff 文档同步。

## 测试策略

1. `tests/test_daemon_protocol.py`：覆盖 `cw.portal.select` 成功路径调用顺序，确认 `read_cw_slots -> apply_cw_equipment_read -> open_cw_shop`，且 equipment 在 slots 后、shop open 前执行。
2. `tests/test_daemon_protocol.py`：fake `apply_cw_equipment_read` 返回 `stale=False` snapshot，断言 response `data.equipment` 与 persisted `cw_state.equipment` 一致，portal select 完成后 `cw_state.equipment.stale=False`；随后执行一个独立 CW mutation，断言同一 equipment snapshot 被标记为 `stale=True`。
3. `tests/test_daemon_protocol.py`：覆盖 equipment 自动读取失败时 `cw.portal.select` 仍成功、继续 shop 扫描、响应不含 `data.equipment`、旧 equipment snapshot 被标记 stale、handoff 最后一行不变。
4. `tests/test_output_rendering.py`：覆盖 portal select 完整 success 输出顺序：首行 -> `shot` -> `info read_image_first=1` -> `# 综合信息` -> `# 攻略提示` -> `# 角色信息` -> `# 羁绊信息` -> `# 装备信息` -> equipment `item` -> `info count=... uncertain=... empty=... backend=... layout=...` -> `# 装备优先级` -> `guide` -> `# 角色装备需求` -> `slot` 或 `info todo=slots` -> `# 商店信息` -> shop facts -> `warn` -> `ref` -> handoff。
5. `tests/test_output_rendering.py`：覆盖 equipment 自动读取异常路径不输出 `# 装备信息`、`# 装备优先级`、`# 角色装备需求`，不输出 `LOW_CONFIDENCE`，仍输出 shop facts，`warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED` 位于 `warn/ref` 区域，handoff 仍为最后一行。
6. `tests/test_output_rendering.py`：覆盖低置信 equipment 成功路径输出 `warn code=LOW_CONFIDENCE count=...`，且不输出 `CW_EQUIPMENT_AUTO_COLLECT_FAILED`；如果同时存在其它 portal warning，顺序仍满足 success entity 行先于 `warn/ref/handoff`。
7. `tests/test_output_rendering.py`：覆盖 `cw.equipment.read` 独立命令不回归，包括首行 `ok cw.equipment.read count=<n> uncertain=<n> empty=<n>`、`shot` 与 `info read_image_first=1` 紧邻、推荐分块顺序、`LOW_CONFIDENCE` warning。
8. `tests/test_cw_rpc_contracts.py`：更新 `cw.portal.select` CLI/RPC success 期望文本和 data shape，断言 `data.equipment` shape 与 `cw.equipment.read` 一致；至少覆盖一个带 equipment warning 的 fake daemon response，断言 stdout 包含 warning 与 shop facts，且 `result.stdout.rstrip().endswith("info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered")`；保留 `cw.equipment.read --format yaml` allowlist 回归。
9. `tests/test_cw_equipment.py`：如需抽 renderer/helper 不改 scene 行为，现有装备推荐测试应保持不变；若新增推荐构建 wrapper，则补最小单测。
10. `tests/test_skill_structure.py`：更新 active skill 文档断言，覆盖 `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-guide/SKILL.md`、`skills/trail-cw-guide/references/command-surface.md`、所有包含 `cw.portal.select` 或 `portal select --card-idx` 后续语义的 guide references、`skills/trail-cw-prep/SKILL.md`、`skills/trail-hsr/references/scene-entry-index.md` 都说明 `# ` 行只是标题，不是 action/prefix/fact，并包含全部固定标题列表。
11. `tests/test_output_debug.py`：只用于锁定 `AGENTS.md` 输出协议文本中新增标题、顺序、soft warning 和 stale 语义；不要把 active skill 文档同步主要放在 debug 测试中。
12. `tests/test_atomic_commands.py`：必须更新 `test_cw_portal_select_help_mentions_selected_guide_auto_apply` 或新增 help 文本断言，确认 `cw portal select --help` 提到自动收集 stage/slots/equipment/shop facts、装备失败是 soft warning、handoff 语义不变。
13. `tests/test_skill_routing_contracts.py`：增加 scene-entry-index/handoff smoke，确认 `cw.portal.select -> trail-cw-prep` 仍是 internal handoff，且 handoff 文档说明现在携带 stage/slots/equipment/shop facts。

## 验收标准

1. `cw.portal.select` success 响应包含 `data.equipment`，其 shape 与 `cw.equipment.read` 一致；同次命令结束后 session 中 `cw_state.equipment.stale=False`。
2. `cw.portal.select` 成功路径调用顺序可由测试断言为 `read_cw_slots -> apply_cw_equipment_read -> open_cw_shop`。
3. `cw.portal.select` 默认文本按可断言顺序输出：首行 -> `shot` -> `info read_image_first=1` -> slots/traits sections -> `# 装备信息` -> equipment `item` -> `info count/uncertain/empty/backend/layout` -> `# 装备优先级` -> `guide` -> `# 角色装备需求` -> `slot` 或 `info todo=slots` -> `# 商店信息` -> shop facts -> `warn/ref` -> final handoff。
4. 在 slots fresh 且 recommendations 可构建的测试数据下，portal select 装备推荐不输出 `info todo=slots`。
5. equipment 自动读取失败时，`cw.portal.select` 仍 success，响应不含 `data.equipment`，不输出 `# 装备信息/# 装备优先级/# 角色装备需求`，输出 `warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED ...`，shop facts 仍存在，handoff 仍是最后一行。
6. 低置信 equipment 成功路径输出 `warn code=LOW_CONFIDENCE count=...`，且不输出 `CW_EQUIPMENT_AUTO_COLLECT_FAILED`。
7. equipment 自动读取失败路径不输出 `LOW_CONFIDENCE`，避免把识别低置信和自动收集异常混淆。
8. portal select 后续独立 CW mutation 会把最近 equipment snapshot 标记 `stale=True`，不破坏既有 stale 规则。
9. `cw.equipment.read` 独立命令保持兼容：首行字段、`shot`/`info read_image_first=1` 顺序、YAML allowlist、推荐分块顺序、`LOW_CONFIDENCE` warning 都有回归测试覆盖。
10. `AGENTS.md`、README、active skills、skill references、相关 tests 均同步 `# 装备信息`、portal select 装备自动收集、soft warning 和标题消费规则。
