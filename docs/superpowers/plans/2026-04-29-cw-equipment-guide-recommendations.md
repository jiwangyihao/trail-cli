# CW 装备攻略推荐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `cw.equipment.read` 中追加当前攻略驱动的装备推荐分块，并新增只写 session 的 `cw.equipment.compose` 装备记录命令。

**Architecture:** 保留现有装备背包识别链路，把配方解析、canonical 角色去重、推荐派生、session 写入和 renderer 输出拆成可单测的小边界。`cw.equipment.read` 继续产出背包快照并可选附加 `recommendations`；`cw.equipment.compose` 走带 request journal 和 taint gate 的 session-only mutation，不触发真实 UI 或截图。

**Tech Stack:** Python 3、Typer CLI、pytest、现有 Trail daemon/session/runtime/output renderer 架构。

---

## 执行约束

- 开发阶段必须先使用项目内 worktree；推荐路径由 `using-git-worktrees` skill 决定，但必须位于当前项目目录体系内，且不得修改用户已有无关变更。
- 新启动的子代理必须在 prompt 中包含完整 spec 路径 `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-29-cw-equipment-guide-recommendations-design.md` 和完整 plan 路径 `C:\Users\34404\source\repos\trail-cli\docs\superpowers\plans\2026-04-29-cw-equipment-guide-recommendations.md`；新会话 prompt 长度至少 2000 字。
- 本仓库开发规则覆盖本 skill 示例里的提交步骤：不要自动创建 git commit。每个任务结束只运行 `git status --short` 和相关测试；只有用户明确要求时才提交。
- TDD 顺序固定：先写失败测试，再实现最小代码，再运行目标测试，最后运行本计划末尾的集成验证命令。

## 文件结构

- Modify: `trail/scenes/cw/equipment_resources.py`：新增 explicit recipe 数据结构与 `build_cw_equipment_recipes(raw_config)`，不从扁平 icon catalog 反推配方。
- Modify: `trail/scenes/cw/slots.py`：新增 canonical role slot helper、Agent-visible slot formatter/parser wrapper，并在 `read_cw_slots()` 刷新后只把 `equipments` 迁移到新 canonical 槽位。
- Modify: `trail/scenes/cw/equipment.py`：新增攻略 name extractor、推荐派生、compose session 写入校验；`apply_cw_equipment_read()` 在快照中附加推荐数据。
- Modify: `trail/output/rendering.py`：`cw.equipment.read` 使用 `_append_section()` 追加 `# 装备优先级` 与 `# 角色装备需求`，新增 `cw.equipment.compose` summary renderer 并注册。
- Modify: `trail/daemon/cw_service.py`：导入并注册 compose handler。
- Modify: `trail/daemon/command_service.py`：新增 session-only mutation 路由集合，保证 compose 有 journal、duplicate replay、taint gate、无截图。
- Modify: `trail/commands/cw.py`：更新 equipment help，新增 `trail cw equipment compose` CLI。
- Modify: `AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`：同步协议、标题、命令和 Agent 使用建议；根目录 `README.md` 本次不更新。
- Modify tests: `tests/test_cw_equipment.py`、`tests/test_cw_slots.py`、`tests/test_output_rendering.py`、`tests/test_daemon_protocol.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_atomic_commands.py`、`tests/test_output_debug.py`。

### Task 1: 装备配方 Helper

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment_resources.py`

- [ ] **Step 1: 写 recipe helper 失败测试**

Append these tests to `tests/test_cw_equipment.py` near the existing equipment catalog tests:

```python
def test_build_equipment_recipes_preserves_basic_identity_and_counts():
    resources = load_equipment_resources_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic-a.png",
                            },
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic-a.png",
                            },
                            {
                                "id": "battery",
                                "name": "光能电池",
                                "icon": "https://act-webstatic.mihoyo.com/basic-b.png",
                            },
                        ]
                    }
                ],
            }
        ],
    }

    recipes = resources.build_cw_equipment_recipes(raw_config)

    assert list(recipes) == ["高周波电锯"]
    recipe = recipes["高周波电锯"]
    assert recipe.name == "高周波电锯"
    assert recipe.cache_key == "advanced-same-id"
    assert [(child.name, child.cache_key, child.need) for child in recipe.basics] == [
        ("基础装甲", "basic-same-id", 2),
        ("光能电池", "basic-battery", 1),
    ]


def test_build_equipment_recipes_requires_valid_equipment_list():
    resources = load_equipment_resources_module()

    with pytest.raises(Exception) as exc_info:
        resources.build_cw_equipment_recipes({"rpg_game_big_version": "3.2", "equipment_list": None})

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_CONFIG_INVALID"
```

- [ ] **Step 2: 运行 recipe helper 测试并确认失败**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_recipes_preserves_basic_identity_and_counts tests/test_cw_equipment.py::test_build_equipment_recipes_requires_valid_equipment_list -v`

Expected: FAIL because `build_cw_equipment_recipes` does not exist.

- [ ] **Step 3: 实现配方 dataclass 与 helper**

Add these dataclasses near `EquipmentCatalogEntry` in `trail/scenes/cw/equipment_resources.py`:

```python
@dataclass(frozen=True)
class EquipmentRecipeChild:
    cache_key: str | None
    id: str | None
    name: str
    kind: str
    need: int


@dataclass(frozen=True)
class EquipmentRecipe:
    cache_key: str | None
    id: str | None
    name: str
    basics: tuple[EquipmentRecipeChild, ...]
```

Add this helper below `build_cw_equipment_catalog()`:

```python
def _recipe_child_identity(child: EquipmentRecipeChild) -> tuple[str, str]:
    if child.cache_key:
        return ("cache_key", child.cache_key)
    if child.id:
        return ("kind_id", f"{child.kind}:{child.id}")
    return ("name", child.name)


def _recipe_child_from_mapping(item: Mapping[str, Any], *, big_version: str) -> EquipmentRecipeChild | None:
    name = _text_or_none(item.get("name"))
    if name is None:
        return None
    entry = _entry_from_mapping(item, kind="basic", big_version=big_version)
    equipment_id = _text_or_none(item.get("id"))
    return EquipmentRecipeChild(
        cache_key=None if entry is None else entry.cache_key,
        id=equipment_id,
        name=name,
        kind="basic",
        need=1,
    )


def build_cw_equipment_recipes(raw_config: Mapping[str, Any]) -> dict[str, EquipmentRecipe]:
    big_version = _text_or_none(raw_config.get("rpg_game_big_version"))
    if big_version is None:
        raise TrailError("CW_EQUIPMENT_VERSION_MISSING", "cw equipment config missing rpg_game_big_version")
    equipment_list = raw_config.get("equipment_list")
    if not isinstance(equipment_list, list):
        raise TrailError("CW_EQUIPMENT_CONFIG_INVALID", "cw equipment config missing equipment_list")

    recipes: dict[str, EquipmentRecipe] = {}
    for item in equipment_list:
        if not isinstance(item, Mapping):
            continue
        advanced = _entry_from_mapping(item, kind="advanced", big_version=big_version)
        if advanced is None:
            continue

        children_by_identity: dict[tuple[str, str], EquipmentRecipeChild] = {}
        child_order: list[tuple[str, str]] = []
        compose_list = item.get("compose_list")
        if isinstance(compose_list, list):
            for compose in compose_list:
                if not isinstance(compose, Mapping):
                    continue
                childrens = compose.get("childrens")
                if not isinstance(childrens, list):
                    continue
                for child_item in childrens:
                    if not isinstance(child_item, Mapping):
                        continue
                    child = _recipe_child_from_mapping(child_item, big_version=big_version)
                    if child is None:
                        continue
                    identity = _recipe_child_identity(child)
                    if identity not in children_by_identity:
                        child_order.append(identity)
                        children_by_identity[identity] = child
                    else:
                        previous = children_by_identity[identity]
                        children_by_identity[identity] = EquipmentRecipeChild(
                            cache_key=previous.cache_key,
                            id=previous.id,
                            name=previous.name,
                            kind=previous.kind,
                            need=previous.need + 1,
                        )

        recipes[advanced.name] = EquipmentRecipe(
            cache_key=advanced.cache_key,
            id=advanced.id,
            name=advanced.name,
            basics=tuple(children_by_identity[key] for key in child_order),
        )
    return recipes
```

