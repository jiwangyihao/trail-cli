# CW Equipment Ignore Privilege Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让装备识别阶段排除 `·特权` 装备候选，把特权装备按同图标普通装备识别，恢复 `uncertain` 对不同装备的区分意义。

**Architecture:** 在 `trail.scenes.cw.equipment_recognition` 内新增一个私有 predicate 过滤特权装备，并在构建预计算特征、普通构造器、旧 payload 加载路径三处复用。测试集中放在现有 `tests/test_cw_equipment.py` 的识别器单测区域，覆盖普通构造器和预计算 payload 两条路径。

**Tech Stack:** Python 3.12、Pillow、pytest、现有 `EquipmentCatalogEntry` 和 `VectorEquipmentIconRecognizer`。

---

## 文件结构

- Modify: `trail/scenes/cw/equipment_recognition.py`
  - 新增 `_is_privilege_equipment_entry(entry: EquipmentCatalogEntry) -> bool`。
  - 在 `build_precomputed_equipment_features()`、`VectorEquipmentIconRecognizer.__init__()`、`VectorEquipmentIconRecognizer.from_precomputed_features()` 中过滤特权装备。
- Modify: `tests/test_cw_equipment.py`
  - 在现有识别器测试附近新增两个 TDD 回归测试。

### Task 1: 识别器排除特权装备候选

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment_recognition.py`

- [ ] **Step 1: 写普通构造器失败测试**

在 `tests/test_cw_equipment.py` 的 `test_vector_equipment_recognizer_picks_best_candidate_with_gap` 后新增：

```python
def test_vector_equipment_recognizer_ignores_privilege_equipment_candidates():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    normal_entry = resources.EquipmentCatalogEntry(
        "advanced-engine",
        "engine",
        "永动机",
        "advanced",
        "4",
        "进阶装备",
        "https://act-webstatic.mihoyo.com/engine.png",
        "3.2",
    )
    privilege_entry = resources.EquipmentCatalogEntry(
        "advanced-engine-privilege",
        "engine-privilege",
        "永动机·特权",
        "advanced",
        "7",
        "特权装备",
        "https://act-webstatic.mihoyo.com/engine.png",
        "3.2",
    )
    other_entry = resources.EquipmentCatalogEntry(
        "advanced-wing",
        "wing",
        "流星飞翼",
        "advanced",
        "4",
        "进阶装备",
        "https://act-webstatic.mihoyo.com/wing.png",
        "3.2",
    )
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [
            (normal_entry, Image.new("RGBA", (128, 128), "red")),
            (privilege_entry, Image.new("RGBA", (128, 128), "red")),
            (other_entry, Image.new("RGBA", (128, 128), "blue")),
        ]
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert result.candidates[0].name == "永动机"
    assert all("特权" not in candidate.name for candidate in result.candidates)
    assert result.gap is not None and result.gap > 0.05
    assert result.uncertain is False
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `uv run pytest tests/test_cw_equipment.py::test_vector_equipment_recognizer_ignores_privilege_equipment_candidates -q`

Expected: FAIL，失败原因是 `result.candidates` 仍包含 `永动机·特权` 或 `result.gap == 0.0` / `uncertain is True`。

- [ ] **Step 3: 写预计算 payload 失败测试**

在 `test_vector_recognizer_from_precomputed_features_matches_png_constructor` 后新增：

```python
def test_vector_recognizer_from_precomputed_features_ignores_privilege_equipment_candidates():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    normal_entry = resources.EquipmentCatalogEntry(
        "advanced-engine",
        "engine",
        "永动机",
        "advanced",
        "4",
        "进阶装备",
        "https://act-webstatic.mihoyo.com/engine.png",
        "3.2",
    )
    privilege_entry = resources.EquipmentCatalogEntry(
        "advanced-engine-privilege",
        "engine-privilege",
        "永动机·特权",
        "advanced",
        "7",
        "特权装备",
        "https://act-webstatic.mihoyo.com/engine.png",
        "3.2",
    )
    other_entry = resources.EquipmentCatalogEntry(
        "advanced-wing",
        "wing",
        "流星飞翼",
        "advanced",
        "4",
        "进阶装备",
        "https://act-webstatic.mihoyo.com/wing.png",
        "3.2",
    )
    payload = recognition.build_precomputed_equipment_features(
        [
            (normal_entry, Image.new("RGBA", (128, 128), "red")),
            (privilege_entry, Image.new("RGBA", (128, 128), "red")),
            (other_entry, Image.new("RGBA", (128, 128), "blue")),
        ]
    )

    result = recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload).recognize(
        Image.new("RGBA", (70, 70), "red")
    )

    assert [item["name"] for item in payload["items"]] == ["永动机", "流星飞翼"]
    assert result.candidates[0].name == "永动机"
    assert all("特权" not in candidate.name for candidate in result.candidates)
    assert result.gap is not None and result.gap > 0.05
    assert result.uncertain is False
```

