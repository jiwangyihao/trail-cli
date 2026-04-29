from collections import Counter
from pathlib import Path
import json
import re

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS = PROJECT_ROOT / "AGENTS.md"
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
WORKFLOW_HANDOFFS = PROJECT_ROOT / "skills" / "registry" / "workflow-handoffs.yaml"
CW_GUIDE_SKILL = PROJECT_ROOT / "skills" / "trail-cw-guide" / "SKILL.md"
CW_GUIDE_TRIGGERS = PROJECT_ROOT / "skills" / "trail-cw-guide" / "evals" / "triggers.json"
CW_GUIDE_COMMAND_SURFACE = (
    PROJECT_ROOT / "skills" / "trail-cw-guide" / "references" / "command-surface.md"
)
CW_GUIDE_CONFIRMATION_CHECKLIST = (
    PROJECT_ROOT / "skills" / "trail-cw-guide" / "references" / "confirmation-checklist.md"
)
CW_PORTAL_TRIGGERS = PROJECT_ROOT / "skills" / "trail-cw-portal" / "evals" / "triggers.json"
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
    r"skills/trail-cw/SKILL\.md\b",
    r"skills/trail-cw-battle-advanced\b",
    r"skills/trail-cw-events\b",
    r"skills/trail-cw-replenish\b",
    r"skills/trail-cw-shop\b",
    r"skills/trail-cw-slots\b",
    r"trail-cw(?!(?:-(?:entry|guide|portal|prep)(?![-\w])))\b",
    r"trail-cw-battle-advanced\b",
    r"trail-cw-events\b",
    r"trail-cw-replenish\b",
    r"trail-cw-shop\b",
    r"trail-cw-slots\b",
)
SUPERSEDE_EXEMPT_DOCS = {
    "2026-04-21-trail-skill-system-redesign.md",
    "2026-04-21-trail-skill-system-redesign-design.md",
    "2026-04-26-cw-prep-skill-infrastructure.md",
    "2026-04-26-cw-prep-skill-infrastructure-design.md",
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
LEGACY_CW_SKILL_PATH_PATTERNS = (
    r"skills/trail-cw/SKILL\.md\b",
    r"skills/trail-cw(?!(?:-(?:entry|guide|portal|prep)(?:/|$)))[^/]*(?:/|\b)",
    r"skills/trail-cw-battle-advanced(?:/|\b)",
    r"skills/trail-cw-events(?:/|\b)",
    r"skills/trail-cw-replenish(?:/|\b)",
    r"skills/trail-cw-shop(?:/|\b)",
    r"skills/trail-cw-slots(?:/|\b)",
)
LEGACY_ACTIVE_CW_PATTERN = r"trail-cw(?!(?:-(?:entry|guide|portal|prep)(?![-\w])))\b"


def _lines_with_token(text: str, token: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if token in line]


def _load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _trigger_rows(path: Path, sample_type: str) -> list[dict]:
    return [row for row in _load_json(path) if row["sample_type"] == sample_type]


def _assert_cw_entry_mentions_are_scoped(text: str) -> None:
    lines = _lines_with_token(text, "trail-cw-entry")

    assert lines
    assert any(("当前入口" in line or "当前 active public" in line) and "scene entry" in line for line in lines)
    assert any(("整局 owner" in line or "owner" in line) and "不是" in line for line in lines)


def _assert_cw_guide_mentions_are_scoped(text: str) -> None:
    lines = _lines_with_token(text, "trail-cw-guide")

    assert lines
    assert any(("攻略" in line or "攻略选择" in line) and "public" in line for line in lines)
    assert any("direct-user" in line or "直接" in line for line in lines)
    assert any("scene entry" in line and "不是" in line for line in lines)
    assert any("默认 owner" in line and "不是" in line for line in lines)
    assert any("整局 owner" in line and "不是" in line for line in lines)


def _assert_cw_portal_mentions_are_scoped(text: str) -> None:
    lines = _lines_with_token(text, "trail-cw-portal")

    assert lines
    assert any("internal" in line and ("投资环境页" in line or "portal-page" in line or "portal page" in line) for line in lines)
    assert any("scene entry" in line and "不是" in line for line in lines)
    assert any("owner" in line and "不是" in line for line in lines)
    assert any(
        ("direct-user" in line or "用户入口" in line) and ("不是" in line or "不作为" in line)
        for line in lines
    )


def _assert_expected_workflow_handoff_doc_smoke(text: str) -> None:
    assert "cw.enter -> trail-cw-entry" in text
    assert "cw.portal.select -> trail-cw-prep" in text
    assert "handoff_skill=trail-cw-prep" in text
    assert "handoff_reason=preparation_stage_entered" in text
    assert "handoff_skill=trail-cw-guide" not in text
    assert "handoff_skill=trail-cw-portal" not in text


def _assert_no_legacy_cw_skill_mentions(text: str) -> None:
    for pattern in LEGACY_CW_SKILL_PATH_PATTERNS:
        assert not re.search(pattern, text)
    assert not re.search(LEGACY_ACTIVE_CW_PATTERN, text)


def _competition_rows(path: Path, *candidates: str) -> list[dict]:
    required = set(candidates)
    return [
        row
        for row in _trigger_rows(path, "competition")
        if required <= set(row.get("candidates", []))
    ]


def _context_windows_within_line(
    text: str,
    *,
    context_tokens: tuple[str, ...],
    stop_tokens: tuple[str, ...],
) -> list[str]:
    windows: list[str] = []

    for line in text.splitlines():
        clauses = [clause.strip() for clause in re.split(r"[，；;]", line) if clause.strip()]
        for idx, clause in enumerate(clauses):
            if not any(token in clause for token in context_tokens):
                continue

            parts = [clause]
            cursor = idx + 1
            while cursor < len(clauses) and not any(
                token in clauses[cursor] for token in stop_tokens
            ):
                parts.append(clauses[cursor])
                cursor += 1

            windows.append(" ".join(parts))

    return windows


def _assert_context_binds_return_owner_in_window(
    text: str,
    *,
    context_tokens: tuple[str, ...],
    stop_tokens: tuple[str, ...],
    return_tokens: tuple[str, ...],
    expected_owner: str,
    wrong_owner: str,
) -> None:
    windows = _context_windows_within_line(
        text,
        context_tokens=context_tokens,
        stop_tokens=stop_tokens,
    )

    assert any(
        any(token in window for token in return_tokens) and expected_owner in window
        for window in windows
    )
    assert not any(
        any(token in window for token in return_tokens) and wrong_owner in window
        for window in windows
    )


def test_registry_keeps_trail_cw_entry_as_only_active_public_scene_entry() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    active_public_entries = [
        entry
        for entry in registry["entries"]
        if entry["status"] == "active" and entry["exposure"] == "public"
    ]

    assert active_public_entries == [
        {
            "scene": "cw",
            "entry_skill": "trail-cw-entry",
            "status": "active",
            "exposure": "public",
            "aliases": ["货币战争", "Currency Wars", "cw"],
        }
    ]
    assert {entry["entry_skill"] for entry in registry["entries"]} == {"trail-cw-entry"}
    assert "trail-cw-guide" not in {entry["entry_skill"] for entry in registry["entries"]}
    assert any(
        skill["name"] == "trail-cw-portal"
        and skill["status"] == "active"
        and skill["exposure"] == "internal"
        for skill in registry["internal_skills"]
    )


def test_cw_guide_trigger_artifacts_cover_direct_user_entry_boundary_and_portal_boundary() -> None:
    should_trigger = _trigger_rows(CW_GUIDE_TRIGGERS, "should-trigger")
    should_not_trigger = _trigger_rows(CW_GUIDE_TRIGGERS, "should-not-trigger")
    entry_vs_guide = _competition_rows(CW_GUIDE_TRIGGERS, "trail-cw-entry", "trail-cw-guide")
    guide_vs_portal = _competition_rows(CW_GUIDE_TRIGGERS, "trail-cw-guide", "trail-cw-portal")
    hsr_vs_guide = _competition_rows(CW_GUIDE_TRIGGERS, "trail-hsr", "trail-cw-guide")

    assert any(row["expected_winner"] == "trail-cw-guide" for row in should_trigger)
    assert any("攻略" in row["prompt"] and "货币战争" in row["prompt"] for row in should_trigger)
    assert all(row["expected_winner"] == "none" for row in should_not_trigger)
    assert any("daemon.request_status" in row["prompt"] for row in should_not_trigger)
    assert any(
        "恢复链路" in row["prompt"] or "request id=" in row["prompt"]
        for row in should_not_trigger
    )
    assert any(
        "guide list cw" in row["prompt"] or "guide fetch cw" in row["prompt"]
        for row in should_not_trigger
    )
    assert any("继续玩星铁" in row["prompt"] for row in should_not_trigger)
    assert any(
        row["expected_winner"] == "trail-cw-guide"
        and ("先定攻略" in row["prompt"] or "攻略优先" in row["prompt"] or "先别开" in row["prompt"])
        for row in entry_vs_guide
    )
    assert any(
        row["expected_winner"] == "trail-cw-entry"
        and ("先看环境" in row["prompt"] or "环境优先" in row["prompt"] or "先开局" in row["prompt"])
        for row in entry_vs_guide
    )
    assert any(
        row["expected_winner"] == "trail-cw-guide"
        and ("当前环境" in row["prompt"] or "投资环境页" in row["prompt"])
        for row in guide_vs_portal
    )
    assert any(
        row["expected_winner"] == "trail-hsr" and "继续玩星铁" in row["prompt"]
        for row in hsr_vs_guide
    )


def test_cw_guide_artifacts_lock_return_owner_semantics() -> None:
    artifact_texts = {
        "SKILL.md": CW_GUIDE_SKILL.read_text(encoding="utf-8"),
        "references/command-surface.md": CW_GUIDE_COMMAND_SURFACE.read_text(encoding="utf-8"),
        "references/confirmation-checklist.md": CW_GUIDE_CONFIRMATION_CHECKLIST.read_text(
            encoding="utf-8"
        ),
    }

    for name, text in artifact_texts.items():
        _assert_context_binds_return_owner_in_window(
            text,
            context_tokens=("direct-user", "开局前"),
            stop_tokens=("无人值守", "投资环境页"),
            return_tokens=("交回", "控制权交回", "回到", "继续由"),
            expected_owner="trail-cw-entry",
            wrong_owner="trail-cw-portal",
        )
        _assert_context_binds_return_owner_in_window(
            text,
            context_tokens=("无人值守", "投资环境页"),
            stop_tokens=("direct-user", "开局前"),
            return_tokens=("交回", "控制权交回", "回到", "继续由"),
            expected_owner="trail-cw-portal",
            wrong_owner="trail-cw-entry",
        )


def test_cw_portal_trigger_artifacts_cover_internal_only_and_guide_handoff_boundary() -> None:
    should_trigger = _trigger_rows(CW_PORTAL_TRIGGERS, "should-trigger")
    should_not_trigger = _trigger_rows(CW_PORTAL_TRIGGERS, "should-not-trigger")
    entry_vs_portal = _competition_rows(CW_PORTAL_TRIGGERS, "trail-cw-entry", "trail-cw-portal")
    guide_vs_portal = _competition_rows(CW_PORTAL_TRIGGERS, "trail-cw-guide", "trail-cw-portal")

    assert any(
        row["expected_winner"] == "trail-cw-portal"
        and ("cw start" in row["prompt"] or "portal refresh" in row["prompt"] or "投资环境页" in row["prompt"])
        for row in should_trigger
    )
    assert all(row["expected_winner"] == "none" for row in should_not_trigger)
    assert any("daemon.request_status" in row["prompt"] for row in should_not_trigger)
    assert any("guide list cw" in row["prompt"] for row in should_not_trigger)
    assert any(
        "我要玩货币战争" in row["prompt"] or "开局入口" in row["prompt"]
        for row in should_not_trigger
    )
    assert any(
        "选攻略" in row["prompt"] or "先定攻略" in row["prompt"]
        for row in should_not_trigger
    )
    assert any(
        row["expected_winner"] == "trail-cw-entry"
        and ("首页" in row["prompt"] or "还没开到环境页" in row["prompt"])
        for row in entry_vs_portal
    )
    assert any(
        row["expected_winner"] == "trail-cw-portal"
        and ("投资环境页" in row["prompt"] or "refresh" in row["prompt"] or "restart" in row["prompt"])
        for row in entry_vs_portal
    )
    assert any(
        row["expected_winner"] == "trail-cw-guide"
        and ("攻略未定" in row["prompt"] or "按当前环境" in row["prompt"])
        for row in guide_vs_portal
    )
    assert any(
        row["expected_winner"] == "trail-cw-portal"
        and ("待收集=1" in row["prompt"] or "refresh" in row["prompt"] or "restart" in row["prompt"])
        for row in guide_vs_portal
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


def test_workflow_handoff_registry_file_exists() -> None:
    assert WORKFLOW_HANDOFFS.is_file()


def test_workflow_handoff_registry_has_expected_scene_and_stage_mappings() -> None:
    registry = yaml.safe_load(WORKFLOW_HANDOFFS.read_text(encoding="utf-8"))

    assert set(registry) == {"commands"}
    assert set(registry["commands"]) == {"cw.enter", "cw.portal.select"}
    assert registry["commands"]["cw.enter"]["default"] == {
        "handoff_skill": "trail-cw-entry",
        "handoff_strength": "strong",
        "handoff_reason": "scene_entered",
    }
    assert registry["commands"]["cw.portal.select"]["default"] == {
        "handoff_skill": "trail-cw-prep",
        "handoff_strength": "strong",
        "handoff_reason": "preparation_stage_entered",
    }
    assert "statuses" not in registry["commands"]["cw.enter"]
    assert "statuses" not in registry["commands"]["cw.portal.select"]


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


def test_new_skill_topology_is_documented_in_agents() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    _assert_cw_entry_mentions_are_scoped(text)
    _assert_cw_guide_mentions_are_scoped(text)
    _assert_cw_portal_mentions_are_scoped(text)
    assert "scene entry" in text and "status=active" in text and "exposure=public" in text
    assert "`info handoff_skill=... handoff_strength=strong ...`" in text
    assert "下一步 skill 切换信号" in text
    assert "在 `warn`、`ref` 之后追加一行尾行强提示" in text
    assert "该行必须是 success 输出最后一行" in text
    assert "任何 active skill 都不得直接或间接调用 archive skill" in text
    _assert_expected_workflow_handoff_doc_smoke(text)
    _assert_no_legacy_cw_skill_mentions(text)
    assert "当前推荐入口" not in text


def test_active_skill_guidance_only_mentions_current_active_cw_topology() -> None:
    text = OUTPUT_RENDERING_TEST.read_text(encoding="utf-8")

    assert 'PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md"' in text
    assert 'PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md"' in text
    assert not re.search(r'skills"\s*/\s*"trail-cw(?!(?:-(?:entry|guide|portal|prep)(?![-\w])))[^\"]*"', text)
    assert not re.search(r"trail-cw(?!(?:-(?:entry|guide|portal|prep)(?![-\w])))\b", text)


def test_legacy_doc_patterns_do_not_misclassify_active_cw_topology_names() -> None:
    active_names = ["trail-cw-entry", "trail-cw-guide", "trail-cw-portal", "trail-cw-prep"]
    invalid_names = ["trail-cw-prep-extra", "trail-cw-guide-old"]

    for pattern in LEGACY_DOC_PATTERNS:
        regex = re.compile(pattern)
        for name in active_names:
            assert regex.search(name) is None, (pattern, name)
    for name in invalid_names:
        assert any(re.search(pattern, name) for pattern in LEGACY_DOC_PATTERNS), name


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
        "skills/trail-cw-prep-extra/SKILL.md",
        "skills/trail-cw-guide-old/SKILL.md",
    ]

    for path in active_paths:
        assert not any(re.search(pattern, path) for pattern in LEGACY_CW_SKILL_PATH_PATTERNS), path

    for path in legacy_paths:
        assert any(re.search(pattern, path) for pattern in LEGACY_CW_SKILL_PATH_PATTERNS), path


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
