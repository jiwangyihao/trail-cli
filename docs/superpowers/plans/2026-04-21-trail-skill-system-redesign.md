# Trail Skill System Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 Trail skill 体系重构为“`trail-hsr` 对外总入口 + `trail-<scene>-entry` 对外场景入口 + `trail-hsr-advanced` 内部恢复层”的三层结构，并将旧 `trail-cw*` 彻底迁出 active namespace。

**Architecture:** 先补一套 repo 侧的 skill registry、shared escalation contract 与结构测试，让新的 public/internal/archive 语义可机读、可断言。然后先把 README、AGENTS 与 active tests 从旧 `trail-cw*` 拓扑里解耦，再分别重写 `trail-hsr` 与 `trail-hsr-advanced`，最后以单次原子变更归档旧 `trail-cw*` 并收口活参考文档与 rollout gate。整个过程只改文档、skill 资产与测试，不触碰 CLI runtime 行为。

**Tech Stack:** Markdown、YAML、JSON、pytest、ripgrep、git worktree、仓库内 `skills/` / `docs/superpowers/` 资产。

**Execution Note:** 未经用户明确要求，不创建 git commit。

**Spec Source:** `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-21-trail-skill-system-redesign-design.md`

**Known Baseline:** 当前 worktree `C:\Users\34404\source\repos\trail-cli\.worktrees\trail-skill-system-redesign` 的 `tests/test_output_rendering.py` 基线已有 2 个现存失败：`test_render_output_renders_cw_slots_place_success_text` 与 `test_render_output_renders_cw_hand_sell_success_text` 仍未接受 `info read_image_first=1`。本计划中的测试命令默认跑新建或定向子集，避免被这两个既有问题污染；等改到 `tests/test_output_rendering.py` 对应段落时再一起收口。

---

## 文件边界

- Create: `.worktrees/trail-skill-system-redesign/skills/registry/scene-entries.yaml`
  skill 拓扑的单一事实源，记录 `root_entry_skill`、scene entry 列表、internal skill 列表。
- Create: `.worktrees/trail-skill-system-redesign/skills/registry/routing-competition.json`
  repo 级 public skill 竞争路由样本。
- Create: `.worktrees/trail-skill-system-redesign/skills/shared/escalation-contract.md`
  `trail-hsr` 与 future `trail-<scene>-entry` 共享的升级到 `trail-hsr-advanced` 契约。
- Modify: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/SKILL.md`
  重写成中文总入口 skill，移除旧 simple-first 原子动作定位。
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/simple-command-surface.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/ocr-and-screenshot.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/scene-entry-index.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/evals/triggers.json`
  `trail-hsr` 的 should-trigger / should-not-trigger / competition 样本。
- Modify: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/SKILL.md`
  重写成内部恢复 skill。
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/advanced-command-surface.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/recovery-ladder.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/request-status-and-taint.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/window-launch.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/evals/escalations.json`
  `trail-hsr-advanced` 的 direct-user negative + internal escalation union schema 样本。
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-battle-advanced` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-battle-advanced`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-guide` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-guide`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-events` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-events`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-replenish` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-replenish`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-shop` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-shop`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-slots` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-slots`
- Create: `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/README.md`
  archive 来源清单与快照说明。
- Modify: `.worktrees/trail-skill-system-redesign/README.md`
  切换到新 public/internal skill 体系。
