# CW 变费角色实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 逐任务实现此计划，并在每个任务后进行规格合规审查与代码质量审查。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 正确支持 `银狼LV.999` 变费角色，确保 Agent 可见身份为 `name + cost + star`，避免与普通 `银狼` 混淆，并在确认升费后确定性同步 shop snapshot。

**架构：** 从干净的项目内 worktree 开始，先写红灯测试锁住领域边界，再引入最小变费角色状态模型。slots/shop/rendering 只追加既有 `slot` / `item` 行的 `cost` 字段；`role_id` 只留在内部识别和诊断边界；购买验证、sell_plan、guide progress 消费同一个变费角色状态，而不是复制普通星级等价模型。

**技术栈：** Python 3.12、pytest、Typer CLI、现有 daemon/session/envelope/output renderer、现有 CW slots/shop/guide/sell_plan 模块。

---

## 已通过规格与审查

- Spec: `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-05-04-cw-variable-cost-role-design.md`
- Plan: `C:\Users\34404\source\repos\trail-cli\docs\superpowers\plans\2026-05-04-cw-variable-cost-role-implementation.md`
- 规格审查：4 个只读子代理两轮审查后全部 PASS。
- 计划审查：进入实现前必须等待领域模型、输出协议、执行性与外部证据 4 类只读 reviewer 全部 PASS；若任一 FAIL，先修订本计划并重新复审。
- 当前主工作树仍有一轮错误方向的 9 个 tracked modified 文件，本计划不得在这些改动上继续叠加实现。
- 公共资料仅作背景佐证，实施与验收一律以本 spec 与本地测试为准；只有当文档确实引用 Reddit 时，才做稳定 permalink 复核。

## 工作区与约束

- 开发 worktree: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-variable-cost-role`
- 主仓库: `C:\Users\34404\source\repos\trail-cli`
- 实现阶段必须从干净基线 worktree 开始，当前 dirty worktree 只保留 spec/plan 审查上下文，不作为实现起点。
- 不创建 git commit，除非用户另行明确要求；任务检查点只运行 `git status`、`git diff --stat`、测试和审查。
- 默认快速回归命令使用 `uv run pytest`；并发或多 profile 测试必须使用不同 `--basetemp`。
- 不新增正文前缀，不新增标题承载 `cost`，不新增位置参数，不扩张 YAML allowlist。
- `cost` 只能作为既有 `slot` / `item` 行的 `key=value` 字段输出。
- `role_id` 不进入 Agent 可见业务身份；只允许内部资源映射、识别器候选和诊断。
- 不实现二星 `银狼LV.999` 特殊选择的 UI 自动化；只定义和消费“可触发选择 / 已确认选择结果”的 session/命令状态。
- `confirmed_choices_by_cost` 必须通过统一 helper 读取，兼容 session JSON 持久化后的字符串 key，例如 `{"4": "equipment"}` 与 `{4: "equipment"}` 语义一致。
- 应用 `variable_cost_choice` 时必须验证当前费用已知、当前阶段二星上场且 `choice_available=True`、同费用阶段尚未确认；重复 payload、非二星状态或同费用已确认都不得 mutation shop。
- 不把 `docs/superpowers/specs` 或 `docs/superpowers/plans` 作为契约测试来源。

## 文件结构

- Create: `trail/scenes/cw/variable_cost.py`。纯函数变费角色模型、业务 key、当前费用阶段读取、选择结果应用、shop item cost 绑定。
- Create: `tests/test_cw_variable_cost.py`。纯函数与状态转移单元测试。
- Modify: `trail/scenes/cw/catalog.py`。仅保留内部资源/诊断映射；禁止用 `role_id` 作为业务身份。
- Modify: `trail/scenes/cw/slots.py`。slots snapshot 保留 `cost`，写入 `cw_state.variable_cost_roles`，标记 `choice_available`。
- Modify: `trail/scenes/cw/shop.py`。shop item 绑定当前 `cost`，未知 cost 不输出 fresh item；buy_slot 使用变费角色验证路径。
- Modify: `trail/scenes/cw/guide.py`。guide progress 记录当前费用阶段、星级、是否可触发选择、选择是否确认。
- Modify: `trail/daemon/cw_service.py`。必要时投影 session/命令状态字段，不从购买或 role_id 自动推断已确认升费。
- Modify: `trail/output/rendering.py`。slots/shop/sell_plan 的既有 `slot` / `item` 实体行追加 `cost`；guide progress 仅在结构化/session data 保留 `cost`，默认 `guide` / `info` 文本不得渲染 `cost`；保持现有 prefix 与顺序。
- Modify: `tests/test_cw_slots.py`、`tests/test_cw_shop.py`、`tests/test_cw_guide.py`、`tests/test_output_rendering.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_daemon_protocol.py`、`tests/test_skill_structure.py`。
- Modify: `AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`。
- Inspect and modify only if their current text reads affected slots/shop/sell_plan/guide facts: `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-entry/references/gameplay-concepts.md`、`skills/trail-cw-entry/references/confirmation-checklist.md`、`skills/trail-cw-guide/SKILL.md`、`skills/trail-cw-guide/references/command-surface.md`、`skills/trail-cw-guide/references/guide-selection-criteria.md`、`skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-portal/references/portal-command-surface.md`、`skills/trail-cw-portal/references/portal-selection-rules.md`、`skills/trail-cw-portal/references/portal-refresh-policy.md`。

## Task 0: 建立干净项目内 worktree

**Files:**
- 不修改生产文件

- [ ] **Step 1: 验证项目内 worktree 目录被忽略**

从主仓库根目录运行：

```powershell
$env:GIT_MASTER = "1"
rtk git check-ignore .worktrees
```

预期：输出 `.worktrees`，exit 0。若未被忽略，停止并回报，不能继续创建项目内 worktree。

- [ ] **Step 2: 创建干净实现 worktree**

```powershell
$env:GIT_MASTER = "1"
rtk git worktree add ".worktrees/cw-variable-cost-role" -b "cw-variable-cost-role" HEAD
```

预期：创建 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-variable-cost-role`。该 worktree 不包含当前主工作树中 9 个错误方向 tracked modifications。

- [ ] **Step 3: 验证 worktree 初始状态**

在 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-variable-cost-role` 运行：

```powershell
$env:GIT_MASTER = "1"
rtk git status --short
```

预期：无 tracked/untracked 变更。

- [ ] **Step 4: 创建 pytest basetemp 父目录**

新 worktree 中 `.pytest-tmp` 可能尚不存在；后续所有命令都使用 `.pytest-tmp/<profile>` 形式的嵌套 `--basetemp`，pytest 只创建最后一层目录，不创建父目录。先运行：

```powershell
New-Item -ItemType Directory -Force ".pytest-tmp" | Out-Null
Test-Path ".pytest-tmp"
```

预期：输出 `True`。该目录必须被 git ignore 覆盖，不得进入变更列表。

- [ ] **Step 5: 运行基线快速回归**

```powershell
uv run pytest tests/test_cw_shop.py tests/test_cw_slots.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_skill_structure.py -q --basetemp .pytest-tmp/cw-variable-baseline
```

预期：基线 PASS。若失败，记录失败并停止，不能把基线失败归因于本计划实现。

Task 1-8 的所有读写、测试、状态检查均必须在 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-variable-cost-role` 中执行，不能回到主仓库 dirty worktree 执行。

## Task 1: 建立变费角色纯模型