- [ ] **Step 4: 运行测试确认 RED**

Run: `uv run pytest tests/test_cw_equipment.py::test_vector_recognizer_from_precomputed_features_ignores_privilege_equipment_candidates -q`

Expected: FAIL，失败原因是 payload 仍包含 `永动机·特权` 或识别结果仍出现特权候选平局。

- [ ] **Step 5: 实现最小过滤 helper**

在 `trail/scenes/cw/equipment_recognition.py` 中 `class _IndexedIcon` 后新增：

```python
def _is_privilege_equipment_entry(entry: EquipmentCatalogEntry) -> bool:
    return entry.category_name == "特权装备" or entry.name.endswith("·特权")
```

修改 `build_precomputed_equipment_features()` 的循环开头：

```python
    for entry, icon in icons:
        if _is_privilege_equipment_entry(entry):
            continue
        feature = _normalized_rgba(icon, FEATURE_SIZE)
```

修改 `VectorEquipmentIconRecognizer.__init__()` 的 `_icons` 构建为：

```python
        self._icons = [
            _IndexedIcon(
                entry=entry,
                feature=_normalized_rgba(icon, FEATURE_SIZE),
                match_image=_normalized_rgba(icon, MATCH_SIZE),
                feature_mask=_alpha_mask(icon, FEATURE_SIZE),
                match_mask=_alpha_mask(icon, MATCH_SIZE),
            )
            for entry, icon in icons
            if not _is_privilege_equipment_entry(entry)
        ]
```

在 `from_precomputed_features()` 构造 `entry` 后、append 前新增：

```python
            if _is_privilege_equipment_entry(entry):
                continue
```

- [ ] **Step 6: 运行新增测试确认 GREEN**

Run: `uv run pytest tests/test_cw_equipment.py::test_vector_equipment_recognizer_ignores_privilege_equipment_candidates tests/test_cw_equipment.py::test_vector_recognizer_from_precomputed_features_ignores_privilege_equipment_candidates -q`

Expected: 两个测试 PASS。

- [ ] **Step 7: 运行装备测试回归**

Run: `uv run pytest tests/test_cw_equipment.py -q`

Expected: 全部 PASS。

- [ ] **Step 8: 真实截图离线验证**

Run:

```powershell
@'
from pathlib import Path
from PIL import Image
from trail.scenes.cw.equipment import read_cw_equipment
from trail.daemon.cw_resource_service import CwResourceService
root=Path.cwd(); shot=root/'.trail/shots/c95af143534143619201454a747d4cd8-cc19a77519.jpg'
class Runtime:
    def capture_image(self, **kwargs):
        return Image.open(shot).convert('RGBA')
svc=CwResourceService(); raw, recognizer = svc.equipment_read_resources(workspace_root=root)
result=read_cw_equipment(Runtime(), workspace_root=root, raw_config=raw, recognizer=recognizer)
print(result['count'], result['empty'], result['uncertain'])
for item in result['items']:
    print(item['idx'], item['name'], item['score'], item['uncertain'], item.get('gap'), item.get('alt'))
'@ | uv run python -
```

Expected: `count=8`，4 个进阶装备的 `uncertain=False`，且候选替代项不再是对应 `·特权` 名称。

- [ ] **Step 9: 运行快速全量回归**

Run: `uv run pytest -n 8`

Expected: PASS，无失败。

- [ ] **Step 10: 提交实现**

只提交实现相关文件，保留并避开用户/其他 agent 的无关改动：

```bash
git add tests/test_cw_equipment.py trail/scenes/cw/equipment_recognition.py
git commit -m "fix(cw): 识别装备时忽略特权版本"
```