- Modify: `.worktrees/trail-skill-system-redesign/AGENTS.md`
  固化 active/internal/archive/registry 新规则。
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_registry.py`
  registry、public/internal、archive completeness 测试。
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_structure.py`
  frontmatter、references、eval 布局、中文 description 负向规则测试。
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_routing_contracts.py`
  总入口、scene entry、advanced 的路由与升级契约测试。
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_output_rendering.py`
  收口 active skill guidance 断言，去掉旧 `trail-cw*` owner 依赖。
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-18-cw-portal-flow-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-18-window-launch-path-resolution-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-19-trail-start-command-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-battle-action-buttons-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-guide-ime-input-fix-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-guide-output-localization-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-help-summary-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-portal-detect-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-slots-read-batch-ocr-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-guide-list-cw-name-filters-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-screenshot-first-guidance-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-shop-two-phase-scan-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-slots-batch-place-sell-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-strategy-commands-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-battle-run.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-battle-action-buttons.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-19-trail-start-command.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-18-cw-portal-flow.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-18-window-launch-path-resolution.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-15-trail-daemon.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-guide-output-localization.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-help-summary.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-portal-detect.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-slots-read-batch-ocr.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-guide-list-cw-name-filters.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-screenshot-first-guidance.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-shop-two-phase-scan.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-slots-batch-place-sell.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-strategy-commands.md`
- Create: `.worktrees/trail-skill-system-redesign/docs/superpowers/reviews/2026-04-21-trail-skill-routing-review.md`
  rollout gate 的人工对照评审记录，固定 5-10 条真实中文 prompt 的旧/新路由对比结果。

### Task 1: 建立 registry、shared contract 与路由测试骨架

**Files:**
- Create: `.worktrees/trail-skill-system-redesign/skills/registry/scene-entries.yaml`
- Create: `.worktrees/trail-skill-system-redesign/skills/registry/routing-competition.json`
- Create: `.worktrees/trail-skill-system-redesign/skills/shared/escalation-contract.md`
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_registry.py`
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_routing_contracts.py`

- [ ] **Step 1: 先写失败测试，锁住 registry 字段、competition quota 和 shared contract 内容**

```python
from collections import Counter
from pathlib import Path
import json

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
COMPETITION = PROJECT_ROOT / "skills" / "registry" / "routing-competition.json"
ESCALATION_CONTRACT = PROJECT_ROOT / "skills" / "shared" / "escalation-contract.md"


def test_scene_entries_registry_declares_root_entry_and_internal_roles() -> None:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert data["root_entry_skill"] == "trail-hsr"
    assert data["entries"] == [
        {
            "scene": "cw",
            "entry_skill": "trail-cw-entry",
            "status": "planned",
            "exposure": "public",
            "aliases": ["货币战争", "Currency Wars", "cw"],
        }
    ]
    assert data["internal_skills"] == [
        {
            "name": "trail-hsr-advanced",
            "status": "active",
            "exposure": "internal",
            "caller_roles": ["root_entry", "scene_entry"],
        }
    ]


def test_routing_competition_fixture_has_required_quota_and_cases() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    data = json.loads(COMPETITION.read_text(encoding="utf-8"))
    assert len(data) >= 6
    counts = Counter(item["sample_type"] for item in data)
    assert counts["root-vs-scene"] >= 2
    assert counts["planned-scene-fallback"] >= 2
    assert counts["root-vs-advanced"] >= 2
    for item in data:
        assert {"prompt", "sample_type", "expected_winner", "candidates"} <= item.keys()
        assert len(item["candidates"]) >= 2
        assert item["expected_winner"] in item["candidates"]
        if item["sample_type"] in {"root-vs-scene", "planned-scene-fallback"}:
            assert set(item["candidates"]) == {"trail-hsr", "trail-cw-entry"}
        if item["sample_type"] == "root-vs-advanced":
            assert set(item["candidates"]) == {"trail-hsr", "trail-hsr-advanced"}
            assert item["expected_winner"] == "trail-hsr"
    assert any(item["expected_winner"] == "trail-hsr" and item["sample_type"] == "planned-scene-fallback" for item in data)
    assert any(item["expected_winner"] == "trail-cw-entry" and item["sample_type"] == "root-vs-scene" for item in data)
    active_public_entries = [entry for entry in registry["entries"] if entry["status"] == "active" and entry["exposure"] == "public"]
    if len(active_public_entries) >= 2:
        scene_entry_names = {entry["entry_skill"] for entry in active_public_entries}
        scene_vs_scene = [item for item in data if item["sample_type"] == "scene-vs-scene"]
        assert scene_vs_scene
        for item in scene_vs_scene:
            assert len(set(item["candidates"])) >= 2
            assert set(item["candidates"]) <= scene_entry_names
            assert any(word in item["prompt"] for word in ("别名", "近似", "混淆", "同名"))


def test_shared_escalation_contract_keeps_required_rules() -> None:
    text = ESCALATION_CONTRACT.read_text(encoding="utf-8")
    assert "caller_roles" in text
    assert "expected_return_owner" in text
    assert "任何 active skill 都不得直接或间接调用 archive skill" in text
```

- [ ] **Step 2: 跑新测试，确认当前 worktree 还没有这些资产与 quota**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py tests/test_skill_routing_contracts.py -k "registry or competition or escalation_contract" -q`