**Files:**
- Create: `trail/scenes/cw/variable_cost.py`
- Create: `tests/test_cw_variable_cost.py`

- [ ] **Step 1: 编写失败测试锁定身份边界**

创建 `tests/test_cw_variable_cost.py`：

```python
from __future__ import annotations

import pytest

from trail.scenes.cw.catalog import build_cw_catalog, resolve_cw_role_name
from trail.scenes.cw.variable_cost import (
    apply_cw_variable_cost_choice,
    bind_cw_variable_cost_shop_item,
    cw_role_business_key,
    is_cw_variable_cost_role,
)


def test_plain_silver_wolf_and_lv999_are_unrelated_business_roles():
    assert is_cw_variable_cost_role("银狼LV.999") is True
    assert is_cw_variable_cost_role("银狼") is False
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼", cost=None, star=1)
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼", cost=4, star=1)


def test_catalog_resolver_never_collapses_plain_silver_wolf_and_lv999():
    catalog = build_cw_catalog(
        {
            "roles": [
                {"id": "1006", "name": "银狼", "trait_ids": []},
                {"id": "15061", "name": "银狼LV.999", "trait_ids": []},
            ],
            "traits": [],
        }
    )

    assert resolve_cw_role_name("银狼", catalog).name == "银狼"
    assert resolve_cw_role_name("银狼LV.999", catalog).name == "银狼LV.999"
    assert resolve_cw_role_name("银狼LV999", catalog).name == "银狼LV.999"


def test_lv999_cost_phases_keep_same_role_name_but_distinct_phase_keys():
    assert cw_role_business_key("银狼LV.999", cost=3, star=1).name == "银狼LV.999"
    assert cw_role_business_key("银狼LV.999", cost=4, star=1).name == "银狼LV.999"
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼LV.999", cost=4, star=1)
    assert cw_role_business_key("银狼LV.999", cost=4, star=1) != cw_role_business_key("银狼LV.999", cost=4, star=2)


def test_confirmed_cost_up_updates_state_and_shop_items_without_stale():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}, {"name": "黑塔", "cost": 1}]},
    }

    apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert cw_state["variable_cost_roles"]["银狼LV.999"] == {
        "cost": 4,
        "star": 1,
        "choice_available": False,
        "choice_confirmed": False,
        "last_confirmed_choice": "cost_up",
        "last_confirmed_choice_cost": 3,
        "confirmed_choices_by_cost": {3: "cost_up"},
    }
    assert cw_state["shop"]["stale"] is False
    assert cw_state["shop"]["items"][0]["cost"] == 4
    assert cw_state["shop"]["items"][1]["cost"] == 1


def test_confirmed_equipment_choice_keeps_cost_and_shop_items():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]},
    }

    apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="equipment")

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["star"] == 2
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["choice_available"] is False
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["last_confirmed_choice_cost"] == 4
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["confirmed_choices_by_cost"] == {4: "equipment"}
    assert cw_state["shop"]["items"][0]["cost"] == 4


def test_replaying_confirmed_same_cost_choice_is_rejected_without_shop_mutation():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {"4": "equipment"}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]},
    }

    with pytest.raises(ValueError, match="choice already confirmed"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["confirmed_choices_by_cost"] == {"4": "equipment"}
    assert cw_state["shop"]["items"][0]["cost"] == 4


def test_confirmed_choice_requires_fielded_two_star_available_state():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 3, "star": 1, "choice_available": False, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]},
    }

    with pytest.raises(ValueError, match="choice is not available"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 3
    assert cw_state["shop"]["items"][0]["cost"] == 3


def test_cost_up_requires_known_current_lv999_cost_without_shop_mutation():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"star": 2, "choice_available": True}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999"}]},
    }

    with pytest.raises(ValueError, match="current cost is unknown"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert "cost" not in cw_state["variable_cost_roles"]["银狼LV.999"]
    assert "cost" not in cw_state["shop"]["items"][0]


def test_cost_up_from_max_lv999_cost_is_rejected_without_shop_mutation():
    cw_state = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 5, "star": 2, "choice_available": True}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 5}]},
    }

    with pytest.raises(ValueError, match="already at max cost"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 5
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["star"] == 2
    assert cw_state["shop"]["items"][0]["cost"] == 5


def test_shop_item_without_confirmed_lv999_cost_is_not_fresh():
    cw_state = {"variable_cost_roles": {}, "shop": {"opened": True, "stale": False}}

    item = bind_cw_variable_cost_shop_item({"name": "银狼LV.999"}, cw_state)

    assert item["name"] == "银狼LV.999"
    assert item["uncertain"] is True
    assert item["stale"] is True
    assert "cost" not in item
```

- [ ] **Step 2: 运行红灯测试**

```powershell
uv run pytest tests/test_cw_variable_cost.py -q --basetemp .pytest-tmp/cw-variable-red
```

预期：FAIL，报 `ModuleNotFoundError` 或找不到 `trail.scenes.cw.variable_cost`。

- [ ] **Step 3: 实现最小纯模型**

创建 `trail/scenes/cw/variable_cost.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VARIABLE_COST_ROLE_NAME = "银狼LV.999"
VARIABLE_COST_ROLE_COSTS = (3, 4, 5)


@dataclass(frozen=True)
class CwRoleBusinessKey:
    name: str
    cost: int | None
    star: int | None


def is_cw_variable_cost_role(name: object) -> bool:
    return str(name or "").strip() == VARIABLE_COST_ROLE_NAME


def cw_role_business_key(name: object, *, cost: object | None, star: object | None) -> CwRoleBusinessKey:
    role_name = str(name or "").strip()
    if not is_cw_variable_cost_role(role_name):
        return CwRoleBusinessKey(role_name, None, _parse_star(star))
    parsed_cost = _parse_variable_cost(cost)
    return CwRoleBusinessKey(role_name, parsed_cost, _parse_star(star))


def _parse_variable_cost(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in VARIABLE_COST_ROLE_COSTS:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed in VARIABLE_COST_ROLE_COSTS else None
    return None


def _parse_star(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _variable_state(cw_state: dict[str, Any], role_name: str) -> dict[str, Any] | None:
    roles = cw_state.get("variable_cost_roles")
    if not isinstance(roles, dict):
        return None
    state = roles.get(role_name)
    return state if isinstance(state, dict) else None


def bind_cw_variable_cost_shop_item(item: dict[str, Any], cw_state: dict[str, Any]) -> dict[str, Any]:
    bound = dict(item)
    if not is_cw_variable_cost_role(bound.get("name")):
        return bound
    state = _variable_state(cw_state, VARIABLE_COST_ROLE_NAME)
    cost = _parse_variable_cost(state.get("cost") if state else None)
    if cost is None:
        bound.pop("cost", None)
        bound["uncertain"] = True
        bound["stale"] = True
        return bound
    bound["cost"] = cost
    return bound


def _choice_for_cost(confirmed_by_cost: dict[Any, Any], cost: int) -> str | None:
    for key in (cost, str(cost)):
        value = confirmed_by_cost.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def apply_cw_variable_cost_choice(cw_state: dict[str, Any], *, role_name: str, choice: str) -> None:
    if not is_cw_variable_cost_role(role_name):
        return
    if choice not in {"cost_up", "equipment"}:
        raise ValueError("unsupported 银狼LV.999 choice")
    roles = cw_state.setdefault("variable_cost_roles", {})
    state = roles.setdefault(VARIABLE_COST_ROLE_NAME, {})
    current_cost = _parse_variable_cost(state.get("cost"))
    if current_cost is None:
        raise ValueError("银狼LV.999 current cost is unknown")
    if _parse_star(state.get("star")) != 2 or state.get("choice_available") is not True:
        raise ValueError("银狼LV.999 choice is not available")
    confirmed_by_cost = state.setdefault("confirmed_choices_by_cost", {})
    if _choice_for_cost(confirmed_by_cost, current_cost) is not None:
        raise ValueError("银狼LV.999 choice already confirmed for current cost")
    if choice == "cost_up":
        if current_cost == 5:
            raise ValueError("银狼LV.999 already at max cost")
        next_cost = current_cost + 1
        confirmed_by_cost[current_cost] = "cost_up"
        state.update({"cost": next_cost, "star": 1, "choice_available": False, "choice_confirmed": False, "last_confirmed_choice": "cost_up", "last_confirmed_choice_cost": current_cost})
        _sync_shop_items_to_cost(cw_state, next_cost)
    elif choice == "equipment":
        confirmed_by_cost[current_cost] = "equipment"
        state.update({"cost": current_cost, "choice_available": False, "choice_confirmed": True, "last_confirmed_choice": "equipment", "last_confirmed_choice_cost": current_cost})


def _sync_shop_items_to_cost(cw_state: dict[str, Any], cost: int) -> None:
    shop = cw_state.get("shop")
    if not isinstance(shop, dict):
        return
    items = shop.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict) and is_cw_variable_cost_role(item.get("name")):
            item["cost"] = cost
```

