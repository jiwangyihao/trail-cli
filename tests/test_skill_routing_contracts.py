from collections import Counter
from pathlib import Path
import json
import re

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = PROJECT_ROOT / "README.md"
AGENTS = PROJECT_ROOT / "AGENTS.md"
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
COMPETITION = PROJECT_ROOT / "skills" / "registry" / "routing-competition.json"
ESCALATION_CONTRACT = PROJECT_ROOT / "skills" / "shared" / "escalation-contract.md"
OUTPUT_RENDERING_TEST = PROJECT_ROOT / "tests" / "test_output_rendering.py"
ADVANCED_ESCALATIONS = (
    PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "evals" / "escalations.json"
)
ROUTING_REVIEW = (
    PROJECT_ROOT
    / "docs"
    / "superpowers"
    / "reviews"
    / "2026-04-21-trail-skill-routing-review.md"
)
SUPERSEDE_LINE = "本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。"
SUPERSEDE_BANNER_LINE = f"> {SUPERSEDE_LINE}"
LEGACY_DOC_PATTERNS = (
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
SUPERSEDE_EXEMPT_DOCS = {
    "2026-04-21-trail-skill-system-redesign.md",
    "2026-04-21-trail-skill-system-redesign-design.md",
}
ROUTING_REVIEW_HEADER = "| Prompt | 旧 skill 集合 winner | 新 skill 集合 winner | 预期 winner | 备注 |"
ROUTING_REVIEW_PROMPTS = [
    "帮我继续玩星铁",
    "帮我打开星铁并接管后续流程",
    "帮我玩货币战争",
    "进入货币战争玩法",
    "星铁启动失败了，帮我恢复后继续玩",
    "游戏窗口不对，帮我接起来继续玩",
    "帮我读一下这个 PDF",
    "帮我接管当前星铁任务",
]
ROUTING_REVIEW_METHOD_LINES = (
    "## 方法说明",
    "- 样本来源：固定使用 Task 6 rollout gate 规定的 8 条中文 prompt。",
    "- 旧 skill 集合 winner：按旧 active skill 拓扑（含 `trail-cw*`）判断该 prompt 最可能命中的对外 owner。",
    "- 新 skill 集合 winner：按重设计后的 active/public/internal 拓扑判断；planned scene 继续回退到 `trail-hsr`，`trail-hsr-advanced` 仅作为内部升级层。",
    "- 比对口径：只比较路由 winner，不比较具体命令细节；若新 skill 集合 winner 与“预期 winner”全部一致，则结论记为 `PASS`，否则记为 `FAIL`。",
)


def _scene_entry_skill() -> str:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return registry["entries"][0]["entry_skill"]


def _iter_non_archive_specs_and_plans() -> list[Path]:
    roots = [PROJECT_ROOT / "docs" / "superpowers" / "specs", PROJECT_ROOT / "docs" / "superpowers" / "plans"]
    paths: list[Path] = []
    for root in roots:
        for path in root.rglob("*.md"):
            if "archive" in path.parts or path.name in SUPERSEDE_EXEMPT_DOCS:
                continue
            paths.append(path)
    return paths


def _parse_routing_review_rows(text: str) -> list[list[str]]:
    lines = [line.strip() for line in text.splitlines()]
    header_index = lines.index(ROUTING_REVIEW_HEADER)
    rows: list[list[str]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _routing_review_expected_conclusion(rows: list[list[str]]) -> str:
    return "PASS" if all(row[2] == row[3] for row in rows) else "FAIL"


def _routing_review_declared_conclusion(text: str) -> str:
    match = re.search(r"(?m)^结论：(PASS|FAIL)$", text)
    assert match is not None
    return match.group(1)


def test_routing_review_rollout_gate_requires_new_winner_to_match_expected_winner() -> None:
    rows = [
        [ROUTING_REVIEW_PROMPTS[0], "trail-hsr", "trail-hsr", "trail-hsr", "无额外差异"],
        [ROUTING_REVIEW_PROMPTS[1], "trail-hsr", "trail-hsr-advanced", "trail-hsr", "误触发 advanced"],
    ]

    assert _routing_review_expected_conclusion(rows) == "FAIL"


def test_routing_review_rollout_gate_requires_conclusion_to_match_row_consistency() -> None:
    text = "\n".join(
        [
            "# Trail Skill Routing Review",
            "",
            *ROUTING_REVIEW_METHOD_LINES,
            "",
            ROUTING_REVIEW_HEADER,
            "| --- | --- | --- | --- | --- |",
            f"| {ROUTING_REVIEW_PROMPTS[0]} | trail-hsr | trail-hsr-advanced | trail-hsr | 误触发 advanced |",
            "",
            "结论：PASS",
        ]
    )

    rows = _parse_routing_review_rows(text)

    assert _routing_review_expected_conclusion(rows) == "FAIL"
    assert _routing_review_declared_conclusion(text) == "PASS"


def test_all_non_archive_legacy_docs_have_fixed_supersede_banner() -> None:
    for path in _iter_non_archive_specs_and_plans():
        text = path.read_text(encoding="utf-8")
        if any(re.search(pattern, text) for pattern in LEGACY_DOC_PATTERNS):
            lines = text.splitlines()
            assert lines[0] == SUPERSEDE_BANNER_LINE, path
            assert text.count(SUPERSEDE_BANNER_LINE) == 1, path


def test_routing_review_keeps_auditable_methodology_and_complete_rows() -> None:
    text = ROUTING_REVIEW.read_text(encoding="utf-8")
    table_index = text.index(ROUTING_REVIEW_HEADER)

    for line in ROUTING_REVIEW_METHOD_LINES:
        assert line in text
        assert text.index(line) < table_index

    rows = _parse_routing_review_rows(text)

    assert len(rows) == len(ROUTING_REVIEW_PROMPTS)
    assert [row[0] for row in rows] == ROUTING_REVIEW_PROMPTS

    for prompt, old_winner, new_winner, expected_winner, note in rows:
        assert prompt
        assert old_winner
        assert new_winner
        assert expected_winner
        assert note
        assert new_winner == expected_winner

    assert _routing_review_declared_conclusion(text) == _routing_review_expected_conclusion(rows)
    assert text.rstrip().endswith(("结论：PASS", "结论：FAIL"))


def test_new_skill_topology_is_documented_in_readme() -> None:
    text = README.read_text(encoding="utf-8")
    legacy_skill_lines = [line.strip() for line in text.splitlines() if "trail-cw" in line]

    assert "`trail-hsr` 是对外总入口" in text
    assert (
        "- `trail-<scene>-entry` 是对外场景入口；只有 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前入口。"
        in text
    )
    assert "`trail-hsr-advanced` 是内部恢复层" in text
    assert "`trail-hsr-advanced` 不作为用户入口" in text
    assert "旧 `trail-cw*` 已归为 archive，不再作为 active owner 或推荐入口" in text
    assert legacy_skill_lines == ["- 旧 `trail-cw*` 已归为 archive，不再作为 active owner 或推荐入口。"]
    assert "`trail-cw-entry`" not in text
    assert not re.search(r"skills/trail-cw[\\w-]*", text)


def test_archive_calls_from_any_active_skill_are_forbidden_in_agents() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    assert (
        "- 只有 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前入口出现在 active 文档与测试中。"
        in text
    )
    assert "- `AGENTS.md` 的 active 拓扑说明不得出现 archive skill 名称或 legacy 场景 skill 名称。" in text
    assert "任何 active skill 都不得直接或间接调用 archive skill" in text
    assert not re.search(r"trail-cw(?!-entry)", text)
    assert "当前推荐入口" not in text
    assert "推荐入口" not in text
    assert "active owner" not in text
    assert "默认 owner" not in text


def test_active_skill_guidance_only_mentions_hsr_pair() -> None:
    text = OUTPUT_RENDERING_TEST.read_text(encoding="utf-8")

    assert 'PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md"' in text
    assert 'PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md"' in text
    assert not re.search(r'skills"\s*/\s*"trail-cw(?!-entry)[^\"]*"', text)
    assert not re.search(r"trail-cw(?!-entry)", text)


def test_routing_competition_fixture_has_required_quota_and_cases() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    data = json.loads(COMPETITION.read_text(encoding="utf-8"))

    assert len(data) >= 6

    counts = Counter(item["sample_type"] for item in data)
    assert counts["root-vs-scene"] >= 2
    assert counts["planned-scene-fallback"] >= 2
    assert counts["root-vs-advanced"] >= 2

    root_vs_scene = [item for item in data if item["sample_type"] == "root-vs-scene"]
    planned_scene_fallback = [
        item for item in data if item["sample_type"] == "planned-scene-fallback"
    ]

    for item in data:
        assert {"prompt", "sample_type", "expected_winner", "candidates"} <= item.keys()
        assert len(item["candidates"]) >= 2
        assert item["expected_winner"] in item["candidates"]
        if item["sample_type"] in {"root-vs-scene", "planned-scene-fallback"}:
            assert set(item["candidates"]) == {"trail-hsr", "trail-cw-entry"}
        if item["sample_type"] == "root-vs-advanced":
            assert set(item["candidates"]) == {"trail-hsr", "trail-hsr-advanced"}
            assert item["expected_winner"] == "trail-hsr"
        if item["sample_type"] == "planned-scene-fallback":
            assert item["expected_winner"] == "trail-hsr"

    assert all(item["expected_winner"] == "trail-hsr" for item in planned_scene_fallback)
    assert any(item["expected_winner"] == "trail-hsr" for item in root_vs_scene)
    assert any(item["expected_winner"] == "trail-cw-entry" for item in root_vs_scene)

    active_public_entries = [
        entry
        for entry in registry["entries"]
        if entry["status"] == "active" and entry["exposure"] == "public"
    ]
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

    assert (
        "- `caller_roles`: 只有 `root_entry` 与 `scene_entry` 可以升级调用 `trail-hsr-advanced`。"
        in text
    )
    assert "- 升级目标固定为 `trail-hsr-advanced`。" in text
    assert (
        "- 调用方必须携带：失败症状、最近动作、可复用上下文、`expected_return_owner`。"
        in text
    )
    assert "- advanced 完成恢复后，控制权必须回到 `expected_return_owner`。" in text
    assert "- 任何 active skill 都不得直接或间接调用 archive skill。" in text


def test_trail_hsr_advanced_escalations_meet_spec_quota_and_schema() -> None:
    data = json.loads(ADVANCED_ESCALATIONS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)
    scene_entry_skill = _scene_entry_skill()
    seen_caller_roles = set()
    seen_return_owners = set()
    seen_return_owner_skills = set()
    seen_invoke_callers = set()

    assert len(data) >= 14
    assert counts["direct-user-negative"] >= 8
    assert counts["internal-escalation"] >= 6

    for item in data:
        assert item["sample_type"] in {"direct-user-negative", "internal-escalation"}
        if item["sample_type"] == "direct-user-negative":
            assert {"prompt", "expected_winner"} <= item.keys()
            assert item["expected_winner"] in {"trail-hsr", "trail-cw-entry", "none"}
            continue

        assert {
            "caller_role",
            "scenario",
            "expected_action",
            "expected_return_owner",
            "expected_return_owner_skill",
        } <= item.keys()
        assert item["caller_role"] in {"root_entry", "scene_entry"}
        assert item["expected_action"] in {"invoke-advanced", "stop"}
        if item["caller_role"] == "root_entry":
            assert item["expected_return_owner"] == "trail-hsr"
            assert item["expected_return_owner_skill"] == "trail-hsr"
        if item["caller_role"] == "scene_entry":
            assert item["expected_return_owner"] == "scene_entry"
            assert item["expected_return_owner_skill"] == scene_entry_skill
            assert item["expected_return_owner_skill"] != "scene_entry"

        seen_caller_roles.add(item["caller_role"])
        seen_return_owners.add(item["expected_return_owner"])
        seen_return_owner_skills.add(item["expected_return_owner_skill"])
        if item["expected_action"] == "invoke-advanced":
            seen_invoke_callers.add(item["caller_role"])

    assert {"root_entry", "scene_entry"} <= seen_caller_roles
    assert {"trail-hsr", "scene_entry"} <= seen_return_owners
    assert {"trail-hsr", scene_entry_skill} <= seen_return_owner_skills
    assert {"root_entry", "scene_entry"} <= seen_invoke_callers