Expected: FAIL，至少会因为文件不存在、样本数量不足、shared contract 缺少固定条款而失败。

- [ ] **Step 3: 写最小 registry / competition / shared contract，并直接补齐到 spec 规定的最低数量**

```yaml
# skills/registry/scene-entries.yaml
root_entry_skill: trail-hsr

entries:
  - scene: cw
    entry_skill: trail-cw-entry
    status: planned
    exposure: public
    aliases:
      - 货币战争
      - Currency Wars
      - cw

internal_skills:
  - name: trail-hsr-advanced
    status: active
    exposure: internal
    caller_roles:
      - root_entry
      - scene_entry
```

```json
[
  {
    "prompt": "帮我继续玩星铁",
    "sample_type": "root-vs-scene",
    "candidates": ["trail-hsr", "trail-cw-entry"],
    "expected_winner": "trail-hsr"
  },
  {
    "prompt": "帮我接管星铁主线",
    "sample_type": "root-vs-scene",
    "candidates": ["trail-hsr", "trail-cw-entry"],
    "expected_winner": "trail-hsr"
  },
  {
    "prompt": "货币战争入口已经上线后，直接带我进货币战争",
    "sample_type": "root-vs-scene",
    "candidates": ["trail-hsr", "trail-cw-entry"],
    "expected_winner": "trail-cw-entry"
  },
  {
    "prompt": "帮我玩货币战争",
    "sample_type": "planned-scene-fallback",
    "candidates": ["trail-hsr", "trail-cw-entry"],
    "expected_winner": "trail-hsr"
  },
  {
    "prompt": "进入货币战争玩法",
    "sample_type": "planned-scene-fallback",
    "candidates": ["trail-hsr", "trail-cw-entry"],
    "expected_winner": "trail-hsr"
  },
  {
    "prompt": "星铁启动失败了，帮我继续玩",
    "sample_type": "root-vs-advanced",
    "candidates": ["trail-hsr", "trail-hsr-advanced"],
    "expected_winner": "trail-hsr"
  },
  {
    "prompt": "游戏窗口不对，帮我恢复后继续玩",
    "sample_type": "root-vs-advanced",
    "candidates": ["trail-hsr", "trail-hsr-advanced"],
    "expected_winner": "trail-hsr"
  }
]
```

```markdown
# Escalation Contract

- 只有 `root_entry` 与 `scene_entry` 可以升级调用 `trail-hsr-advanced`。
- 调用方必须携带：当前失败症状、最近动作、可复用上下文、`expected_return_owner`。
- advanced 完成恢复后，控制权必须回到 `expected_return_owner`。
- 任何 active skill 都不得直接或间接调用 archive skill。
```

- [ ] **Step 4: 重跑 registry / routing 测试，确认路由骨架可机读**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py tests/test_skill_routing_contracts.py -k "registry or competition or escalation_contract" -q`

Expected: PASS。

### Task 2: 先收口 README / AGENTS / active tests 对旧拓扑的依赖

**Files:**
- Modify: `.worktrees/trail-skill-system-redesign/README.md`
- Modify: `.worktrees/trail-skill-system-redesign/AGENTS.md`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_output_rendering.py`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_skill_routing_contracts.py`

- [ ] **Step 1: 先写失败测试，锁住新的 public/internal 文案和 `test_output_rendering.py` 的 active guidance 边界**

```python
# tests/test_skill_routing_contracts.py
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_new_skill_topology_without_legacy_skill_paths() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "`trail-hsr` 是对外总入口" in readme
    assert "`trail-<scene>-entry` 是对外场景入口" in readme
    assert "`trail-hsr-advanced` 是内部恢复层" in readme
    assert "`trail-hsr-advanced` 不作为用户入口" in readme
    assert "`trail-cw-entry`" not in readme
    for legacy in (
        "skills/trail-cw",
        "skills/trail-cw-battle-advanced",
        "skills/trail-cw-guide",
        "skills/trail-cw-events",
        "skills/trail-cw-replenish",
        "skills/trail-cw-shop",
        "skills/trail-cw-slots",
    ):
        assert legacy not in readme


def test_agents_forbid_archive_calls_from_any_active_skill() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "任何 active skill 都不得直接或间接调用 archive skill" in agents