- [ ] **Step 4: 运行 recipe helper 测试并确认通过**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_recipes_preserves_basic_identity_and_counts tests/test_cw_equipment.py::test_build_equipment_recipes_requires_valid_equipment_list -v`

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run: `git status --short`

Expected: only intended files from this task are modified; do not commit.

### Task 2: Canonical 角色 Helper 与装备迁移

**Files:**
- Modify: `tests/test_cw_slots.py`
- Modify: `trail/scenes/cw/slots.py`

- [ ] **Step 1: 写 canonical helper 和 slots 刷新失败测试**

Append these tests to `tests/test_cw_slots.py` near existing slot state tests:

```python
def test_canonical_cw_role_slots_deduplicates_front_back_hand_order():
    slots = load_cw_slots_module()
    snapshot = {
        "front": [{"name": "希儿"}],
        "back": [{"name": "佩拉"}, {"name": "希儿"}],
        "hand": ["希儿", "银狼", {"name": "银狼"}],
        "stale": False,
    }

    roles = slots.canonical_cw_role_slots(snapshot)

    assert [(role["pos"], role["name"]) for role in roles] == [
        ("front:1", "希儿"),
        ("back:1", "佩拉"),
        ("hand:2", "银狼"),
    ]


def test_read_cw_slots_preserves_equipments_for_new_canonical_only(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {
        "front": [{"name": "希儿", "equipments": ["高周波电锯"]}],
        "back": [None],
        "hand": [{"name": "希儿", "equipments": ["错误副本装备"]}, {"name": "佩拉", "equipments": ["战场手册"]}],
        "stale": False,
    }

    def reader():
        return [None], [{"name": "希儿"}], [{"name": "希儿"}, {"name": "佩拉"}]

    result = slots.read_cw_slots(session, reader=reader, guide_config=None)

    assert result.response_snapshot["back"][0]["equipments"] == ["高周波电锯"]
    assert "equipments" not in result.response_snapshot["hand"][0]
    assert result.response_snapshot["hand"][1]["equipments"] == ["战场手册"]
    assert cw_state["slots"]["back"][0]["equipments"] == ["高周波电锯"]
    assert "equipments" not in cw_state["slots"]["hand"][0]
```

- [ ] **Step 2: 运行 slots 测试并确认失败**

Run: `pytest tests/test_cw_slots.py::test_canonical_cw_role_slots_deduplicates_front_back_hand_order tests/test_cw_slots.py::test_read_cw_slots_preserves_equipments_for_new_canonical_only -v`

Expected: FAIL because `canonical_cw_role_slots` does not exist and equipment migration is not implemented.

- [ ] **Step 3: 添加公开 slot helper**

Add these helpers below `_slot_value_name()` in `trail/scenes/cw/slots.py`:

```python
CW_ROLE_SLOT_AREAS = ("front", "back", "hand")


def format_cw_agent_slot_reference(area: str, index: int) -> str:
    return _format_agent_slot_reference(f"{area}:{index}")


def parse_cw_slot_reference(value: str, *, allowed_areas: set[str] | None = None) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {value}")
    return _parse_slot_reference(value, allowed_areas=allowed_areas)


def canonical_cw_role_slots(slots: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(slots, dict):
        return []
    seen: set[str] = set()
    roles: list[dict[str, Any]] = []
    for area in CW_ROLE_SLOT_AREAS:
        values = slots.get(area)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            name = _slot_value_name(value)
            if not name or name in seen:
                continue
            seen.add(name)
            roles.append(
                {
                    "area": area,
                    "index": index,
                    "pos": format_cw_agent_slot_reference(area, index),
                    "name": name,
                    "value": value,
                }
            )
    return roles
```

- [ ] **Step 4: 添加 equipments 规范化和迁移 helper**

Add these private helpers near `_merge_area_snapshot()` in `trail/scenes/cw/slots.py`:

```python
def _normalized_slot_equipments(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    raw = value.get("equipments")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _role_value_with_equipments(value: Any, equipments: list[str] | None) -> Any:
    if not equipments:
        if isinstance(value, dict) and "equipments" in value:
            cleaned = deepcopy(value)
            cleaned.pop("equipments", None)
            return cleaned
        return value
    if isinstance(value, dict):
        return {**deepcopy(value), "equipments": list(equipments)}
    name = _slot_value_name(value)
    return {"name": name, "equipments": list(equipments)} if name else value


def _preserve_canonical_slot_equipments(previous: dict[str, Any], merged: dict[str, list[Any]]) -> dict[str, list[Any]]:
    previous_by_name = {
        role["name"]: _normalized_slot_equipments(role.get("value"))
        for role in canonical_cw_role_slots(previous)
        if _normalized_slot_equipments(role.get("value"))
    }
    canonical_positions = {(role["area"], role["index"]): role["name"] for role in canonical_cw_role_slots(merged)}
    preserved = {area: list(values) for area, values in merged.items()}
    for area in CW_ROLE_SLOT_AREAS:
        values = preserved.get(area)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            canonical_name = canonical_positions.get((area, index))
            equipments = previous_by_name.get(canonical_name or "") if canonical_name else None
            values[index] = _role_value_with_equipments(value, equipments)
    return preserved


def _merge_area_response_snapshot_with_equipments(
    preserved: list[Any],
    response: list[Any],
    *,
    size: int,
    targets: set[int] | None,
) -> list[Any]:
    output = _merge_area_snapshot(preserved, response, size=size, targets=targets)
    for index, preserved_value in enumerate(preserved[: len(output)]):
        equipments = _normalized_slot_equipments(preserved_value)
        output_value = output[index]
        if equipments:
            base_value = output_value if _slot_value_name(output_value) else preserved_value
            output[index] = _role_value_with_equipments(base_value, equipments)
        elif isinstance(output_value, dict) and "equipments" in output_value:
            output[index] = _role_value_with_equipments(output_value, None)
    return output
```

- [ ] **Step 5: 在 `read_cw_slots()` 计算 response 前调用迁移 helper**

Insert the preservation block immediately after `merged_front/merged_back/merged_hand` are computed and before `fact_front` and `output_front/output_back/output_hand` are computed:

```python
    preserved = _preserve_canonical_slot_equipments(
        previous,
        {"front": merged_front, "back": merged_back, "hand": merged_hand},
    )
    merged_front = preserved["front"]
    merged_back = preserved["back"]
    merged_hand = preserved["hand"]
```

Then replace the three existing `output_*` assignments with these calls so response-only diagnostics can remain while preserved canonical `equipments` are not dropped:

```python
    output_front = _merge_area_response_snapshot_with_equipments(
        merged_front,
        response_front,
        size=len(FRONT_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["front"],
    )
    output_back = _merge_area_response_snapshot_with_equipments(
        merged_back,
        response_back,
        size=len(BACK_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["back"],
    )
    output_hand = _merge_area_response_snapshot_with_equipments(
        merged_hand,
        response_hand,
        size=len(HAND_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["hand"],
    )
```

Do not keep the old `_merge_area_snapshot(merged_front, response_front, targets=None)` output calculation for full reads, because that returns `response_front` and loses the just-preserved `equipments`.

The resulting write block remains:

```python
    cw_state["slots"] = {
        "front": deepcopy(merged_front),
        "back": deepcopy(merged_back),
        "hand": deepcopy(merged_hand),
        "stale": _next_slots_stale(previous, parsed_targets=parsed_targets),
    }
```

- [ ] **Step 6: 运行 slots 测试并确认通过**

Run: `pytest tests/test_cw_slots.py::test_canonical_cw_role_slots_deduplicates_front_back_hand_order tests/test_cw_slots.py::test_read_cw_slots_preserves_equipments_for_new_canonical_only -v`

Expected: PASS.

- [ ] **Step 7: Checkpoint**

Run: `git status --short`

Expected: only intended files from Tasks 1-2 are modified; do not commit.

### Task 3: `cw.equipment.read` 推荐数据派生

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: 写推荐派生失败测试**

Append these helpers and tests to `tests/test_cw_equipment.py`:

```python
def load_equipment_module():
    return importlib.import_module("trail.scenes.cw.equipment")


def _cw_session_with_equipment_guide(tmp_path, *, slots_stale: bool = False):
    from trail.scenes.cw.models import ensure_cw_state
    from trail.session.store import SessionStore

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = ensure_cw_state(session)
    cw_state["guide"] = {
        "lineup_id": "guide-equipment",
        "title": "装备攻略",
        "share_code": "##equipment##",
        "version": "4.0",
        "operation_guide": "测试运营",
        "role_stages": [
            {
                "front_roles": [
                    {"name": "希儿", "first_equipments": [{"name": "高周波电锯"}], "second_equipments": ["战场手册"]}
                ],
                "back_roles": [{"name": "佩拉", "first_equipments": ["战场手册"], "second_equipments": []}],
            }
        ],
        "first_fight_augments": [],
        "second_fight_augments": [],
        "order_basic": [],
        "order_compose": [{"name": "高周波电锯"}, "战场手册", {"name": "未知攻略装备"}],
    }
    cw_state["constraints"] = {"min_coins": 0, "min_level": 0, "mid_level": 0}
    cw_state["slots"] = {
        "front": [{"name": "希儿", "equipments": ["战场手册"]}],
        "back": ["佩拉"],
        "hand": ["希儿", "佩拉"],
        "stale": slots_stale,
    }
    return session


def _equipment_raw_config_for_recommendations():
    return {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "saw",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/saw.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"},
                            {"id": "battery", "name": "光能电池", "icon": "https://act-webstatic.mihoyo.com/battery.png"},
                        ]
                    }
                ],
            },
            {"id": "manual", "name": "战场手册", "icon": "https://act-webstatic.mihoyo.com/manual.png", "compose_list": []},
        ],
    }


def test_build_equipment_recommendations_uses_guide_order_slots_and_basic_counts(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    snapshot = {
        "items": [
            {"name": "基础装甲", "equipment_id": "armor", "cache_key": "basic-armor"},
            {"name": "高周波电锯", "equipment_id": "saw", "cache_key": "advanced-saw"},
        ]
    }

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot=snapshot,
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert recommendations["priority"][0] == {
        "idx": 1,
        "name": "高周波电锯",
        "known": True,
        "basics": [
            {"name": "基础装甲", "have": 1, "need": 1},
            {"name": "光能电池", "have": 0, "need": 1},
        ],
        "required_roles": ["希儿"],
        "acquired_roles": [],
        "missing_roles": ["希儿"],
    }
    assert recommendations["priority"][1]["name"] == "战场手册"
    assert recommendations["priority"][2]["known"] is False
    assert recommendations["role_missing"] == [
        {"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"},
        {"pos": "back:1", "role": "佩拉", "equipment": "战场手册", "category": "优选"},
    ]
    assert recommendations["todos"] == []


def test_build_equipment_recommendations_with_stale_slots_omits_current_role_fields(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path, slots_stale=True)

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot={"items": []},
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    first = recommendations["priority"][0]
    assert first["required_roles"] == ["希儿"]
    assert "acquired_roles" not in first
    assert "missing_roles" not in first
    assert recommendations["role_missing"] == []
    assert recommendations["todos"] == ["slots"]


def test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_id_matches(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "same-id", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/basic.png"}
                        ]
                    }
                ],
            }
        ],
    }
    snapshot = {"items": [{"name": "高周波电锯", "equipment_id": "same-id", "cache_key": "advanced-same-id"}]}

    recommendations = equipment.build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=raw_config)

    assert recommendations["priority"][0]["basics"] == [{"name": "基础装甲", "have": 0, "need": 1}]


