# `cw.hand.sell_plan` 参考化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 canonical command `cw.hand.sell_plan` 从不可解释的非空手牌候选改成基于攻略阶段、槽位星级、阶段与人口信息的参考命令，并移除 session guide 中不可靠的派生字段。

**Architecture:** session guide 只保存攻略原始/规范化字段，`sell-plan` 每次从 `role_stages`、fresh `slots`、`shop.team_size`、`stage` 即时推导参考 item、推荐度和缺失信息 token。输出继续使用现有 renderer 家族与 `slot` / `info` 前缀，固定 `reference_only=1`，不新增 YAML allowlist，不生成权威可执行候选。

**Tech Stack:** Python 3.12, Typer, Trail daemon/session state, pytest, Markdown docs

**Spec:** `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-26-cw-sell-plan-reference-design.md`

**Git Note:** 当前会话未经用户明确要求，不创建 commit；计划中的验证步骤只检查工作区修改。

---

### Task 1: 移除 Guide Session 派生字段

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_guide_rpc_contracts.py`

- [ ] **Step 1: 写 guide state 失败测试**

在 `tests/test_cw_guide.py` 中更新 `test_apply_guide_populates_cw_scene_state`，让输入包含非空 `role_stages`，并断言 session guide 不再含派生字段：

```python
def test_apply_guide_populates_cw_scene_state(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    role_stages = [
        {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
        {"stage": "Final", "front_roles": [{"name": "希儿", "star": 3}], "back_roles": [{"name": "佩拉", "star": 2}]},
    ]
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = apply_cw_guide(session, guide_data={**fake_guide(), "role_stages": role_stages})

    cw_state = refreshed.scene_state["cw"]
    assert "guide" in cw_state
    assert "constraints" in cw_state
    assert "slots" in cw_state
    assert cw_state["guide"]["artifact"] == "guide-demo"
    assert cw_state["guide"]["share_code"] == "##demo##"
    assert "on_field" not in cw_state["guide"]
    assert "off_field" not in cw_state["guide"]
    assert "remaining_purchases" not in cw_state["guide"]
    assert cw_state["guide"]["role_stages"] == role_stages
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["constraints"]["min_level"] == 7
    assert cw_state["constraints"]["mid_level"] == 9
    assert cw_state["slots"]["stale"] is True
```

更新 `test_apply_guide_preserves_source_metadata_for_later_scene_steps` 的完整 guide 期望，删除 `on_field`、`off_field`、`remaining_purchases` 三个键，并保留 `role_stages`。

- [ ] **Step 2: 更新测试夹具失败预期**

在 `tests/conftest.py` 中准备后续修改：`build_fake_cw_session()` 默认 guide 不再通过 `purchases` 写 `remaining_purchases`。先更新依赖它的测试预期，明确后续需要显式设置 guide：

```python
def build_fake_cw_session(tmp_path, purchases: dict | None = None):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    session.scene_state["cw"] = {
        "guide": {"role_stages": []},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": True, "hand": []},
        "shop": {"stale": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    return session
```

先只写/调整测试，不改 fixture 生产代码。

- [ ] **Step 3: 运行 guide 测试确认失败**

Run:

```bash
uv run pytest tests/test_cw_guide.py tests/test_guide_rpc_contracts.py -q --basetemp .pytest-tmp
```

Expected: FAIL，失败原因是 session guide 仍含 `on_field` / `off_field` / `remaining_purchases`，以及测试夹具仍构造旧 guide shape。

- [ ] **Step 4: 修改 `apply_cw_guide()` 最小实现**

在 `trail/scenes/cw/guide.py` 的 `apply_cw_guide()` 中保留 `role_stages`，删除写入 session guide 的 `on_field`、`off_field`、`remaining_purchases`：

```python
cw_state["guide"] = {
    "artifact": guide_payload.get("artifact_id"),
    "lineup_id": guide_payload.get("lineup_id"),
    "share_code": guide_payload["share_code"],
    "source_url": guide_payload.get("source_url"),
    "title": guide_payload.get("title"),
    "author": guide_payload.get("author"),
    "uploader": guide_payload.get("uploader"),
    "labels": guide_payload.get("labels", []),
    "support_hard": bool(guide_payload.get("support_hard")),
    "has_change_equip": bool(guide_payload.get("has_change_equip")),
    "has_expert": bool(guide_payload.get("has_expert")),
    "version": guide_payload.get("version"),
    "role_stages": guide_payload.get("role_stages", []),
    "first_fight_augments": guide_payload.get("first_fight_augments", []),
    "second_fight_augments": guide_payload.get("second_fight_augments", []),
    "portals": guide_payload.get("portals", []),
    "order_basic": guide_payload.get("order_basic", []),
    "order_compose": guide_payload.get("order_compose", []),
}
```

保留 `normalize_cw_guide_payload()` 和 `fetch_cw_guide_payload()` 中的 `on_field` / `off_field`，因为 spec 明确只移除 session guide 派生字段，不在本轮破坏 `guide.fetch.cw` 结构化兼容输出。

- [ ] **Step 5: 修改 `tests/conftest.py` fixture**

把 `build_fake_cw_session()` 改为默认写 `"guide": {"role_stages": []}`。保留 `purchases` 参数会误导后续使用，删除参数或忽略参数都会影响调用点；本轮最小改动是保留参数但不写入 guide，并逐步更新调用点测试。

- [ ] **Step 6: 验证 guide 测试通过**

Run:

```bash
uv run pytest tests/test_cw_guide.py tests/test_guide_rpc_contracts.py -q --basetemp .pytest-tmp
```

Expected: PASS。

---

### Task 2: 清理 Shop 对 `remaining_purchases` 的依赖

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Test: `tests/test_cw_shop.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写 shop 不再修改 guide purchase state 的失败测试**

在 `tests/test_cw_shop.py` 中把 `test_shop_buy_slot_mutates_remaining_purchases` 改为：

```python
def test_shop_buy_slot_does_not_mutate_guide_purchase_state(tmp_path):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": []}
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"] == {"role_stages": []}
    assert refreshed.scene_state["cw"]["shop"]["guide_summary"] == {
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}
    }
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
```

把同文件中失败路径对 `remaining_purchases` 的断言改成 guide 未变化：

```python
before_guide = deepcopy(session.scene_state["cw"]["guide"])
...
assert session.scene_state["cw"]["guide"] == before_guide
```

- [ ] **Step 2: 更新渲染和 RPC 测试预期**

在 `tests/test_output_rendering.py` 中搜索 `remaining_purchases`，把 shop summary 相关 payload 改为：

```python
"guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}}
```

在 `tests/test_cw_rpc_contracts.py` 中同步删除 `guide_summary.remaining_purchases` 断言。

在 `tests/test_daemon_protocol.py` 中搜索 `remaining_purchases`，更新 guide select/current/shop status 相关持久化断言，确保 session guide 不再恢复或维护该字段。

- [ ] **Step 3: 运行 shop 相关测试确认失败**

Run:

```bash
uv run pytest tests/test_cw_shop.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp
```

Expected: FAIL，失败原因是 `_decrement_remaining_purchase()` 仍写回 guide，shop summary 仍暴露 `remaining_purchases`。

- [ ] **Step 4: 修改 `trail/scenes/cw/shop.py`**

删除 `_remaining_purchases()` 的业务使用，保留或删除函数均可；推荐删除 `_decrement_remaining_purchase()`，并把 `_guide_summary()` 改为只输出 constraints：

```python
def _guide_summary(cw_state: dict) -> dict[str, Any] | None:
    if _guide_state(cw_state) is None:
        return None
    return {"constraints": _stable_constraints_summary(cw_state)}
```

在 `_build_shop_snapshot()` 中用 `_guide_summary()` 条件写入，避免无 guide 时无条件输出 guide_summary：

```python
snapshot = {
    **_preserved_shop_flags(cw_state),
    "items": deepcopy(items),
    "coins": coins,
    "level": level,
    "exp": exp,
    "reserve_full": reserve_full,
    "team_size": preserved_team_size,
    "stale": False,
}
guide_summary = _guide_summary(cw_state)
if guide_summary is not None:
    snapshot["guide_summary"] = guide_summary
return snapshot
```

在 `buy_cw_shop_slot()` 中删除：

```python
_decrement_remaining_purchase(cw_state, expect=expect)
```

并把 `updated_shop["guide_summary"]` 改成条件写入：

```python
guide_summary = _guide_summary(cw_state)
if guide_summary is not None:
    updated_shop["guide_summary"] = guide_summary
```

- [ ] **Step 5: 验证 shop 相关测试通过**

Run:

```bash
uv run pytest tests/test_cw_shop.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp
```

Expected: PASS。

---

### Task 3: 更新 Slots Guide 角色名候选来源

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Test: `tests/test_cw_slots.py`

- [ ] **Step 1: 写 `role_stages` OCR 候选失败测试**

在 `tests/test_cw_slots.py` 中找到现有基于 `on_field/off_field` 的候选测试，把 guide 改成 `role_stages`，并加入装备名，确保装备名不会混入角色候选：

```python
def test_slots_read_uses_role_stages_as_authoritative_name_candidates(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {
                "stage": "Final",
                "front_roles": [{"name": "布洛妮娅", "star": 3, "first_equipments": [{"name": "不要作为候选"}]}],
                "back_roles": [{"name": "椒丘", "star": 2}],
            }
        ]
    }

    authoritative, _ = slots_module._session_slot_name_candidates(session.scene_state["cw"])
    assert "布洛妮娅" in authoritative
    assert "椒丘" in authoritative
    assert "不要作为候选" not in authoritative

    refreshed = read_cw_slots(
        session,
        reader=lambda: (["布罗妮娅"], ["椒丘"], [None] * 9),
        guide_config=None,
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布洛妮娅"
    assert refreshed.scene_state["cw"]["slots"]["back"][0] == "椒丘"
```

- [ ] **Step 2: 运行测试确认失败或退化**

Run:

```bash
uv run pytest tests/test_cw_slots.py::test_slots_read_uses_role_stages_as_authoritative_name_candidates -q --basetemp .pytest-tmp
```

Expected: FAIL 或证明现有递归候选仍混入非角色 `name`，需要收紧来源。

- [ ] **Step 3: 实现显式 role_stages 候选 helper**

在 `trail/scenes/cw/slots.py` 中新增：

```python
def _collect_role_stage_name_candidates(guide: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    role_stages = guide.get("role_stages")
    if not isinstance(role_stages, list):
        return candidates
    for stage in role_stages:
        if not isinstance(stage, dict):
            continue
        for key in ("front_roles", "back_roles"):
            roles = stage.get(key)
            if not isinstance(roles, list):
                continue
            for role in roles:
                if not isinstance(role, dict):
                    continue
                text = str(role.get("name") or "").strip()
                if text:
                    candidates.append(text)
    return candidates
```

修改 `_session_slot_name_candidates()`：先加入 `_collect_role_stage_name_candidates(guide)`，不要再对整个 guide 做 `_collect_nested_slot_name_candidates()`，避免装备名等字段混入权威角色候选。portal 仍可递归收集，因为 portal 卡片名字用于环境/攻略提示，不是角色 OCR 权威来源；如果测试发现 portal 混入角色纠错，也只保留 role_stages。

- [ ] **Step 4: 验证 slots 读名测试通过**

Run:

```bash
uv run pytest tests/test_cw_slots.py -q --basetemp .pytest-tmp
```

Expected: PASS。

---

### Task 4: 实现 Sell Plan 参考模型

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Test: `tests/test_cw_slots.py`

- [ ] **Step 1: 写分类排序与 3-x BOSS 前失败测试**

在 `tests/test_cw_slots.py` 中替换当前 `test_sell_plan_returns_candidates_and_refreshes_snapshot`：

```python
def test_sell_plan_returns_reference_items_ordered_by_priority(tmp_path):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [
            {"name": "阮·梅", "star": 1},
            {"name": "黑塔", "star": 1},
            {"name": "停云", "star": 2},
            {"name": "银狼", "star": 3},
        ],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "5/5", "stale": False}

    result = plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert [item["name"] for item in result["items"]] == ["阮·梅", "黑塔", "停云", "银狼"]
    assert [item["category"] for item in result["items"]] == ["非攻略", "前期", "中期", "后期超买"]
    assert [item["recommendation"] for item in result["items"]] == ["推荐", "推荐", "推荐", "可以"]
    assert result["todos"] == []
    assert session.scene_state["cw"]["sell_plan"] == result
```

- [ ] **Step 2: 写阶段推荐表参数化失败测试**

新增参数化测试覆盖 1-x、2-x、2-x BOSS 前、3-x、3-x BOSS 前：

```python
@pytest.mark.parametrize(
    ("stage_value", "boss_preview", "expected"),
    [
        ("1-2", False, {"非攻略": "可以", "前期": "不推荐", "中期": "不推荐", "后期超买": "不推荐"}),
        ("2-1", False, {"非攻略": "推荐", "前期": "可以", "中期": "不推荐", "后期超买": "不推荐"}),
        ("2-5", True, {"非攻略": "推荐", "前期": "推荐", "中期": "不推荐", "后期超买": "不推荐"}),
        ("3-1", False, {"非攻略": "推荐", "前期": "推荐", "中期": "可以", "后期超买": "不推荐"}),
        ("3-5", True, {"非攻略": "推荐", "前期": "推荐", "中期": "推荐", "后期超买": "可以"}),
    ],
)
def test_sell_plan_recommendation_table_by_stage(tmp_path, stage_value, boss_preview, expected):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Mid", "front_roles": [{"name": "停云", "star": 2}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "银狼", "star": 3}],
        "back": [],
        "hand": [
            {"name": "阮·梅", "star": 1},
            {"name": "黑塔", "star": 1},
            {"name": "停云", "star": 2},
            {"name": "银狼", "star": 3},
        ],
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {"value": stage_value, "boss_preview": boss_preview, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "5/5", "stale": False}

    plan = load_cw_slots_module().plan_cw_hand_sell(session)

    by_category = {item["category"]: item["recommendation"] for item in plan["items"]}
    for category, recommendation in expected.items():
        assert by_category[category] == recommendation
```

- [ ] **Step 3: 写 Final 保护与星级降级失败测试**

新增三条测试：

```python
def test_sell_plan_protects_final_role_when_missing_from_field(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "银狼", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    item = load_cw_slots_module().plan_cw_hand_sell(session)["items"][0]

    assert item["category"] == "后期"
    assert item["protected"] is True
    assert item["recommendation"] == "不推荐"
    assert item["target_star"] == 3
    assert item["current_star"] is None


def test_sell_plan_protects_final_role_when_field_star_below_target(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼", "star": 2}], "back": [], "hand": [{"name": "银狼", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    item = load_cw_slots_module().plan_cw_hand_sell(session)["items"][0]

    assert item["category"] == "后期"
    assert item["protected"] is True
    assert item["recommendation"] == "不推荐"
    assert item["current_star"] == 2


def test_sell_plan_missing_star_marks_todo_and_protects_final_role(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼"}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼"}], "back": [], "hand": [{"name": "银狼"}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "2/2", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "star" in result["todos"]
    assert result["items"][0]["protected"] is True
    assert result["items"][0]["recommendation"] == "不推荐"
```

- [ ] **Step 4: 写缺信息只给参考失败测试**

```python
def test_sell_plan_missing_stage_or_team_size_marks_todos_and_avoids_authoritative_candidates(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"stale": True}
    session.scene_state["cw"]["shop"] = {"stale": True}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert result["reference_only"] is True
    assert result["candidates"] == []
    assert "stage" in result["todos"]
    assert "team_size" in result["todos"]
```

- [ ] **Step 5: 写人口不足保护失败测试**

```python
def test_sell_plan_marks_all_items_not_recommended_when_under_team_size(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {"role_stages": [{"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []}]}
    session.scene_state["cw"]["slots"] = {"front": [{"name": "银狼", "star": 3}], "back": [], "hand": [{"name": "阮·梅", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": "3-5", "boss_preview": True, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "3/3", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert all(item["recommendation"] == "不推荐" for item in result["items"])
```

- [ ] **Step 6: 写 missing Final / stage_granularity / stage value / boss_preview 失败测试**

新增测试断言没有显式 Final 时 `missing_final` 出现在 `todos`；只有 `Final` 或阶段数量不足以区分前/中/后时 `stage_granularity` 出现在 `todos`；当前 stage 是 `2-5` 但缺 `boss_preview` 字段时输出 `boss_preview`，并按非 BOSS 前规则处理。

新增不可解析 stage value 测试：

```python
@pytest.mark.parametrize("stage_value", ["shop", "battle", "boss_preview"])
def test_sell_plan_unparseable_stage_value_marks_stage_todo(tmp_path, stage_value):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "role_stages": [
            {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
            {"stage": "Final", "front_roles": [{"name": "银狼", "star": 3}], "back_roles": []},
        ]
    }
    session.scene_state["cw"]["slots"] = {"front": [], "back": [], "hand": [{"name": "黑塔", "star": 1}], "stale": False}
    session.scene_state["cw"]["stage"] = {"value": stage_value, "stale": False}
    session.scene_state["cw"]["shop"] = {"team_size": "1/1", "stale": False}

    result = load_cw_slots_module().plan_cw_hand_sell(session)

    assert "stage" in result["todos"]
```

- [ ] **Step 7: 运行 sell-plan scene 测试确认失败**

Run:

```bash
uv run pytest tests/test_cw_slots.py -q --basetemp .pytest-tmp
```

Expected: FAIL，失败原因是 `plan_cw_hand_sell()` 仍只返回旧 `candidates` 或当前简化修复 shape。

- [ ] **Step 8: 实现 `plan_cw_hand_sell()` helpers**

在 `trail/scenes/cw/slots.py` 中新增这些小 helper，保持在同文件内，避免本轮新增模块：

```python
def _slot_star(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    star = value.get("star")
    if isinstance(star, bool):
        return None
    if isinstance(star, int):
        return star
    return int(star) if isinstance(star, str) and star.isdigit() else None


def _iter_stage_roles(stage: dict[str, Any]) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for key in ("front_roles", "back_roles"):
        values = stage.get(key)
        if isinstance(values, list):
            roles.extend([dict(item) for item in values if isinstance(item, dict) and item.get("name")])
    return roles


def _parse_team_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        left, sep, right = value.partition("/")
        if sep and right.isdigit():
            return int(right)
        if value.isdigit():
            return int(value)
    return None
```

实现阶段解析 helper：只有 `stage.value` 能解析为 `1-*`、`2-*`、`3-*` 时套推荐表；`stage.value == "boss_preview"`、`shop`、`battle` 或其他不可解析值输出 `stage` todo；缺少 `boss_preview` 布尔时输出 `boss_preview` todo，并按非 BOSS 保守规则。阶段归类 helper 同时负责追加 `missing_final` 和 `stage_granularity`。

- [ ] **Step 9: 实现新 `sell_plan` shape**

让 `plan_cw_hand_sell()` 输出并持久化：

```python
{
    "reference_only": True,
    "candidates": [],
    "items": items,
    "todos": todos,
}
```

每个 item 至少包含：`slot`、`name`、`star`、`target_star`、`current_star`、`category`、`recommendation`、`priority`、`protected`、`reason`。

- [ ] **Step 10: 更新 command service journal sell_plan case**

在 `tests/test_cw_slots.py` 后部 command service journal 参数化 case 中，把 `cw.hand.sell_plan` stub 从 `{"candidates": [0, 2]}` 改为新 shape，断言持久化 `reference_only=True` 与 `items`。

- [ ] **Step 11: 验证 slots 测试通过**

Run:

```bash
uv run pytest tests/test_cw_slots.py -q --basetemp .pytest-tmp
```

Expected: PASS。

---

### Task 5: 更新 Renderer 与 CLI 契约

**Files:**
- Modify: `trail/output/rendering.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 写 renderer 失败测试**

更新 `tests/test_output_rendering.py::test_render_output_renders_cw_hand_sell_plan_text`：

```python
def test_render_output_renders_cw_hand_sell_plan_text():
    payload = {
        "ok": True,
        "data": {
            "reference_only": True,
            "candidates": [],
            "todos": ["stage"],
            "items": [
                {
                    "slot": 0,
                    "name": "阮·梅",
                    "star": 1,
                    "target_star": None,
                    "current_star": None,
                    "category": "非攻略",
                    "recommendation": "不推荐",
                    "priority": 10,
                    "protected": False,
                    "reason": "缺少当前阶段，仅提供参考",
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell_plan", payload).splitlines() == [
        "ok cw.hand.sell_plan count=1 reference_only=1 candidates=0 todos=1",
        "slot pos=hand:0 name=阮·梅 star=1 分类=非攻略 推荐度=不推荐 priority=10 protected=0 reason=缺少当前阶段，仅提供参考",
        "info todo=stage",
    ]
```

`target_star` / `current_star` 为 `None` 时默认省略，避免输出误导性空值。

- [ ] **Step 2: 写 YAML 不支持回归测试**

新增或更新 `cw.hand.sell_plan` 的 YAML 拒绝测试，期望：

```python
assert render_output("cw.hand.sell_plan", payload, output_format="yaml").splitlines() == [
    "fail cw.hand.sell_plan code=OUTPUT_FORMAT_NOT_SUPPORTED",
    'why msg="yaml not supported for cw.hand.sell_plan"',
]
```

- [ ] **Step 3: 运行 renderer 测试确认失败**

Run:

```bash
uv run pytest tests/test_output_rendering.py::test_render_output_renders_cw_hand_sell_plan_text -q --basetemp .pytest-tmp
```

Expected: FAIL，失败原因是 renderer 还按旧 `candidates` 输出。

- [ ] **Step 4: 修改 `_render_cw_sell_plan()`**

在 `trail/output/rendering.py` 中让 renderer 优先读取 `data.items`，首行固定输出 `reference_only=1`：

```python
def _render_cw_sell_plan(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("items"))
    candidates = _as_list(data.get("candidates"))
    todos = _as_list(data.get("todos"))
    lines = [
        f"ok {command} "
        + _format_fact_sequence(
            ("count", len(items)),
            ("reference_only", True),
            ("candidates", len(candidates)),
            ("todos", len(todos)),
        )
    ]
    _append_success_capture_block(lines, payload)
    for item in items:
        entry = _as_dict(item)
        slot = _coerce_int(entry.get("slot"))
        if slot is None:
            continue
        lines.append(
            "slot "
            + _format_fact_sequence(
                ("pos", f"hand:{slot}"),
                ("name", entry.get("name")),
                ("star", entry.get("star")),
                ("target_star", entry.get("target_star")),
                ("current_star", entry.get("current_star")),
                ("分类", entry.get("category")),
                ("推荐度", entry.get("recommendation")),
                ("priority", entry.get("priority")),
                ("protected", bool(entry.get("protected"))),
                ("reason", entry.get("reason")),
            )
        )
    for todo in todos:
        lines.append("info " + _format_fact_sequence(("todo", todo)))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

- [ ] **Step 5: 更新 RPC stdout 契约**

在 `tests/test_cw_rpc_contracts.py` 的 `cw_hand_sell_plan` case 中，把 response data 改成新 shape，并把 expected stdout 改为 renderer 测试同款输出。

- [ ] **Step 6: 验证 renderer 和 RPC 契约通过**

Run:

```bash
uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q --basetemp .pytest-tmp
```

Expected: PASS。

---

### Task 6: 文档、协议与总验证

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/cw-stage-reference/README.md`
- Inspect: `skills/**/*.md`
- Test: related pytest suites

- [ ] **Step 1: 更新 `AGENTS.md` 输出协议冻结说明**

在 `AGENTS.md` 的 `guide / cw 攻略摘要约束` 附近补充：

```markdown
- `cw.hand.sell_plan` success 首行固定为 `ok cw.hand.sell_plan count=... reference_only=1 candidates=... todos=...`；该命令只提供 Agent 参考信息，不是权威出售计划。
- `cw.hand.sell_plan` 正文 `slot` 行固定使用 `pos/name/star/target_star/current_star/分类/推荐度/priority/protected/reason`，缺失值按默认 key=value 省略规则处理。
- `cw.hand.sell_plan` 缺失参考信息用 `info todo=stage|team_size|boss_preview|missing_final|stage_granularity|star`；不新增正文前缀，不进入 YAML allowlist。
```

- [ ] **Step 2: 更新 README 说明**

把 README 中 `sell-plan` 描述改成：

```markdown
- 如需卖牌，先看参考：`trail cw hand sell-plan --session <id>`；它会基于当前攻略 `role_stages`、槽位星级、阶段与人口信息输出 `slot ... 分类=... 推荐度=...`，但不是权威售出计划；真正出售时仍需显式执行 `trail cw hand sell --session <id> --slot <n>`
```

- [ ] **Step 3: 更新阶段参考文档**

把 `docs/cw-stage-reference/README.md` 中满员提示页的建议改成先执行 `sell-plan` 看参考，再由 Agent 决定是否执行 `cw.hand.sell`。

- [ ] **Step 4: 检查 active skills**

Run:

```bash
rg "sell-plan|卖牌|cw hand sell|cw\.hand\.sell" skills
```

如果命中 active skill，更新为“sell-plan 只提供参考，出售仍需显式 `cw.hand.sell`”。如果没有命中，在最终报告中写明“已检查 active skills，无需更新”。

- [ ] **Step 5: 运行相关测试套件**

Run:

```bash
uv run pytest tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_cw_shop.py tests/test_cw_slots.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp
```

Expected: PASS。

- [ ] **Step 6: 检查 diff 空白错误**

Run:

```bash
rtk git diff --check
```

Expected: no output except possible `rtk` hook notice.

- [ ] **Step 7: 汇总结果，不提交**

报告修改文件、关键行为变化、测试命令与结果。除非用户明确要求，不运行 `git commit`。

---

## Self-Review

- Spec coverage: 覆盖派生字段移除、slot OCR 候选来源、阶段化参考分类、Final 保护、超买、缺信息 todo、renderer stdout、AGENTS/README/skill 检查与测试。
- Placeholder scan: 文档中的 `todo=...` 是业务输出 token，不是计划占位符。
- Type consistency: `sell_plan` 新 shape 在 scene、renderer、RPC 测试中统一为 `reference_only`、`candidates`、`items`、`todos`；stdout 使用 `info todo=...`。