def test_output_rendering_active_skill_guidance_only_mentions_hsr_pair() -> None:
    text = (PROJECT_ROOT / "tests" / "test_output_rendering.py").read_text(encoding="utf-8")
    assert '"skills" / "trail-hsr" / "SKILL.md"' in text
    assert '"skills" / "trail-hsr-advanced" / "SKILL.md"' in text
    assert re.search(r"trail-cw(?!-entry)", text) is None
    for legacy in (
        '"skills" / "trail-cw" / "SKILL.md"',
        '"skills" / "trail-cw-battle-advanced" / "SKILL.md"',
        '"skills" / "trail-cw-guide" / "SKILL.md"',
        '"skills" / "trail-cw-events" / "SKILL.md"',
        '"skills" / "trail-cw-replenish" / "SKILL.md"',
        '"skills" / "trail-cw-shop" / "SKILL.md"',
        '"skills" / "trail-cw-slots" / "SKILL.md"',
        'trail-cw-battle-advanced',
        'trail-cw-guide',
        'trail-cw-events',
        'trail-cw-replenish',
        'trail-cw-shop',
        'trail-cw-slots',
    ):
        assert legacy not in text
```

- [ ] **Step 2: 跑定向失败测试，确认仓库文档与 active guidance 还停留在旧拓扑**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_output_rendering.py tests/test_skill_routing_contracts.py -k "new_skill_topology or archive_calls_from_any_active_skill or active_skill_guidance_only_mentions_hsr_pair" -q`

Expected: FAIL。

- [ ] **Step 3: 先把 README / AGENTS / `test_output_rendering.py` 切到新拓扑，再动 skill 文档本体**

```markdown
README / AGENTS / tests 收口后的最低事实：

- `trail-hsr` 是对外总入口。
- `trail-<scene>-entry` 是对外场景入口模式；只有 `status=active` 且 `exposure=public` 的 scene entry 才算当前可直达入口。
- `trail-hsr-advanced` 是内部恢复层，不出现在用户入口清单中。
- 旧 `trail-cw*` 已归为 archive，不再作为 active owner 或推荐入口。
```

- [ ] **Step 4: 重跑定向文档测试，确认旧 active owner 依赖已经先被拆掉**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_output_rendering.py tests/test_skill_routing_contracts.py -k "new_skill_topology or archive_calls_from_any_active_skill or active_skill_guidance_only_mentions_hsr_pair" -q`

Expected: PASS。

### Task 3: 重写 `trail-hsr` 为对外总入口，并把 root-skill eval 收紧到 spec 配额

**Files:**
- Modify: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/SKILL.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/simple-command-surface.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/ocr-and-screenshot.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/references/scene-entry-index.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr/evals/triggers.json`
- Create: `.worktrees/trail-skill-system-redesign/tests/test_skill_structure.py`

- [ ] **Step 1: 写失败测试，锁住中文 description 负向规则、references 内容契约和 trigger quota**

```python
from collections import Counter
from pathlib import Path
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HSR_SKILL = PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md"
HSR_EVALS = PROJECT_ROOT / "skills" / "trail-hsr" / "evals" / "triggers.json"


def test_trail_hsr_description_stays_user_facing_and_excludes_internal_terms() -> None:
    text = HSR_SKILL.read_text(encoding="utf-8")
    assert "description: 当用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》" in text
    for forbidden in ("daemon", "session", "tainted", "trail start", "trail ocr read", "trail input", "货币战争", "进入某个具体玩法"):
        assert forbidden not in text


def test_trail_hsr_references_keep_required_topics() -> None:
    refs = {
        "skills/trail-hsr/references/simple-command-surface.md": ["trail start", "trail ocr read", "trail input"],
        "skills/trail-hsr/references/ocr-and-screenshot.md": ["shot path=", "info read_image_first=1"],
        "skills/trail-hsr/references/scene-entry-index.md": ["scene-entries.yaml", "status=active", "exposure=public"],
    }
    for relative, needles in refs.items():
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, (relative, needle)


def test_trail_hsr_trigger_eval_meets_spec_quota() -> None:
    data = json.loads(HSR_EVALS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)
    assert counts["should-trigger"] >= 8
    assert counts["should-not-trigger"] >= 8
    assert counts["competition"] >= 8
    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        if item["sample_type"] == "competition":
            assert "candidates" in item and len(item["candidates"]) >= 2
            assert item["expected_winner"] in item["candidates"]
            assert set(item["candidates"]) == {"trail-hsr", "trail-cw-entry"}
    assert any(item["sample_type"] == "should-trigger" and item["expected_winner"] == "trail-hsr" and any(word in item["prompt"] for word in ("继续玩星铁", "接管当前星铁任务", "接管并继续推进")) for item in data)
    assert any(item["sample_type"] == "should-not-trigger" and item["expected_winner"] == "none" for item in data)
    assert any(item["sample_type"] == "should-not-trigger" and any(word in item["prompt"] for word in ("PDF", "表格", "邮件")) and item["expected_winner"] == "none" for item in data)
    assert any(item["sample_type"] == "competition" and item["expected_winner"] == "trail-hsr" and any(word in item["prompt"] for word in ("货币战争", "玩法")) for item in data)
    assert any(item["sample_type"] == "competition" and item["expected_winner"] == "trail-cw-entry" and any(word in item["prompt"] for word in ("上线", "直达", "入口")) for item in data)
    assert any(item["expected_winner"] == "trail-hsr" and ("启动失败" in item["prompt"] or "窗口" in item["prompt"]) for item in data)
```

