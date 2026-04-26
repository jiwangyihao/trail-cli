# CW Prep Skill Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the infrastructure for an internal `trail-cw-prep` ordinary-preparation skill, including portal handoff, dynamic guide operation hints, current-guide sessionization, and a `cw.shop.buy_exp` command, without encoding concrete prep-stage economy strategy.

**Architecture:** Keep strategy out of code and skill content for this pass. Make `cw.portal.select` hand off to `trail-cw-prep`, pass guide `operation_guide` through response data as `skill_info`, store the complete selected guide in session, and add `cw.shop.buy_exp` as a normal mutating shop action with stable renderer output and recovery semantics.

**Tech Stack:** Python 3.12, Typer CLI, pytest, YAML skill registries, Trail text renderer, daemon request journal, session scene state.

**Spec:** `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-26-cw-prep-skill-infrastructure-design.md`

**Worktree:** `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure`

**Commit Policy:** Do not create commits unless the user explicitly asks. Use checkpoints and verification instead.

---

## File Map

- Modify `skills/registry/scene-entries.yaml`: register `trail-cw-prep` as active internal with `caller_roles: [scene_entry, internal_skill]`.
- Modify `skills/registry/workflow-handoffs.yaml`: add `cw.portal.select -> trail-cw-prep` handoff.
- Create `skills/trail-cw-prep/SKILL.md`: minimal ordinary-prep owner with no concrete economy strategy.
- Create `skills/trail-cw-prep/references/command-surface.md`: command map and safety boundaries.
- Create `skills/trail-cw-prep/references/stage-boundaries.md`: ordinary prep vs event/BOSS/settle boundaries.
- Create `skills/trail-cw-prep/references/decision-points-pending-strategy.md`: decision topics only, no priorities.
- Create `skills/trail-cw-prep/evals/triggers.json`: routing fixtures.
- Modify `skills/trail-hsr/references/scene-entry-index.md`: document scene-entry and stage-internal handoffs separately.
- Modify `skills/trail-cw-entry/SKILL.md`, `skills/trail-cw-portal/SKILL.md`, `skills/trail-cw-guide/SKILL.md`: update handoff/current-guide semantics.
- Modify `trail/scenes/cw/guide.py`: store complete selected guide in session, remove current-guide artifact dependency, preserve derived fields.
- Modify `trail/daemon/command_service.py`: adjust `guide.fetch.cw --select` behavior and mutation allowlist for `cw.shop.buy_exp`.
- Modify `trail/daemon/cw_service.py`: register `cw.shop.buy_exp`, add portal `skill_info` to response data, inject exp buyer factory.
- Modify `trail/scenes/cw/portal.py` only if portal select result assembly needs helper changes.
- Modify `trail/scenes/cw/shop.py`: add exp buyer abstraction and scene function.
- Modify `trail/commands/cw.py`: add `trail cw shop buy-exp` CLI command.
- Modify `trail/output/rendering.py`: render `skill_info`, update shop action renderer for `cw.shop.buy_exp`, remove current-guide artifact line.
- Modify `README.md` and `AGENTS.md`: output protocol, skill topology, guide/session semantics, buy-exp examples.
- Modify tests: `tests/test_skill_registry.py`, `tests/test_skill_structure.py`, `tests/test_skill_routing_contracts.py`, `tests/test_cw_guide.py`, `tests/test_cw_portal.py`, `tests/test_cw_strategy.py`, `tests/test_cw_shop.py`, `tests/test_cw_slots.py`, `tests/test_cw_rpc_contracts.py`, `tests/test_guide_rpc_contracts.py`, `tests/test_daemon_protocol.py`, `tests/test_output_rendering.py`, `tests/test_output_debug.py`, and any existing guide/strategy/shop/slots tests that assert current-guide artifact behavior.

---

### Task 1: RED Tests For Skill Topology And Handoff Registry

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure\tests\test_skill_registry.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure\tests\test_skill_routing_contracts.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure\tests\test_skill_structure.py`

- [ ] **Step 1: Update registry topology tests first**

In `tests/test_skill_registry.py`, change the active internal set and add expectations for `trail-cw-prep`:

```python
ACTIVE_INTERNAL_SKILL_DIRS = {"trail-hsr-advanced", "trail-cw-portal", "trail-cw-prep"}


def test_registry_keeps_cw_prep_as_active_internal_skill() -> None:
    data = _load_registry()
    internal_skills = _active_internal_skills(data)

    assert "trail-cw-prep" in internal_skills
    assert internal_skills["trail-cw-prep"]["status"] == "active"
    assert internal_skills["trail-cw-prep"]["exposure"] == "internal"
    assert set(internal_skills["trail-cw-prep"]["caller_roles"]) == {"scene_entry", "internal_skill"}
    assert "root_entry" not in internal_skills["trail-cw-prep"]["caller_roles"]
    assert all(entry["entry_skill"] != "trail-cw-prep" for entry in data.get("entries", []))
```

- [ ] **Step 2: Update workflow handoff routing tests first**

In `tests/test_skill_routing_contracts.py`, replace the hard-coded only-`cw.enter` expectation with an explicit two-command map:

```python
def test_workflow_handoff_registry_has_expected_scene_and_stage_mappings() -> None:
    data = yaml.safe_load(WORKFLOW_HANDOFFS.read_text(encoding="utf-8"))

    assert data["commands"]["cw.enter"]["default"] == {
        "handoff_skill": "trail-cw-entry",
        "handoff_strength": "strong",
        "handoff_reason": "scene_entered",
    }
    assert data["commands"]["cw.portal.select"]["default"] == {
        "handoff_skill": "trail-cw-prep",
        "handoff_strength": "strong",
        "handoff_reason": "preparation_stage_entered",
    }
```

Also update legacy-name tests so `trail-cw-prep` is treated as an active topology name, not legacy archive residue. In `tests/test_skill_routing_contracts.py`, update the negative-lookahead regexes and active lists:

```python
LEGACY_DOC_PATTERNS = (
    r"skills/trail-cw/SKILL\.md\b",
    r"skills/trail-cw-battle-advanced\b",
    r"skills/trail-cw-events\b",
    r"skills/trail-cw-replenish\b",
    r"skills/trail-cw-shop\b",
    r"skills/trail-cw-slots\b",
    r"trail-cw(?!-(entry|guide|portal|prep)\b)\b",
    r"trail-cw-battle-advanced\b",
    r"trail-cw-events\b",
    r"trail-cw-replenish\b",
    r"trail-cw-shop\b",
    r"trail-cw-slots\b",
)
LEGACY_ACTIVE_CW_PATTERN = r"trail-cw(?!-(entry|guide|portal|prep)\b)\b"


def test_active_skill_guidance_only_mentions_current_active_cw_topology() -> None:
    text = OUTPUT_RENDERING_TEST.read_text(encoding="utf-8")

    assert 'PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md"' in text
    assert 'PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md"' in text
    assert not re.search(r'skills"\s*/\s*"trail-cw(?!-(entry|guide|portal|prep))[^"]*"', text)
    assert not re.search(r"trail-cw(?!-(entry|guide|portal|prep)\b)", text)


def test_legacy_doc_patterns_do_not_misclassify_active_cw_topology_names() -> None:
    active_names = ["trail-cw-entry", "trail-cw-guide", "trail-cw-portal", "trail-cw-prep"]

    for pattern in LEGACY_DOC_PATTERNS:
        regex = re.compile(pattern)
        for name in active_names:
            assert regex.search(name) is None, (pattern, name)


def test_legacy_path_patterns_do_not_misclassify_active_cw_skill_paths() -> None:
    active_paths = [
        "skills/trail-cw-entry/SKILL.md",
        "skills/trail-cw-guide/references/command-surface.md",
        "skills/trail-cw-portal/evals/triggers.json",
        "skills/trail-cw-prep/SKILL.md",
        "skills/trail-cw-prep/evals/triggers.json",
    ]
    legacy_paths = [
        "skills/trail-cw/SKILL.md",
        "skills/trail-cw-battle-advanced/SKILL.md",
        "skills/trail-cw-events/SKILL.md",
        "skills/trail-cw-replenish/SKILL.md",
        "skills/trail-cw-shop/SKILL.md",
        "skills/trail-cw-slots/SKILL.md",
    ]

    for path in active_paths:
        assert not any(re.search(pattern, path) for pattern in LEGACY_CW_SKILL_PATH_PATTERNS), path
    for path in legacy_paths:
        assert any(re.search(pattern, path) for pattern in LEGACY_CW_SKILL_PATH_PATTERNS), path