- [ ] **Step 4: 运行绿灯测试**

```powershell
uv run pytest tests/test_cw_variable_cost.py -q --basetemp .pytest-tmp/cw-variable-green
```

预期：PASS。

## Task 2: slots 保留 cost 并写入变费状态

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 编写 renderer 协议守护测试**

这些 renderer 测试用于锁定默认文本协议，不能作为本任务唯一红灯；真正红灯必须来自 Step 2 的 `read_cw_slots(...)` 业务路径测试，因为当前 renderer 可能已经能渲染给定 data 里的 `cost` 字段。

在 `tests/test_output_rendering.py` 增加：

```python
def test_render_output_keeps_lv999_slot_cost():
    envelope = {
        "ok": True,
        "data": {
            "front": [{"name": "银狼LV.999", "cost": 4, "star": 1}],
            "back": [],
            "hand": [],
            "stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.slots.read", envelope).splitlines()

    assert any(
        line.startswith("slot ") and "name=银狼LV.999" in line and "cost=4" in line and "star=1" in line
        for line in lines
    )


def test_render_output_marks_lv999_slot_without_cost_uncertain():
    envelope = {
        "ok": True,
        "data": {
            "front": [{"name": "银狼LV.999", "star": 1, "uncertain": True, "stale": True}],
            "back": [],
            "hand": [],
            "stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.slots.read", envelope).splitlines()

    assert any(
        line.startswith("slot ") and "name=银狼LV.999" in line and "uncertain=1" in line and "stale=1" in line and "cost=" not in line
        for line in lines
    )
```

- [ ] **Step 2: 编写 slots 状态红灯测试**

在 `tests/test_cw_slots.py` 增加：

```python
def _read_cw_slots_variable_cost(session, snapshot, **kwargs):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots", None)
    assert read_cw_slots is not None
    return read_cw_slots(session, reader=lambda: snapshot, **kwargs)


def test_read_cw_slots_records_lv999_variable_cost_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    assert result.response_snapshot["front"][0]["cost"] == 4
    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 1
    assert state["choice_available"] is False


def test_read_cw_slots_marks_fielded_lv999_two_star_choice_available(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["cost"] == 4
    assert state["star"] == 2
    assert state["choice_available"] is True
    assert state["choice_pending_after_fielding"] is False


def test_read_cw_slots_fills_missing_lv999_cost_from_known_current_phase(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    snapshot = ([{"name": "银狼LV.999", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1
    assert session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 4


def test_read_cw_slots_marks_lv999_missing_cost_uncertain_without_reliable_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["uncertain"] is True
    assert role["stale"] is True
    assert "cost" not in role
    assert "银狼LV.999" not in session.scene_state["cw"].get("variable_cost_roles", {})


def test_read_cw_slots_fills_invalid_lv999_cost_from_known_current_phase(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    snapshot = ([{"name": "银狼LV.999", "cost": 2, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1
    assert session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 4


def test_read_cw_slots_marks_invalid_lv999_cost_uncertain_without_reliable_state(tmp_path):
    session = build_fake_cw_session(tmp_path)
    snapshot = ([{"name": "银狼LV.999", "cost": "bad", "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["uncertain"] is True
    assert role["stale"] is True
    assert "cost" not in role
    assert "银狼LV.999" not in session.scene_state["cw"].get("variable_cost_roles", {})


def test_read_cw_slots_allows_new_cost_phase_choice_after_previous_cost_confirmed(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {"cost": 4, "star": 1, "choice_available": False, "last_confirmed_choice": "cost_up", "last_confirmed_choice_cost": 3, "confirmed_choices_by_cost": {3: "cost_up"}}
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is True
    assert state["choice_confirmed"] is False


def test_read_cw_slots_does_not_reopen_same_cost_confirmed_choice(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {"cost": 4, "star": 2, "choice_available": False, "last_confirmed_choice": "equipment", "last_confirmed_choice_cost": 4, "confirmed_choices_by_cost": {4: "equipment"}}
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is False
    assert state["choice_confirmed"] is True


def test_read_cw_slots_normalizes_persisted_choice_cost_keys(tmp_path):
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {
        "银狼LV.999": {"cost": 4, "star": 2, "choice_available": False, "last_confirmed_choice": "equipment", "last_confirmed_choice_cost": 4, "confirmed_choices_by_cost": {"4": "equipment"}}
    }
    snapshot = ([{"name": "银狼LV.999", "cost": 4, "star": 2}], [], [])

    _read_cw_slots_variable_cost(session, snapshot)

    state = session.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]
    assert state["choice_available"] is False
    assert state["choice_confirmed"] is True


def test_read_cw_slots_keeps_lv999_name_when_role_id_conflicts_with_plain_silver_wolf(tmp_path):
    session = build_fake_cw_session(tmp_path)
    guide_config = {
        "roles": [
            {"id": "1006", "name": "银狼", "trait_ids": []},
            {"id": "15062", "name": "银狼LV.999", "trait_ids": []},
        ],
        "traits": [],
    }
    snapshot = ([{"name": "银狼LV.999", "role_id": "1006", "cost": 4, "star": 1}], [], [])

    result = _read_cw_slots_variable_cost(session, snapshot, guide_config=guide_config)

    role = result.response_snapshot["front"][0]
    assert role["name"] == "银狼LV.999"
    assert role["cost"] == 4
    assert role["star"] == 1
```

- [ ] **Step 3: 运行红灯测试**

```powershell
uv run pytest tests/test_output_rendering.py tests/test_cw_slots.py -k "lv999" -q --basetemp .pytest-tmp/cw-variable-slots-red
```

预期：FAIL，至少有 `read_cw_slots(...)` 业务路径测试失败：未写入 `variable_cost_roles`、未补齐/标记 LV999 missing/invalid cost，或未按持久化后的 `confirmed_choices_by_cost` string key 判断同费用选择已确认。不要把单独 renderer cost-positive 测试是否已通过当成本任务红灯证据。

