# cw.equipment.read 孤立装备位过滤 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `cw.equipment.read` 中过滤远端孤立装备位，防止 `equipment:10` 这类不连续低置信误识别进入默认文本、YAML 和 session 快照。

**Architecture:** 在 `trail/scenes/cw/equipment.py` 内新增私有纯函数 `_filter_isolated_equipment_items()`，只基于已识别 item 的 `idx` 邻接关系做后处理。`read_cw_equipment()` 仍先完成截图、slot marker、recognizer 和同槽位最高分去重，再调用过滤函数并用过滤后 items 计算 `count/uncertain/empty`。

**Tech Stack:** Python 3.12、pytest、Pillow、现有 `trail.scenes.cw.equipment` / `tests/test_cw_equipment.py` 测试工具。

---

## 文件结构

- Modify: `trail/scenes/cw/equipment.py`
  - 新增 `_filter_isolated_equipment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]`。
  - 在 `read_cw_equipment()` 的 `items = [best_by_idx[idx] for idx in sorted(best_by_idx)]` 后调用过滤函数。
- Modify: `tests/test_cw_equipment.py`
  - 新增纯函数单测，锁定丢孤立项规则。
  - 新增 `read_cw_equipment()` 集成测试，复现 `equipment:1..4 + equipment:10` 并验证 10 被过滤。
  - 更新现有 marker 过滤测试，避免单个 `idx=2` 旧预期与新规则冲突。

不要修改 renderer、daemon、README、AGENTS 或 skill 文档；本变更不新增协议字段。不要提交 git commit，除非用户后续明确要求。

---

### Task 1: 纯过滤函数

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_cw_equipment.py` 中，放在 `test_read_cw_equipment_recognizes_best_variants_and_counts` 之前，新增：

```python
def test_filter_isolated_equipment_items_drops_remote_singletons():
    scene = load_equipment_scene_module()

    def item(idx):
        return {"idx": idx, "pos": f"equipment:{idx}"}

    result = scene._filter_isolated_equipment_items([item(1), item(2), item(3), item(4), item(10)])

    assert [entry["idx"] for entry in result] == [1, 2, 3, 4]


def test_filter_isolated_equipment_items_keeps_front_singleton_and_adjacent_blocks():
    scene = load_equipment_scene_module()

    def item(idx):
        return {"idx": idx, "pos": f"equipment:{idx}"}

    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(1)])] == [1]
    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(1), item(2), item(5), item(6)])] == [1, 2, 5, 6]
    assert scene._filter_isolated_equipment_items([item(5)]) == []
    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(7), item(8)])] == [7, 8]
```

- [ ] **Step 2: 验证测试红灯**

Run:

```bash
uv run pytest tests/test_cw_equipment.py::test_filter_isolated_equipment_items_drops_remote_singletons tests/test_cw_equipment.py::test_filter_isolated_equipment_items_keeps_front_singleton_and_adjacent_blocks -q --basetemp .trail/pytest-tmp/equipment-isolated-filter-red1 -p no:cacheprovider
```

Expected: 失败，原因为 `trail.scenes.cw.equipment` 没有 `_filter_isolated_equipment_items`。

- [ ] **Step 3: 实现最小过滤函数**

在 `trail/scenes/cw/equipment.py` 中，放在 `_item_from_result()` 后、`read_cw_equipment()` 前，新增：

```python
def _filter_isolated_equipment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idxs = {int(item["idx"]) for item in items}
    return [
        item
        for item in items
        if int(item["idx"]) == 1
        or int(item["idx"]) - 1 in idxs
        or int(item["idx"]) + 1 in idxs
    ]