```

Also update the non-archive specs/plans supersede scanner so this new active plan and its design spec can mention legacy skill names only in denylist/negative-test context without being classified as legacy active documentation:

```python
SUPERSEDE_EXEMPT_DOCS = {
    "2026-04-21-trail-skill-system-redesign.md",
    "2026-04-21-trail-skill-system-redesign-design.md",
    "2026-04-26-cw-prep-skill-infrastructure.md",
    "2026-04-26-cw-prep-skill-infrastructure-design.md",
}
```

This exemption must stay narrow: do not relax `LEGACY_DOC_PATTERNS`, and keep active skill docs, README, and AGENTS blocked from mentioning archive skill names outside explicit negative tests.

- [ ] **Step 3: Add skill structure constants and required-section tests**

In `tests/test_skill_structure.py`, add the missing prep constants; reuse the existing `SCENE_ENTRY_INDEX` constant if it is already present:

```python
CW_PREP_SKILL = PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md"
CW_PREP_COMMAND_SURFACE = PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "command-surface.md"
CW_PREP_STAGE_BOUNDARIES = PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "stage-boundaries.md"
CW_PREP_DECISION_POINTS = PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "decision-points-pending-strategy.md"
CW_PREP_TRIGGERS = PROJECT_ROOT / "skills" / "trail-cw-prep" / "evals" / "triggers.json"
SCENE_ENTRY_INDEX = PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "scene-entry-index.md"
```

Add a section test:

```python
def test_cw_prep_skill_has_required_sections_and_no_strategy_defaults() -> None:
    assert CW_PREP_SKILL.exists(), f"missing skill file: {CW_PREP_SKILL}"
    frontmatter, text = _frontmatter_markdown(CW_PREP_SKILL)

    assert frontmatter["name"] == "trail-cw-prep"
    assert "普通备战" in frontmatter["description"]
    assert "direct-user" not in frontmatter["description"]
    for section in (
        "## Role",
        "## When To Use",
        "## Stage Boundaries",
        "## Required First Actions",
        "## Command Surface",
        "## Autonomy Boundary",
        "## Decision Points Pending Strategy",
        "## Stop Conditions",
        "## Reference Map",
    ):
        assert section in text
    assert "不是 scene entry" in text
    assert "不是 direct-user 公共入口" in text
    assert "不得" in text and "具体经营策略" in text
    assert "不得直接或间接调用 archive skill" in text
    assert "trail-cw-shop" not in text
    assert "trail-cw-slots" not in text
    assert "trail-cw-replenish" not in text
```

Add reference-file content tests:

```python
def test_cw_prep_reference_files_exist_with_required_content() -> None:
    command_surface = CW_PREP_COMMAND_SURFACE.read_text(encoding="utf-8")
    stage_boundaries = CW_PREP_STAGE_BOUNDARIES.read_text(encoding="utf-8")
    decision_points = CW_PREP_DECISION_POINTS.read_text(encoding="utf-8")

    for command in (
        "trail cw stage detect",
        "trail cw shop scan",
        "trail cw slots read",
        "trail cw crystals collect",
        "trail cw shop buy-exp",
        "trail cw shop buy-slot",
        "trail cw battle run",
    ):
        assert command in command_surface
    for fragment in ("preparation", "shop", "replenish", "invest", "encounter", "fortune", "event", "boss_preview", "settle", "game_over", "unknown"):
        assert fragment in stage_boundaries
    for forbidden in ("优先买", "必须刷新", "默认卖", "直接出战"):
        assert forbidden not in command_surface
        assert forbidden not in decision_points
    for pending in ("哪些角色值得买", "如何决定卖牌", "什么时候买经验", "是否刷新商店", "何时结束备战"):
        assert pending in decision_points
```

Add scene-entry index contract tests:

```python
def test_scene_entry_index_documents_scene_and_stage_handoffs() -> None:
    text = SCENE_ENTRY_INDEX.read_text(encoding="utf-8")

    assert "cw.enter -> trail-cw-entry" in text
    assert "scene entry handoff" in text
    assert "cw.portal.select -> trail-cw-prep" in text
    assert "阶段" in text or "stage-internal" in text
    assert "不是 direct-user" in text or "不让 `trail-cw-prep` 成为 direct-user scene entry" in text
    assert "当前第一批是 `cw.enter`" not in text