- [ ] **Step 4: 最小实现**

实现要求：

```python
# slots.py 中在 fresh slots 写入 cw_state 前，对 front/back/hand 中 name=银狼LV.999 且 cost=3|4|5 的项：
# 1. 保留 item["cost"]。
# 2. 更新 cw_state["variable_cost_roles"]["银狼LV.999"]。
# 3. confirmed_choices_by_cost 按 cost 记录已经消费过的选择，例如 {3: "cost_up", 4: "equipment"}。
# 4. 读取 confirmed_choices_by_cost 必须兼容 int key 与 JSON 持久化后的 str key。
# 5. 当 front/back 中 star == 2 且该 cost 阶段没有已确认选择时，设置 choice_available=True、choice_confirmed=False。
# 6. 当 front/back 中 star == 2 且同一 cost 阶段已经确认过选择时，设置 choice_available=False、choice_confirmed=True。
# 7. 当 hand 中 star == 2 时，只设置 choice_pending_after_fielding=True；不得把手牌二星当作当前可选择状态。
# 8. 当 name=银狼LV.999 但 snapshot 缺 cost 或 cost 不在 3|4|5 时，优先从已知 current variable_cost_roles cost 安全补齐；若仍未知，则该 slot 标记 uncertain=1/stale=1，不输出 cost，不写入可靠 variable_cost_roles，并产生 warning。
# 9. missing/invalid cost 必须走同一 unknown-cost 安全路径，使用 variable_cost._parse_variable_cost 或等价统一 helper 判断，不能保留 `cost=0/2/9/"bad"` 这类无效阶段。
# 10. 当 name=银狼LV.999 但 role_id 与普通银狼冲突时，业务输出仍以 name + cost + star 为准，不能改写成普通银狼。
```

renderer 要求：

```python
# rendering.py 现有 slot 行字段组中追加 cost；不要求改变既有 star/name 字段相对顺序。
# slot 行还需能渲染 per-slot uncertain=1/stale=1，以便 LV999 缺 cost 时不是 fresh 完整事实。
# 不新增 prefix，不改变 shot/read_image_first/标题顺序。
```

- [ ] **Step 5: 运行 slots 相关测试**

```powershell
uv run pytest tests/test_cw_slots.py tests/test_output_rendering.py -q --basetemp .pytest-tmp/cw-variable-slots-green
```

预期：PASS。

## Task 3: shop 绑定当前 cost 与未知路径

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_shop.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 编写 shop cost 绑定红灯测试**

在 `tests/test_cw_shop.py` 增加：

```python
def _build_cw_shop_variable_cost_session(tmp_path):
    from tests.conftest import build_fake_cw_session

    return build_fake_cw_session(tmp_path)


def _scan_cw_shop_variable_cost(session, *, scanner, guide_config=None):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert scan_cw_shop is not None
    return scan_cw_shop(session, scanner=scanner, guide_config=guide_config)


def test_shop_scan_binds_lv999_cost_from_current_variable_phase(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}

    result = _scan_cw_shop_variable_cost(
        session,
        scanner=lambda: {"opened": True, "stale": False, "items": [{"name": "银狼LV.999"}], "coins": 2, "reserve_full": False},
    )

    assert result.response_snapshot["items"][0]["name"] == "银狼LV.999"
    assert result.response_snapshot["items"][0]["cost"] == 4
    assert session.scene_state["cw"]["shop"]["items"][0]["cost"] == 4


def test_shop_scan_keeps_lv999_name_when_role_id_conflicts_with_plain_silver_wolf(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    guide_config = {
        "roles": [
            {"id": "1006", "name": "银狼", "trait_ids": []},
            {"id": "15062", "name": "银狼LV.999", "trait_ids": []},
        ],
        "traits": [],
    }

    result = _scan_cw_shop_variable_cost(
        session,
        scanner=lambda: {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "role_id": "1006"}], "coins": 2, "reserve_full": False},
        guide_config=guide_config,
    )

    item = result.response_snapshot["items"][0]
    assert item["name"] == "银狼LV.999"
    assert item["cost"] == 4
```

- [ ] **Step 2: 编写 unknown cost 红灯测试**

```python
def test_shop_scan_marks_lv999_without_known_cost_uncertain(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)

    result = _scan_cw_shop_variable_cost(
        session,
        scanner=lambda: {"opened": True, "stale": False, "items": [{"name": "银狼LV.999"}], "coins": 2, "reserve_full": False},
    )

    item = result.response_snapshot["items"][0]
    assert item["name"] == "银狼LV.999"
    assert item["uncertain"] is True
    assert item["stale"] is True
    assert "cost" not in item
```

- [ ] **Step 3: 编写 renderer 协议守护测试**

该测试只锁定 `item` 行协议，不作为本任务唯一红灯；红灯必须来自 Step 1/2 的 `scan_cw_shop(...)` 业务路径。

```python
def test_render_output_keeps_lv999_shop_cost():
    envelope = {
        "ok": True,
        "data": {"opened": True, "stale": False, "items": [{"idx": 1, "name": "银狼LV.999", "cost": 4}], "coins": 2, "reserve_full": False},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.shop.scan", envelope).splitlines()

    assert any(line.startswith("item ") and "name=银狼LV.999" in line and "cost=4" in line for line in lines)


def test_render_output_marks_lv999_shop_item_without_cost_uncertain():
    envelope = {
        "ok": True,
        "data": {
            "opened": True,
            "stale": False,
            "items": [{"idx": 1, "name": "银狼LV.999", "uncertain": True, "stale": True}],
            "coins": 2,
            "reserve_full": False,
        },
        "timing": {},
        "warnings": [{"code": "CW_SHOP_LV999_COST_UNKNOWN", "message": "银狼LV.999 cost unknown"}],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.shop.scan", envelope).splitlines()

    assert any(
        line.startswith("item ") and "name=银狼LV.999" in line and "uncertain=1" in line and "stale=1" in line and "cost=" not in line
        for line in lines
    )
```

- [ ] **Step 4: 运行红灯测试**

```powershell
uv run pytest tests/test_cw_shop.py tests/test_output_rendering.py -k "lv999_cost or role_id_conflicts or lv999_shop_item_without_cost" -q --basetemp .pytest-tmp/cw-variable-shop-red
```

预期：FAIL，至少有 `scan_cw_shop(...)` 业务路径测试失败：shop item 还不能从当前阶段绑定 `cost`、unknown cost 未标记为不可购买的 uncertain/stale，或 role_id 冲突仍被改写为普通银狼。不要把单独 renderer cost-positive 测试是否已通过当成本任务红灯证据。

- [ ] **Step 5: 最小实现**

实现要求：

```python
# shop.py 中 canonicalize/scan/project shop item 时调用 bind_cw_variable_cost_shop_item(item, cw_state)。
# name=银狼LV.999 且 cw_state.variable_cost_roles 有 cost 时，写 item["cost"]。
# name=银狼LV.999 且 cost 未知时，不输出 fresh item：item["uncertain"]=True、item["stale"]=True，并产生 LOW_CONFIDENCE/UNCERTAIN warning。
# 默认文本若仍输出该 item，必须在既有 item 行渲染 uncertain=1/stale=1；也可不输出该 item 并给出明确 warn，但不得输出看似 fresh 的无 cost LV999 item。
# 持久化 shop state 与 response 都不得留下可购买的无 cost LV999 fresh item；_require_fresh_shop_item 必须把 uncertain/stale/missing-cost LV999 视为不可购买。
# name=银狼LV.999 且 role_id 与普通银狼冲突时，业务输出仍以 name + cost 为准；role_id 最多保留在内部/诊断，不得改写为普通银狼。
# 普通角色 item 不改变现有字段。
```