```

- [ ] **Step 4: 验证测试绿灯**

Run:

```bash
uv run pytest tests/test_cw_equipment.py::test_filter_isolated_equipment_items_drops_remote_singletons tests/test_cw_equipment.py::test_filter_isolated_equipment_items_keeps_front_singleton_and_adjacent_blocks -q --basetemp .trail/pytest-tmp/equipment-isolated-filter-green1 -p no:cacheprovider
```

Expected: `2 passed`。

---

### Task 2: read_cw_equipment 集成过滤

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: 写失败的集成测试**

在 `tests/test_cw_equipment.py` 中，放在 `test_read_cw_equipment_recognizes_best_variants_and_counts` 后，新增：

```python
def test_read_cw_equipment_filters_isolated_remote_items(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]
    selected_idxs = {1, 2, 3, 4, 10}

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def recognize(self, image):
            idx = image.getpixel((0, 0))[0]
            return recognition.EquipmentRecognitionResult(
                candidates=[recognition.EquipmentCandidate("e1", f"advanced-e{idx}", f"装备{idx}", 0.9)],
                score=0.9,
                gap=0.2,
                uncertain=False,
                empty=False,
            )

    cells = list(grid.iter_equipment_grid_cells(grid.DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=10, rows=6: cells)
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), (cell.idx, 0, 0, 255)), "exact")
            for cell in cells
            if cell.idx in selected_idxs
        ],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 4
    assert result["uncertain"] == 0
    assert result["empty"] == 56
    assert [item["pos"] for item in result["items"]] == ["equipment:1", "equipment:2", "equipment:3", "equipment:4"]
    assert "equipment:10" not in [item["pos"] for item in result["items"]]
```

- [ ] **Step 2: 更新现有 marker 过滤测试**

在 `test_read_cw_equipment_filters_crops_without_slot_markers` 中，把 `crop_equipment_cells` 的 crop 颜色调换，让 `idx=1` 通过 marker、`idx=2` 被过滤：

```python
lambda image, cells: [
    grid.EquipmentCrop(cells[0], Image.new("RGBA", (70, 70), "red"), "exact"),
    grid.EquipmentCrop(cells[1], Image.new("RGBA", (70, 70), "black"), "exact"),
]
```

并把最后断言改为：

```python
assert recognizer.calls == 1
assert result["count"] == 1
assert result["empty"] == 1
assert result["items"][0]["idx"] == 1
```

- [ ] **Step 3: 验证集成测试红灯**

Run:

```bash
uv run pytest tests/test_cw_equipment.py::test_read_cw_equipment_filters_isolated_remote_items tests/test_cw_equipment.py::test_read_cw_equipment_filters_crops_without_slot_markers -q --basetemp .trail/pytest-tmp/equipment-isolated-filter-red2 -p no:cacheprovider
```

Expected: 新增集成测试失败，`equipment:10` 仍出现在结果中；marker 测试通过或继续暴露待调整预期。

- [ ] **Step 4: 在 read_cw_equipment 中调用过滤函数**

在 `trail/scenes/cw/equipment.py` 中，把：

```python
items = [best_by_idx[idx] for idx in sorted(best_by_idx)]
```

改为：

```python
items = _filter_isolated_equipment_items([best_by_idx[idx] for idx in sorted(best_by_idx)])
```

- [ ] **Step 5: 验证集成测试绿灯**

Run:

```bash
uv run pytest tests/test_cw_equipment.py::test_read_cw_equipment_filters_isolated_remote_items tests/test_cw_equipment.py::test_read_cw_equipment_filters_crops_without_slot_markers -q --basetemp .trail/pytest-tmp/equipment-isolated-filter-green2 -p no:cacheprovider
```

Expected: `2 passed`。

---

### Task 3: 聚焦验证

**Files:**
- Verify only; no file changes expected.

- [ ] **Step 1: 运行完整装备测试**

Run:

```bash
uv run pytest tests/test_cw_equipment.py -q --basetemp .trail/pytest-tmp/equipment-isolated-filter-all -p no:cacheprovider
```

Expected: all tests in `tests/test_cw_equipment.py` pass.

- [ ] **Step 2: 运行 whitespace 检查**

Run:

```bash
rtk git diff --check
```

Expected: no whitespace errors. The existing `rtk` hook warning is not a whitespace failure.

- [ ] **Step 3: 检查修改范围**

Run:

```bash
rtk git status --short --branch
```

Expected: branch is `feature/cw-equipment-isolated-filter`; tracked changes are limited to `tests/test_cw_equipment.py` and `trail/scenes/cw/equipment.py`; untracked spec/plan documents are this feature's docs.

---

## 自审清单

- 规格覆盖：Task 1 覆盖纯过滤规则；Task 2 覆盖 `read_cw_equipment()` 后处理、`count/uncertain/empty` 和旧 marker 测试冲突；Task 3 覆盖验证。
- 占位扫描：本文未发现占位文本或未展开的实现步骤。
- 类型一致性：计划用 `list[dict[str, Any]]` 避免新增 `Sequence` import；字段名使用现有 item 的 `idx`、`pos`、`items`、`count`、`uncertain`、`empty`。
- 范围控制：不改 renderer、daemon、README、AGENTS、skills，不提交 commit。