```

Add trigger fixture coverage tests in `tests/test_skill_structure.py`:

```python
def test_cw_prep_trigger_fixture_has_required_boundary_cases() -> None:
    assert CW_PREP_TRIGGERS.exists(), f"missing trigger fixture: {CW_PREP_TRIGGERS}"
    data = json.loads(CW_PREP_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    assert counts["should-trigger"] >= 4
    assert counts["should-not-trigger"] >= 10
    assert counts["competition"] >= 6
    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        if item["sample_type"] == "competition":
            assert "candidates" in item
            if item["expected_winner"] != "none":
                assert item["expected_winner"] in item["candidates"]
            else:
                assert "trail-cw-prep" in item["candidates"]

    prompts = "\n".join(item["prompt"] for item in data)
    for fragment in (
        "普通备战",
        "普通商店",
        "投资环境页",
        "攻略选择页",
        "补给事件",
        "遭遇事件",
        "命运卜者",
        "普通投资事件",
        "投资策略页",
        "特殊事件",
        "BOSS 前",
        "结算",
        "game over",
        "unknown",
        "tainted",
        "request-status",
    ):
        assert fragment in prompts
```

- [ ] **Step 4: Run the RED subset and confirm failure**

Run:

```text
uv run pytest tests/test_skill_registry.py tests/test_skill_routing_contracts.py tests/test_skill_structure.py -q --basetemp .trail/pytest-tmp/task1-red -p no:cacheprovider
```

Expected: FAIL because `trail-cw-prep` files and registry mappings do not exist yet.

---

### Task 2: GREEN Skill Topology, Handoff Registry, And Prep Skill Files

**Files:**
- Modify: `skills/registry/scene-entries.yaml`
- Modify: `skills/registry/workflow-handoffs.yaml`
- Create: `skills/trail-cw-prep/SKILL.md`
- Create: `skills/trail-cw-prep/references/command-surface.md`
- Create: `skills/trail-cw-prep/references/stage-boundaries.md`
- Create: `skills/trail-cw-prep/references/decision-points-pending-strategy.md`
- Create: `skills/trail-cw-prep/evals/triggers.json`
- Modify: `skills/trail-hsr/references/scene-entry-index.md`

- [ ] **Step 1: Register `trail-cw-prep`**

Append to `internal_skills` in `skills/registry/scene-entries.yaml`:

```yaml
  - name: trail-cw-prep
    status: active
    exposure: internal
    caller_roles:
      - scene_entry
      - internal_skill
```

- [ ] **Step 2: Add workflow handoff**

Update `skills/registry/workflow-handoffs.yaml`:

```yaml
commands:
  cw.enter:
    default:
      handoff_skill: trail-cw-entry
      handoff_strength: strong
      handoff_reason: scene_entered
  cw.portal.select:
    default:
      handoff_skill: trail-cw-prep
      handoff_strength: strong
      handoff_reason: preparation_stage_entered
```

- [ ] **Step 3: Create `SKILL.md` with no strategy defaults**

Create `skills/trail-cw-prep/SKILL.md`:

```markdown
---
name: trail-cw-prep
description: 当上游已经进入货币战争普通备战阶段，并且需要先收集阶段、槽位、商店、晶矿或出战前事实时使用。
---

# Skill: trail-cw-prep

## Role

- `trail-cw-prep` 是货币战争普通备战阶段的 active internal 跟进 skill，不是 scene entry、不是 direct-user 公共入口、不是整局 owner。
- 它只负责事实收集、安全边界、命令地图和待讨论决策点，不写具体经营策略。
- 它不得直接或间接调用 archive skill；`cw.shop.*`、`cw.slots.*`、`cw.hand.*` 只是 CLI command family，不是旧 skill。

## When To Use

- `cw.portal.select` 成功并返回 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。
- 上游已经明确确认当前在普通备战/商店/槽位阶段，需要先收集事实。
- 货币战争首页、投资环境页、攻略选择页、特殊事件页、BOSS 前备战、结算和 game over 不进入本 skill 自治。

## Stage Boundaries

- 普通备战/商店阶段可以继续收集事实。
- 补给、投资、遭遇、命运卜者、通用事件、投资策略页交给后续专用 skill 或上游。
- BOSS 前备战交给后续单独 skill。
- unknown、tainted、request-status、daemon 恢复问题停止自治，保留事实并交回上游 scene entry；只有上游按 shared escalation contract 升级到 `trail-hsr-advanced`。

## Required First Actions

- 若上一条命令输出 `shot path=...` 和 `info read_image_first=1`，必须先读取原始截图。
- 不确定阶段时先用 `trail cw stage detect --session <id>` 或 `trail cw stage wait --session <id>`。
- 如果截图和结构化文本冲突，以截图为准并重新读取相关事实。

## Command Surface

- `trail cw stage detect|wait`：确认当前 CW 阶段。
- `trail cw slots read`：读取前台、后台、手牌和羁绊摘要。
- `trail cw shop scan|status|buy-slot|buy-exp|refresh|close`：读取和执行商店动作。
- `trail cw crystals collect`：执行晶矿收集动作，但是否执行属于后续策略决策。
- `trail cw hand sell-plan|sell`：读取或执行卖牌动作。
- `trail cw battle run --timeout 570`：执行出战和战斗链，但是否出战属于后续策略决策。

## Autonomy Boundary

- 本轮可自治的只有读图、阶段确认、只读事实收集、输出协议判断和安全恢复。
- 买角色、买经验、刷新、卖牌、上场、换位、收晶矿、出战等 mutation 只能在用户明确指示或后续策略设计给出决策后执行。

## Decision Points Pending Strategy

- 本 skill 只列出待讨论主题，不提供默认优先级。
- 需要讨论的主题见 `references/decision-points-pending-strategy.md`。

## Stop Conditions

- mutation 结果未知、session tainted、request-status 未确认时停止自治。
- 特殊事件、BOSS 前备战、结算、game over、unknown 阶段停止自治。
- 当前攻略缺失或不完整时，不继续推进依赖攻略的动作。

## Reference Map

- `references/command-surface.md`：普通备战阶段可用命令和边界。
- `references/stage-boundaries.md`：普通备战与其它阶段的分界。
- `references/decision-points-pending-strategy.md`：后续策略讨论主题清单。
```

- [ ] **Step 4: Create references**

Create `skills/trail-cw-prep/references/command-surface.md`:

```markdown
# Command Surface

- `trail cw stage detect|wait --session <id>`：只用于已进入货币战争后的阶段确认。
- `trail cw slots read --session <id> [--slot area:n]`：读取槽位快照；截图优先。
- `trail cw slots place|swap --session <id> ...`：显式改变槽位；本 skill 不决定何时执行。
- `trail cw shop scan|status --session <id>`：读取商店和经济事实。
- `trail cw shop buy-slot|buy-exp|refresh|close --session <id>`：商店 mutation；本 skill 不决定何时执行。
- `trail cw crystals collect --session <id>`：晶矿 mutation；本 skill 不决定何时执行。
- `trail cw hand sell-plan|sell --session <id>`：卖牌建议和显式卖牌；本 skill 不决定何时执行。
- `trail cw battle run --session <id> --timeout 570`：战斗链；本 skill 不决定何时出战。
```

Create `skills/trail-cw-prep/references/stage-boundaries.md`:

```markdown
# Stage Boundaries

- `preparation` / `shop`：普通备战事实收集范围。
- `replenish` / `invest` / `encounter` / `fortune` / `event`：特殊事件范围，停止并交给后续专用 skill。
- `boss_preview`：BOSS 前流程，停止并交给后续单独 skill。
- `settle` / `game_over`：结算或结束，不由本 skill 自治。
- `unknown`：不猜测，停止自治并交回上游 scene entry；本 internal skill 不直接升级到恢复层。
```

Create `skills/trail-cw-prep/references/decision-points-pending-strategy.md`:

```markdown
# Decision Points Pending Strategy

本文件只列后续专题讨论项，不提供默认优先级。

- 商店中哪些角色值得买。
- 备战位满时如何决定卖牌。
- 前台、后台、手牌如何上场或换位。
- 什么时候买经验或升等级。
- 晶矿奖励何时收取以及收取后要重读哪些事实。
- 是否刷新商店。
- 何时结束备战并出战。
```

- [ ] **Step 5: Create trigger fixture**

Create `skills/trail-cw-prep/evals/triggers.json` with at least this shape:

```json
[
  {"prompt":"cw.portal.select 成功后已经进入普通备战页，先收集事实","sample_type":"should-trigger","expected_winner":"trail-cw-prep"},
  {"prompt":"当前货币战争备战页有商店和槽位，先读图再看能用哪些命令","sample_type":"should-trigger","expected_winner":"trail-cw-prep"},
  {"prompt":"现在普通商店页，先别决定买谁，只整理 shop/slots/crystals 事实","sample_type":"should-trigger","expected_winner":"trail-cw-prep"},
  {"prompt":"普通备战阶段只需要确认有哪些可用命令和安全边界","sample_type":"should-trigger","expected_winner":"trail-cw-prep"},
  {"prompt":"我要玩货币战争","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"投资环境页三张卡出来了，要不要 refresh","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"攻略选择页打开了，帮我筛选一套阵容攻略","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"帮我选一套货币战争攻略","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"补给事件页出现三个选项，决定选哪个","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"遭遇事件页出现两个选项，帮我选","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"命运卜者页面出现了，选哪张牌","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"普通投资事件页面出来了，不是投资环境页","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"投资策略页三张策略卡，判断刷新哪张","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"特殊事件页面需要选择奖励","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"BOSS 前最后一次备战，决定怎么站位","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"结算页面显示挑战成功，继续下一步","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"game over 页面，处理整局结束","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"当前阶段 unknown，先 request-status 还是乱点","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"session tainted=1，需要 reconcile-session","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"daemon.request_status 返回 applied_but_not_persisted","sample_type":"should-not-trigger","expected_winner":"none"},
  {"prompt":"货币战争首页，先确认目标和难度","sample_type":"competition","candidates":["trail-cw-entry","trail-cw-prep"],"expected_winner":"trail-cw-entry"},
  {"prompt":"当前三张投资环境卡有待收集=1，判断要不要 select","sample_type":"competition","candidates":["trail-cw-portal","trail-cw-prep"],"expected_winner":"trail-cw-portal"},
  {"prompt":"当前环境已出，按当前环境挑攻略","sample_type":"competition","candidates":["trail-cw-guide","trail-cw-prep"],"expected_winner":"trail-cw-guide"},
  {"prompt":"portal select 已成功，备战页显示 1-1 和出战按钮，先整理事实","sample_type":"competition","candidates":["trail-cw-portal","trail-cw-prep"],"expected_winner":"trail-cw-prep"},
  {"prompt":"普通商店页，只收集 shop/slots 事实，不决定买谁","sample_type":"competition","candidates":["trail-cw-prep","trail-cw-portal"],"expected_winner":"trail-cw-prep"},
  {"prompt":"货币战争普通备战页，让我先看当前攻略运营提醒和可用命令","sample_type":"competition","candidates":["trail-cw-guide","trail-cw-prep"],"expected_winner":"trail-cw-prep"},
  {"prompt":"补给事件页出现三个选项，trail-cw-prep 能不能直接选","sample_type":"competition","candidates":["trail-cw-prep"],"expected_winner":"none"},
  {"prompt":"BOSS 前最后一次备战要决定站位，普通 prep skill 不应接管","sample_type":"competition","candidates":["trail-cw-prep"],"expected_winner":"none"},
  {"prompt":"unknown 阶段且 session tainted=1，prep 应停止自治并交回上游","sample_type":"competition","candidates":["trail-cw-prep"],"expected_winner":"none"}
]
```

Do not update `skills/shared/escalation-contract.md` in this pass. `trail-cw-prep` has `caller_roles: [scene_entry, internal_skill]`, but it is an internal stage skill and must not document or fixture a direct `trail-hsr-advanced` handoff. Unknown/tainted/request-status cases are stop conditions for prep, not escalation ownership.

- [ ] **Step 6: Update scene-entry index**

In `skills/trail-hsr/references/scene-entry-index.md`, replace the old `cw.enter`-only sentence with a paragraph that distinguishes scene entry handoff and stage-internal handoff:

```markdown
- `cw.enter -> trail-cw-entry` 是 scene entry handoff；`cw.portal.select -> trail-cw-prep` 是货币战争内部阶段 handoff。后者不让 `trail-cw-prep` 成为 direct-user scene entry。
```

- [ ] **Step 7: Run task 1/2 tests and confirm pass**

Run:

```text
uv run pytest tests/test_skill_registry.py tests/test_skill_routing_contracts.py tests/test_skill_structure.py -q --basetemp .trail/pytest-tmp/task2-green -p no:cacheprovider
```

Expected: PASS for the edited topology tests. Existing unrelated tests must not be changed.

---

### Task 3: RED/GREEN Current Guide Sessionization And Artifact Removal

**Files:**
- Modify: `tests/test_cw_guide.py`
- Modify: `tests/test_cw_portal.py`
- Modify: `tests/test_cw_strategy.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_debug.py`
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: Write failing tests for complete guide in session**

Add tests that call the existing guide selection helper and assert full fields remain:

```python
def test_select_cw_guide_persists_complete_payload_and_operation_guide(tmp_path: Path) -> None:
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    guide = normalize_cw_guide_payload({
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "g1",
        "title": "测试攻略",
        "share_code": "##code##",
        "operation_guide": "前期按测试运营",
        "version": "4.0",
        "min_coins": 40,
        "min_level": 6,
        "mid_level": 9,
        "on_field": {"灵砂": 1},
        "off_field": {"星期日": 1},
        "role_stages": [{"stage": "Opening", "front_roles": [{"name": "灵砂"}], "back_roles": [{"name": "星期日"}], "traits": ["2丰饶"]}],
        "first_fight_augments": [{"name": "击破概念股"}],
        "second_fight_augments": [{"name": "折射棱镜"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
    })

    select_cw_guide(session, guide_data=guide)
    cw_state = session.scene_state["cw"]

    assert cw_state["guide"]["operation_guide"] == "前期按测试运营"
    assert cw_state["guide"]["remaining_purchases"] == {"灵砂": 1, "星期日": 1}
    assert cw_state["guide"]["first_fight_augments"] == [{"name": "击破概念股"}]
    assert cw_state["guide"]["second_fight_augments"] == [{"name": "折射棱镜"}]
    assert cw_state["guide"]["order_basic"] == [{"name": "钻头"}]
    assert cw_state["guide"]["order_compose"] == [{"name": "风暴"}]
    assert cw_state["guide"]["role_stages"][0]["stage"] == "Opening"
    assert cw_state["constraints"]["min_coins"] == 40
    assert "artifact" not in cw_state["guide"]
    assert "artifact_id" not in cw_state["guide"]
```

- [ ] **Step 2: Write failing renderer tests for no `攻略快照ID`**

In `tests/test_output_rendering.py`, update current/apply expected output:

```python
def test_cw_guide_current_no_longer_outputs_artifact_snapshot_id(capsys) -> None:
    print_output("cw.guide.current", {
        "ok": True,
        "data": {
            "lineup_id": "g1",
            "title": "测试攻略",
            "share_code": "##code##",
            "version": "4.0",
            "labels": [],
        },
    })
    out = capsys.readouterr().out
    assert "ok cw.guide.current 攻略ID=g1 攻略标题=测试攻略 攻略码=##code## 版本=4.0" in out
    assert "攻略快照ID" not in out
```

Also rewrite existing current/apply tests that still assume an artifact-backed current guide. In `tests/test_cw_guide.py`, `tests/test_cw_rpc_contracts.py`, and `tests/test_output_rendering.py`, cover both the new success and invalid legacy states:

- Complete guide state succeeds for `cw.guide.current` and `cw.guide.apply`, and stdout/data contain `攻略ID/攻略标题/攻略码/版本` only, not `攻略快照ID`, `artifact`, or `artifact_id`.
- Legacy artifact-only state such as `{"artifact": "legacy-artifact", "lineup_id": "g1", "share_code": "##code##"}` fails with `CW_GUIDE_STATE_INVALID`.
- Incomplete guide state such as missing `operation_guide`, `remaining_purchases`, role/equipment lists, or constraints fails with `CW_GUIDE_STATE_INVALID`.
- The error message should tell the caller to rerun `guide.fetch.cw --select`.

Add daemon/RPC coverage for both methods so the invariant is enforced through `trail.daemon.cw_service._require_selected_guide`, not only through direct scene helper tests.

- [ ] **Step 3: Write failing `guide.fetch.cw --select` daemon tests**

In `tests/test_guide_rpc_contracts.py`, keep the existing CLI/YAML select test and extend it so `artifact_id:` never appears in either text or YAML output:

```python
    assert "artifact_id" not in text_result.stdout
    assert "artifact_id:" not in yaml_result.stdout
```

Keep or strengthen the existing non-select `guide.fetch.cw --format yaml` regression test so it still returns the full structured guide data. The fetch/artifact split must not accidentally narrow YAML output to only summary fields or remove nested guide fields from normal non-select fetch.

In `tests/test_daemon_protocol.py`, add a command-service test that proves select no longer creates or stores a guide artifact:

```python
def test_command_service_guide_fetch_select_stores_complete_guide_no_artifact(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide",
        lambda *args, **kwargs: {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": "g1",
            "title": "测试攻略",
            "share_code": "##code##",
            "version": "4.0",
            "operation_guide": "前期 按测试运营",
            "on_field": {"灵砂": 1},
            "off_field": {"星期日": 1},
            "role_stages": [{"stage": "Opening", "front_roles": [{"name": "灵砂"}], "back_roles": [], "traits": []}],
            "first_fight_augments": [{"name": "击破概念股"}],
            "second_fight_augments": [{"name": "折射棱镜"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
            "min_coins": 40,
            "min_level": 6,
            "mid_level": 9,
        },
    )
    monkeypatch.setattr(
        "trail.daemon.command_service.ArtifactStore.create",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select path must not create artifact")),
    )
    request = DaemonRequest(
        request_id="req-guide-fetch-select-no-artifact",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="guide.fetch.cw",
        payload={"url": "g1", "select": True},
    )

    payload = command_service.handle(request)
    persisted = service.load_session(session.session_id)

    assert payload["ok"] is True
    assert payload["data"]["operation_guide"] == "前期 按测试运营"
    assert "artifact_id" not in payload["data"]
    assert persisted.scene_state["cw"]["guide"]["operation_guide"] == "前期 按测试运营"
    assert persisted.scene_state["cw"]["constraints"]["min_level"] == 6
    assert "artifact" not in persisted.scene_state["cw"]["guide"]
    assert "artifact_id" not in persisted.scene_state["cw"]["guide"]
```

Also rewrite old artifact-cleanup tests around `guide.fetch.cw --select`: instead of expecting select to create an artifact and unlink it on save failure, assert `ArtifactStore.create` is not called and the failed mutation remains `failed_before_side_effect`.

- [ ] **Step 4: Write failing portal preflight tests for incomplete guide state**

In `tests/test_cw_portal.py`, extend the existing no-guide / invalid-guide tests so every invalid state fails before `select_cw_portal` or any runtime click:

```python
@pytest.mark.parametrize(
    "guide_state",
    [
        None,
        {"artifact": "legacy-artifact", "lineup_id": "legacy-only"},
        {"lineup_id": "g1", "share_code": "##code##"},
        {"lineup_id": "g1", "title": "缺少攻略码", "operation_guide": "前期 读图"},
    ],
    ids=["missing-guide", "legacy-artifact-only", "incomplete-no-operation-guide", "incomplete-no-share-code"],
)
def test_cw_portal_select_guide_preflight_rejects_missing_or_incomplete_guide_before_click(tmp_path: Path, guide_state, monkeypatch):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    if guide_state is not None:
        session.scene_state["cw"]["guide"] = dict(guide_state)
    service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select should not run before guide preflight")),
    )

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-select-guide-preflight",
        method="cw.portal.select",
        payload={"card_idx": 2},
    )

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "CW_GUIDE_STATE_INVALID"
    assert "guide.fetch.cw --select" in envelope["error"]["message"]
    assert runtime.clicks == []
    assert service.request_status("req-cw-portal-select-guide-preflight")["final_state"] == "failed_before_side_effect"
```

- [ ] **Step 5: Run RED tests**

Run:

```text
uv run pytest tests/test_cw_guide.py tests/test_cw_portal.py tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py tests/test_cw_strategy.py tests/test_cw_shop.py tests/test_cw_slots.py -k "complete_payload or artifact_snapshot or no_artifact or guide_preflight or guide_current or guide_apply or complete_guide or guide_summary" -q --basetemp .trail/pytest-tmp/task3-red -p no:cacheprovider
```

Expected: FAIL because `operation_guide` is not persisted, select still uses artifacts, portal accepts artifact-only legacy guide state, and renderer still emits artifact info.

- [ ] **Step 6: Implement guide session shape**

In `trail/scenes/cw/guide.py`, update `apply_cw_guide` so `cw_state["guide"]` starts from the full normalized payload and then adds mutable `remaining_purchases`:

```python
    cw_state["guide"] = {
        **deepcopy(guide_payload),
        "remaining_purchases": {
            **on_field,
            **off_field,
        },
    }
    cw_state["guide"].pop("artifact", None)
    cw_state["guide"].pop("artifact_id", None)
```

Keep `cw_state["constraints"]` exactly as today:

```python
    cw_state["constraints"] = {
        "min_coins": guide_payload.get("min_coins", 40),
        "min_level": guide_payload.get("min_level", 7),
        "mid_level": guide_payload.get("mid_level", 7),
        "priority": guide_payload.get("priority", {}),
        "positioning": guide_payload.get("positioning", {}),
    }
```

- [ ] **Step 7: Remove current-guide artifact rendering**

In `_render_cw_guide_summary` in `trail/output/rendering.py`, remove the `_append_fact_line(... "攻略快照ID" ...)` block. Keep the first line and `guide 攻略标签=...` line.

In `tests/test_output_debug.py`, replace the old AGENTS assertion that says `cw.guide.current|apply` emits `info 攻略快照ID=...` with assertions for the new session semantics:

```python
    assert "cw.guide.current|apply` 使用 `攻略ID/攻略标题/攻略码/版本`" in agents
    assert "攻略快照ID" not in agents
    assert "guide.fetch.cw --select` 只负责把完整攻略写入 session" in agents
```

- [ ] **Step 8: Update guide fetch select artifact path**

In `trail/daemon/command_service.py`, split guide fetching from artifact creation so `guide.fetch.cw --select` calls the pure fetch path and only normal non-select `guide.fetch.cw` creates an artifact for recovery/debug history:

```python
    def _fetch_cw_guide_payload(self, request):
        self._guide_scene(request.method, "guide.fetch.")
        from trail.scenes.cw import guide as cw_guide

        return to_jsonable(cw_guide.fetch_cw_guide(request.payload["url"], fetcher=cw_guide.fetch_cw_guide_payload))

    def _fetch_cw_guide_with_artifact(self, request):
        guide_payload = self._fetch_cw_guide_payload(request)
        artifact = ArtifactStore(Path(request.workspace_root) / ".trail" / "artifacts").create(
            scene="cw",
            kind="guide",
            payload={**guide_payload, "recovery_origin": "guide.fetch.cw"},
        )
        return guide_payload, artifact
```

Then update `_handle_guide_fetch_select` so it persists the fetched payload directly and never passes `artifact_id` into `select_cw_guide`:

```python
    def _handle_guide_fetch_select(self, request, service):
        from trail.scenes.cw.guide import select_cw_guide

        session = service.load_session(request.session_id)
        guide_payload = self._fetch_cw_guide_payload(request)
        select_cw_guide(session, guide_data=guide_payload)
        service.save_session(session)
        return guide_payload
```

- [ ] **Step 9: Make complete current-guide validation a shared invariant**

Require a complete guide with `share_code` and no artifact-only fallback for all current-guide consumers that claim a current guide is selected. Put the raising helper in `trail/scenes/cw/guide.py` so daemon/service code can share it:

```python
def require_complete_cw_guide(cw_state: dict) -> dict:
    guide = cw_state.get("guide")
    if not isinstance(guide, dict):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    required_text_keys = ("lineup_id", "title", "share_code")
    if any(not isinstance(guide.get(key), str) or not guide.get(key).strip() for key in required_text_keys):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    if not isinstance(guide.get("version"), str):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    if SHARE_CODE_PATTERN.fullmatch(guide["share_code"]) is None:
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略码无效，请重新执行 guide.fetch.cw --select")
    if not isinstance(guide.get("remaining_purchases"), dict):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    if "operation_guide" not in guide or not isinstance(guide.get("operation_guide"), str):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    for mapping_key in ("on_field", "off_field"):
        if not isinstance(guide.get(mapping_key), dict):
            raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    for list_key in ("role_stages", "first_fight_augments", "second_fight_augments", "order_basic", "order_compose"):
        if not isinstance(guide.get(list_key), list):
            raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略不完整，请重新执行 guide.fetch.cw --select")
    constraints = cw_state.get("constraints")
    if not isinstance(constraints, dict) or any(key not in constraints for key in ("min_coins", "min_level", "mid_level")):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略约束不完整，请重新执行 guide.fetch.cw --select")
    if guide.get("artifact") or guide.get("artifact_id"):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略来自旧 artifact 状态，请重新执行 guide.fetch.cw --select")
    return guide
```

`version` may be an empty string because current guide payload normalization can preserve missing upstream `rpg_game_big_version` as `version=""`; require the key and string type, but do not require non-empty content.

Use this helper in `trail/daemon/cw_service.py` by routing the existing `_require_selected_guide` through it. That makes `cw.guide.current`, `cw.guide.apply`, and `cw.portal.select` all reject legacy artifact-only or incomplete guide state before any UI side effect. Keep the same `CW_GUIDE_STATE_INVALID` code for current/apply/portal preflight failures so recovery guidance is stable.

For guide-dependent summaries in strategy/shop/slots code paths, add a non-raising sibling helper (for example `complete_cw_guide_or_none(cw_state)`) that returns `None` for missing, legacy artifact-only, or incomplete guide state. Use it where the command can still safely proceed without a guide, and add tests that incomplete legacy guide state is reported as no loaded guide instead of leaking stale `remaining_purchases` or artifact fields. Mutations that actually require a current guide must call the raising helper before side effects.

Update the old artifact cleanup/current-guide tests explicitly:

- `tests/test_cw_guide.py` current/apply tests around the old artifact snapshot assertions should become complete-guide success plus artifact-only failure tests.
- `tests/test_cw_rpc_contracts.py` current/apply RPC assertions should expect no `artifact`, no `artifact_id`, and no `攻略快照ID`.
- `tests/test_output_rendering.py` current/apply renderer tests should only assert the Chinese summary fields and tags.
- `tests/test_cw_strategy.py` and `tests/test_cw_shop.py` should include at least one incomplete-guide state that is treated as no loaded guide for summaries.

- [ ] **Step 10: Verify shop/strategy/slots consumers still work**

Run targeted guide and strategy tests:

```text
uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py tests/test_cw_shop.py tests/test_cw_slots.py tests/test_cw_portal.py tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py -k "select_cw_guide or guide_current or guide_summary or keeps_guide_loaded or guide_fetch_select or guide_preflight" -q --basetemp .trail/pytest-tmp/task3-green -p no:cacheprovider
```

Expected: PASS after updating obsolete artifact assertions.

---

### Task 4: RED/GREEN `skill_info` And Portal Select Handoff Rendering

**Files:**
- Modify: `tests/test_cw_portal.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `skills/registry/workflow-handoffs.yaml`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: Write failing output-order tests**

In `tests/test_output_rendering.py`, add:

```python
def test_portal_select_renders_skill_info_before_warn_ref_and_handoff_last(capsys) -> None:
    print_output("cw.portal.select", {
        "ok": True,
        "screenshot": ".trail/shots/portal.png",
        "data": {
            "card_idx": 1,
            "portal_title": "击破概念股",
            "skill_info": [{"name": "运营思路", "text": "前期 先读图"}],
        },
        "warnings": [{"code": "W", "message": "warn text"}],
        "references": [{"path": "p", "similarity": 0.9}],
    })
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines[0] == "ok cw.portal.select idx=1 投资环境=击破概念股"
    assert lines[1] == "shot path=.trail/shots/portal.png"
    assert lines[2] == "info read_image_first=1"
    assert lines.index("info skill_info=运营思路 text=\"前期 先读图\"") < next(i for i, line in enumerate(lines) if line.startswith("warn "))
    assert lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
```

Update every existing exact `cw.portal.select` success expectation in `tests/test_output_rendering.py` and `tests/test_cw_rpc_contracts.py` so the workflow handoff line is expected as the final line. At minimum, migrate the existing portal-select renderer test, CLI/RPC stdout test, and README/doc-lock smoke test that currently assume success output ends without handoff:

```python
assert lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
```

For tests that assert a full list of lines, append the handoff after all `warn` and `ref` lines. For tests that only check stdout snippets, require the handoff line to be present and make sure no outdated README example still shows `ok cw.portal.select ...` without the final handoff.

- [ ] **Step 2: Run RED output test**

Run:

```text
uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -k "portal_select or portal-select or handoff" -q --basetemp .trail/pytest-tmp/task4-red -p no:cacheprovider
```

Expected: FAIL because workflow handoff and skill_info rendering do not exist, and existing exact portal-select expectations have not yet been updated.

- [ ] **Step 3: Add `skill_info` renderer helper**

In `trail/output/rendering.py`, add:

```python
def _append_skill_info(lines: list[str], data: dict[str, Any]) -> None:
    for item in _as_list(data.get("skill_info")):
        if not isinstance(item, dict):
            continue
        name = _non_empty(item.get("name"))
        text = _non_empty(item.get("text"))
        if name is None or text is None:
            continue
        _append_fact_line(lines, "info", ("skill_info", name), ("text", text))
```

Call it from `_render_cw_portal_select` after `_append_success_capture_block(lines, payload)` and before `_append_warnings(lines, payload)`.

- [ ] **Step 4: Add portal select handoff registry**

Ensure `skills/registry/workflow-handoffs.yaml` includes the mapping from Task 2. The existing renderer handoff loader should append the final line automatically.

- [ ] **Step 5: Add portal service tests for `skill_info` and complete-guide fixtures**

In `tests/test_cw_portal.py`, update existing successful `cw.portal.select` fixtures so `cw_state["guide"]` is a complete normalized guide, not legacy artifact state. Use a helper like this inside the test module:

```python
def _complete_selected_guide(*, operation_guide: str = "前期 先读图") -> dict[str, object]:
    return {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "selected-lineup",
        "title": "测试攻略",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": operation_guide,
        "remaining_purchases": {"银狼": 1},
        "on_field": {"银狼": 1},
        "off_field": {},
        "role_stages": [{"stage": "Opening", "front_roles": [{"name": "银狼"}], "back_roles": [], "traits": []}],
        "first_fight_augments": [{"name": "击破概念股"}],
        "second_fight_augments": [{"name": "折射棱镜"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
        "min_coins": 40,
        "min_level": 6,
        "mid_level": 9,
    }
```

Then add or update portal select tests:

```python
def test_cw_portal_select_adds_operation_guide_skill_info(tmp_path: Path, monkeypatch):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": _complete_selected_guide(operation_guide="前期 先读图"),
        "constraints": {"min_coins": 40, "min_level": 6, "mid_level": 9, "priority": {}, "positioning": {}},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: dict(_portal_cards()[card_idx - 1]),
    )
    monkeypatch.setattr("trail.daemon.cw_service.apply_cw_guide_via_ui", lambda runtime, share_code: None)
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_portal_preparation", lambda session, runtime: None, raising=False)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-select-skill-info",
        method="cw.portal.select",
        payload={"card_idx": 2},
    )

    assert envelope["ok"] is True
    assert envelope["data"]["skill_info"] == [{"name": "运营思路", "text": "前期 先读图"}]
```

Also add a companion assertion that a complete guide with `operation_guide=""` succeeds but omits the `skill_info` key.

Apply the same `_complete_selected_guide(...)` and `constraints` fixture to every existing successful or post-click-failure `cw.portal.select` test in both `tests/test_cw_portal.py` and `tests/test_daemon_protocol.py`; remove legacy `{"artifact": ..., "lineup_id": ..., "share_code": ...}` success fixtures so the new preflight does not block auto-apply, capture-delay, recoverable post-click failure, or verbose-trace tests before their intended assertions.

- [ ] **Step 6: Add `skill_info` to portal select data**

In `trail/daemon/cw_service.py`, where `cw.portal.select` returns selected portal data, inject:

```python
def _operation_guide_skill_info(session) -> list[dict[str, str]]:
    guide = ensure_cw_state(session).get("guide")
    if not isinstance(guide, dict):
        return []
    operation_guide = str(guide.get("operation_guide") or "").strip()
    if not operation_guide:
        return []
    return [{"name": "运营思路", "text": operation_guide}]
```

Then add `selected_data["skill_info"] = _operation_guide_skill_info(session)` only when the list is non-empty.

- [ ] **Step 7: Run portal output tests**

Run:

```text
uv run pytest tests/test_cw_portal.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -k "portal_select or portal-select or skill_info or handoff" -q --basetemp .trail/pytest-tmp/task4-green -p no:cacheprovider
```

Expected: PASS.

---

### Task 5: RED/GREEN `cw.shop.buy_exp` Command And Renderer

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_cw_shop.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/scenes/cw/shop.py`
- Modify: `trail/output/rendering.py`

- [ ] **Step 1: Write failing CLI/RPC test**

In `tests/test_cw_rpc_contracts.py`, add a case following existing cw command mapping tests:

```python
def test_cw_shop_buy_exp_maps_to_canonical_command(cli_runner, fake_daemon_client, tmp_path) -> None:
    client = fake_daemon_client(
        {
            "cw.shop.buy_exp": build_success_response(
                request_id="req-cw-shop-buy-exp",
                data={"opened": True, "stale": False, "items": [], "coins": 36, "level": 4, "exp": "4/8", "reserve_full": False, "team_size": "4/4"},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "buy-exp", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0] == "ok cw.shop.buy_exp opened=1 stale=0 count=0"
    _assert_single_call(client, method="cw.shop.buy_exp", payload={}, tmp_path=tmp_path)
```

- [ ] **Step 2: Write failing scene/shop tests**

In `tests/test_cw_shop.py`, add scene-level tests:

```python
def _complete_shop_guide() -> dict[str, object]:
    return {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "selected-lineup",
        "title": "测试攻略",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": "前期 先读图",
        "remaining_purchases": {"灵砂": 1},
        "on_field": {"灵砂": 1},
        "off_field": {},
        "role_stages": [{"stage": "Opening", "front_roles": [{"name": "灵砂"}], "back_roles": [], "traits": []}],
        "first_fight_augments": [{"name": "击破概念股"}],
        "second_fight_augments": [{"name": "折射棱镜"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
    }


def test_buy_cw_shop_exp_updates_fresh_snapshot_and_marks_slots_stale(tmp_path: Path) -> None:
    shop_module = load_cw_shop_module()
    buy_cw_shop_exp = getattr(shop_module, "buy_cw_shop_exp", None)
    assert buy_cw_shop_exp is not None
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = session.scene_state.setdefault("cw", {})
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"slot": 1, "name": "灵砂", "price": 3}], "coins": 40, "level": 3, "exp": "0/4", "team_size": "3/3"}
    cw_state["guide"] = _complete_shop_guide()
    cw_state["constraints"] = {"min_coins": 40, "min_level": 6, "mid_level": 9, "priority": {}, "positioning": {}}

    clicked = []
    def exp_buyer():
        clicked.append(True)
    def scanner():
        return {"opened": True, "stale": False, "items": [{"slot": 1, "name": "灵砂", "price": 3}], "coins": 36, "level": 4, "exp": "0/8", "reserve_full": False, "team_size": "4/4"}

    buy_cw_shop_exp(session, buyer=exp_buyer, scanner=scanner)

    assert clicked == [True]
    assert session.scene_state["cw"]["shop"]["coins"] == 36
    assert session.scene_state["cw"]["shop"]["level"] == 4
    assert session.scene_state["cw"]["shop"]["team_size"] == "4/4"
    assert session.scene_state["cw"]["shop"]["guide_summary"]["remaining_purchases"] == {"灵砂": 1}
    assert session.scene_state["cw"]["slots"]["stale"] is True


def test_buy_cw_shop_exp_treats_incomplete_guide_as_unloaded(tmp_path: Path) -> None:
    shop_module = load_cw_shop_module()
    buy_cw_shop_exp = getattr(shop_module, "buy_cw_shop_exp", None)
    assert buy_cw_shop_exp is not None
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = session.scene_state.setdefault("cw", {})
    cw_state["shop"] = {"opened": True, "stale": False, "items": [], "coins": 40, "level": 3, "exp": "0/4", "team_size": "3/3"}
    cw_state["guide"] = {"remaining_purchases": {"灵砂": 1}}
    cw_state["constraints"] = {"min_coins": 40, "min_level": 6, "mid_level": 9, "priority": {}, "positioning": {}}

    buy_cw_shop_exp(
        session,
        buyer=lambda: None,
        scanner=lambda: {"opened": True, "stale": False, "items": [], "coins": 36, "level": 4, "exp": "0/8", "reserve_full": False, "team_size": "4/4"},
    )

    assert "guide_summary" not in session.scene_state["cw"]["shop"]
```

Add a builder-level test that locks the exp click abstraction and coordinate independently from the higher-level scene test:

```python
def test_build_cw_shop_exp_buyer_clicks_exp_button_point(monkeypatch) -> None:
    shop_module = load_cw_shop_module()
    build_cw_shop_exp_buyer = getattr(shop_module, "build_cw_shop_exp_buyer", None)
    assert build_cw_shop_exp_buyer is not None
    expected_point = getattr(shop_module, "SHOP_EXP_BUY_POINT")
    sleep_calls: list[float] = []
    monkeypatch.setattr(shop_module, "sleep", lambda seconds: sleep_calls.append(seconds))

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))

    runtime = Runtime()
    buyer = build_cw_shop_exp_buyer(runtime)
    buyer()

    assert runtime.clicks == [expected_point]
    assert sleep_calls == [shop_module.SHOP_BUY_CONFIRM_RETRY_SECONDS]
```

If the coordinate is adjusted during manual validation, update only `SHOP_EXP_BUY_POINT` and this test together.

- [ ] **Step 3: Write failing renderer test**

In `tests/test_output_rendering.py`, add:

```python
def test_cw_shop_buy_exp_outputs_snapshot_facts(capsys) -> None:
    print_output("cw.shop.buy_exp", {
        "ok": True,
        "screenshot": ".trail/shots/buy-exp.png",
        "data": {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "灵砂", "price": 3}],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": None,
        },
    })
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "ok cw.shop.buy_exp opened=1 stale=0 count=1"
    assert lines[1] == "shot path=.trail/shots/buy-exp.png"
    assert lines[2] == "info read_image_first=1"
    assert "item idx=1 slot=1 name=灵砂 cost=3" in lines
    assert "info coins=36 level=4 exp=0/8 reserve_full=0 team_size=null" in lines
```

Add failure / YAML coverage beside nearby renderer protocol tests:

```python
def test_render_output_rejects_yaml_for_cw_shop_buy_exp():
    payload = {
        "ok": True,
        "data": {"opened": True, "stale": False, "items": [], "coins": 36, "level": 4, "exp": "0/8", "reserve_full": False, "team_size": None},
        "screenshot": ".trail/shots/req-cw-shop-buy-exp.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.buy_exp", payload, output_format="yaml").splitlines() == [
        "fail cw.shop.buy_exp code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-shop-buy-exp.png",
        'why msg="yaml not supported for cw.shop.buy_exp"',
    ]


def test_render_output_preserves_cw_shop_buy_exp_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-shop-buy-exp-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-cw-shop-buy-exp-unknown", "last_known_stage": "side_effect_applied", "detail": "flush failed"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("cw.shop.buy_exp", payload).splitlines() == [
        "fail cw.shop.buy_exp code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-buy-exp-unknown",
        "shot path=.trail/shots/req-cw-shop-buy-exp-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-buy-exp-unknown",
    ]