- [ ] **Step 2: 跑失败测试，确认 `trail-hsr` 仍停留在旧 simple-first 形态**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_structure.py -k "trail_hsr" -q`

Expected: FAIL。

- [ ] **Step 3: 重写 `trail-hsr`，并把 `triggers.json` 一次性补到 spec 要求的最小数量**

```markdown
---
name: trail-hsr
description: 当用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》，或从当前局面继续推进游戏任务时使用。
---

# Skill: trail-hsr

## Role

- 这是对外总入口 skill。
- 负责承接“接管并自动游玩星铁”的用户意图。

## Default Workflow

1. 建立或复用当前游戏工作流。
2. 若命中 `active + public` 的 scene entry，则把 owner 交给该 scene entry。
3. 若未命中，则继续由 `trail-hsr` 作为 owner 推进。

## Scene Entry Index

- scene entry 只看 `skills/registry/scene-entries.yaml`。
- `planned` scene 只能回退到 `trail-hsr`，不得回退 archive skill。

## Escalate to Advanced When

- 只在上层无法继续推进时升级。
- 升级契约统一引用 `skills/shared/escalation-contract.md`。

## Reference Map

- `references/simple-command-surface.md`
- `references/ocr-and-screenshot.md`
- `references/scene-entry-index.md`
```

```json
要求：`skills/trail-hsr/evals/triggers.json` 最终必须至少包含 24 条样本，其中
- `should-trigger` >= 8
- `should-not-trigger` >= 8
- `competition` >= 8

并且至少覆盖：
- 总入口意图
- 非游戏任务误触发排除
- `trail-hsr` vs `trail-cw-entry` 的 planned-scene fallback
- future active scene-entry winner contract
- “启动失败/窗口异常”这类用户表述仍应先落到 `trail-hsr` 而不是 `trail-hsr-advanced`

每条样本都必须至少包含 `prompt`、`sample_type`、`expected_winner`；`competition` 样本必须再带 `candidates`。
```

- [ ] **Step 4: 重跑 `trail-hsr` 结构测试，确认 root skill 契约收紧到 spec 标准**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_structure.py -k "trail_hsr" -q`

Expected: PASS。

### Task 4: 重写 `trail-hsr-advanced` 为内部恢复层，并把误触发防护做完整

**Files:**
- Modify: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/SKILL.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/advanced-command-surface.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/recovery-ladder.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/request-status-and-taint.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/references/window-launch.md`
- Create: `.worktrees/trail-skill-system-redesign/skills/trail-hsr-advanced/evals/escalations.json`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_skill_structure.py`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_skill_routing_contracts.py`

- [ ] **Step 1: 写失败测试，锁住 internal description、reference 内容与 `escalations.json` 的全集校验**

```python
from collections import Counter
from pathlib import Path
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADV_SKILL = PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md"
ADV_EVALS = PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "evals" / "escalations.json"


def test_trail_hsr_advanced_is_internal_only() -> None:
    text = ADV_SKILL.read_text(encoding="utf-8")
    assert "description: 当上层 Trail 技能在自动游玩过程中遇到启动失败" in text
    assert "用户希望" not in text
    assert "trail start" not in text


