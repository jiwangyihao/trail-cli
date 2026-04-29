# CW 装备读取持久化与输出协议设计

## 背景

`cw.equipment.read` 已能在备战页识别装备追踪栏，并按列优先顺序返回装备列表。当前第一版协议仍带有调试导向字段：默认文本输出 `row/col/box/gap/alt/alt_score`，且读取结果只作为当次响应返回，没有写入 `session.scene_state["cw"]`。后续动作需要稳定引用装备格中心坐标，因此装备读取需要和角色槽位一样暴露 Agent 可见的槽位标记。

## 目标

1. 每次 `cw.equipment.read` 成功后，将读取结果持久化到当前 session 的 `cw_state.equipment`。
2. 提供装备槽位 helper：输入 Agent 可见 1-based `equipment:<idx>`，输出当前网格下该格中心坐标。
3. 默认文本输出改为面向 Agent 的稳定动作事实：使用归一化装备槽位标记和中心点，不再输出 `row/col/box`。
4. `gap/alt/alt_score` 只在单格 `uncertain=1` 时默认输出；确定结果不输出第二候选噪声。
5. 结构化数据仍保留 `row/col` 诊断信息；通过 `cw.equipment.read --format yaml` 和 `state.dump --format yaml` 可查看。

## 非目标

1. 不新增装备点击、拖拽或购买动作。
2. 不改变装备识别算法、阈值和图标缓存策略。
3. 不把 `row/col` 作为 Agent 动作输入主通道。
4. 不通过 `--verbose` 塞入业务事实；verbose 继续只承载 debug/trace 信息。

## 输出协议

`cw.equipment.read` 仍归入列表读取 renderer 家族。success 首行保持：

```text
ok cw.equipment.read count=<n> uncertain=<n> empty=<n>
```

`count/uncertain/empty` 即使为 `0` 也必须保留。

默认正文顺序保持：首行 -> `shot` -> `info read_image_first=1` -> `item` -> `info` -> `warn` -> `ref`。

确定装备默认输出示例：

```text
item pos=equipment:1 center=1855,275 name=生命之花 score=0.93 uncertain=0
```

不确定装备默认输出示例：

```text
item pos=equipment:7 center=1775,275 name=蓝钻 score=0.78 uncertain=1 gap=0.03 alt=光能电池 alt_score=0.75
```

默认文本不输出 `idx`、`row`、`col`、`box`。`pos=equipment:<idx>` 是 1-based、列优先、从右到左的归一化装备槽位标记；其数值等价于原有 `item idx`，但字段名改为和 slots family 一致的 `pos`。

## 结构化数据与 YAML

`cw.equipment.read` 加入 YAML allowlist。`--format yaml` 继续先输出默认文本，再追加完整 `data`，不是回退旧式 envelope。实现必须同步更新 `AGENTS.md` 当前 YAML allowlist 列表和 `cw.equipment.read` 冻结字段说明。结构化 `data.items[*]` 包含：

- `pos`: `equipment:<idx>`
- `idx`: 1-based 顺序序号，用于兼容内部排序与诊断
- `row` / `col`: 网格诊断信息，不进入默认文本
- `center`: `{x, y}`
- `name`, `equipment_id`, `cache_key`
- `score`, `gap`, `uncertain`
- `alt`, `alt_score`, `candidates`：保留在结构化数据中，默认文本只在 `uncertain=1` 时渲染 `gap/alt/alt_score`

结构化数据不再包含 `box`。如果后续确实需要边界框，应由 helper 或网格 profile 重新计算，而不是长期保存冗余矩形。

## Session 持久化

新增 `CwSceneState.equipment` 默认值：

```python
{"stale": True}
```

每次 `cw.equipment.read` 成功后写入：

```python
{
    "items": [
        {
            "pos": "equipment:1",
            "idx": 1,
            "row": 1,
            "col": 1,
            "center": {"x": 1855, "y": 275},
            "name": "生命之花",
            "equipment_id": "example-equipment-id",
            "cache_key": "advanced-example-equipment-id",
            "score": 0.93,
            "gap": 0.14,
            "uncertain": False,
            "alt": "光能电池",
            "alt_score": 0.79,
            "candidates": [],
        }
    ],
    "count": count,
    "uncertain": uncertain,
    "empty": empty,
    "backend": "vector",
    "layout": "default",
    "columns": 10,
    "rows": 6,
    "stale": False,
}
```

持久化 item 与结构化响应 item 使用同一规范化 shape，包含 `pos/idx/row/col/center/name/equipment_id/cache_key/score/gap/uncertain/alt/alt_score/candidates`，不包含 `box`。读取命令本身不会标记角色槽位、商店或阶段状态 stale。

## 失效策略

`cw_state.equipment` 表示最近一次装备读取快照，不是永久真相。`cw.equipment.read` 成功时写入 `stale=False`。任何成功的 CW mutation 若可能推进页面、改变对局状态或改变装备背包，都必须保留最近 items 并把 `cw_state.equipment.stale` 设为 `True`；第一版采用保守策略：除 `cw.equipment.prepare` 外，成功进入 `handle_mutation` 的 CW 命令统一标记装备快照 stale。这样不会丢失最近一次识别结果，也避免后续动作把旧装备快照当作 fresh truth。