- [ ] **Step 6: 运行 shop 与 renderer 回归**

```powershell
uv run pytest tests/test_cw_shop.py tests/test_output_rendering.py -q --basetemp .pytest-tmp/cw-variable-shop-green
```

预期：PASS。

## Task 4: 已确认选择结果与 shop snapshot 确定性同步

**Files:**
- Modify: `trail/scenes/cw/variable_cost.py`
- Modify: `trail/scenes/cw/events.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_variable_cost.py`
- Modify: `tests/test_cw_events.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 编写 `cw.event.handle` 已确认选择红灯测试**

在 `tests/test_cw_variable_cost.py` 增加纯 `events.handle_cw_event` 入口测试，并在 `tests/test_cw_events.py` 增加 daemon/command service payload 透传测试；必须调用真实入口而不是只预置 state 后断言：

```python
from tests.conftest import build_fake_cw_session
from trail.scenes.cw.events import handle_cw_event


def test_cw_event_handle_confirmed_lv999_choice_updates_shop_snapshot(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}

    result = handle_cw_event(
        session,
        handler=lambda: ("special", "confirm"),
        variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
    )

    assert result["event_type"] == "special"
    assert result["variable_cost_choice"] == {"role_name": "银狼LV.999", "choice": "cost_up"}
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["star"] == 1
    assert cw_state["shop"]["stale"] is False
    assert cw_state["shop"]["items"][0]["cost"] == 4


def test_cw_event_handle_rejects_replayed_lv999_choice_without_shop_mutation(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {"4": "equipment"}}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]}

    with pytest.raises(ValueError, match="choice already confirmed"):
        handle_cw_event(
            session,
            handler=lambda: ("special", "confirm"),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert cw_state["shop"]["stale"] is False
    assert cw_state["shop"]["items"][0]["cost"] == 4
```

在 `tests/test_cw_events.py` 增加 daemon/command service payload 透传红灯测试，复用该文件已有的 `_build_cw_harness` 与 `_run_cw_mutation`：

```python

def test_cw_event_handle_payload_confirmed_lv999_choice_updates_persisted_shop_snapshot(tmp_path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}}
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    service.save_session(session)
    monkeypatch.setattr("trail.daemon.cw_service.event_handler_factory", lambda runtime: lambda: ("special", "confirm"))

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-choice",
        method="cw.event.handle",
        payload={"variable_cost_choice": {"role_name": "银狼LV.999", "choice": "cost_up"}},
    )
    persisted = service.load_session(session.session_id)

    assert envelope["data"]["variable_cost_choice"] == {"role_name": "银狼LV.999", "choice": "cost_up"}
    assert persisted.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert persisted.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["star"] == 1
    assert persisted.scene_state["cw"]["shop"]["stale"] is False
    assert persisted.scene_state["cw"]["shop"]["items"][0]["cost"] == 4
```

- [ ] **Step 2: 编写未确认不得推断红灯测试**

```python
from tests.conftest import build_fake_cw_session
from trail.scenes.cw.events import handle_cw_event


def test_cw_event_handle_without_explicit_lv999_choice_does_not_infer_cost_up(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}

    handle_cw_event(session, handler=lambda: ("special", "confirm"))

    assert cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 3
    assert cw_state["variable_cost_roles"]["银狼LV.999"]["star"] == 2
    assert cw_state["shop"]["items"][0]["cost"] == 3
```

在 `tests/test_cw_events.py` 增加无 `variable_cost_choice` 的 command service 回归：

```python

def test_cw_event_handle_payload_without_variable_choice_keeps_legacy_behavior(tmp_path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    service.save_session(session)
    monkeypatch.setattr("trail.daemon.cw_service.event_handler_factory", lambda runtime: lambda: ("special", "confirm"))

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-no-choice",
        method="cw.event.handle",
        payload={},
    )
    persisted = service.load_session(session.session_id)

    assert envelope["data"] == {"event_type": "special", "handled_action": "confirm"}
    assert persisted.scene_state["cw"]["variable_cost_roles"]["银狼LV.999"]["cost"] == 3
    assert persisted.scene_state["cw"]["shop"]["items"][0]["cost"] == 3
```

- [ ] **Step 3: 运行红灯测试**

```powershell
uv run pytest tests/test_cw_variable_cost.py tests/test_cw_events.py tests/test_daemon_protocol.py -k "lv999_choice or infer_cost_up or variable_choice" -q --basetemp .pytest-tmp/cw-variable-choice-red
```

预期：FAIL，`handle_cw_event` 还不接受明确的 `variable_cost_choice`，或会在无明确选择结果时错误推断升费。

- [ ] **Step 4: 最小实现**

实现要求：

```python
# 不新增 UI 自动化命令。
# 在现有 cw.event.handle 路径上只接受显式确认字段 variable_cost_choice。
# cw_service.py 使用 payload.get("variable_cost_choice") 可选透传给 events.handle_cw_event；无该字段时保持既有 cw.event.handle 行为。
# events.handle_cw_event 仅当 variable_cost_choice={"role_name":"银狼LV.999","choice":"cost_up"|"equipment"} 存在时调用 apply_cw_variable_cost_choice。
# apply_cw_variable_cost_choice 必须在 mutation 前验证当前 cost 已知、star==2、choice_available=True、当前 cost 尚未出现在 confirmed_choices_by_cost（兼容 str/int key）。
# 重放同一 cost 的 variable_cost_choice、非二星状态、choice_available=False 或非法 choice 都不得更新 shop snapshot。
# 如果没有显式字段，只返回原有 event_type/handled_action，不修改 variable_cost_roles 或 shop cost。
# session 内部字段形态：
# cw_state["variable_cost_roles"]["银狼LV.999"] = {
#   "cost": 4,
#   "star": 1,
#   "choice_available": False,
#   "choice_confirmed": False,
#   "last_confirmed_choice": "cost_up" | "equipment",
#   "last_confirmed_choice_cost": 3 | 4,
#   "confirmed_choices_by_cost": {3: "cost_up", 4: "equipment"},
# }
# 任何购买、二星、数量变化、role_id 变化都不能调用 cost_up 分支。
```

- [ ] **Step 5: 运行选择状态测试**

```powershell
uv run pytest tests/test_cw_variable_cost.py tests/test_cw_events.py tests/test_daemon_protocol.py -k "lv999 or variable_cost" -q --basetemp .pytest-tmp/cw-variable-choice-green
```

预期：PASS。

## Task 5: buy_slot 变费角色验证

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_shop.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 编写普通角色回归红灯测试**

在 `tests/test_cw_shop.py` 中保留普通角色现有 `1/3/9` 行为，并增加断言：

```python
def _buy_cw_shop_slot_variable_cost(session, **kwargs):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None
    return buy_cw_shop_slot(session, **kwargs)