def test_trail_hsr_advanced_references_keep_required_topics() -> None:
    refs = {
        "skills/trail-hsr-advanced/references/advanced-command-surface.md": ["daemon", "window", "state"],
        "skills/trail-hsr-advanced/references/recovery-ladder.md": ["request id", "expected_return_owner"],
        "skills/trail-hsr-advanced/references/request-status-and-taint.md": ["tainted", "unknown result"],
        "skills/trail-hsr-advanced/references/window-launch.md": ["game path", "channel"],
    }
    for relative, needles in refs.items():
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, (relative, needle)


def test_trail_hsr_advanced_escalations_meet_spec_quota_and_schema() -> None:
    data = json.loads(ADV_EVALS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)
    assert counts["direct-user-negative"] >= 8
    assert counts["internal-escalation"] >= 6
    seen_caller_roles = set()
    seen_return_owners = set()
    seen_invoke_callers = set()
    for item in data:
        assert item["sample_type"] in {"direct-user-negative", "internal-escalation"}
        if item["sample_type"] == "direct-user-negative":
            assert {"prompt", "expected_winner"} <= item.keys()
            assert item["expected_winner"] in {"trail-hsr", "trail-cw-entry", "none"}
        else:
            assert {"caller_role", "scenario", "expected_action", "expected_return_owner"} <= item.keys()
            assert item["caller_role"] in {"root_entry", "scene_entry"}
            assert item["expected_action"] in {"invoke-advanced", "stop"}
            if item["caller_role"] == "root_entry":
                assert item["expected_return_owner"] == "trail-hsr"
            if item["caller_role"] == "scene_entry":
                assert item["expected_return_owner"] == "scene_entry"
            seen_caller_roles.add(item["caller_role"])
            seen_return_owners.add(item["expected_return_owner"])
            if item["expected_action"] == "invoke-advanced":
                seen_invoke_callers.add(item["caller_role"])
    assert {"root_entry", "scene_entry"} <= seen_caller_roles
    assert {"trail-hsr", "scene_entry"} <= seen_return_owners
    assert {"root_entry", "scene_entry"} <= seen_invoke_callers
```

- [ ] **Step 2: 跑失败测试，确认 advanced 仍是旧 user-facing 文案和弱 schema**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_structure.py tests/test_skill_routing_contracts.py -k "advanced" -q`

Expected: FAIL。

- [ ] **Step 3: 重写 `trail-hsr-advanced`、补齐 references，并把 `escalations.json` 一次性补到 8+6 的最低配额**

```markdown
---
name: trail-hsr-advanced
description: 当上层 Trail 技能在自动游玩过程中遇到启动失败、环境异常、窗口接管异常或需要恢复运行链路时使用。
---

# Skill: trail-hsr-advanced

## Role

- 这是内部恢复 skill。
- 不作为用户入口。

## When To Use

- 只由 `root_entry` 或 `scene_entry` 升级调用。

## Recovery Ladder

1. 识别异常症状。
2. 执行恢复链路。
3. 把控制权交回 `expected_return_owner`。

## Command Families

- daemon / window / session / screen / image / state

## Stop Conditions

- 无法安全恢复时停止自动重试。

## Reference Map

- `references/advanced-command-surface.md`
- `references/recovery-ladder.md`
- `references/request-status-and-taint.md`
- `references/window-launch.md`
```

```json
要求：`skills/trail-hsr-advanced/evals/escalations.json` 最终必须至少包含 14 条样本，其中
- `direct-user-negative` >= 8，且 `expected_winner` 只能是 public skill 或 `none`
- `internal-escalation` >= 6，且每条都要包含 `caller_role`、`scenario`、`expected_action`、`expected_return_owner`

并且至少覆盖：
- root entry 升级到 advanced
- future scene entry 升级到 advanced
- advanced 完成后回跳到 `trail-hsr`
- advanced 完成后回跳到 `scene_entry`
- direct-user near-miss 不能直接命中 `trail-hsr-advanced`
```