实现计划需要至少补一个失效测试，证明 `cw.equipment.read` 后再运行一个代表性 CW mutation，`state.dump` 中 `cw.equipment.stale` 会变为 `True`。

## Helper

在 `trail.scenes.cw.equipment_grid` 提供 helper：

```python
equipment_slot_center(
    value: str,
    *,
    profile: EquipmentGridProfile = DEFAULT_EQUIPMENT_GRID_PROFILE,
    columns: int = 10,
    rows: int = 6,
) -> tuple[int, int]
```

规则：

- 输入必须是 `equipment:<idx>`。
- `idx` 为 Agent 可见 1-based 顺序序号。
- 有效范围默认是当前 profile 的 `1..60`。
- 无效前缀、非数字、越界编号统一抛 `TrailError("CW_EQUIPMENT_SLOT_INVALID", "invalid equipment slot: <value>")`。
- `equipment:1` 对应 `row=1,col=1`，`equipment:6` 对应 `row=6,col=1`，`equipment:7` 对应 `row=1,col=2`。
- 坐标由现有网格 profile 计算中心点，避免复制硬编码表。中心点必须先通过 `iter_equipment_grid_cells(profile, columns, rows)` 找到 `EquipmentGridCell`，再按 `cell.box["left"] + cell.box["width"] // 2` 与 `cell.box["top"] + cell.box["height"] // 2` 计算；`read_cw_equipment` 写入的 `center` 和默认文本 `center=x,y` 必须使用同一规则，renderer 不自行从 `row/col` 或 float 坐标重新推导。
- helper 可以接受 keyword-only `profile/columns/rows` 以便测试和未来扩展，但默认值必须等同当前 `DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6`。

## 测试范围

1. `tests/test_cw_equipment.py` 覆盖 helper 的合法/非法输入、中心坐标、`equipment:1/6/7` 的列优先映射、item shape 和 `gap/alt` 默认隐藏规则需要的数据基础。现有断言 `item["box"]` 的测试要改为断言结构化 item 有 `pos/center/row/col` 且不含 `box`。
2. `tests/test_daemon_protocol.py` 覆盖 `cw.equipment.read` 通过 capture 路径后会保存 `cw_state.equipment`，并覆盖代表性 CW mutation 会把已有 `cw_state.equipment.stale` 标记为 `True`。现有 `test_command_service_routes_cw_equipment_read_through_capture` 要扩展持久化断言；新增 `test_command_service_marks_cw_equipment_snapshot_stale_after_mutation` 锁定 mutation 失效语义。
3. `tests/test_output_rendering.py` 覆盖默认文本改为 `pos/center`，隐藏 `row/col/box`，确定项隐藏 `gap/alt/alt_score`，不确定项显示 `gap/alt/alt_score`。现有 `test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref` 的旧字段断言必须迁移。
4. `tests/test_cw_rpc_contracts.py` 覆盖 `cw.equipment.read --format yaml` 从不支持变为允许，并保留默认文本前缀。现有 `test_cw_equipment_read_maps_to_canonical_command_and_rejects_yaml` 必须改为 success YAML contract。
5. `tests/test_output_debug.py::test_project_agents_declares_renderer_contracts` 覆盖 `AGENTS.md` 中 YAML allowlist 与 `cw.equipment.read` 协议说明的更新。
6. `tests/test_atomic_commands.py` 新增 `test_state_dump_yaml_includes_cw_equipment_snapshot`，覆盖 `scene_state.cw.equipment.items[*]` 在 `state.dump --format yaml` 中可见，且包含 `pos/center/row/col`、不含 `box`。
7. README、`AGENTS.md`、`skills/trail-cw-prep/SKILL.md` 与 command surface 同步更新输出协议和使用建议。必须删除 `cw.equipment.read` “第一版不写入 cw_state / 不加入 YAML allowlist”的旧语义；README 示例改为 `pos/center`；skill 文档写明 `pos=equipment:<idx>`、`center`、低置信诊断字段和 YAML/state.dump 诊断路径。

## 验收标准

1. `cw.equipment.read` 默认文本每个装备 item 至少包含 `pos/center/name/score/uncertain`。
2. 确定装备不输出 `gap/alt/alt_score`；不确定装备输出这些诊断字段。
3. 默认文本不出现 `row=`、`col=` 或 `box=`。
4. `state.dump --format yaml` 可看到最近一次装备读取结果。
5. `cw.equipment.read --format yaml` 可看到结构化 `row/col`。
6. 代表性 CW mutation 会把已有装备快照标记为 `stale=True`。
7. 以下聚焦测试通过：`uv run pytest tests/test_cw_equipment.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture tests/test_daemon_protocol.py::test_cw_equipment_methods_are_classified_for_command_routing tests/test_daemon_protocol.py::test_command_service_marks_cw_equipment_snapshot_stale_after_mutation tests/test_output_debug.py::test_project_agents_declares_renderer_contracts tests/test_atomic_commands.py::test_state_dump_renders_summary_before_yaml tests/test_atomic_commands.py::test_state_dump_yaml_includes_cw_equipment_snapshot -q --basetemp .trail/pytest-tmp/equipment-session-output -p no:cacheprovider`。
8. `rtk git diff --check` 无输出。