def test_shop_buy_slot_keeps_fixed_cost_role_equivalent_count(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼", "cost": 4}]}
    before_slots = {"front": [], "back": [], "hand": []}
    after_slots = {"front": [], "back": [], "hand": [{"name": "银狼", "star": 1}]}
    reads = iter([
        (before_slots["front"], before_slots["back"], before_slots["hand"]),
        (after_slots["front"], after_slots["back"], after_slots["hand"]),
    ])

    result = _buy_cw_shop_slot_variable_cost(
        session,
        slot=1,
        expect="银狼",
        buyer=lambda **_: None,
        scanner=lambda: {"opened": True, "stale": False, "items": [], "coins": 0, "reserve_full": False},
        slots_reader=lambda: next(reads),
    )

    assert result.response_snapshot["role_verification"]["verified"] is True
```

- [ ] **Step 2: 编写 `银狼LV.999` 不使用普通等价完整验证的红灯测试**

```python
def test_shop_buy_slot_verifies_lv999_by_name_cost_and_choice_state(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 1, "choice_available": False}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    before_slots = {"front": [], "back": [], "hand": [{"name": "银狼LV.999", "cost": 3, "star": 1}]}
    after_slots = {"front": [], "back": [], "hand": [{"name": "银狼LV.999", "cost": 3, "star": 2}]}
    reads = iter([
        (before_slots["front"], before_slots["back"], before_slots["hand"]),
        (after_slots["front"], after_slots["back"], after_slots["hand"]),
    ])

    result = _buy_cw_shop_slot_variable_cost(
        session,
        slot=1,
        expect="银狼LV.999",
        buyer=lambda **_: None,
        scanner=lambda: {"opened": True, "stale": False, "items": [], "coins": 0, "reserve_full": False},
        slots_reader=lambda: next(reads),
    )

    verification = result.response_snapshot["role_verification"]
    assert verification["name"] == "银狼LV.999"
    assert verification["cost"] == 3
    assert verification["star"] == 2
    assert verification["verified"] is True
    assert verification["choice_available"] is False
    assert verification["choice_pending_after_fielding"] is True
    assert "role_id" not in verification


def test_shop_buy_slot_rejects_lv999_unknown_cost_before_clicking(tmp_path):
    session = _build_cw_shop_variable_cost_session(tmp_path)
    session.scene_state["cw"]["shop"] = {
        "opened": True,
        "stale": False,
        "items": [{"slot": 1, "name": "银狼LV.999", "uncertain": True, "stale": True}],
        "coins": 2,
        "reserve_full": False,
    }
    buyer_calls: list[tuple[int, str]] = []

    with pytest.raises(TrailError) as exc_info:
        _buy_cw_shop_slot_variable_cost(
            session,
            slot=1,
            expect="银狼LV.999",
            buyer=lambda *, slot, expect: buyer_calls.append((slot, expect)),
            scanner=lambda: {"opened": True, "stale": False, "items": [], "coins": 2, "reserve_full": False},
        )

    assert exc_info.value.code == "SHOP_LV999_COST_UNKNOWN"
    assert buyer_calls == []
```

- [ ] **Step 3: 运行红灯测试**

```powershell
uv run pytest tests/test_cw_shop.py -k "fixed_cost_role_equivalent_count or lv999_by_name_cost or lv999_unknown_cost" -q --basetemp .pytest-tmp/cw-variable-buy-red
```

预期：FAIL，`银狼LV.999` 还走普通 `role_equivalent_count` 或输出缺少 `cost/choice_available`。

- [ ] **Step 4: 最小实现**

实现要求：

```python
# shop.py 中购买验证分流：
# - 普通角色继续用现有 1/3/9 equivalent count。
# - name=银狼LV.999 时，点击前必须要求 fresh shop item 同时具备 cost=3|4|5；missing cost、uncertain=1 或 stale=1 立即抛 TrailError(code="SHOP_LV999_COST_UNKNOWN")，且 buyer 不得被调用。
# - name=银狼LV.999 时，验证 before/after slots 中同 name + same cost 的 star/数量变化。
# - 手牌达成 2 星只设置 choice_pending_after_fielding=True，不设置当前 choice_available，不执行 cost_up。
# - 已上场 front/back 达成 2 星才可设置 choice_available=True，但仍不执行 cost_up。
# - 结构化 data/session 的 response_shop["role_verification"] 包含 name/cost/star/verified/choice_available/choice_pending_after_fielding，不包含 role_id 作为业务身份。
# - 默认文本不得把 cost 渲染到 role_verification、info 或其他非实体行；Agent 可见 cost 只能出现在对应 item/slot 行。
# - guide remaining_purchases 若仍为按 name 记录，只扣银狼LV.999 自己的 key，绝不扣普通 银狼。
```

- [ ] **Step 5: 运行 buy_slot 与 RPC 回归**

```powershell
uv run pytest tests/test_cw_shop.py tests/test_cw_rpc_contracts.py -q --basetemp .pytest-tmp/cw-variable-buy-green
```

预期：PASS。

## Task 6: sell_plan 与 guide progress 保护

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_cw_guide.py`
- Modify: `tests/test_output_rendering.py`

本任务的首要红灯是 `project_cw_guide_progress(cw_state)` 与 sell_plan 业务路径；renderer 只做协议守护，不能先实现 renderer 来绕过缺失 helper。

- [ ] **Step 1: 编写 guide role filter 与 sell_plan 业务红灯测试**

在 `tests/test_cw_guide.py` 增加 guide role filter 红灯测试：

```python
def test_guide_role_filter_keeps_plain_silver_wolf_and_lv999_separate():
    guide_module = load_cw_guide_module()
    resolve_filters = getattr(guide_module, "_resolve_guide_role_filters", None)
    assert resolve_filters is not None
    raw_config = {
        "role_list": [
            {"id": "1006", "name": "银狼", "trait_ids": []},
            {"id": "15061", "name": "银狼LV.999", "trait_ids": []},
        ],
        "trait_info_list": [],
    }

    plain = resolve_filters(raw_config=raw_config, role="银狼")
    lv999 = resolve_filters(raw_config=raw_config, role="银狼LV.999")

    assert plain["role_ids"] == ["1006"]
    assert lv999["role_ids"] == ["15061"]
```

在 `tests/test_cw_slots.py` 增加 sell_plan 业务红灯测试：

```python
def _plan_cw_hand_sell_variable_cost(session):
    slots_module = load_cw_slots_module()
    plan_cw_hand_sell = getattr(slots_module, "plan_cw_hand_sell", None)
    assert plan_cw_hand_sell is not None
    return plan_cw_hand_sell(session)


def test_sell_plan_protects_upgraded_lv999_one_star(tmp_path):
    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["slots"] = {
        "front": [{"name": "银狼LV.999", "cost": 4, "star": 1}],
        "back": [],
        "hand": [{"name": "银狼LV.999", "cost": 4, "star": 1}],
        "stale": False,
    }
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1, "choice_available": False}}
    # 当前实现若只看 target_star=1 / 当前 star=1，可能把它当成已达标的一星普通单卡；
    # 本规则要求 cost=4/5 的已升费阶段仍被保护。
    cw_state["guide"] = {
        "role_stages": [
            {"stage": "Final", "front_roles": [{"name": "银狼LV.999", "cost": 4, "star": 1}], "back_roles": []}
        ]
    }

    result = _plan_cw_hand_sell_variable_cost(session)

    lv999_items = [item for item in result["items"] if item["name"] == "银狼LV.999"]
    assert lv999_items
    assert all(item["protected"] is True for item in lv999_items)
```

在 `tests/test_output_rendering.py` 增加 renderer 协议守护测试；这些测试不能先于上面的业务测试实现：


```python
# 下列 renderer 测试是协议守护，不能先于 `project_cw_guide_progress` 与 sell_plan 业务测试实现。
# 本任务是否红灯以 Step 1/2 的业务测试为准。
def test_rendered_sell_plan_slot_keeps_lv999_cost_without_new_prefix():
    envelope = {
        "ok": True,
        "data": {
            "items": [{"slot": 0, "name": "银狼LV.999", "cost": 4, "star": 1, "protected": True}],
            "count": 1,
            "reference_only": True,
            "candidates": 1,
            "todos": [],
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.hand.sell_plan", envelope).splitlines()

    assert any(line.startswith("slot ") and "pos=hand:1" in line and "name=银狼LV.999" in line and "cost=4" in line for line in lines)


def test_guide_and_info_lines_do_not_leak_lv999_cost_from_structured_progress():
    envelope = {
        "ok": True,
        "data": {
            "攻略标题": "测试攻略",
            "攻略码": "TEST",
            "版本": "v1",
            "最低金币": 0,
            "最低等级": 1,
            "中期等级": 4,
            "role_stages": [
                {"stage": "Final", "front_roles": [{"name": "银狼LV.999", "cost": 5, "star": 1}], "back_roles": []}
            ],
            "guide_progress": {"银狼LV.999": {"cost": 5, "star": 1, "choice_available": False, "choice_confirmed": True}},
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.fetch.cw", envelope).splitlines()

    guide_or_info = [line for line in lines if line.startswith(("guide ", "info "))]
    assert guide_or_info
    assert all("cost=" not in line and "cost:" not in line for line in guide_or_info)


def test_guide_list_does_not_leak_lv999_cost_from_final_role_cards():
    envelope = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "guide-lv999",
                    "carry_roles": ["银狼LV.999"],
                    "final_role_cards": [{"name": "银狼LV.999", "cost": 4, "star": 1, "rarity": 4, "is_carry": True}],
                }
            ],
            "next_page_token": "",
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.list.cw", envelope).splitlines()

    guide_or_info = [line for line in lines if line.startswith(("guide ", "info "))]
    assert guide_or_info
    assert all("cost=" not in line and "cost:" not in line for line in guide_or_info)
```

- [ ] **Step 2: 编写 `project_cw_guide_progress` helper 红灯测试**

该步骤必须先于 renderer 实现通过；`project_cw_guide_progress` 当前在 HEAD 中不存在，因此它是本任务的核心红灯。

```python
def _build_cw_guide_variable_cost_session(tmp_path):
    from tests.conftest import build_fake_cw_session

    return build_fake_cw_session(tmp_path)


def _project_cw_guide_progress_variable_cost(cw_state):
    guide_module = load_cw_guide_module()
    project_cw_guide_progress = getattr(guide_module, "project_cw_guide_progress", None)
    assert project_cw_guide_progress is not None
    return project_cw_guide_progress(cw_state)


def test_guide_progress_records_lv999_cost_phase_and_choice_state(tmp_path):
    session = _build_cw_guide_variable_cost_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 5, "star": 1, "choice_available": False, "last_confirmed_choice": "cost_up", "last_confirmed_choice_cost": 4, "confirmed_choices_by_cost": {3: "cost_up", 4: "cost_up"}}}

    progress = _project_cw_guide_progress_variable_cost(session.scene_state["cw"])

    assert progress["银狼LV.999"] == {"cost": 5, "star": 1, "choice_available": False, "choice_confirmed": False, "last_confirmed_choice": "cost_up", "last_confirmed_choice_cost": 4}


def test_guide_progress_normalizes_persisted_choice_cost_keys(tmp_path):
    session = _build_cw_guide_variable_cost_session(tmp_path)
    session.scene_state["cw"]["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": False, "last_confirmed_choice": "equipment", "last_confirmed_choice_cost": 4, "confirmed_choices_by_cost": {"4": "equipment"}}}

    progress = _project_cw_guide_progress_variable_cost(session.scene_state["cw"])

    assert progress["银狼LV.999"]["choice_confirmed"] is True
```

- [ ] **Step 3: 运行红灯测试**

```powershell
uv run pytest tests/test_cw_slots.py tests/test_cw_guide.py tests/test_output_rendering.py -k "lv999" -q --basetemp .pytest-tmp/cw-variable-guide-red
```

预期：FAIL，sell_plan 或 guide progress 尚未理解费用阶段。

- [ ] **Step 4: 最小实现**

实现要求：

```python
# slots.py sell_plan：如果 hand item.name == 银狼LV.999 且 cost in (4, 5)，不得仅因 star=1 或 Final target_star=1 就降权为普通已达标单卡。
# guide.py role filter：普通 银狼 与 银狼LV.999 必须 exact-match 到各自 role_id；fuzzy/high-risk 候选只能 warning，不能把 selected role_ids 互相替代。
# guide.py：新增 project_cw_guide_progress(cw_state) 或同等显式 helper；结构化/session data 中的 guide progress 针对 银狼LV.999 输出 current cost、star、choice_available、choice_confirmed、last_confirmed_choice、last_confirmed_choice_cost。
# choice_confirmed 必须按当前 cost 检查 confirmed_choices_by_cost，兼容 str/int cost key；不能由 choice_available=False 或上一 cost 的 last_confirmed_choice 反推。
# rendering.py：guide.fetch.cw、guide.list.cw 与 cw.start/cw.portal.* 共享 final role cards/guide summary 路径中，guide/info 默认文本均不得输出 cost；sell_plan 若需要展示 cost，只能追加到既有 slot 行，不新增 prefix。
```

- [ ] **Step 5: 运行 sell_plan/guide/renderer 回归**

```powershell
uv run pytest tests/test_cw_slots.py tests/test_cw_guide.py tests/test_output_rendering.py -q --basetemp .pytest-tmp/cw-variable-guide-green
```

预期：PASS。

## Task 7: 文档、skill 与协议测试同步

**Files:**
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Inspect and modify only if current text references affected slots/shop/sell_plan/guide facts: `skills/trail-cw-entry/SKILL.md`、`skills/trail-cw-entry/references/gameplay-concepts.md`、`skills/trail-cw-entry/references/confirmation-checklist.md`
- Inspect and modify only if current text references affected slots/shop/sell_plan/guide facts: `skills/trail-cw-guide/SKILL.md`、`skills/trail-cw-guide/references/command-surface.md`、`skills/trail-cw-guide/references/guide-selection-criteria.md`
- Inspect and modify only if current text references affected slots/shop/sell_plan/guide facts: `skills/trail-cw-portal/SKILL.md`、`skills/trail-cw-portal/references/portal-command-surface.md`、`skills/trail-cw-portal/references/portal-selection-rules.md`、`skills/trail-cw-portal/references/portal-refresh-policy.md`
- Modify: `tests/test_skill_structure.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_cli_output_protocol.py`
- Modify: `tests/test_cli.py`（仅当 buy-slot CLI 参数或 stdout 入口受影响）

- [ ] **Step 1: 编写文档契约红灯测试**

在 `tests/test_skill_structure.py` 中增加断言，锁定 active skill 文档提到 `cost` must-keep 与 `role_id` 边界：

```python
def test_trail_cw_prep_documents_lv999_cost_protocol():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    text = (root / "skills" / "trail-cw-prep" / "SKILL.md").read_text(encoding="utf-8")
    surface = (root / "skills" / "trail-cw-prep" / "references" / "command-surface.md").read_text(encoding="utf-8")

    assert "银狼LV.999 与普通 银狼 完全无关" in agents
    assert "cost 只能作为默认文本既有 slot/item 行字段输出" in agents
    assert "guide/info/role_verification 默认文本不得输出 cost" in agents
    assert "银狼LV.999 不使用普通 star-equivalent 1/3/9" in agents
    assert "银狼LV.999 默认文本身份使用 name + cost + star" in text
    assert "role_id 只作内部资源/诊断，不作 Agent 业务身份" in text
    assert "银狼LV.999 不使用普通 star-equivalent 1/3/9" in surface
    assert "确认升费后确定性同步当前 shop snapshot，不刷新商店、不标 stale" in surface
    combined = agents + "\n" + text + "\n" + surface
    lv999_lines = [line for line in combined.splitlines() if "银狼LV.999" in line]
    assert lv999_lines
    assert all("1/3/9" not in line or "不使用普通 star-equivalent 1/3/9" in line for line in lv999_lines)
```

- [ ] **Step 2: 运行红灯测试**

```powershell
uv run pytest tests/test_skill_structure.py::test_trail_cw_prep_documents_lv999_cost_protocol -q --basetemp .pytest-tmp/cw-variable-docs-red
```

预期：FAIL，active skill 文档尚未同步。

- [ ] **Step 3: 更新文档与协议说明**

更新内容必须包括：

```text
银狼LV.999 与普通 银狼 完全无关。
银狼LV.999 默认文本身份使用 name + cost + star。
cost 是 slots/shop 中的 must-keep fact。
cost 只能作为默认文本既有 slot/item 行字段输出，guide/info/role_verification 默认文本不得输出 cost。
role_id 只作内部资源/诊断，不作 Agent 业务身份。
银狼LV.999 不使用普通 star-equivalent 1/3/9 作为完整购买验证或 sell_plan 判断。
AGENTS.md 中任何包含 银狼LV.999 的行不得沿用普通 1 星=1、2 星=3、3 星=9 说明，除非同一行明确写 银狼LV.999 不使用普通 star-equivalent 1/3/9。
确认升费后确定性同步当前 shop snapshot，不刷新商店、不标 stale。
```

不要更新根目录 `README.md`，除非实现审查确认该变化影响普通安装、用户入口或公开定位。

- [ ] **Step 4: 运行文档与协议回归**

```powershell
uv run pytest tests/test_skill_structure.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_cli_output_protocol.py -q --basetemp .pytest-tmp/cw-variable-docs-green
```

预期：PASS。

## Task 8: 全量验证与手动 QA

**Files:**
- 不新增实现文件

- [ ] **Step 1: 运行 targeted 回归**

```powershell
uv run pytest tests/test_cw_variable_cost.py tests/test_cw_shop.py tests/test_cw_slots.py tests/test_cw_guide.py tests/test_cw_events.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_skill_structure.py tests/test_cli_output_protocol.py -q --basetemp .pytest-tmp/cw-variable-targeted
```

预期：PASS。

- [ ] **Step 2: 运行默认 full pytest**

```powershell
uv run pytest --basetemp .pytest-tmp/cw-variable-full
```

预期：PASS。若出现与本变更无关的既有失败，记录完整失败测试名和原因，不修改无关代码。

- [ ] **Step 3: 运行 LSP 诊断**

对所有改动过的 Python 文件运行 `lsp_diagnostics`，至少包括：

```text
trail/scenes/cw/variable_cost.py
trail/scenes/cw/catalog.py
trail/scenes/cw/slots.py
trail/scenes/cw/shop.py
trail/scenes/cw/guide.py
trail/scenes/cw/events.py
trail/daemon/cw_service.py
trail/output/rendering.py
```

预期：改动文件无新增 LSP error。若仓库既有 basedpyright 噪声仍存在，逐项标记为既有并给出证据。

- [ ] **Step 4: CLI 表面 QA**

运行：

```powershell
uv run trail cw shop buy-slot --help
uv run trail cw shop buy-slot --session invalid --slot 1 --expect 银狼LV.999
```

预期：

- help 路径展示 `--session`、`--slot`、`--expect`。
- invalid session 路径返回 `fail cw.shop.buy_slot ...`、`request id=...` 和 `why msg=...`。

再写一次性 Python driver 调用 `render_output`：

```python
from trail.output.rendering import render_output

shop_lines = render_output(
    "cw.shop.scan",
    {
        "ok": True,
        "data": {"opened": True, "stale": False, "items": [{"idx": 1, "name": "银狼LV.999", "cost": 4}], "coins": 2, "reserve_full": False},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    },
).splitlines()
slot_lines = render_output(
    "cw.slots.read",
    {
        "ok": True,
        "data": {"front": [{"name": "银狼LV.999", "cost": 4, "star": 1}], "back": [], "hand": [], "stale": False},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    },
).splitlines()

assert any(line.startswith("item ") and "name=银狼LV.999" in line and "cost=4" in line for line in shop_lines)
assert any(line.startswith("slot ") and "name=银狼LV.999" in line and "cost=4" in line and "star=1" in line for line in slot_lines)

payload = {
    "ok": True,
    "screenshot": ".trail/shots/req-buy-slot.png",
    "image_guidance": {"read_image_first": True},
    "data": {
        "opened": True,
        "stale": False,
        "items": [{"idx": 1, "name": "银狼LV.999", "cost": 4}],
        "role_verification": {"name": "银狼LV.999", "cost": 4, "star": 2, "verified": True, "choice_available": False, "choice_pending_after_fielding": True},
        "slots": {"front": [{"name": "银狼LV.999", "cost": 4, "star": 2}], "back": [], "hand": []},
        "coins": 2,
        "reserve_full": False,
    },
    "timing": {},
    "warnings": [],
    "references": [],
    "debug": None,
    "error": None,
}
buy_lines = render_output("cw.shop.buy_slot", payload).splitlines()

assert buy_lines[0].startswith("ok cw.shop.buy_slot ")
assert buy_lines[1] == "shot path=.trail/shots/req-buy-slot.png"
assert buy_lines[2] == "info read_image_first=1"
assert all("cost=" not in line and "cost:" not in line for line in buy_lines if not line.startswith(("item ", "slot ")))
assert any(line.startswith("item ") and "name=银狼LV.999" in line and "cost=4" in line for line in buy_lines)
assert any(line.startswith("slot ") and "name=银狼LV.999" in line and "cost=4" in line for line in buy_lines)
print("\n".join(buy_lines))
```

预期输出中只有 `item` / `slot` 行包含 `cost=4`；verification/info 行不得包含 `cost=` 或 `cost:`。同时显式断言首行之后立即是 `shot path=.trail/shots/req-buy-slot.png`，下一行立即是 `info read_image_first=1`，之后才允许出现任何 `#` 标题、`info`、`item`、`slot`、`warn` 或 `ref` 行。

- [ ] **Step 5: 最终状态检查**

```powershell
$env:GIT_MASTER = "1"
rtk git status --short
rtk git diff --stat
```

预期：只包含本计划实现相关文件；不包含当前主工作树那 9 个错误方向 diff 的残留。

## 执行与审查规则

- 每个 Task 由一个实现子代理执行，传入完整 spec 路径、plan 路径、任务全文、worktree 路径和相关文件路径。
- 每个 Task 完成后先运行规格合规审查子代理，再运行代码质量审查子代理；任一审查 FAIL 必须让同一实现子代理修复并复审。
- 所有 Task 完成后运行最终整体审查；审查通过后再进入收尾。
- 不提交、不推送、不 amend，除非用户明确要求。