```

- [ ] **Step 4: Write failing daemon journal tests**

In `tests/test_daemon_protocol.py`, add success and side-effect-failure tests for the new mutation. Follow existing `cw.shop.buy_slot` / `cw.strategy.select` patterns:

```python
def test_command_service_handles_cw_shop_buy_exp_through_mutation_journal(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def click_point(self, x: int, y: int, **kwargs):
            pass

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-shop-buy-exp.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    buyer_calls = []

    def fake_buyer_factory(factory_runtime):
        assert factory_runtime is runtime
        return lambda: buyer_calls.append("clicked")

    def fake_buy_exp(session, *, buyer, scanner):
        buyer()
        session.scene_state.setdefault("cw", {})["shop"] = {"opened": True, "stale": False, "items": [], "coins": 36, "level": 4, "exp": "0/8", "reserve_full": False, "team_size": "4/4"}
        return session

    monkeypatch.setattr("trail.daemon.cw_service.shop_exp_buyer_factory", fake_buyer_factory)
    monkeypatch.setattr("trail.daemon.cw_service.buy_cw_shop_exp", fake_buy_exp)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-buy-exp",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_exp",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["coins"] == 36
    assert payload["screenshot"] == ".trail/shots/req-cw-shop-buy-exp.png"
    assert buyer_calls == ["clicked"]
    assert runtime.capture_requests == [(False, "req-cw-shop-buy-exp")]
    assert service.load_session(session.session_id).scene_state["cw"]["shop"]["level"] == 4
    assert service.request_status("req-cw-shop-buy-exp")["final_state"] == "completed"


def test_command_service_marks_cw_shop_buy_exp_post_click_failure_recoverable(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.scenes.cw.shop import SHOP_EXP_BUY_POINT

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            return tmp_path / ".trail" / "shots" / "req-cw-shop-buy-exp-fail.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            return []

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)

    def fake_buyer_factory(factory_runtime):
        assert factory_runtime is runtime
        return lambda: factory_runtime.click_point(*SHOP_EXP_BUY_POINT)

    def fake_buy_exp(session, *, buyer, scanner):
        buyer()
        raise TrailError("CW_SHOP_SCAN_FAILED", "shop scan failed after buying exp")

    monkeypatch.setattr("trail.daemon.cw_service.shop_exp_buyer_factory", fake_buyer_factory)
    monkeypatch.setattr("trail.daemon.cw_service.buy_cw_shop_exp", fake_buy_exp)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-shop-buy-exp-fail",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_exp",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert runtime.clicks == [SHOP_EXP_BUY_POINT]
    assert service.request_status("req-cw-shop-buy-exp-fail")["final_state"] == "applied_but_not_persisted"
```

- [ ] **Step 5: Run RED tests**

Run:

```text
uv run pytest tests/test_cw_rpc_contracts.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_daemon_protocol.py -k "buy_exp or buy-exp" -q --basetemp .trail/pytest-tmp/task5-red -p no:cacheprovider
```

Expected: FAIL because command/function/renderer/daemon handler/journal coverage do not exist.

- [ ] **Step 6: Add CLI command**

In `trail/commands/cw.py`, add:

```python
@shop_app.command("buy-exp")
def cw_shop_buy_exp(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.shop.buy_exp", session_id=session)
```

- [ ] **Step 7: Add scene implementation**

In `trail/scenes/cw/shop.py`, add constants/types near other shop definitions:

```python
SHOP_EXP_BUY_POINT = _pixel_point(320, 992)
ShopExpBuyer = Callable[[], object]
```

Add builder:

```python
def build_cw_shop_exp_buyer(runtime: "RuntimeOperator") -> ShopExpBuyer:
    def buyer() -> None:
        runtime.click_point(*SHOP_EXP_BUY_POINT)
        sleep(SHOP_BUY_CONFIRM_RETRY_SECONDS)
    return buyer
```

Add scene function:

```python
def buy_cw_shop_exp(session: SessionModel, *, buyer: ShopExpBuyer, scanner: ShopSnapshotSource) -> SessionModel:
    buyer()
    cw_state = ensure_cw_state(session)
    updated_shop = _scan_shop_snapshot(cw_state, scanner=scanner)
    guide = complete_cw_guide_or_none(cw_state)
    if guide is not None:
        updated_shop["guide_summary"] = {
            "remaining_purchases": deepcopy(guide.get("remaining_purchases") or {}),
            "constraints": _stable_constraints_summary(cw_state),
        }
    cw_state["shop"] = updated_shop
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    return session
```

Import and reuse the non-raising `complete_cw_guide_or_none` helper from Task 3. Do not use `_remaining_purchases(cw_state)` directly here because incomplete legacy guide state must be treated as no loaded guide and must not leak stale purchases into shop summaries.

If the click point is wrong during manual validation, adjust only the constant and test fixture together.

- [ ] **Step 8: Register daemon handler and mutation**

In `trail/daemon/command_service.py`, add `"cw.shop.buy_exp"` to `CW_MUTATING_METHODS`.

In `trail/daemon/cw_service.py`, import the builder and function, add a factory variable next to existing shop factories, and register handler:

```python
"cw.shop.buy_exp": lambda: buy_cw_shop_exp(
    session,
    buyer=shop_exp_buyer_factory(runtime()),
    scanner=shop_scan_snapshot_reader_factory(runtime()),
).scene_state["cw"]["shop"],
```

- [ ] **Step 9: Update renderer**

In `_render_cw_shop_action`, make `cw.shop.buy_exp` append snapshot facts like scan:

```python
    if command in {"cw.shop.scan", "cw.shop.buy_exp"}:
        _append_cw_shop_snapshot_info(lines, data)
```

Ensure `_append_cw_shop_snapshot_info` formats `team_size=None` as `team_size=null` for `cw.shop.buy_exp`. If the helper currently skips `None`, update it to use explicit keys that must keep null:

```python
def _append_cw_shop_snapshot_info(lines: list[str], data: dict[str, Any]) -> None:
    facts = [
        ("coins", data.get("coins")),
        ("level", data.get("level")),
        ("exp", data.get("exp")),
        ("reserve_full", bool(data.get("reserve_full")) if "reserve_full" in data else None),
        ("team_size", data.get("team_size") if "team_size" in data else None),
    ]
    if any(key in data for key, _ in facts):
        lines.append("info " + _format_fact_sequence(*facts))
```

If `_format_fact_sequence` still skips `None`, add a separate `_format_fact_sequence_keep_null` or local formatter for shop snapshot facts so `team_size=null` is emitted when the key exists.

Add the renderer to `TEXT_RENDERERS`:

```python
    "cw.shop.buy_exp": _render_cw_shop_action,
```

- [ ] **Step 10: Run GREEN tests**

Run:

```text
uv run pytest tests/test_cw_rpc_contracts.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_daemon_protocol.py -k "buy_exp or buy-exp" -q --basetemp .trail/pytest-tmp/task5-green -p no:cacheprovider
```

Expected: PASS.

---

### Task 6: Docs Sync And Obsolete Artifact Assertions

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Search affected tests/docs for `攻略快照ID`, old `cw.enter`-only handoff assumptions, and old portal-select outputs without prep handoff.

- [ ] **Step 1: Search obsolete text**

Use Grep or `rg` via Bash only if counting is needed. Search for these strings:

```text
攻略快照ID
artifact id / 恢复追踪 id
当前第一批是 `cw.enter`
coins` / `level` / `exp` / `reserve_full` / `team_size` 当前只在
ok cw.portal.select
handoff_reason=scene_entered
```

Expected: find README/AGENTS/tests/docs that need edits.

- [ ] **Step 2: Update README output examples**

Add a portal select example with `skill_info` and handoff:

```text
ok cw.portal.select idx=1 投资环境=击破概念股
shot path=.trail/shots/req-portal-select.png
info read_image_first=1
info skill_info=运营思路 text="前期：先收集事实，再按后续策略处理"
info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered
```

Add a buy-exp example:

```text
ok cw.shop.buy_exp opened=1 stale=0 count=1
shot path=.trail/shots/req-buy-exp.png
info read_image_first=1
item idx=1 slot=1 name=灵砂 cost=3
info coins=36 level=4 exp=0/8 reserve_full=0 team_size=4/4
```

Remove `攻略快照ID` semantics and add a positive statement that `guide.fetch.cw --select` writes the complete guide into session instead of establishing a current-guide artifact. Update the selected-guide / portal auto-apply README flow and its doc-lock assertion (for example `test_readme_documents_selected_guide_and_portal_auto_apply_flow`) so it expects complete session guide semantics and no `攻略快照ID` wording. Also update the shop snapshot facts line so `shop scan/status/buy-exp` are listed, including README/doc-lock assertions in `tests/test_output_rendering.py` that currently say shop snapshot facts are only exposed by `trail cw shop scan` and `trail cw shop status`.

- [ ] **Step 3: Update AGENTS topology and output protocol**

Add `trail-cw-prep` as active internal and add the portal-select handoff line. Remove the `攻略快照ID` bullet. Lock the `cw.portal.select` output protocol changes in `AGENTS.md`:

- success 首行仍固定为 `ok cw.portal.select idx=... 投资环境=...`。
- 若响应 `data.skill_info` 非空，默认正文使用 `info skill_info=运营思路 text=...`；该行属于 success entity/info 行，必须在 `warn`、`ref` 之前输出。
- `cw.portal.select` 命中 workflow handoff 时，最后一行必须是 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`。
- `skill_info` 不新增正文前缀，不属于 verbose/debug，不进入 YAML allowlist。

Explicitly document `cw.shop.buy_exp` in the shop action renderer family: canonical command `cw.shop.buy_exp`,首行 `ok cw.shop.buy_exp opened=1 stale=0 count=<n>`、`item idx=... slot=... name=... cost=...`、snapshot facts `coins/level/exp/reserve_full/team_size`，其中 `team_size=null` 是 must-keep null 事实；同时说明 `cw.shop.buy_exp` 不在 YAML allowlist，`--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。

- [ ] **Step 4: Update active CW skills**

In `skills/trail-cw-portal/SKILL.md`, add that successful `portal select --card-idx` hands off to `trail-cw-prep`.

In `skills/trail-cw-guide/SKILL.md`, change `guide.fetch.cw --select` language to say it stores the complete guide in session and does not establish a current-guide artifact.

In `skills/trail-cw-entry/SKILL.md`, avoid making prep direct-user; only mention post-portal handoff if needed.

In `tests/test_skill_structure.py`, add document-contract assertions that lock these skill semantics:

```python
def test_active_cw_skills_document_prep_handoff_without_direct_user_prep() -> None:
    portal_text = CW_PORTAL_SKILL.read_text(encoding="utf-8")
    guide_text = CW_GUIDE_SKILL.read_text(encoding="utf-8")
    entry_text = CW_ENTRY_SKILL.read_text(encoding="utf-8")

    assert "trail-cw-prep" in portal_text
    assert "cw.portal.select" in portal_text or "portal select" in portal_text
    assert "guide.fetch.cw --select" in guide_text
    assert "完整攻略" in guide_text and "session" in guide_text
    assert "攻略快照ID" not in guide_text
    prep_lines = [line for line in entry_text.splitlines() if "trail-cw-prep" in line]
    assert all("direct-user" not in line and "公共入口" not in line for line in prep_lines)
```

Use the existing skill path constants where present; if missing, add `CW_ENTRY_SKILL`, `CW_PORTAL_SKILL`, and `CW_GUIDE_SKILL` constants beside the other skill structure constants.

- [ ] **Step 5: Update tests that assert old docs**

Adjust `tests/test_output_debug.py` assertion that expects `攻略快照ID`. Replace it with assertions that current/apply no longer output artifact id, `cw.portal.select` documents `info skill_info=运营思路 text=...` before warn/ref, and the portal-select handoff line is final. Update `tests/test_cw_rpc_contracts.py` assertions that still expect current-guide artifact fields, old portal-select stdout without handoff, or old help text.

- [ ] **Step 6: Run docs/structure tests**

Run:

```text
uv run pytest tests/test_skill_registry.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_rpc_contracts.py -q --basetemp .trail/pytest-tmp/task6-green -p no:cacheprovider
```

Expected: PASS.

---

### Task 7: Full Verification And Review Handoff

**Files:**
- No planned source edits in this task.

- [ ] **Step 1: Run required verification suites**

Run:

```text
uv run pytest tests/test_skill_structure.py -q --basetemp .trail/pytest-tmp/tests-skill-prep -p no:cacheprovider
uv run pytest tests/test_skill_registry.py tests/test_skill_routing_contracts.py -q --basetemp .trail/pytest-tmp/tests-skill-routing-prep -p no:cacheprovider
uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py tests/test_cw_portal.py tests/test_cw_shop.py tests/test_cw_slots.py -q --basetemp .trail/pytest-tmp/tests-cw-prep -p no:cacheprovider
uv run pytest tests/test_cw_rpc_contracts.py tests/test_guide_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_events.py tests/test_cw_battle_run.py -q --basetemp .trail/pytest-tmp/tests-output-prep -p no:cacheprovider
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full test suite if time permits**

Run:

```text
uv run pytest -q --basetemp .trail/pytest-tmp/full-cw-prep -p no:cacheprovider
```

Expected: pass. If it fails outside touched areas, record the failing files and decide whether they are related before changing anything.

- [ ] **Step 3: Request subagent review**

Dispatch 3 to 5 read-only review subagents with prompts over 2000 Chinese characters each if starting fresh. Give each subagent these full paths:

```text
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure\docs\superpowers\plans\2026-04-26-cw-prep-skill-infrastructure.md
C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-26-cw-prep-skill-infrastructure-design.md
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-prep-infrastructure
```

Use focused review domains: skill topology, guide/session data flow, buy-exp command/output, docs/tests/protocol. Apply review fixes until all reviewers return `PASS` or `PASS_WITH_NITS` with no blocking findings.

- [ ] **Step 4: Report final status without committing**

Report changed files, verification commands and results, and any residual risks. Do not commit unless the user explicitly asks.