- [ ] **Step 4: 重跑 advanced 定向测试，确认 internal boundary 已经锁死**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_structure.py tests/test_skill_routing_contracts.py -k "advanced" -q`

Expected: PASS。

### Task 5: 以单次原子变更迁出旧 `trail-cw*` 目录，并同步清空 active namespace

**Files:**
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-battle-advanced` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-battle-advanced`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-guide` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-guide`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-events` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-events`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-replenish` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-replenish`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-shop` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-shop`
- Move: `.worktrees/trail-skill-system-redesign/skills/trail-cw-slots` -> `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-slots`
- Create: `.worktrees/trail-skill-system-redesign/docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/README.md`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_skill_registry.py`
- Modify: `.worktrees/trail-skill-system-redesign/tests/test_output_rendering.py`

- [ ] **Step 1: 先补失败测试，锁住 archive 完整性、active skill whitelist 和旧目录消失**

```python
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = PROJECT_ROOT / "docs" / "superpowers" / "archive" / "skills" / "2026-04-21-cw-skill-snapshot"


def test_archive_snapshot_keeps_all_legacy_cw_skill_dirs() -> None:
    expected = {
        "trail-cw",
        "trail-cw-battle-advanced",
        "trail-cw-guide",
        "trail-cw-events",
        "trail-cw-replenish",
        "trail-cw-shop",
        "trail-cw-slots",
    }
    actual = {path.name for path in ARCHIVE_ROOT.iterdir() if path.is_dir()}
    assert expected <= actual
    assert (ARCHIVE_ROOT / "README.md").is_file()
    for name in expected:
        assert (ARCHIVE_ROOT / name / "SKILL.md").is_file()


def test_active_skills_directory_no_longer_contains_legacy_cw_dirs() -> None:
    skills_root = PROJECT_ROOT / "skills"
    children = {path.name for path in skills_root.iterdir() if path.is_dir()}
    assert children == {"trail-hsr", "trail-hsr-advanced", "registry", "shared"}
```

- [ ] **Step 2: 跑失败测试，确认旧目录当前仍在 active namespace**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py -k "archive_snapshot or no_longer_contains_legacy_cw_dirs" -q`

Expected: FAIL。

- [ ] **Step 3: 用 pwsh 兼容命令创建 archive 目录并一次性移动整棵旧 skill 目录**

```powershell
New-Item -ItemType Directory -Force -Path "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot" | Out-Null
git mv "skills/trail-cw" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw"
git mv "skills/trail-cw-battle-advanced" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-battle-advanced"
git mv "skills/trail-cw-guide" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-guide"
git mv "skills/trail-cw-events" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-events"
git mv "skills/trail-cw-replenish" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-replenish"
git mv "skills/trail-cw-shop" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-shop"
git mv "skills/trail-cw-slots" "docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/trail-cw-slots"
```

```markdown
# 2026-04-21 CW Skill Snapshot

- 快照日期：2026-04-21
- 来源：原 `skills/trail-cw*` active skill 集合
- 说明：本目录仅供历史参考，不代表当前 active skill 拓扑。
```

- [ ] **Step 4: 重跑 archive / whitelist 定向测试，确认 active namespace 已被清空**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py -k "archive_snapshot or no_longer_contains_legacy_cw_dirs" -q`

Expected: PASS。

### Task 6: 扫描并 supersede 所有旧活参考文档，再做 rollout gate

**Files:**
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-18-cw-portal-flow-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-18-window-launch-path-resolution-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-19-trail-start-command-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-battle-action-buttons-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-guide-ime-input-fix-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-guide-output-localization-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-help-summary-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-portal-detect-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-cw-slots-read-batch-ocr-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-guide-list-cw-name-filters-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-20-screenshot-first-guidance-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-shop-two-phase-scan-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-slots-batch-place-sell-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/specs/2026-04-21-cw-strategy-commands-design.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-15-trail-daemon.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-18-cw-portal-flow.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-18-window-launch-path-resolution.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-19-trail-start-command.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-battle-action-buttons.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-battle-run.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-guide-output-localization.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-help-summary.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-portal-detect.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-cw-slots-read-batch-ocr.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-guide-list-cw-name-filters.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-20-screenshot-first-guidance.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-shop-two-phase-scan.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-slots-batch-place-sell.md`
- Modify: `.worktrees/trail-skill-system-redesign/docs/superpowers/plans/2026-04-21-cw-strategy-commands.md`
- Create: `.worktrees/trail-skill-system-redesign/docs/superpowers/reviews/2026-04-21-trail-skill-routing-review.md`

- [ ] **Step 1: 先写失败测试，扫描所有非 archive spec/plan，并要求每个 legacy 命中文档都带固定 supersede banner**

```python
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPERSCEDE_LINE = "本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。"
LEGACY_REGEXES = (
    r"skills/trail-cw\b",
    r"skills/trail-cw-battle-advanced\b",
    r"skills/trail-cw-guide\b",
    r"skills/trail-cw-events\b",
    r"skills/trail-cw-replenish\b",
    r"skills/trail-cw-shop\b",
    r"skills/trail-cw-slots\b",
    r"trail-cw-battle-advanced\b",
    r"trail-cw-guide\b",
    r"trail-cw-events\b",
    r"trail-cw-replenish\b",
    r"trail-cw-shop\b",
    r"trail-cw-slots\b",
    r"trail-cw(?!-entry)\b",
)