def test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_name_matches(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "advanced-armor",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "basic-armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/basic.png"}
                        ]
                    }
                ],
            }
        ],
    }
    snapshot = {"items": [{"name": "基础装甲", "equipment_id": "advanced-armor", "cache_key": "advanced-advanced-armor"}]}

    recommendations = equipment.build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=raw_config)

    assert recommendations["priority"][0]["basics"] == [{"name": "基础装甲", "have": 0, "need": 1}]


def test_build_equipment_recommendations_prefers_first_equipment_category_across_stages(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["guide"]["role_stages"] = [
        {"front_roles": [{"name": "希儿", "first_equipments": [], "second_equipments": ["高周波电锯"]}], "back_roles": []},
        {"front_roles": [{"name": "希儿", "first_equipments": ["高周波电锯"], "second_equipments": []}], "back_roles": []},
    ]
    cw_state["slots"]["front"] = [{"name": "希儿", "equipments": []}]
    cw_state["slots"]["back"] = []
    cw_state["slots"]["hand"] = []

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot={"items": []},
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert recommendations["role_missing"] == [
        {"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}
    ]
```

- [ ] **Step 2: 运行推荐派生测试并确认失败**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_recommendations_uses_guide_order_slots_and_basic_counts tests/test_cw_equipment.py::test_build_equipment_recommendations_with_stale_slots_omits_current_role_fields tests/test_cw_equipment.py::test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_id_matches tests/test_cw_equipment.py::test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_name_matches tests/test_cw_equipment.py::test_build_equipment_recommendations_prefers_first_equipment_category_across_stages -v`

Expected: FAIL because `build_cw_equipment_recommendations` does not exist.

- [ ] **Step 3: 在 `equipment.py` 添加推荐派生 helper**

Update imports in `trail/scenes/cw/equipment.py`:

```python
from collections.abc import Mapping

from trail.scenes.cw.equipment_resources import (
    build_cw_equipment_catalog,
    build_cw_equipment_recipes,
    load_cached_equipment_icons,
    prepare_equipment_icon_cache,
)
from trail.scenes.cw.guide import complete_cw_guide_or_none, fetch_cw_raw_guide_config, require_complete_cw_guide
from trail.scenes.cw.slots import canonical_cw_role_slots
```

Add these helpers above `read_cw_equipment()`:

```python
def _guide_name(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        name = value.get("name")
        if isinstance(name, str):
            text = name.strip()
            return text or None
    return None


def _ordered_names(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        name = _guide_name(value)
        if name is None or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _guide_role_equipment_requirements(guide: dict[str, Any]) -> dict[str, dict[str, str]]:
    by_role: dict[str, dict[str, str]] = {}
    for stage in guide.get("role_stages") if isinstance(guide.get("role_stages"), list) else []:
        if not isinstance(stage, Mapping):
            continue
        for area_key in ("front_roles", "back_roles"):
            roles = stage.get(area_key)
            if not isinstance(roles, list):
                continue
            for role in roles:
                if not isinstance(role, Mapping):
                    continue
                role_name = _guide_name(role)
                if role_name is None:
                    continue
                equipment_by_name = by_role.setdefault(role_name, {})
                for equipment_name in _ordered_names(role.get("first_equipments")):
                    equipment_by_name[equipment_name] = "优选"
                for equipment_name in _ordered_names(role.get("second_equipments")):
                    equipment_by_name.setdefault(equipment_name, "次选")
    return by_role


def _snapshot_equipment_counts(snapshot: dict[str, Any]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for item in snapshot.get("items") if isinstance(snapshot.get("items"), list) else []:
        if not isinstance(item, Mapping):
            continue
        item_kind = None
        cache_key = item.get("cache_key")
        if isinstance(cache_key, str) and cache_key:
            counts[("cache_key", cache_key)] = counts.get(("cache_key", cache_key), 0) + 1
            prefix = cache_key.split("-", 1)[0]
            if prefix in {"advanced", "basic"}:
                item_kind = prefix
        equipment_id = item.get("equipment_id")
        if isinstance(equipment_id, str) and equipment_id and item_kind is not None:
            kind_id = f"{item_kind}:{equipment_id}"
            counts[("kind_id", kind_id)] = counts.get(("kind_id", kind_id), 0) + 1
        name = item.get("name")
        if isinstance(name, str) and name:
            counts[("name", name)] = counts.get(("name", name), 0) + 1
    return counts


def _basic_have_count(counts: dict[tuple[str, str], int], child) -> int:
    if child.cache_key and ("cache_key", child.cache_key) in counts:
        return counts[("cache_key", child.cache_key)]
    if child.id and ("kind_id", f"basic:{child.id}") in counts:
        return counts[("kind_id", f"basic:{child.id}")]
    if child.cache_key or child.id:
        return 0
    return counts.get(("name", child.name), 0)


def _role_equipments(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("equipments")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result
```

Add the public builder:

```python
def build_cw_equipment_recommendations(
    session: SessionModel,
    *,
    snapshot: dict[str, Any],
    raw_config: dict[str, Any],
) -> dict[str, Any] | None:
    cw_state = ensure_cw_state(session)
    guide = complete_cw_guide_or_none(cw_state)
    if guide is None:
        return None

    recipes = build_cw_equipment_recipes(raw_config)
    role_requirements = _guide_role_equipment_requirements(guide)
    required_roles_by_equipment: dict[str, list[str]] = {}
    for role_name, equipments in role_requirements.items():
        for equipment_name in equipments:
            required_roles_by_equipment.setdefault(equipment_name, []).append(role_name)

    slots = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    slots_fresh = isinstance(slots, dict) and slots.get("stale") is False
    canonical_roles = canonical_cw_role_slots(slots if slots_fresh else {})
    canonical_by_name = {role["name"]: role for role in canonical_roles}
    counts = _snapshot_equipment_counts(snapshot)

    priority: list[dict[str, Any]] = []
    for idx, equipment_name in enumerate(_ordered_names(guide.get("order_compose")), start=1):
        recipe = recipes.get(equipment_name)
        item: dict[str, Any] = {
            "idx": idx,
            "name": equipment_name,
            "known": recipe is not None,
            "required_roles": required_roles_by_equipment.get(equipment_name, []),
        }
        if recipe is not None and recipe.basics:
            item["basics"] = [
                {"name": child.name, "have": _basic_have_count(counts, child), "need": child.need}
                for child in recipe.basics
            ]
        if slots_fresh:
            acquired: list[str] = []
            missing: list[str] = []
            for role_name in item["required_roles"]:
                role = canonical_by_name.get(role_name)
                if role is None:
                    continue
                if equipment_name in _role_equipments(role.get("value")):
                    acquired.append(role_name)
                else:
                    missing.append(role_name)
            item["acquired_roles"] = acquired
            item["missing_roles"] = missing
        priority.append(item)

    role_missing: list[dict[str, Any]] = []
    todos: list[str] = []
    if not slots_fresh:
        todos.append("slots")
    else:
        for role in canonical_roles:
            requirements = role_requirements.get(role["name"], {})
            held = set(_role_equipments(role.get("value")))
            for equipment_name, category in requirements.items():
                if equipment_name in held:
                    continue
                role_missing.append(
                    {
                        "pos": role["pos"],
                        "role": role["name"],
                        "equipment": equipment_name,
                        "category": category,
                    }
                )
    return {"priority": priority, "role_missing": role_missing, "todos": todos}
```

- [ ] **Step 4: 在装备读取快照中附加推荐数据**

Update `read_cw_equipment()` to accept an optional raw config so the same config feeds recognition and recommendation:

```python
def read_cw_equipment(
    runtime,
    *,
    workspace_root: str | Path | None = None,
    raw_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(resolved_raw_config)
    prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
    recognizer = VectorEquipmentIconRecognizer(load_cached_equipment_icons(catalog, workspace_root=workspace_root))
    image = _runtime_image(runtime)
    cells = list(iter_equipment_grid_cells(DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))
    best_by_idx: dict[int, dict[str, Any]] = {}

    for crop in crop_equipment_cells(image, cells):
        if not crop_has_equipment_slot_markers(crop.image):
            continue
        item = _item_from_result(crop, recognizer.recognize(crop.image))
        if item is None:
            continue
        previous = best_by_idx.get(crop.cell.idx)
        item_score = item.get("score")
        previous_score = None if previous is None else previous.get("score")
        if previous is None or float(item_score if item_score is not None else -1.0) > float(previous_score if previous_score is not None else -1.0):
            best_by_idx[crop.cell.idx] = item

    items = [best_by_idx[idx] for idx in sorted(best_by_idx)]
    return {
        "count": len(items),
        "uncertain": sum(1 for item in items if item.get("uncertain")),
        "empty": len(cells) - len(items),
        "items": items,
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }
```

Then update `apply_cw_equipment_read()` in `trail/scenes/cw/equipment.py`:

```python
def apply_cw_equipment_read(
    session: SessionModel,
    runtime,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    snapshot = read_cw_equipment(runtime, workspace_root=workspace_root, raw_config=raw_config)
    recommendations = build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=raw_config)
    if recommendations is not None:
        snapshot["recommendations"] = recommendations
    ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
    return snapshot
```

- [ ] **Step 5: 运行推荐派生测试并确认通过**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_recommendations_uses_guide_order_slots_and_basic_counts tests/test_cw_equipment.py::test_build_equipment_recommendations_with_stale_slots_omits_current_role_fields tests/test_cw_equipment.py::test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_id_matches tests/test_cw_equipment.py::test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_name_matches tests/test_cw_equipment.py::test_build_equipment_recommendations_prefers_first_equipment_category_across_stages -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run: `git status --short`

Expected: only intended files from Tasks 1-3 are modified; do not commit.

### Task 4: `cw.equipment.compose` Session 写入

**Files:**
- Modify: `tests/test_cw_equipment.py`
- Modify: `trail/scenes/cw/equipment.py`

- [ ] **Step 1: 写 compose 成功与失败矩阵测试**

Append these tests to `tests/test_cw_equipment.py`:

```python
def test_record_equipment_compose_writes_role_object_and_marks_equipment_stale(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    ensure_cw_state(session)["equipment"] = {"stale": False, "items": [], "recommendations": {}}

    result = equipment.record_cw_equipment_compose(
        session,
        name="高周波电锯",
        slot="front:0",
        role="希儿",
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert result == {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 2}
    role = ensure_cw_state(session)["slots"]["front"][0]
    assert role["equipments"] == ["战场手册", "高周波电锯"]
    assert ensure_cw_state(session)["equipment"]["stale"] is True
    assert "recommendations" not in ensure_cw_state(session)["equipment"]


@pytest.mark.parametrize(
    ("mutate", "kwargs", "code"),
    [
        (lambda cw_state: cw_state["slots"].update(stale=True), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_STALE"),
        (lambda cw_state: cw_state.pop("guide", None), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_GUIDE_STATE_INVALID"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, None), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_EMPTY"),
        (lambda cw_state: None, {"slot": "front:0", "role": "佩拉", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_MISMATCH"),
        (lambda cw_state: None, {"slot": "hand:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_DUPLICATE_SLOT"),
        (lambda cw_state: None, {"slot": "front:0", "role": "希儿", "name": "不存在装备"}, "CW_EQUIPMENT_NAME_INVALID"),
        (lambda cw_state: None, {"slot": "front:0", "role": "希儿", "name": "战场手册"}, "CW_EQUIPMENT_ALREADY_HELD"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, {"name": "希儿", "equipments": ["A", "B", "C"]}), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_EQUIPMENT_FULL"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, {"name": "希儿", "equipments": "bad"}), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID"),
    ],
)
def test_record_equipment_compose_rejects_invalid_state(tmp_path, mutate, kwargs, code):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    cw_state = ensure_cw_state(session)
    mutate(cw_state)

    with pytest.raises(Exception) as exc_info:
        equipment.record_cw_equipment_compose(
            session,
            raw_config=_equipment_raw_config_for_recommendations(),
            **kwargs,
        )

    assert getattr(exc_info.value, "code", None) == code


@pytest.mark.parametrize("slot", ["front:99", "enemy:0", "bad", 123])
def test_record_equipment_compose_rejects_invalid_rpc_slot(tmp_path, slot):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)

    with pytest.raises(Exception) as exc_info:
        equipment.record_cw_equipment_compose(
            session,
            name="高周波电锯",
            slot=slot,
            role="希儿",
            raw_config=_equipment_raw_config_for_recommendations(),
        )

    assert getattr(exc_info.value, "code", None) == "SLOTS_POSITION_INVALID"
```

- [ ] **Step 2: 运行 compose 测试并确认失败**

Run: `pytest tests/test_cw_equipment.py::test_record_equipment_compose_writes_role_object_and_marks_equipment_stale tests/test_cw_equipment.py::test_record_equipment_compose_rejects_invalid_state tests/test_cw_equipment.py::test_record_equipment_compose_rejects_invalid_rpc_slot -v`

Expected: FAIL because `record_cw_equipment_compose` does not exist.

- [ ] **Step 3: 实现 compose 校验与写入 helper**

Update imports in `trail/scenes/cw/equipment.py` to include slot helpers:

```python
from trail.scenes.cw.slots import canonical_cw_role_slots, format_cw_agent_slot_reference, parse_cw_slot_reference
```

Add these helpers below recommendation builder helpers:

```python
def _valid_compose_equipment_names(guide: dict[str, Any], raw_config: dict[str, Any]) -> set[str]:
    names = set(build_cw_equipment_recipes(raw_config))
    names.update(_ordered_names(guide.get("order_compose")))
    for equipments in _guide_role_equipment_requirements(guide).values():
        names.update(equipments)
    return names


def _normalize_existing_role_equipments(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("equipments")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID", "role equipments must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID", "role equipments must contain strings")
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _role_value_for_compose(value: Any, *, role: str, equipments: list[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {**deepcopy(dict(value)), "name": role, "equipments": equipments}
    return {"name": role, "equipments": equipments}


def _mark_equipment_snapshot_stale(cw_state: dict[str, Any]) -> None:
    equipment = cw_state.get("equipment")
    if isinstance(equipment, dict):
        equipment["stale"] = True
        equipment.pop("recommendations", None)
```

Add the public compose function:

```python
def record_cw_equipment_compose(
    session: SessionModel,
    *,
    name: str,
    slot: str,
    role: str,
    raw_config: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    equipment_name = str(name or "").strip()
    role_name = str(role or "").strip()
    if not equipment_name:
        raise TrailError("CW_EQUIPMENT_NAME_INVALID", "equipment name is required")
    if not role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_MISMATCH", "role name is required")

    cw_state = ensure_cw_state(session)
    guide = require_complete_cw_guide(cw_state)
    slots = cw_state.get("slots")
    if not isinstance(slots, dict) or slots.get("stale") is not False:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_STALE", "当前角色槽位已过期，请先执行 cw.slots.read")

    area, index = parse_cw_slot_reference(slot, allowed_areas={"front", "back", "hand"})
    values = slots.get(area)
    if not isinstance(values, list) or index >= len(values):
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_EMPTY", f"目标槽位没有角色: {format_cw_agent_slot_reference(area, index)}")
    value = values[index]
    slot_role_name = _guide_name(value) or (str(value).strip() if value is not None else "")
    if not slot_role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_EMPTY", f"目标槽位没有角色: {format_cw_agent_slot_reference(area, index)}")
    if slot_role_name != role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_MISMATCH", f"槽位角色为 {slot_role_name}，不是 {role_name}")

    canonical = canonical_cw_role_slots(slots)
    canonical_by_name = {item["name"]: item for item in canonical}
    canonical_role = canonical_by_name.get(role_name)
    if canonical_role is None or canonical_role["area"] != area or canonical_role["index"] != index:
        expected = canonical_role.get("pos") if canonical_role else role_name
        raise TrailError("CW_EQUIPMENT_ROLE_DUPLICATE_SLOT", f"请使用 canonical slot: {expected}")

    resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
    if equipment_name not in _valid_compose_equipment_names(guide, resolved_raw_config):
        raise TrailError("CW_EQUIPMENT_NAME_INVALID", f"unknown equipment: {equipment_name}")

    equipments = _normalize_existing_role_equipments(value)
    if equipment_name in equipments:
        raise TrailError("CW_EQUIPMENT_ALREADY_HELD", f"{role_name} already has {equipment_name}")
    if len(equipments) >= 3:
        raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_FULL", f"{role_name} already has 3 equipments")

    equipments.append(equipment_name)
    values[index] = _role_value_for_compose(value, role=role_name, equipments=equipments)
    _mark_equipment_snapshot_stale(cw_state)
    return {
        "pos": format_cw_agent_slot_reference(area, index),
        "name": role_name,
        "equipment": equipment_name,
        "count": len(equipments),
    }
```

- [ ] **Step 4: 运行 compose 测试并确认通过**

Run: `pytest tests/test_cw_equipment.py::test_record_equipment_compose_writes_role_object_and_marks_equipment_stale tests/test_cw_equipment.py::test_record_equipment_compose_rejects_invalid_state tests/test_cw_equipment.py::test_record_equipment_compose_rejects_invalid_rpc_slot -v`

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run: `git status --short`

Expected: only intended files from Tasks 1-4 are modified; do not commit.

### Task 5: 输出 Renderer 与协议顺序

**Files:**
- Modify: `tests/test_output_rendering.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: 写 renderer 失败测试**

Append these tests near existing equipment renderer tests in `tests/test_output_rendering.py`:

```python
def test_render_output_cw_equipment_read_appends_recommendation_sections_before_warn_ref():
    payload = {
        "ok": True,
        "data": {
            "count": 1,
            "uncertain": 1,
            "empty": 59,
            "backend": "vector",
            "layout": "default",
            "items": [{"pos": "equipment:1", "center": {"x": 100, "y": 200}, "name": "基础装甲", "score": 0.9, "uncertain": True, "gap": 0.01, "alt": "光能电池", "alt_score": 0.89}],
            "recommendations": {
                "priority": [
                    {
                        "idx": 1,
                        "name": "高周波电锯",
                        "known": True,
                        "basics": [{"name": "基础装甲", "have": 1, "need": 1}, {"name": "光能电池", "have": 0, "need": 1}],
                        "required_roles": ["希儿"],
                        "acquired_roles": [],
                        "missing_roles": ["希儿"],
                    }
                ],
                "role_missing": [{"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}],
                "todos": [],
            },
        },
        "screenshot": ".trail/shots/req-equipment.png",
        "timing": {},
        "warnings": [],
        "references": [{"path": "trail/references/cw/equipment.png", "similarity": 0.9}],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.equipment.read", payload).splitlines() == [
        "ok cw.equipment.read count=1 uncertain=1 empty=59",
        "shot path=.trail/shots/req-equipment.png",
        "info read_image_first=1",
        "item pos=equipment:1 center=100,200 name=基础装甲 score=0.90 uncertain=1 gap=0.01 alt=光能电池 alt_score=0.89",
        "info backend=vector layout=default",
        "# 装备优先级",
        "guide idx=1 装备=高周波电锯 基础装备=基础装甲:1/1|光能电池:0/1 需求角色=希儿 已获取数=0 未获取数=1 未获取角色=希儿",
        "# 角色装备需求",
        "slot pos=front:1 name=希儿 装备=高周波电锯 分类=优选",
        'warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"',
        "ref path=trail/references/cw/equipment.png sim=0.9",
    ]


def test_render_output_cw_equipment_read_stale_slots_outputs_todo_without_current_role_counts():
    payload = {
        "ok": True,
        "data": {
            "count": 0,
            "uncertain": 0,
            "empty": 60,
            "backend": "vector",
            "layout": "default",
            "items": [],
            "recommendations": {
                "priority": [{"idx": 1, "name": "高周波电锯", "known": True, "basics": [], "required_roles": ["希儿"]}],
                "role_missing": [],
                "todos": ["slots"],
            },
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    rendered = render_output("cw.equipment.read", payload)

    assert "# 装备优先级" in rendered
    assert "guide idx=1 装备=高周波电锯 需求角色=希儿" in rendered
    assert "已获取数" not in rendered
    assert "未获取数" not in rendered
    assert "# 角色装备需求" in rendered
    assert "info todo=slots" in rendered


def test_render_output_cw_equipment_compose_summary():
    payload = {
        "ok": True,
        "data": {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.equipment.compose", payload).splitlines() == [
        "ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=1",
    ]
```

- [ ] **Step 2: 运行 renderer 测试并确认失败**

Run: `pytest tests/test_output_rendering.py::test_render_output_cw_equipment_read_appends_recommendation_sections_before_warn_ref tests/test_output_rendering.py::test_render_output_cw_equipment_read_stale_slots_outputs_todo_without_current_role_counts tests/test_output_rendering.py::test_render_output_cw_equipment_compose_summary -v`

Expected: FAIL because renderer does not output recommendation sections or compose summary.

- [ ] **Step 3: 添加 renderer helper**

Add these helpers above `_render_cw_equipment_read()` in `trail/output/rendering.py`:

```python
def _compact_equipment_basics(value: Any) -> str | None:
    items = []
    for item in _as_list(value):
        entry = _as_dict(item)
        name = _first_string(entry.get("name"))
        if name is None:
            continue
        have = entry.get("have") if "have" in entry else 0
        need = entry.get("need") if "need" in entry else 0
        items.append(f"{name}:{have}/{need}")
    return "|".join(items) if items else None


def _append_cw_equipment_recommendation_lines(lines: list[str], data: dict[str, Any]) -> None:
    recommendations = _as_dict(data.get("recommendations"))
    if not recommendations:
        return
    priority = _as_list(recommendations.get("priority"))
    role_missing = _as_list(recommendations.get("role_missing"))
    todos = [todo for todo in _as_list(recommendations.get("todos")) if isinstance(todo, str) and todo]
    if priority:
        _append_section(lines, "装备优先级")
        for item in priority:
            entry = _as_dict(item)
            facts: list[tuple[str, Any]] = [
                ("idx", entry.get("idx")),
                ("装备", entry.get("name")),
                ("基础装备", _compact_equipment_basics(entry.get("basics"))),
                ("需求角色", _compact_sequence(entry.get("required_roles"))),
            ]
            if "acquired_roles" in entry:
                acquired = _as_list(entry.get("acquired_roles"))
                facts.append(("已获取数", len(acquired)))
                facts.append(("已获取角色", _compact_sequence(acquired)))
            if "missing_roles" in entry:
                missing = _as_list(entry.get("missing_roles"))
                facts.append(("未获取数", len(missing)))
                facts.append(("未获取角色", _compact_sequence(missing)))
            _append_fact_line(lines, "guide", *facts)
    if role_missing or todos:
        _append_section(lines, "角色装备需求")
        for item in role_missing:
            entry = _as_dict(item)
            _append_fact_line(
                lines,
                "slot",
                ("pos", entry.get("pos")),
                ("name", entry.get("role")),
                ("装备", entry.get("equipment")),
                ("分类", entry.get("category")),
            )
        for todo in todos:
            _append_fact_line(lines, "info", ("todo", todo))
```

- [ ] **Step 4: 更新 equipment read 和 compose renderer**

In `_render_cw_equipment_read()`, call `_append_cw_equipment_recommendation_lines(lines, data)` immediately after backend/layout info and before low-confidence `warn`.

Add this renderer near `_render_cw_equipment_prepare()`:

```python
def _render_cw_equipment_compose(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("pos", data.get("pos")),
        ("name", data.get("name")),
        ("装备", data.get("equipment")),
        ("count", data.get("count") if "count" in data else 0),
    )
```

Register it in `TEXT_RENDERERS`:

```python
    "cw.equipment.compose": _render_cw_equipment_compose,
```

- [ ] **Step 5: 运行 renderer 测试并确认通过**

Run: `pytest tests/test_output_rendering.py::test_render_output_cw_equipment_read_appends_recommendation_sections_before_warn_ref tests/test_output_rendering.py::test_render_output_cw_equipment_read_stale_slots_outputs_todo_without_current_role_counts tests/test_output_rendering.py::test_render_output_cw_equipment_compose_summary -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run: `git status --short`

Expected: only intended files from Tasks 1-5 are modified; do not commit.

### Task 6: CLI、Daemon 与 RPC 契约

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/cw_service.py`

- [ ] **Step 1: 写 CLI/RPC 失败测试**

Append these tests to `tests/test_cw_rpc_contracts.py` near equipment or CW command tests:

```python
def test_cw_equipment_compose_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose",
                data={"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=1"]
    _assert_single_call(
        client,
        method="cw.equipment.compose",
        payload={"name": "高周波电锯", "slot": "front:0", "role": "希儿"},
        tmp_path=tmp_path,
    )


def test_cw_equipment_compose_rejects_zero_slot_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:0", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert "fail cw.equipment.compose code=CW_OPTION_INVALID" in result.stdout
    assert client.calls == []


def test_cw_equipment_compose_rejects_yaml_output(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose-yaml",
                data={"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["--format", "yaml", "cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.equipment.compose code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.equipment.compose"',
    ]
```

- [ ] **Step 2: 写 daemon session-only mutation 失败测试**

Append this route-level test near `test_command_service_routes_guide_fetch_select_through_run_mutation()` in `tests/test_daemon_protocol.py`:

```python
def test_command_service_routes_cw_equipment_compose_through_journaled_session_mutation(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    observed: dict[str, object] = {}

    def fake_run_mutation(request, command_name, handler, **kwargs):
        observed["request"] = request
        observed["command_name"] = command_name
        observed["handler"] = handler
        observed["kwargs"] = kwargs
        return {
            "request_id": request.request_id,
            "ok": True,
            "data": {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        }

    monkeypatch.setattr(command_service, "_run_mutation", fake_run_mutation)
    request = DaemonRequest(
        request_id="req-cw-equipment-compose-route",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.equipment.compose",
        payload={"name": "高周波电锯", "slot": "front:0", "role": "希儿"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["screenshot"] is None
    assert observed["request"] is request
    assert observed["command_name"] == "cw.equipment.compose"
    assert callable(observed["handler"])
    assert observed["kwargs"] == {
        "handler_persisted_state": True,
        "response_builder": observed["kwargs"]["response_builder"],
        "enforce_cw_tainted": True,
        "tainted_session_id": session.session_id,
    }
```

- [ ] **Step 3: 写 help 失败测试**

In `tests/test_atomic_commands.py`, extend the existing help assertions with:

```python
def test_cw_equipment_help_mentions_compose_session_record(cli_runner):
    from trail.cli import app

    result = cli_runner.invoke(app, ["cw", "equipment", "--help"])

    assert result.exit_code == 0
    assert "compose" in result.stdout
    assert "只写 session" in result.stdout
    assert "不执行真实 UI 合成" in result.stdout


def test_cw_equipment_compose_help_lists_required_options(cli_runner):
    from trail.cli import app

    result = cli_runner.invoke(app, ["cw", "equipment", "compose", "--help"])

    assert result.exit_code == 0
    assert "--name" in result.stdout
    assert "--slot" in result.stdout
    assert "--role" in result.stdout
    assert "1-based" in result.stdout or "从 1 开始" in result.stdout
```

- [ ] **Step 4: 运行契约测试并确认失败**

Run: `pytest tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_rpc_contract tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_rejects_zero_slot_without_rpc tests/test_cw_rpc_contracts.py::test_cw_equipment_compose_rejects_yaml_output tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_compose_through_journaled_session_mutation tests/test_atomic_commands.py::test_cw_equipment_help_mentions_compose_session_record tests/test_atomic_commands.py::test_cw_equipment_compose_help_lists_required_options -v`

Expected: FAIL because CLI command/help and daemon route are not wired.

- [ ] **Step 5: 添加 CLI 命令**

Update `CW_EQUIPMENT_HELP` in `trail/commands/cw.py`:

```python
CW_EQUIPMENT_HELP = (
    "读取货币战争装备背包图标；prepare 只准备资源缓存，read 会截图并识别当前装备网格。"
    "compose 是只写 session 的装备记录命令，不执行真实 UI 合成，slot 使用从 1 开始的位置。"
)
```

Add this command below `cw_equipment_read()`:

```python
@equipment_app.command(
    "compose",
    help=(
        "记录某个角色已合成/装备指定进阶装备；只写 session，不执行真实 UI 合成。"
        "--slot 使用 front:1/back:1/hand:1 这种 Agent 可见 1-based 位置。"
    ),
)
def cw_equipment_compose(
    session: str = typer.Option(..., "--session"),
    name: str = typer.Option(..., "--name", help="要记录的进阶装备名称。"),
    slot: str = typer.Option(..., "--slot", help="角色所在槽位，格式 front:1/back:1/hand:1，从 1 开始。"),
    role: str = typer.Option(..., "--role", help="槽位中预期的角色名称。"),
) -> None:
    try:
        parsed_slot = _parse_agent_slot_ref(slot)
    except TrailError as error:
        print_output("cw.equipment.compose", _with_auto_capture(None, lambda: (_ for _ in ()).throw(error)))
        return
    _print_cw(
        "cw.equipment.compose",
        session_id=session,
        payload={"name": name, "slot": parsed_slot, "role": role},
    )
```

- [ ] **Step 6: Wire cw_service handler**

Update import in `trail/daemon/cw_service.py`:

```python
from trail.scenes.cw.equipment import apply_cw_equipment_read, prepare_cw_equipment, record_cw_equipment_compose
```

Register handler in `_context()`:

```python
            "cw.equipment.compose": lambda: record_cw_equipment_compose(
                session,
                name=payload["name"],
                slot=payload["slot"],
                role=payload["role"],
                workspace_root=workspace_root,
            ),
```

- [ ] **Step 7: Wire command_service journaled session-only mutation**

Add a set next to `CW_SESSION_SAVE_METHODS` in `trail/daemon/command_service.py`:

```python
CW_SESSION_ONLY_MUTATION_METHODS = {
    "cw.equipment.compose",
}
```

In `handle()`, insert this block after `CW_SESSION_SAVE_METHODS` and before `CW_MUTATING_METHODS`:

```python
            if request.method in CW_SESSION_ONLY_MUTATION_METHODS:
                return self._run_mutation(
                    request,
                    request.method,
                    lambda session_service: self._run_cw(request, service=session_service, payload=payload),
                    handler_persisted_state=True,
                    response_builder=lambda payload: success(payload),
                    enforce_cw_tainted=True,
                    tainted_session_id=session_id,
                )
```

- [ ] **Step 8: 运行契约测试并确认通过**

Run the same command from Step 4.

Expected: PASS.

- [ ] **Step 9: 运行 daemon protocol 目标测试**

Run: `pytest tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_compose_through_journaled_session_mutation -v`

Expected: PASS.

- [ ] **Step 10: Checkpoint**

Run: `git status --short`

Expected: only intended files from Tasks 1-6 are modified; do not commit.

### Task 7: 文档、协议断言与 Skill 同步

**Files:**
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `tests/test_output_debug.py`

- [ ] **Step 1: 写协议文档测试失败断言**

Append this complete test to `tests/test_output_debug.py` after `test_project_agents_declares_renderer_contracts()`:

```python
def test_cw_equipment_recommendation_docs_are_synced() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    prep_skill = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md").read_text(encoding="utf-8")
    command_surface = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "command-surface.md").read_text(encoding="utf-8")

    for text in (agents, prep_skill, command_surface):
        assert "# 装备优先级" in text
        assert "# 角色装备需求" in text
        assert "cw.equipment.compose" in text

    assert "ok cw.equipment.compose pos=" in agents
    assert "trail cw equipment compose" in command_surface
    assert "只写 session" in prep_skill
    assert "info todo=slots" in prep_skill
    assert "装备推荐分块位于 `warn`、`ref` 之前" in prep_skill
```

- [ ] **Step 2: 运行协议文档测试并确认失败**

Run: `pytest tests/test_output_debug.py -k "equipment or headings or AGENTS" -v`

Expected: FAIL because docs do not mention the new command/title yet.

- [ ] **Step 3: 更新 `AGENTS.md` 输出协议**

In `AGENTS.md`, update fixed headings and equipment command rules with these facts:

```markdown
- 固定标题增加 `# 装备优先级`、`# 角色装备需求`；新增标题仍只用于分组，不承载 must-keep 事实。
- `cw.equipment.read` 在背包 `item` 与 `info backend/layout` 后，允许追加 `# 装备优先级` 的 `guide` 行和 `# 角色装备需求` 的 `slot`/`info todo=slots` 行，然后才输出 `warn`、`ref`。
- `cw.equipment.compose` 归入检测/状态摘要 renderer 家族；canonical command 固定为 `cw.equipment.compose`；success 首行固定为 `ok cw.equipment.compose pos=<agent-visible-slot> name=<角色名> 装备=<装备名> count=<n>`，其中 `count` 是该角色写入后已记录装备数量。
- `cw.equipment.compose` 只写 session，不截图，不执行真实 UI 合成，不加入 YAML allowlist；`--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
```

- [ ] **Step 4: 更新 AGENTS 与 prep skill 命令面**

In `skills/trail-cw-prep/SKILL.md`, add this Agent guidance near equipment/preparation guidance:

```markdown
执行 `cw.equipment.read` 后必须先读截图，再消费文本。若输出 `# 装备优先级`，优先看其中 `guide` 行的基础装备 `have/need`，避免为了低优先级装备过早消耗基础装备。若输出 `# 角色装备需求`，按 `slot` 行处理当前 canonical 角色缺口；如果只有 `info todo=slots`，先刷新 `cw.slots.read`。

`cw.equipment.compose` / `trail cw equipment compose` 是只写 session 的记录命令，不执行真实 UI 合成。只有当你已决定合成并装备某个进阶装备时，才用 `--name`、`--slot`、`--role` 记录；slot 使用 front:1/back:1/hand:1 这种从 1 开始的位置。该命令成功首行为 `ok cw.equipment.compose pos=... name=... 装备=... count=...`。

`# 装备优先级` 与 `# 角色装备需求` 均出现在 `warn`、`ref` 之前；低置信装备识别仍以截图为准。
```

In `skills/trail-cw-prep/references/command-surface.md`, add this command surface entry:

```markdown
### cw.equipment.compose

CLI: `trail cw equipment compose --session <id> --name <进阶装备名> --slot <front|back|hand>:<1-based> --role <角色名>`，例如 `--slot front:1`。

用途：只写 session，记录某个 canonical 角色已持有一件进阶装备；不截图，不执行真实 UI 合成，不支持 YAML。成功首行固定为 `ok cw.equipment.compose pos=<slot> name=<角色名> 装备=<装备名> count=<角色装备数>`。

相关读取：`cw.equipment.read` 可输出 `# 装备优先级` 的 `guide` 行与 `# 角色装备需求` 的 `slot` 行或 `info todo=slots`。
```

- [ ] **Step 5: 运行协议文档测试并确认通过**

Run: `pytest tests/test_output_debug.py -k "equipment or headings or AGENTS" -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run: `git status --short`

Expected: docs, skills and intended tests are modified; do not commit.

### Task 8: 集成验证与回归检查

**Files:**
- No new files.
- Verify all files touched by Tasks 1-7.

- [ ] **Step 1: 运行装备与槽位核心测试**

Run: `pytest tests/test_cw_equipment.py tests/test_cw_slots.py -v`

Expected: PASS.

- [ ] **Step 2: 运行输出协议测试**

Run: `pytest tests/test_output_rendering.py tests/test_output_debug.py -v`

Expected: PASS.

- [ ] **Step 3: 运行 CLI/RPC/daemon 契约测试**

Run: `pytest tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_atomic_commands.py -v`

Expected: PASS.

- [ ] **Step 4: 运行 help smoke tests**

Run: `trail cw equipment --help`

Expected: stdout contains `compose`, `prepare`, `read`, `只写 session`, and does not show a traceback.

Run: `trail cw equipment compose --help`

Expected: stdout contains `--name`, `--slot`, `--role`, and either `1-based` or `从 1 开始`.

- [ ] **Step 5: 运行完整目标回归集合**

Run: `pytest tests/test_cw_equipment.py tests/test_cw_slots.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_atomic_commands.py -v`

Expected: PASS.

- [ ] **Step 6: 检查无意变更**

Run: `git status --short`

Expected: only files listed in this plan plus the spec/plan documents are modified; unrelated untracked release-readiness files remain untouched.

Run: `git diff --stat`

Expected: changes are limited to equipment recommendation, compose command, output protocol, tests, docs and skill command surface.

## 自审记录

- Spec coverage: Tasks 1-4 cover recipe helper, name extraction, canonical role rules, recommendations, stale slots, role-mounted equipments and compose validation. Tasks 5-7 cover renderer family, output ordering, YAML non-allowlist, CLI/help, daemon journal/taint route, AGENTS/skill sync. Task 8 covers targeted verification.
- Placeholder scan: this plan contains no incomplete markers; every test/implementation step names exact files, commands, expected outcomes and concrete code shapes.
- Type consistency: planned public helpers are `build_cw_equipment_recipes`, `canonical_cw_role_slots`, `format_cw_agent_slot_reference`, `parse_cw_slot_reference`, `build_cw_equipment_recommendations`, and `record_cw_equipment_compose`; later tasks use these same names.
