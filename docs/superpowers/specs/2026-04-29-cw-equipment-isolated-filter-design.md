# cw.equipment.read 孤立装备位过滤设计

## 背景

`cw.equipment.read` 已能读取右侧装备追踪栏，并输出 `pos=equipment:<idx>`、`center`、`name`、`score`、`uncertain`，同时把结构化快照写入 `session.scene_state["cw"]["equipment"]`。实机测试中曾出现一次低置信误识别：真实装备只有 `equipment:1..4`，但额外输出了孤立的 `equipment:10`。

用户确认装备栏的有效装备不应出现“远端孤零零一格”的形态。正常装备位置按列优先、从右到左排列，真实识别结果应表现为局部连续关系。这个事实可以作为识别后处理约束，过滤掉通过图像相似度但不符合位置连续性的噪声。

## 目标

- 在 `cw.equipment.read` 内过滤孤立装备位，减少 `equipment:10` 这类远端误识别。
- 不改变现有图标识别算法、slot marker 判定、输出字段协议、YAML shape 或 session shape。
- 被过滤的孤立项不输出、不进入 YAML data、不写入 session 快照，并自然计入 `empty`。
- 保留合法的连续装备块，避免比“连续前缀”更激进地误删未来可能出现的中间空洞后的连续块。

## 非目标

- 不重新训练或替换 `VectorEquipmentIconRecognizer`。
- 不新增默认文本字段、warn、info 或 debug 事件。
- 不改变 `equipment:<idx>` 的 1-based、列优先、从右到左语义。
- 不调整 `score/gap/uncertain` 的计算或阈值。
- 不修改 README/AGENTS/skill 的输出协议示例；本变更不新增协议字段。

## 设计

在 `trail/scenes/cw/equipment.py` 中新增一个纯函数，用于过滤已经识别出的候选 item：

```python
def _filter_isolated_equipment_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    idxs = {int(item["idx"]) for item in items}
    return [
        item
        for item in items
        if int(item["idx"]) == 1
        or int(item["idx"]) - 1 in idxs
        or int(item["idx"]) + 1 in idxs
    ]
```

过滤发生在 `read_cw_equipment()` 汇总 `best_by_idx` 之后、计算 `count/uncertain/empty` 和返回结果之前。当前流程会先通过 grid crop、slot marker、recognizer 和同 idx 最高分去重得到候选 item；新过滤只处理这些候选，不参与图片裁剪和相似度排名。

保留规则：

- `idx=1` 永远保留，因为第一格可以单独存在。
- 除 `idx=1` 外，如果一个 item 的 `idx-1` 或 `idx+1` 也在候选集合中，则保留。
- 如果一个 item 既不是 `idx=1`，也没有左右相邻候选，则视为孤立误识别并丢弃。

示例：

- `1,2,3,4,10 -> 1,2,3,4`
- `1 -> 1`
- `1,3 -> 1`
- `1,2,5,6 -> 1,2,5,6`
- `5 -> 空列表`
- `7,8 -> 7,8`

该规则对应用户选择的“丢孤立项”，而不是“连续前缀”。因此 `1,2,5,6` 中的第二个连续块会保留，避免未来合法连续块被第一个空洞一刀切删除。

## 数据流

`read_cw_equipment()` 的数据流调整为：

1. 获取归一化截图。
2. 使用 `crop_equipment_cells(image, profile=profile, columns=10, rows=6)` 得到 60 个候选格。
3. 对每格执行 slot marker 过滤。
4. 对通过 marker 的 crop 执行 recognizer。
5. 对同一 `idx` 保留最高分 item，得到 `best_by_idx`。
6. 按 `idx` 排序生成候选 items。
7. 调用 `_filter_isolated_equipment_items(items)` 丢弃孤立项。
8. 用过滤后的 items 计算 `count`、`uncertain`、`empty = len(cells) - len(items)`。
9. 返回结果；session wrapper 持久化同一个过滤后的 snapshot。

## 测试计划

在 `tests/test_cw_equipment.py` 增加单元测试覆盖纯过滤函数：

- `1,2,3,4,10` 过滤后只剩 `1,2,3,4`。
- 单个 `equipment:1` 保留。
- `1,2,5,6` 保留两个连续块。
- 单个远端 `equipment:5` 被过滤为空。
- `7,8` 在没有 `equipment:1` 时仍保留，防止实现误写成“必须从 1 开始的连续前缀”。

增加 `read_cw_equipment()` 集成级测试：构造 recognizer 返回 `idx=1..4` 与孤立 `idx=10`，断言返回结果与 session 可持久化数据中不包含 `equipment:10`，`count=4`，`empty=56`。如果复用现有纯函数测试已充分覆盖 session wrapper，可只在 `read_cw_equipment()` 结果层断言，因为 session 持久化写入的是同一 snapshot。

同步更新现有 marker 过滤测试：当前 `test_read_cw_equipment_filters_crops_without_slot_markers` 只让单个非 1 的 `idx=2` 通过 marker，并断言它会被保留；新规则会把单个 `idx=2` 视为孤立项。该测试应改为只让 `idx=1` 通过 marker，或让 `idx=2/3` 成对通过 marker，从而继续验证“未通过 marker 的 crop 不进入 recognizer”，而不是验证孤立项旧行为。

验证命令：

```bash
uv run pytest tests/test_cw_equipment.py -q --basetemp .trail/pytest-tmp/equipment-isolated-filter -p no:cacheprovider
rtk git diff --check
```

如后续修改触及 renderer 或 session wrapper，再补跑相关契约测试；本设计预期只修改 `trail/scenes/cw/equipment.py` 与 `tests/test_cw_equipment.py`。

## 风险与取舍

- 该规则会丢弃单个远端真实装备。如果未来游戏 UI 允许只在远端单独出现装备，这条假设需要回滚或改为降置信；当前用户明确表示这种情况不可能存在。
- `idx=1` 单独保留是为了支持只有一个装备时的合法状态。
- 连续块保留策略比连续前缀更宽松，可以减少误删，但仍能过滤 `equipment:10` 这类孤立噪声。

## 验收标准

- `cw.equipment.read` 对候选 `equipment:1..4` 加孤立 `equipment:10` 只输出 `equipment:1..4`。
- 单个 `equipment:1` 仍输出，不被误删。
- `equipment:1,2,5,6` 两个连续块都保留。
- 过滤后 `count/uncertain/empty` 基于最终 items 计算，`empty` 包含被过滤的孤立项。
- 默认文本、YAML data 和 session 快照都不包含被过滤的孤立项。