def test_all_non_archive_legacy_docs_have_fixed_supersede_banner() -> None:
    roots = [PROJECT_ROOT / "docs/superpowers/specs", PROJECT_ROOT / "docs/superpowers/plans"]
    for root in roots:
        for path in root.rglob("*.md"):
            if "archive" in path.parts or path.name in {"2026-04-21-trail-skill-system-redesign.md", "2026-04-21-trail-skill-system-redesign-design.md"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(re.search(pattern, text) for pattern in LEGACY_REGEXES):
                assert SUPERSCEDE_LINE in text, path
```

- [ ] **Step 2: 跑失败测试，确认旧文档当前还没有全部加上固定 supersede 说明**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_routing_contracts.py -k "all_non_archive_legacy_docs_have_fixed_supersede_banner" -q`

Expected: FAIL。

- [ ] **Step 3: 对扫描命中的所有旧活参考文档统一加 banner，并写 rollout gate 评审记录模板**

```markdown
> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。
```

```markdown
# Trail Skill Routing Review

| Prompt | 旧 skill 集合 winner | 新 skill 集合 winner | 预期 winner | 备注 |
| --- | --- | --- | --- | --- |
| 帮我继续玩星铁 |  |  | trail-hsr |  |
| 帮我打开星铁并接管后续流程 |  |  | trail-hsr |  |
| 帮我玩货币战争 |  |  | trail-hsr | planned scene fallback |
| 进入货币战争玩法 |  |  | trail-hsr | planned scene fallback |
| 星铁启动失败了，帮我恢复后继续玩 |  |  | trail-hsr | root-vs-advanced |
| 游戏窗口不对，帮我接起来继续玩 |  |  | trail-hsr | root-vs-advanced |
| 帮我读一下这个 PDF |  |  | none | should-not-trigger |
| 帮我接管当前星铁任务 |  |  | trail-hsr | should-trigger |
```

- [ ] **Step 4: 跑最终验证清单，并把 rollout gate 结论写进 review 记录**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -q`

Expected: PASS。

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_output_rendering.py tests/test_skill_routing_contracts.py -k "new_skill_topology or archive_calls_from_any_active_skill or active_skill_guidance_only_mentions_hsr_pair" -q`

Expected: PASS；不应被计划头部记录的那 2 个既有 screenshot-guidance 基线失败影响。

Run: `rg -nP "trail-cw(?!-entry)|trail-cw-battle-advanced|trail-cw-guide|trail-cw-events|trail-cw-replenish|trail-cw-shop|trail-cw-slots" README.md AGENTS.md skills --glob '!docs/superpowers/archive/**'`

Expected: `README.md`、`AGENTS.md`、`skills/` 不再出现旧 active owner 表述；`tests/` 中为负向断言与扫描保留的 legacy 名称不再纳入这条 `rg` gate；活参考文档的残留由 `test_all_non_archive_legacy_docs_have_fixed_supersede_banner` 单独兜底。

Rollout gate 通过标准：
- `docs/superpowers/reviews/2026-04-21-trail-skill-routing-review.md` 的 8 行 prompt 必须全部填写。
- 每一行的“旧 skill 集合 winner”与“备注”都必须非空；`备注` 至少要记录误触发/升级路径观察，或显式写出“无额外差异”。
- 每一行的“新 skill 集合 winner”必须等于“预期 winner”。
- 记录末尾必须补一段结论，明确写出 `PASS` 或 `FAIL`。
- 只要任一行不匹配，或结论为 `FAIL`，就必须回到 Task 1 / Task 3 / Task 4 修正后再重跑 Task 6 Step 4，不得直接结束实现。
