from collections import Counter
from pathlib import Path
import json
import re
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAIL_HSR_SKILL = PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md"
SIMPLE_COMMAND_SURFACE = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr"
    / "references"
    / "simple-command-surface.md"
)
OCR_AND_SCREENSHOT = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr"
    / "references"
    / "ocr-and-screenshot.md"
)
START_RUN_STATUS_HANDLING = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr"
    / "references"
    / "start-run-status-handling.md"
)
START_RUN_WORLD_REFERENCE_IMAGE = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr"
    / "references"
    / "04-world-chaoluguan.jpg"
)
SCENE_ENTRY_INDEX = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr"
    / "references"
    / "scene-entry-index.md"
)
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
TRIGGERS = PROJECT_ROOT / "skills" / "trail-hsr" / "evals" / "triggers.json"
TRAIL_HSR_ADVANCED_SKILL = PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md"
ADVANCED_COMMAND_SURFACE = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "advanced-command-surface.md"
)
RECOVERY_LADDER = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "recovery-ladder.md"
)
REQUEST_STATUS_AND_TAINT = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "request-status-and-taint.md"
)
WINDOW_LAUNCH = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "window-launch.md"
)
CW_ENTRY_SKILL = PROJECT_ROOT / "skills" / "trail-cw-entry" / "SKILL.md"
CW_ENTRY_GAMEPLAY_CONCEPTS = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-entry"
    / "references"
    / "gameplay-concepts.md"
)
CW_ENTRY_PLAYER_LANGUAGE_MAPPING = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-entry"
    / "references"
    / "player-language-mapping.md"
)
CW_ENTRY_CONFIRMATION_CHECKLIST = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-entry"
    / "references"
    / "confirmation-checklist.md"
)
CW_ENTRY_TRIGGERS = (
    PROJECT_ROOT / "skills" / "trail-cw-entry" / "evals" / "triggers.json"
)
CW_GUIDE_SKILL = PROJECT_ROOT / "skills" / "trail-cw-guide" / "SKILL.md"
CW_GUIDE_SELECTION_CRITERIA = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-guide"
    / "references"
    / "guide-selection-criteria.md"
)
CW_GUIDE_COMMAND_SURFACE = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-guide"
    / "references"
    / "command-surface.md"
)
CW_GUIDE_CONFIRMATION_CHECKLIST = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-guide"
    / "references"
    / "confirmation-checklist.md"
)
CW_GUIDE_TRIGGERS = (
    PROJECT_ROOT / "skills" / "trail-cw-guide" / "evals" / "triggers.json"
)
CW_PORTAL_SKILL = PROJECT_ROOT / "skills" / "trail-cw-portal" / "SKILL.md"
CW_PORTAL_COMMAND_SURFACE = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-portal"
    / "references"
    / "portal-command-surface.md"
)
CW_PORTAL_SELECTION_RULES = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-portal"
    / "references"
    / "portal-selection-rules.md"
)
CW_PORTAL_REFRESH_POLICY = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-portal"
    / "references"
    / "portal-refresh-policy.md"
)
CW_PORTAL_TRIGGERS = (
    PROJECT_ROOT / "skills" / "trail-cw-portal" / "evals" / "triggers.json"
)
CW_PREP_SKILL = PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md"
CW_PREP_COMMAND_SURFACE = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-prep"
    / "references"
    / "command-surface.md"
)
CW_PREP_STAGE_BOUNDARIES = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-prep"
    / "references"
    / "stage-boundaries.md"
)
CW_PREP_TRIGGERS = (
    PROJECT_ROOT / "skills" / "trail-cw-prep" / "evals" / "triggers.json"
)
CW_EVENT_UNKNOWN_SKILL = PROJECT_ROOT / "skills" / "trail-cw-event-unknown" / "SKILL.md"
CW_EVENT_UNKNOWN_MANUAL_GUIDE = (
    PROJECT_ROOT
    / "skills"
    / "trail-cw-event-unknown"
    / "references"
    / "manual-resolution-guide.md"
)
CW_EVENT_UNKNOWN_TRIGGERS = (
    PROJECT_ROOT / "skills" / "trail-cw-event-unknown" / "evals" / "triggers.json"
)


def _frontmatter_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, f"missing frontmatter in {path}"
    return yaml.safe_load(match.group(1)), match.group(2)


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"missing section {heading}"
    return match.group(1).strip()


def _markdown_bullets(section_text: str) -> list[str]:
    bullets = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            bullets.append(stripped.removeprefix("- [ ] "))
        elif stripped.startswith("- "):
            bullets.append(stripped.removeprefix("- "))
    return bullets


def _markdown_table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows


def _table_row_by_first_cell(rows: list[list[str]], first_cell: str) -> list[str]:
    for row in rows[2:]:
        if row and row[0] == first_cell:
            return row
    raise AssertionError(f"missing table row starting with {first_cell}")


def _lines_with_tokens(text: str, *tokens: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if all(token in line for token in tokens)
    ]


def _assert_text_contains_in_order(text: str, *snippets: str) -> None:
    cursor = 0
    for snippet in snippets:
        index = text.find(snippet, cursor)
        assert index != -1, snippet
        cursor = index + len(snippet)


def _load_triggers() -> list[dict[str, Any]]:
    return json.loads(TRIGGERS.read_text(encoding="utf-8"))


def _load_registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _scene_entry_skill() -> str:
    return _load_registry()["entries"][0]["entry_skill"]


def test_trail_hsr_frontmatter_uses_user_facing_root_entry_description() -> None:
    frontmatter, _ = _frontmatter_markdown(TRAIL_HSR_SKILL)

    description = frontmatter["description"]
    assert frontmatter["name"] == "trail-hsr"
    assert "当用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》" in description
    for forbidden in (
        "daemon",
        "session",
        "tainted",
        "trail start",
        "trail ocr read",
        "trail input",
        "货币战争",
        "进入某个具体玩法",
    ):
        assert forbidden not in description


def test_trail_hsr_skill_has_required_sections_and_root_entry_semantics() -> None:
    text = TRAIL_HSR_SKILL.read_text(encoding="utf-8")

    for section in (
        "## Role",
        "## Default Workflow",
        "## Scene Entry Index",
        "## Escalate to Advanced When",
        "## Reference Map",
    ):
        assert section in text
    assert "对外总入口" in text
    assert "active" in text and "public" in text
    assert "planned" in text
    assert "trail start" in text
    assert "status" in text
    assert "state=running request=<job_id>" in text
    assert "--request-id <job_id>" in text
    assert "references/async-command-model.md" in text
    assert "shot path=" in text
    assert "info read_image_first=1" in text
    assert "launched_needs_check" in text
    assert "launched_clicked_enter" in text
    assert "skills/shared/escalation-contract.md" in text
    assert "archive" in text
    assert "references/start-run-status-handling.md" in text


def test_trail_hsr_reference_files_exist_with_required_content() -> None:
    async_command_model = PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "async-command-model.md"
    reference_expectations = {
        SIMPLE_COMMAND_SURFACE: [
            "trail start",
            "trail ocr read",
            "trail input",
            "status=",
            "state=running request=<job_id>",
            "--request-id <job_id>",
            "shot path=",
        ],
        async_command_model: [
            "state=running request=<job_id>",
            "重发同一条业务命令",
            "trail --request-id <job_id> start",
            "trail cw battle run --session <session_id> --request-id <job_id>",
            "DAEMON_BUSY",
            "trail daemon request-status --request-id <id>",
            "trail daemon request-result --request-id <job_id>",
            "trail daemon request-cancel --request-id <id>",
            "没有显式 `--request-id`",
        ],
        OCR_AND_SCREENSHOT: [
            "shot path=",
            "info read_image_first=1",
            "attached",
            "launched_needs_check",
            "launched_clicked_enter",
        ],
        START_RUN_STATUS_HANDLING: [
            "state=running request=<job_id>",
            "trail --request-id <job_id> start",
            "attached",
            "launched_needs_check",
            "launched_clicked_enter",
            "shot path=",
            "info read_image_first=1",
            "trail ocr read",
            "点击进入",
            "04-world-chaoluguan.jpg",
        ],
        SCENE_ENTRY_INDEX: ["scene-entries.yaml", "status=active", "exposure=public"],
    }

    for path, expected_fragments in reference_expectations.items():
        text = path.read_text(encoding="utf-8")
        for fragment in expected_fragments:
            assert fragment in text


def test_trail_hsr_simple_command_surface_documents_start_run_status_and_screenshot() -> None:
    text = SIMPLE_COMMAND_SURFACE.read_text(encoding="utf-8")

    assert "trail start" in text
    assert "status" in text
    assert "screenshot" in text or "截图" in text
    assert "shot path=" in text
    assert "session" in text
    assert "不是只有 session" in text or "不再只是 session" in text


def test_trail_hsr_ocr_and_screenshot_requires_reading_images_first_for_start_run_statuses() -> None:
    text = OCR_AND_SCREENSHOT.read_text(encoding="utf-8")

    for status in ("attached", "launched_needs_check", "launched_clicked_enter"):
        assert status in text

    assert "trail start" in text
    assert "shot path=" in text
    assert "info read_image_first=1" in text
    assert "先读" in text and "截图" in text


def test_trail_hsr_start_run_status_handling_reference_covers_triage_contract() -> None:
    text = START_RUN_STATUS_HANDLING.read_text(encoding="utf-8")

    for status in ("attached", "launched_needs_check", "launched_clicked_enter"):
        assert status in text

    assert "trail start" in text
    assert "shot path=" in text
    assert "info read_image_first=1" in text
    assert "先读" in text and "截图" in text
    assert "不能盲目继续" in text or "不要盲目继续" in text
    assert "加载态" in text
    assert "黑屏" in text
    assert "动画" in text
    assert "先继续观察" in text or "先观察" in text
    assert "trail ocr read" in text
    assert "不要额外乱点" in text or "不要乱点" in text
    assert "点击进入" in text
    assert "OCR" in text and "点击" in text
    assert "征求用户意见" in text or "先征求用户意见" in text
    assert START_RUN_WORLD_REFERENCE_IMAGE.is_file()
    assert "04-world-chaoluguan.jpg" in text
    assert "大世界探索态" in text
    assert "启动/过场画面" in text or "过渡态" in text
    assert "即使角色还在落地光柱里，也可以按大世界稳定态处理" in text
    assert "不要把左上角的地点名当成大世界的核心判据" in text
    assert "不要把恰好出现在附近的功能入口" in text
    assert "差分宇宙等模式也可能有小地图" in text
    assert "不要为了“再确认一下 attach 正不正常”而重复跑第二次 `trail start`" in text
    assert "不要把 `trail start` 当成下一步动作本身" in text


def test_scene_entry_index_documents_cw_entry_active_public_and_handoff() -> None:
    text = SCENE_ENTRY_INDEX.read_text(encoding="utf-8")

    assert "scene-entries.yaml" in text
    assert "status=active" in text
    assert "exposure=public" in text
    assert "cw.enter" in text
    assert "trail-cw-entry" in text
    assert "workflow handoff" in text
    assert "registry 是唯一索引来源。" in text
    assert "不要凭 archive、旧文档或历史习惯推断当前入口。" in text
    assert "cw.enter -> trail-cw-entry" in text
    assert "scene entry handoff" in text


def test_scene_entry_index_documents_scene_and_stage_handoffs() -> None:
    text = SCENE_ENTRY_INDEX.read_text(encoding="utf-8")

    assert "cw.enter -> trail-cw-entry" in text
    assert "scene entry handoff" in text
    assert "cw.portal.select -> trail-cw-prep" in text
    assert "阶段" in text or "stage-internal" in text
    assert "不是 direct-user" in text or "不让 `trail-cw-prep` 成为 direct-user scene entry" in text
    assert "当前第一批是 `cw.enter`" not in text


def test_trail_hsr_trigger_fixture_has_required_quota_and_schema() -> None:
    data = _load_triggers()
    scene_entry_skill = _scene_entry_skill()
    counts = Counter(item["sample_type"] for item in data)

    assert len(data) >= 24
    assert counts["should-trigger"] >= 8
    assert counts["should-not-trigger"] >= 8
    assert counts["competition"] >= 8

    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        if item["sample_type"] == "competition":
            assert {"candidates", "scene_status"} <= item.keys()
            assert item["expected_winner"] in item["candidates"]
            assert item["scene_status"] in {"planned", "active"}
            assert set(item["candidates"]) == {"trail-hsr", scene_entry_skill}


def test_trail_hsr_trigger_fixture_covers_representative_prompts() -> None:
    data = _load_triggers()
    scene_entry_skill = _scene_entry_skill()

    assert any(
        item["sample_type"] == "should-trigger"
        and item["expected_winner"] == "trail-hsr"
        and any(
            phrase in item["prompt"]
            for phrase in ("继续玩星铁", "接管当前星铁任务", "接管并继续推进")
        )
        for item in data
    )

    assert any(
        item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
        and "星铁" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("截图", "OCR"))
        for item in data
    )

    assert any(
        item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
        and "星铁" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("攻略", "玩法说明"))
        for item in data
    )

    assert any(
        item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
        and "trail" in item["prompt"].lower()
        and any(keyword in item["prompt"] for keyword in ("daemon status", "status 输出"))
        for item in data
    )

    assert any(
        item["sample_type"] == "competition"
        and item["expected_winner"] == scene_entry_skill
        and item["scene_status"] == "active"
        and set(item["candidates"]) == {"trail-hsr", scene_entry_skill}
        and any(keyword in item["prompt"] for keyword in ("上线", "直达", "入口"))
        for item in data
    )

    assert any(
        item["sample_type"] == "competition"
        and item["expected_winner"] == "trail-hsr"
        and any(keyword in item["prompt"] for keyword in ("启动失败", "窗口异常"))
        and any(keyword in item["prompt"] for keyword in ("继续玩", "继续推进", "接管"))
        for item in data
    )


def test_trail_hsr_trigger_fixture_keeps_explicit_non_game_negative() -> None:
    data = _load_triggers()

    assert any(
        item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
        and any(keyword in item["prompt"] for keyword in ("PDF", "表格", "邮件"))
        for item in data
    )


def test_trail_hsr_trigger_fixture_has_explicit_planned_fallback_competition() -> None:
    data = _load_triggers()
    scene_entry_skill = _scene_entry_skill()

    assert any(
        item["sample_type"] == "competition"
        and item["expected_winner"] == "trail-hsr"
        and item["scene_status"] == "planned"
        and set(item["candidates"]) == {"trail-hsr", scene_entry_skill}
        and any(
            keyword in item["prompt"]
            for keyword in ("未上线", "planned", "尚未 active")
        )
        for item in data
    )


def test_cw_entry_frontmatter_uses_user_facing_scene_entry_description() -> None:
    frontmatter, _ = _frontmatter_markdown(CW_ENTRY_SKILL)

    description = frontmatter["description"]
    assert frontmatter["name"] == "trail-cw-entry"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in description)
    assert "货币战争" in description
    for forbidden in (
        "cw enter",
        "cw start",
        "battle_mode",
        "difficulty",
        "portal",
        "strategy",
        "确认",
    ):
        assert forbidden not in description


def test_cw_entry_skill_has_required_sections_and_command_positioning() -> None:
    text = CW_ENTRY_SKILL.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    confirm_section = _markdown_section(text, "What To Confirm First")
    handoff_section = _markdown_section(text, "Workflow Handoff")
    confirm_items = _markdown_bullets(confirm_section)
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "开局前必问清单"))
    goal_items = confirm_items[:3]
    checklist_goal_items = checklist_items[:3]

    for section in (
        "## Role",
        "## When To Use",
        "## What To Confirm First",
        "## Workflow Handoff",
        "## Reference Map",
    ):
        assert section in text

    assert "cw enter" in text
    assert "cw start" in text
    assert "不是整局 owner" in text or "不是整局的 owner" in text
    assert len(goal_items) == 3
    assert len(checklist_goal_items) == 3
    assert "提升职级" in goal_items[0]
    assert "标准博弈" in goal_items[0]
    assert "highest" in goal_items[0]
    assert "提升职级" in checklist_goal_items[0]
    assert any(token in goal_items[1] for token in ("速刷周常", "速刷奖励"))
    assert "超频博弈" in goal_items[1]
    assert "lowest" in goal_items[1]
    assert any(token in checklist_goal_items[1] for token in ("速刷周常", "速刷奖励"))
    assert "羁绊" in goal_items[2]
    assert "成就" in goal_items[2]
    assert "A5-1" in goal_items[2]
    assert "攻略优先" in goal_items[2]
    assert "羁绊" in checklist_goal_items[2]
    assert "成就" in checklist_goal_items[2]
    assert "A5-1" in checklist_goal_items[2]
    assert "攻略优先" in checklist_goal_items[2]
    assert any(
        "继续当前职级" in item
        and "更低" in item
        and "最高职级" in item
        and "AX-X" in item
        for item in confirm_items
    )
    assert any(
        "进一步限制" in item
        and "投资环境" in item
        and any(token in item for token in ("角色", "阵容倾向"))
        and "尽量满足" in item
        for item in confirm_items
    )
    for expected_tokens in (
        ("提升职级", "标准博弈", "highest"),
        ("速刷周常", "超频博弈", "lowest"),
        ("羁绊", "成就", "A5-1", "攻略优先"),
    ):
        assert any(all(token in item for token in expected_tokens) for item in confirm_items)
    assert "A0-1..A8-40" in text
    assert "A7-3" in text
    assert any(
        "攻略优先" in item
        and "环境优先" in item
        and "羁绊" in item
        and "成就" in item
        and "刷环境时间" in item
        for item in confirm_items
    )
    assert _lines_with_tokens(text, "攻略优先", "先定攻略", "trail-cw-guide")
    assert _lines_with_tokens(text, "攻略优先", "刷开局")
    assert _lines_with_tokens(checklist_text, "攻略优先", "刷开局")
    assert _lines_with_tokens(text, "环境优先", "投资环境页", "portal")
    assert _lines_with_tokens(checklist_text, "环境优先", "投资环境页", "portal")
    assert _lines_with_tokens(text + "\n" + checklist_text, "只负责确认", "刷开局")
    assert all("继续上一局" not in item for item in goal_items)
    assert all("继续上一局" not in item for item in checklist_goal_items)
    conditional_follow_up_items = [
        item for item in confirm_items if "继续" in item and "结算" in item
    ]
    assert conditional_follow_up_items
    assert all(
        any(token in item for token in ("未结束对局", "未收尾进度", "检测到"))
        for item in conditional_follow_up_items
    )
    env_priority_refresh_items = [
        item
        for item in confirm_items + checklist_items
        if "环境优先" in item and "刷开局" in item
    ]
    assert env_priority_refresh_items
    assert all(
        any(token in item for token in ("不再在这里", "投资环境页", "trail-cw-portal"))
        for item in env_priority_refresh_items
    )
    assert any(
        "进一步限制" in item
        and "投资环境" in item
        and any(token in item for token in ("角色", "阵容倾向"))
        and "尽量满足" in item
        for item in checklist_items
    )
    checklist_follow_up_items = [
        item for item in checklist_items if "继续" in item and "结算" in item
    ]
    assert checklist_follow_up_items
    assert all(
        any(token in item for token in ("未结束对局", "未收尾进度", "检测到"))
        for item in checklist_follow_up_items
    )
    assert "允许执行一次 refresh" in text
    assert "cw.portal.select" in text
    assert "自动应用当前已选攻略" in text
    assert "cw guide apply" in handoff_section
    assert "默认第一步" in handoff_section and "不要把" in handoff_section
    assert "自动应用" in handoff_section and any(token in handoff_section for token in ("失败", "失效", "手动重试"))
    for forbidden in ("battle_mode=", "difficulty=", "portal refresh", "strategy="):
        assert forbidden not in text


def test_cw_entry_reference_files_exist_with_required_content() -> None:
    gameplay_text = CW_ENTRY_GAMEPLAY_CONCEPTS.read_text(encoding="utf-8")
    mapping_text = CW_ENTRY_PLAYER_LANGUAGE_MAPPING.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    combined_text = "\n".join((gameplay_text, mapping_text, checklist_text))
    mapping_rows = _markdown_table_rows(mapping_text)
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "开局前必问清单"))
    next_step_section = _markdown_section(checklist_text, "确认后的一般下一步")
    mapping_body_rows = mapping_rows[2:]
    player_terms = [row[0] for row in mapping_body_rows]
    action_targets = [row[2] for row in mapping_body_rows]

    for fragment in (
        "全新角色玩法能力",
        "角色招募规则",
        "金币，等级与商店",
        "装备",
        "投资环境与投资策略",
        "攻略",
        "优势布局",
        "标准博弈",
        "超频博弈",
        "站位",
        "角色赋能",
        "羁绊",
        "星级",
        "金币是",
        "核心经济资源",
        "投资环境",
        "投资策略",
        "整局提供",
        ):
        assert fragment in gameplay_text

    for fragment in (
        "玩家目标",
        "官方职级",
        "项目内难度表达",
        "A0 黑铁 3层",
        "A1 青铜 3层",
        "A2 翠钢 3层",
        "A3 钴银 5层",
        "A4 冰钛 5层",
        "A5 紫金 7层",
        "A6 投资大师 7层",
        "A7 资本帝王 9层",
        "A8 财富造物主 40层",
        "highest",
        "lowest",
        "A5-1",
        "紫金1",
        "资本帝王3",
        "财富造物主10",
        "攻略优先",
        "环境优先",
        "trail-cw-portal",
    ):
        assert fragment in combined_text

    assert mapping_rows[0] == ["玩家常用说法", "官方化名词", "项目内命令或字段"]
    assert len(mapping_body_rows) >= 10
    for expected_phrase in ("A8", "A7-3", "紫金1", "资本帝王3", "财富造物主10", "上分", "周常", "奖励", "投资环境", "攻略开局", "最高职级"):
        assert any(expected_phrase in term for term in player_terms)

    promote_row = _table_row_by_first_cell(mapping_rows, "提升职级")
    reward_row = _table_row_by_first_cell(mapping_rows, "周常/奖励")
    bond_row = _table_row_by_first_cell(mapping_rows, "羁绊/成就")
    purple_gold_row = _table_row_by_first_cell(mapping_rows, "紫金1")
    emperor_row = _table_row_by_first_cell(mapping_rows, "资本帝王3")
    creator_row = _table_row_by_first_cell(mapping_rows, "财富造物主10")
    guide_priority_row = _table_row_by_first_cell(mapping_rows, "攻略优先")
    env_priority_row = _table_row_by_first_cell(mapping_rows, "环境优先")
    reroll_row = _table_row_by_first_cell(mapping_rows, "刷开局")
    resume_row = _table_row_by_first_cell(mapping_rows, "继续上一局")
    view_portal_row = _table_row_by_first_cell(mapping_rows, "看投资环境 / 看词条")

    assert "标准博弈" in promote_row[1]
    assert any(token in promote_row[1] for token in ("更高职级", "职级推进"))
    assert "超频博弈" in reward_row[1]
    assert "标准博弈" in bond_row[1]
    assert "职级难度" in bond_row[1]
    for official_row in (
        promote_row,
        reward_row,
        bond_row,
        purple_gold_row,
        emperor_row,
        creator_row,
        guide_priority_row,
        env_priority_row,
        reroll_row,
        resume_row,
        view_portal_row,
    ):
        assert not any(
            token in official_row[1]
            for token in (
                "cw ",
                "guide ",
                "portal",
                "battle_mode",
                "difficulty=",
                "trail-cw",
            )
        )
    assert "攻略" in guide_priority_row[1]
    assert not any(token in guide_priority_row[1] for token in ("主C", "阵容倾向", "角色偏好"))
    assert "投资环境" in env_priority_row[1]
    assert "投资环境" in view_portal_row[1]
    assert "对局" in resume_row[1]
    assert not any(token in reroll_row[1] for token in ("refresh", "restart", "select"))
    assert not any(token in view_portal_row[1] for token in ("词条", "portal", "refresh"))

    assert "标准博弈" in promote_row[2] and "highest" in promote_row[2]
    assert "超频博弈" in reward_row[2] and "lowest" in reward_row[2]
    assert "标准博弈" in bond_row[2] and "A5-1" in bond_row[2] and "攻略优先" in bond_row[2]
    assert "difficulty=A5-1" in purple_gold_row[2]
    assert "difficulty=A7-3" in emperor_row[2]
    assert "difficulty=A8-10" in creator_row[2]

    assert any("cw enter" in target for target in action_targets)
    assert any("cw start" in target for target in action_targets)
    assert any("portal" in target for target in action_targets)
    assert any("guide" in target for target in action_targets)
    assert any("guide.fetch.cw --select --session <id>" in target for target in action_targets)
    assert any("cw.portal.select" in target and "自动应用当前已选攻略" in target for target in action_targets)
    assert any("cw.guide.apply" in target and "手动兜底" in target for target in action_targets)
    assert any("battle_mode=" in target for target in action_targets)
    assert any("difficulty=current" in target for target in action_targets)
    assert any("difficulty=lowest" in target for target in action_targets)
    assert any("difficulty=highest" in target for target in action_targets)
    assert any("difficulty=AX-X" in target for target in action_targets)
    assert sum(
        1
        for target in action_targets
        if any(keyword in target for keyword in ("cw enter", "cw start", "portal", "guide", "确认后"))
    ) >= len(action_targets) // 2

    for expected_tokens in (
        ("提升职级", "标准博弈", "highest"),
        ("速刷周常", "超频博弈", "lowest"),
        ("羁绊", "成就", "A5-1", "攻略优先"),
        ("继续当前职级", "更低", "最高职级", "AX-X"),
        ("进一步限制", "投资环境", "角色"),
        ("攻略优先", "环境优先"),
        ("未结束对局", "继续", "结算"),
    ):
        assert any(all(token in item for token in expected_tokens) for item in checklist_items)

    assert any("攻略优先" in item and "刷开局" in item for item in checklist_items)
    assert any(
        "环境优先" in item and "投资环境页" in item and "trail-cw-portal" in item
        for item in checklist_items
    )
    assert all(
        "即使这次不刷开局，也允许" not in item for item in checklist_items
    )

    for fragment in ("cw enter", "cw start", "portal", "guide"):
        assert fragment in checklist_text
    assert "当前已选攻略" in checklist_text
    assert "当前已应用攻略" not in checklist_text
    assert "guide.fetch.cw --select --session <id>" in checklist_text
    assert "cw.portal.select" in checklist_text
    assert "自动应用当前已选攻略" in checklist_text
    assert "cw.guide.apply" in checklist_text
    assert "手动兜底" in checklist_text
    assert next_step_section.index("guide.fetch.cw --select --session <id>") < next_step_section.index("cw.portal.select")
    assert next_step_section.index("cw.portal.select") < next_step_section.index("cw.guide.apply")
    assert "A0-1..A8-40" in mapping_text
    assert "A0-1..A8-40" in checklist_text


def test_cw_entry_top_level_and_checklist_confirmations_stay_in_sync() -> None:
    skill_text = CW_ENTRY_SKILL.read_text(encoding="utf-8")
    mapping_text = CW_ENTRY_PLAYER_LANGUAGE_MAPPING.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    skill_items = _markdown_bullets(_markdown_section(skill_text, "What To Confirm First"))
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "开局前必问清单"))

    confirmation_groups = (
        ("提升职级", "标准博弈", "highest"),
        ("速刷周常", "超频博弈", "lowest"),
        ("羁绊", "成就", "A5-1", "攻略优先"),
        ("继续当前职级", "更低", "最高职级", "AX-X"),
        ("进一步限制", "投资环境", "角色"),
        ("攻略优先", "环境优先"),
        ("刷开局", "refresh"),
        ("未结束对局", "继续", "结算"),
    )

    for expected_tokens in confirmation_groups:
        assert any(all(token in item for token in expected_tokens) for item in skill_items)
        assert any(all(token in item for token in expected_tokens) for item in checklist_items)

    assert _lines_with_tokens(skill_text, "攻略优先", "先定攻略", "trail-cw-guide")
    assert _lines_with_tokens(checklist_text, "先定攻略", "trail-cw-guide")
    assert "guide.fetch.cw --select --session <id>" in mapping_text
    assert "guide.fetch.cw --select --session <id>" in checklist_text
    assert "cw.portal.select" in mapping_text
    assert "cw.portal.select" in checklist_text
    assert "自动应用当前已选攻略" in mapping_text
    assert "自动应用当前已选攻略" in checklist_text
    assert "cw.guide.apply" in mapping_text
    assert "cw.guide.apply" in checklist_text
    assert "手动兜底" in mapping_text
    assert "手动兜底" in checklist_text
    assert "A0-1..A8-40" in skill_text
    assert "A0-1..A8-40" in checklist_text


def test_cw_entry_trigger_fixture_has_required_quota_and_schema() -> None:
    data = json.loads(CW_ENTRY_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    assert len(data) >= 18
    assert counts["should-trigger"] >= 6
    assert counts["should-not-trigger"] >= 6
    assert counts["competition"] >= 6

    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        assert item["sample_type"] in {
            "should-trigger",
            "should-not-trigger",
            "competition",
        }
        if item["sample_type"] == "competition":
            assert "candidates" in item
            assert item["expected_winner"] in item["candidates"]
            assert {"trail-hsr", "trail-cw-entry"} <= set(item["candidates"])


def test_cw_entry_trigger_fixture_covers_representative_prompts() -> None:
    data = json.loads(CW_ENTRY_TRIGGERS.read_text(encoding="utf-8"))

    should_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-trigger"
        and item["expected_winner"] == "trail-cw-entry"
    ]
    should_not_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
    ]
    competition_prompts = [item for item in data if item["sample_type"] == "competition"]

    assert any("货币战争" in item["prompt"] for item in should_trigger_prompts)
    assert any("A8" in item["prompt"] for item in should_trigger_prompts)
    assert any("周常" in item["prompt"] for item in should_trigger_prompts)
    assert any("超频博弈" in item["prompt"] for item in should_trigger_prompts)
    assert any("投资环境" in item["prompt"] for item in should_trigger_prompts)
    assert any("羁绊" in item["prompt"] or "成就" in item["prompt"] for item in should_trigger_prompts)

    assert any(
        "cw start" in item["prompt"] or "battle_mode" in item["prompt"]
        for item in should_not_trigger_prompts
    )
    assert any(
        "daemon.request_status" in item["prompt"] or "request id" in item["prompt"]
        for item in should_not_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("Python", "PDF", "表格", "邮件"))
        for item in should_not_trigger_prompts
    )

    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and set(item["candidates"]) >= {"trail-hsr", "trail-cw-entry"}
        and "货币战争" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and {"trail-cw-entry", "trail-cw-guide"} <= set(item["candidates"])
        and "攻略" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("选", "定", "挑"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-hsr"
        and set(item["candidates"]) >= {"trail-hsr", "trail-cw-entry"}
        and "继续玩星铁" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "继续玩星铁" in item["prompt"]
        and "货币战争" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and any(keyword in item["prompt"] for keyword in ("开星铁", "打开星铁", "启动星铁"))
        and "货币战争" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("周常", "奖励"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-hsr"
        and any(keyword in item["prompt"] for keyword in ("启动失败", "窗口异常", "daemon status"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and any(keyword in item["prompt"] for keyword in ("攻略优先", "先定攻略"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and any(keyword in item["prompt"] for keyword in ("环境优先", "看词条", "看路线"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "刷开局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "开新局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "继续上一局" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("结算", "未结束对局", "未收尾进度"))
        for item in competition_prompts
    )


def test_cw_guide_frontmatter_uses_user_facing_guide_selection_description() -> None:
    frontmatter, _ = _frontmatter_markdown(CW_GUIDE_SKILL)

    description = frontmatter["description"]
    assert frontmatter["name"] == "trail-cw-guide"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in description)
    assert "货币战争" in description
    assert "攻略" in description
    assert any(keyword in description for keyword in ("选攻略", "定攻略", "挑攻略"))
    for forbidden in (
        "session",
        "lineup",
        "apply",
        "current",
        "guide list cw",
        "cw guide apply",
    ):
        assert forbidden not in description


def test_cw_guide_skill_has_required_sections_and_selection_entry_semantics() -> None:
    text = CW_GUIDE_SKILL.read_text(encoding="utf-8")
    checklist_text = CW_GUIDE_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    confirm_items = _markdown_bullets(_markdown_section(text, "What To Confirm First"))
    command_items = _markdown_bullets(_markdown_section(text, "Command Surface"))
    command_section = _markdown_section(text, "Command Surface")
    handoff_section = _markdown_section(text, "Workflow Handoff")

    for section in (
        "## Role",
        "## When To Use",
        "## What To Confirm First",
        "## Command Surface",
        "## Workflow Handoff",
        "## Reference Map",
    ):
        assert section in text

    assert "不是整局 owner" in text or "不是整局的 owner" in text
    assert "攻略选择入口" in text or "选攻略入口" in text
    assert any("目标" in item for item in confirm_items)
    assert any("羁绊" in item and "成就" in item for item in confirm_items)
    assert any("版本" in item for item in confirm_items)
    assert any("投资环境" in item for item in confirm_items)
    assert any("主C" in item or "阵容倾向" in item for item in confirm_items)
    assert "public" in text
    assert _lines_with_tokens(text, "interactive", "direct-user")
    assert _lines_with_tokens(text, "无人值守", "投资环境页", "不再继续追问")
    assert len(command_items) <= 6
    assert "guide list cw" in text
    assert "guide fetch cw" in text
    assert "当前已选攻略" in text
    assert "当前已应用攻略" not in text
    assert "guide.fetch.cw --select" in text
    assert text.index("guide.fetch.cw --select") < text.index("cw guide apply")
    assert "cw guide apply" in text
    assert "cw guide current" in text
    assert "真正进入游戏后" in text or "之后才轮到" in text
    assert "当前已选攻略摘要" in command_section
    assert "当前已应用攻略" not in command_section
    assert "完整攻略写入" in text
    assert "额外追踪产物" in text
    assert "current-guide artifact" not in text
    assert "攻略快照ID" not in text
    assert "cw.portal.select" in text
    assert "自动应用" in text
    assert command_section.index("guide.fetch.cw --select") < command_section.index("cw guide apply")
    assert handoff_section.index("guide.fetch.cw --select") < handoff_section.index("cw.portal.select")
    assert handoff_section.index("cw.portal.select") < handoff_section.index("cw guide apply")
    assert _lines_with_tokens(text, "trail-cw-entry", "继承", "只追问缺失项")
    assert any(
        token in "\n".join(_lines_with_tokens(text + "\n" + checklist_text, "继承", "只追问缺失项"))
        for token in ("目标", "羁绊", "环境")
    )
    assert any(
        "trail-cw-portal" in line and "投资环境页" in line and "无人值守" in line
        for line in (text + "\n" + checklist_text).splitlines()
    )
    for forbidden in ("session_id", "lineup_id", "--session", "--lineup-id"):
        assert forbidden not in text


def test_cw_guide_reference_files_exist_with_required_content() -> None:
    selection_text = CW_GUIDE_SELECTION_CRITERIA.read_text(encoding="utf-8")
    command_surface_text = CW_GUIDE_COMMAND_SURFACE.read_text(encoding="utf-8")
    checklist_text = CW_GUIDE_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "选攻略前确认清单"))
    record_section = _markdown_section(command_surface_text, "记录当前攻略")
    entry_section = _markdown_section(command_surface_text, "交回入口")
    fallback_section = _markdown_section(command_surface_text, "真正进入游戏后")
    next_step_section = _markdown_section(checklist_text, "确认后的一般下一步")

    for fragment in (
        "目标",
        "羁绊",
        "成就",
        "版本",
        "投资环境",
        "主C",
        "阵容倾向",
        "标准博弈",
        "超频博弈",
        "#适用超频博弈",
        "不是硬冲突",
        "版本越新越好",
        "不是自动排除旧版本",
        "待收集=1",
        "热门度",
    ):
        assert fragment in selection_text

    for fragment in (
        "guide list cw",
        "guide fetch cw",
        "guide.fetch.cw --select",
        "cw guide apply",
        "cw guide current",
        "真正进入游戏后",
        "不要一上来就 apply",
        "记录当前攻略",
        "cw.portal.select",
        "自动应用",
    ):
        assert fragment in command_surface_text
    assert any(
        "trail-cw-entry" in line
        and any(token in line for token in ("direct-user", "开局前", "cw enter", "cw start"))
        for line in command_surface_text.splitlines()
    )
    assert any(
        "trail-cw-portal" in line
        and any(token in line for token in ("投资环境页", "无人值守", "portal select --card-idx"))
        for line in command_surface_text.splitlines()
    )

    assert "当前已选攻略" in command_surface_text
    assert "当前已应用攻略" not in command_surface_text
    assert "只把完整攻略写入 session，不做 UI 应用，也不创建额外追踪产物" in record_section
    assert "current-guide artifact" not in command_surface_text
    assert "攻略快照ID" not in command_surface_text
    assert "自动应用当前已选攻略" in entry_section
    assert "手动兜底" in fallback_section
    assert command_surface_text.index("guide.fetch.cw --select") < command_surface_text.index("cw guide apply")
    assert command_surface_text.index("guide.fetch.cw --select") < command_surface_text.index("cw.portal.select")

    for expected_tokens in (
        ("目标",),
        ("羁绊", "成就"),
        ("版本",),
        ("投资环境",),
        ("主C", "阵容倾向"),
    ):
        assert any(all(token in item for token in expected_tokens) for item in checklist_items)

    assert _lines_with_tokens(checklist_text, "trail-cw-entry", "继承", "只追问缺失项")
    assert "当前已选攻略" in checklist_text
    assert "当前已应用攻略" not in checklist_text
    assert "guide.fetch.cw --select" in checklist_text
    assert "cw.portal.select" in checklist_text
    assert "自动应用" in checklist_text
    assert next_step_section.index("guide.fetch.cw --select --session <id>") < next_step_section.index("cw.portal.select")
    assert next_step_section.index("cw.portal.select") < next_step_section.index("cw guide apply")


def test_cw_guide_trigger_fixture_has_required_quota_and_schema() -> None:
    data = json.loads(CW_GUIDE_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    assert len(data) >= 18
    assert counts["should-trigger"] >= 6
    assert counts["should-not-trigger"] >= 6
    assert counts["competition"] >= 6

    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        assert item["sample_type"] in {
            "should-trigger",
            "should-not-trigger",
            "competition",
        }
        if item["sample_type"] == "competition":
            assert "candidates" in item
            assert item["expected_winner"] in item["candidates"]
            candidates = set(item["candidates"])
            assert (
                {"trail-cw-entry", "trail-cw-guide"} <= candidates
                or candidates == {"trail-cw-guide", "trail-cw-portal"}
            )

    assert any(
        item["sample_type"] == "competition"
        and set(item["candidates"]) == {"trail-cw-guide", "trail-cw-portal"}
        and item["expected_winner"] == "trail-cw-guide"
        for item in data
    )


def test_cw_guide_trigger_fixture_covers_representative_prompts() -> None:
    data = json.loads(CW_GUIDE_TRIGGERS.read_text(encoding="utf-8"))

    should_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-trigger"
        and item["expected_winner"] == "trail-cw-guide"
    ]
    should_not_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
    ]
    competition_prompts = [item for item in data if item["sample_type"] == "competition"]

    assert any(
        "货币战争攻略" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("选", "定", "挑"))
        for item in should_trigger_prompts
    )
    assert any("羁绊" in item["prompt"] or "成就" in item["prompt"] for item in should_trigger_prompts)
    assert any("版本" in item["prompt"] for item in should_trigger_prompts)
    assert any("投资环境" in item["prompt"] for item in should_trigger_prompts)

    assert any(
        "daemon.request_status" in item["prompt"] or "request id" in item["prompt"]
        for item in should_not_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("guide list cw", "guide fetch cw", "参数", "--portal"))
        for item in should_not_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("继续玩星铁", "接管星铁", "继续推进"))
        for item in should_not_trigger_prompts
    )

    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and {"trail-cw-entry", "trail-cw-guide"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("选攻略", "定攻略", "挑攻略", "攻略优先"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and {"trail-cw-entry", "trail-cw-guide"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("环境优先", "看词条", "看路线", "开局", "先看环境", "直接进货币战争"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-hsr"
        and "继续玩星铁" in item["prompt"]
        and "trail-cw-guide" in item["candidates"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and any(keyword in item["prompt"] for keyword in ("先看环境", "先看投资环境", "先看词条", "先开局"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and {"trail-cw-entry", "trail-cw-guide"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("投资环境页", "环境卡片"))
        and any(keyword in item["prompt"] for keyword in ("按当前环境", "按环境", "别再问我"))
        and any(keyword in item["prompt"] for keyword in ("挑攻略", "选一套攻略"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and "trail-cw-portal" in item["candidates"]
        and any(keyword in item["prompt"] for keyword in ("投资环境页", "环境卡片", "当前环境"))
        and any(keyword in item["prompt"] for keyword in ("按当前环境", "别再问", "直接"))
        and any(keyword in item["prompt"] for keyword in ("挑攻略", "选攻略", "选一套攻略"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and set(item["candidates"]) == {"trail-cw-guide", "trail-cw-portal"}
        and any(keyword in item["prompt"] for keyword in ("投资环境页", "环境卡片", "当前环境"))
        and any(keyword in item["prompt"] for keyword in ("按当前环境", "别再问", "直接"))
        and any(keyword in item["prompt"] for keyword in ("挑攻略", "选攻略", "选一套攻略"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "先看环境" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "先开局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "看词条" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "看路线" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and all(
            keyword in item["prompt"]
            for keyword in ("投资环境页", "别再问", "当前环境", "挑攻略")
        )
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "刷开局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and "开新局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] in {"trail-cw-entry", "trail-hsr"}
        and "继续上一局" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and "帮我选攻略" in item["prompt"]
        for item in competition_prompts
    )


def test_cw_portal_frontmatter_stays_internal_to_portal_page_follow_up() -> None:
    assert CW_PORTAL_SKILL.exists(), f"missing skill file: {CW_PORTAL_SKILL}"

    frontmatter, _ = _frontmatter_markdown(CW_PORTAL_SKILL)

    description = frontmatter["description"]
    assert frontmatter["name"] == "trail-cw-portal"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in description)
    assert "投资环境页" in description
    assert "cw start" in description
    for forbidden in (
        "trail start",
        "direct-user",
        "公共入口",
        "我要玩货币战争",
        "帮我选攻略",
    ):
        assert forbidden not in description


def test_cw_portal_skill_has_required_sections_and_portal_action_contract() -> None:
    text = CW_PORTAL_SKILL.read_text(encoding="utf-8")
    action_items = _markdown_bullets(
        _markdown_section(text, "How To Act On The Portal Page")
    )
    handoff_items = _markdown_bullets(
        _markdown_section(text, "When To Hand Off To trail-cw-guide")
    )

    for section in (
        "## Role",
        "## When To Use",
        "## How To Act On The Portal Page",
        "## When To Hand Off To trail-cw-guide",
        "## Reference Map",
    ):
        assert section in text

    assert "internal" in text or "内部" in text
    assert "不是 direct-user 公共入口" in text or "不做 direct-user 公共入口" in text
    assert "不是 scene entry" in text
    assert any(
        token in text for token in ("不是 owner", "不是整局 owner", "不是整局的 owner")
    )
    assert "cw start" in text
    assert "投资环境页" in text
    assert "trail start" not in text
    assert 5 <= len(action_items) <= 9
    assert 3 <= len(handoff_items) <= 5
    assert _lines_with_tokens(text, "cw start", "portal refresh", "投资环境页")
    assert _lines_with_tokens(text, "选哪套攻略", "选哪个环境")
    assert any(
        "portal detect" in item
        and "portal refresh" in item
        and "portal restart" in item
        and "portal select" in item
        for item in action_items
    )
    assert "portal select --card-idx" in text
    assert "--idx" not in text
    assert any(
        "待收集=1" in item and "收集奖励" in item for item in action_items
    )
    assert _lines_with_tokens(text, "推荐攻略", "热度", "版本", "待收集=1")
    assert any(
        "环境优先" in item and any(token in item for token in ("继续按环境走", "继续按环境"))
        for item in action_items
    )
    assert any(
        "允许刷开局" in item and "refresh" in item and "restart" in item
        for item in action_items
    )
    assert any(
        "不允许刷开局" in item and "一次 refresh" in item and "当前可见环境" in item
        for item in action_items
    )
    assert any(
        (
            "没有带 `待收集=1` 的环境" in item
            or "所有推荐攻略互动数据都 < 5000" in item
        )
        and "refresh" in item
        for item in action_items
    )
    assert any(
        "不允许刷开局" in item
        and "已确认" in item
        and "环境优先" in item
        and "最贴近目标" in item
        for item in action_items
    )
    assert any(
        "不允许刷开局" in item
        and "未确定攻略" in item
        and "必须切到" in item
        and "trail-cw-guide" in item
        and "无人值守" in item
        for item in action_items
    )
    assert all(
        "最接近攻略或最能服务目标的一项" not in item
        and "最接近攻略" not in item
        for item in action_items
    )
    assert any(
        "未确定攻略" in item
        and "trail-cw-guide" in item
        and "无人值守" in item
        for item in handoff_items
    )
    assert any(
        "选定攻略后" in item and "portal select --card-idx" in item
        for item in handoff_items
    )
    assert _lines_with_tokens(text, "版本过旧", "权衡因素")
    assert all(command not in text for command in ("guide list cw", "guide fetch cw", "cw guide current"))


def test_active_cw_skills_document_prep_handoff_without_direct_user_prep() -> None:
    portal_text = CW_PORTAL_SKILL.read_text(encoding="utf-8")
    guide_text = CW_GUIDE_SKILL.read_text(encoding="utf-8")
    entry_text = CW_ENTRY_SKILL.read_text(encoding="utf-8")

    assert "trail-cw-prep" in portal_text
    assert "cw.portal.select" in portal_text or "portal select" in portal_text
    assert "handoff_skill=trail-cw-prep" in portal_text
    assert "guide.fetch.cw --select" in guide_text
    assert "完整攻略" in guide_text and "session" in guide_text
    assert "额外追踪产物" in guide_text
    assert "current-guide artifact" not in guide_text
    assert "攻略快照ID" not in guide_text
    prep_lines = [line for line in entry_text.splitlines() if "trail-cw-prep" in line]
    assert prep_lines
    assert all("direct-user" not in line and "公共入口" not in line for line in prep_lines)


def test_active_cw_skills_document_portal_select_auto_collect_prep_facts() -> None:
    entry_text = CW_ENTRY_SKILL.read_text(encoding="utf-8")
    portal_text = CW_PORTAL_SKILL.read_text(encoding="utf-8")
    portal_command_text = CW_PORTAL_COMMAND_SURFACE.read_text(encoding="utf-8")
    guide_text = CW_GUIDE_SKILL.read_text(encoding="utf-8")
    guide_command_text = CW_GUIDE_COMMAND_SURFACE.read_text(encoding="utf-8")
    guide_checklist_text = CW_GUIDE_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    prep_text = CW_PREP_SKILL.read_text(encoding="utf-8")
    prep_command_text = CW_PREP_COMMAND_SURFACE.read_text(encoding="utf-8")
    scene_index_text = SCENE_ENTRY_INDEX.read_text(encoding="utf-8")
    section_titles = (
        "# 综合信息",
        "# 攻略提示",
        "# 角色信息",
        "# 羁绊信息",
        "# 装备信息",
        "# 装备优先级",
        "# 角色装备需求",
        "# 商店信息",
    )
    portal_consumer_texts = (
        entry_text,
        portal_text,
        portal_command_text,
        guide_text,
        guide_command_text,
        guide_checklist_text,
        prep_text,
        prep_command_text,
        scene_index_text,
    )

    assert "不是整局 owner" in entry_text
    assert "自动收集初始备战 stage/slots/equipment/shop facts" in entry_text
    assert "不需要再立即重复扫描相同事实" in entry_text

    _assert_text_contains_in_order(
        portal_text,
        "`cw.portal.select` / `portal select --card-idx ...` 成功后",
        "自动收集水晶、stage/slots/equipment/shop 预备事实并关闭商店",
        "装备读取发生在 slots fresh 后、shop open 前",
        "先读原始截图",
        "不要手动再跑 slots/equipment/shop 初始扫描",
        "按 handoff 切到 `trail-cw-prep`",
    )
    assert "recover/taint" in portal_text
    assert "不要继续假设已进入 prep 并操作商店" in portal_text
    assert "CW_EQUIPMENT_AUTO_COLLECT_FAILED" in portal_text
    assert "soft warning" in portal_text

    _assert_text_contains_in_order(
        prep_text,
        "若上一条 `cw.portal.select` success 输出含 stage/slots/equipment/shop facts",
        "必须先读截图",
        "制定第一步备战动作",
        "只有事实缺失、stale 或页面已变化时",
        "才主动调用 `trail cw slots read`、`trail cw equipment read` 或 `trail cw shop scan`",
    )

    assert "自动收集 stage/slots/equipment/shop 初始快照" in scene_index_text
    assert "不会改变 `trail-cw-prep` 的 internal 身份" in scene_index_text
    assert "cw.portal.select -> trail-cw-prep" in scene_index_text
    assert "`# ` headings 只是默认文本分组" in scene_index_text
    assert "不参与路由/事实判断" in scene_index_text

    for skill_text in portal_consumer_texts:
        assert "stage/slots/equipment/shop" in skill_text
        assert "先读" in skill_text and "截图" in skill_text
        assert "`# ` 行只是板块标题" in skill_text or "`# ` headings 只是默认文本分组" in skill_text
        assert "action/prefix/fact" in skill_text or "不参与路由/事实判断" in skill_text
        assert "Agent 只消费实体行" in skill_text or "不要把" in skill_text
        assert "trail-cw-prep" in skill_text
        for title in section_titles:
            assert title in skill_text

    assert "接收 `cw.portal.select` handoff 时，优先复用该响应中标题下 facts" in prep_text
    assert "优先复用同次 equipment facts" in prep_text
    assert "缺失/stale/page changed 时才重跑 `cw.equipment.read`" in prep_text
    assert "只有缺失、stale 或页面变化才重扫" in prep_text


def test_cw_portal_reference_files_exist_with_required_content() -> None:
    for path in (
        CW_PORTAL_COMMAND_SURFACE,
        CW_PORTAL_SELECTION_RULES,
        CW_PORTAL_REFRESH_POLICY,
    ):
        assert path.exists(), f"missing reference file: {path}"

    command_surface_text = CW_PORTAL_COMMAND_SURFACE.read_text(encoding="utf-8")
    selection_text = CW_PORTAL_SELECTION_RULES.read_text(encoding="utf-8")
    refresh_text = CW_PORTAL_REFRESH_POLICY.read_text(encoding="utf-8")

    for fragment in (
        "portal detect",
        "portal refresh",
        "portal restart",
        "portal select",
        "portal select --card-idx",
        "cw start",
        "投资环境页",
    ):
        assert fragment in command_surface_text
    assert "trail start" not in command_surface_text
    assert "--idx" not in command_surface_text

    for fragment in (
        "待收集=1",
        "通常表示",
        "收集奖励",
        "环境优先",
        "继续按环境走",
        "未确定攻略",
        "trail-cw-guide",
        "无人值守",
        "当前环境",
        "推荐攻略",
        "热度",
        "版本",
        "选哪套攻略",
        "选哪个环境",
        "版本过旧",
        "权衡因素",
    ):
        assert fragment in selection_text

    for fragment in (
        "允许刷开局",
        "refresh",
        "restart",
        "接近攻略",
        "不允许刷开局",
        "一次 refresh",
        "当前可见环境",
        "环境优先",
        "最贴近目标",
        "trail-cw-guide",
        "无人值守",
        "没有带 `待收集=1` 的环境",
        "所有推荐攻略互动数据都 < 5000",
    ):
        assert fragment in refresh_text
    assert "只有在已确认 `环境优先` 时" in refresh_text
    assert "如果攻略仍未确定" in refresh_text
    assert "如果还没定攻略，就选最能服务目标的一项" not in refresh_text


def test_cw_portal_trigger_fixture_has_required_quota_and_schema() -> None:
    assert CW_PORTAL_TRIGGERS.exists(), f"missing triggers file: {CW_PORTAL_TRIGGERS}"

    data = json.loads(CW_PORTAL_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    assert len(data) >= 18
    assert counts["should-trigger"] >= 6
    assert counts["should-not-trigger"] >= 6
    assert counts["competition"] >= 6

    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        assert item["sample_type"] in {
            "should-trigger",
            "should-not-trigger",
            "competition",
        }
        if item["sample_type"] == "competition":
            assert "candidates" in item
            assert item["expected_winner"] in item["candidates"]
            assert "trail-cw-portal" in item["candidates"]


def test_cw_portal_trigger_fixture_covers_representative_prompts() -> None:
    data = json.loads(CW_PORTAL_TRIGGERS.read_text(encoding="utf-8"))

    should_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-trigger"
        and item["expected_winner"] == "trail-cw-portal"
    ]
    should_not_trigger_prompts = [
        item
        for item in data
        if item["sample_type"] == "should-not-trigger"
        and item["expected_winner"] == "none"
    ]
    competition_prompts = [item for item in data if item["sample_type"] == "competition"]

    assert any(
        any(keyword in item["prompt"] for keyword in ("投资环境页", "环境卡片", "当前环境"))
        for item in should_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("refresh", "restart", "刷开局"))
        for item in should_trigger_prompts
    )
    assert any("待收集=1" in item["prompt"] for item in should_trigger_prompts)
    assert any("环境优先" in item["prompt"] for item in should_trigger_prompts)
    assert any(
        "已确认环境优先" in item["prompt"]
        and "不允许刷开局" in item["prompt"]
        and "refresh 一次" in item["prompt"]
        for item in should_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("cw start 成功", "进到投资环境页", "开局后"))
        for item in should_trigger_prompts
    )

    assert any(
        any(keyword in item["prompt"] for keyword in ("玩货币战争", "货币战争开局入口"))
        for item in should_not_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("帮我选攻略", "先定攻略"))
        for item in should_not_trigger_prompts
    )
    assert any(
        any(keyword in item["prompt"] for keyword in ("daemon.request_status", "request id", "参数怎么填"))
        for item in should_not_trigger_prompts
    )

    assert any(
        item["expected_winner"] == "trail-cw-portal"
        and {"trail-cw-entry", "trail-cw-portal"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("投资环境页", "环境卡片", "刷开局"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and {"trail-cw-portal", "trail-cw-guide"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("按当前环境", "当前环境已出"))
        and any(keyword in item["prompt"] for keyword in ("定攻略", "挑攻略", "选攻略"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-guide"
        and {"trail-cw-portal", "trail-cw-guide"} <= set(item["candidates"])
        and "攻略未定" in item["prompt"]
        and "不允许刷开局" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("当前环境已出", "当前环境卡片已出", "投资环境页已经出来"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-entry"
        and {"trail-cw-entry", "trail-cw-portal"} <= set(item["candidates"])
        and any(keyword in item["prompt"] for keyword in ("货币战争首页", "先决定路线", "刚进首页"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-portal"
        and {"trail-cw-entry", "trail-cw-portal"} <= set(item["candidates"])
        and "已确认环境优先" in item["prompt"]
        and "不允许刷开局" in item["prompt"]
        and "refresh 一次" in item["prompt"]
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-portal"
        and {"trail-cw-portal", "trail-cw-guide"} <= set(item["candidates"])
        and "已确认环境优先" in item["prompt"]
        and "不允许刷开局" in item["prompt"]
        and "refresh 一次" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("当前环境已出", "当前环境卡片已出", "环境卡片已经出来"))
        for item in competition_prompts
    )
    assert any(
        item["expected_winner"] == "trail-cw-portal"
        and {"trail-cw-portal", "trail-cw-guide"} <= set(item["candidates"])
        and "待收集=1" in item["prompt"]
        for item in competition_prompts
    )


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
        "## Stop Conditions",
        "## Reference Map",
    ):
        assert section in text
    assert "## Decision Points Pending Strategy" not in text
    assert "## Strategy Maintenance" not in text
    assert "decision-points-pending-strategy.md" not in text
    assert "strategy-maintenance.md" not in text
    assert "不是 scene entry" in text
    assert "不是 direct-user 公共入口" in text
    assert "不得" in text and "具体经营策略" in text
    assert "skill_info" in text
    assert "运营思路" in text
    assert "动态提醒" in text
    assert "不发明默认优先级" in text
    assert "不得直接或间接调用 archive skill" in text
    assert "trail-cw-shop" not in text
    assert "trail-cw-slots" not in text
    assert "trail-cw-replenish" not in text


def test_cw_prep_reference_files_exist_with_required_content() -> None:
    command_surface = CW_PREP_COMMAND_SURFACE.read_text(encoding="utf-8")
    stage_boundaries = CW_PREP_STAGE_BOUNDARIES.read_text(encoding="utf-8")

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
    assert "trail cw shop buy-slot --session <id> --slot <n> --expect <name>" in command_surface
    assert "trail cw event handle --session <id> --variable-cost-choice cost_up|equipment" in command_surface
    assert "trail cw event reconcile --session <id>" in command_surface
    assert "stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles" in command_surface
    assert "crystals_stale=0" in command_surface
    assert "CW_EVENT_LV999_STATE_STALE" in command_surface
    assert "银狼LV.999" in command_surface
    assert "cost 只在 `item`/`slot` 行" in command_surface
    assert "LV999" in CW_PREP_SKILL.read_text(encoding="utf-8")
    for fragment in (
        "preparation",
        "shop",
        "replenish",
        "invest",
        "encounter",
        "fortune",
        "event",
        "boss_preview",
        "layer_transition",
        "settle",
        "game_over",
        "unknown",
    ):
        assert fragment in stage_boundaries
    boss_preview_lines = _lines_with_tokens(stage_boundaries, "boss_preview")
    layer_transition_lines = _lines_with_tokens(stage_boundaries, "layer_transition")
    assert boss_preview_lines
    assert layer_transition_lines
    assert any("本场对局首领" in line or "BOSS" in line for line in boss_preview_lines)
    assert any("停止" in line or "交给后续" in line for line in boss_preview_lines)
    assert not any("battle flow" in line for line in boss_preview_lines)
    assert not any("trail cw battle run --session <id>" in line for line in boss_preview_lines)
    assert any("battle flow" in line for line in layer_transition_lines)
    assert any("trail cw battle run --session <id>" in line for line in layer_transition_lines)
    assert any("不是普通稳定阶段" in line or "手工中断点" in line for line in layer_transition_lines)
    assert not any("本场对局首领" in line for line in layer_transition_lines)
    unknown_lines = _lines_with_tokens(stage_boundaries, "unknown")
    assert unknown_lines
    assert any("trail-cw-event-unknown" in line for line in unknown_lines)
    assert any("cw.event.reconcile" in line or "event reconcile" in line for line in unknown_lines)
    for forbidden in ("优先买", "必须刷新", "默认卖", "直接出战"):
        assert forbidden not in command_surface


def test_cw_event_unknown_skill_has_required_sections_and_internal_boundary() -> None:
    assert CW_EVENT_UNKNOWN_SKILL.exists(), f"missing skill file: {CW_EVENT_UNKNOWN_SKILL}"
    frontmatter, text = _frontmatter_markdown(CW_EVENT_UNKNOWN_SKILL)

    assert frontmatter["name"] == "trail-cw-event-unknown"
    assert "cw.event.handle" in frontmatter["description"]
    assert "event_type=unknown" in frontmatter["description"]
    assert "direct-user" not in frontmatter["description"]
    for section in (
        "## Role",
        "## When To Use",
        "## Required First Actions",
        "## Manual Resolution Loop",
        "## Reconcile Contract",
        "## Stop Conditions",
        "## Reference Map",
    ):
        assert section in text
    assert "active internal" in text
    assert "不是 scene entry" in text
    assert "不是 direct-user" in text
    assert "不是 owner" in text
    assert "handoff_reason=event_unknown_manual_required" in text
    assert "stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles" in text
    assert "crystals_stale=0" in text
    assert "trail cw event reconcile --session <id>" in text
    assert "不得直接或间接调用 archive skill" in text


def test_cw_event_unknown_reference_and_triggers_cover_manual_reconcile_contract() -> None:
    assert CW_EVENT_UNKNOWN_MANUAL_GUIDE.exists(), f"missing reference file: {CW_EVENT_UNKNOWN_MANUAL_GUIDE}"
    assert CW_EVENT_UNKNOWN_TRIGGERS.exists(), f"missing trigger fixture: {CW_EVENT_UNKNOWN_TRIGGERS}"

    guide = CW_EVENT_UNKNOWN_MANUAL_GUIDE.read_text(encoding="utf-8")
    data = json.loads(CW_EVENT_UNKNOWN_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    for fragment in (
        "shot path=...",
        "info read_image_first=1",
        "next_action=manual",
        "手工处理未知事件",
        "trail cw event reconcile --session <id>",
        "stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles",
        "crystals_stale=0",
        "CW_EVENT_LV999_STATE_STALE",
        "strategy",
        "sell_plan",
    ):
        assert fragment in guide

    assert counts["should-trigger"] >= 3
    assert counts["should-not-trigger"] >= 6
    assert counts["competition"] >= 3
    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        assert item["sample_type"] in {"should-trigger", "should-not-trigger", "competition"}
        if item["sample_type"] == "should-trigger":
            assert item["expected_winner"] == "trail-cw-event-unknown"
            assert "unknown" in item["prompt"] or "next_action=manual" in item["prompt"]
        elif item["sample_type"] == "should-not-trigger":
            assert item["expected_winner"] == "none"
        else:
            assert "candidates" in item
            assert "trail-cw-event-unknown" in item["candidates"]
            if item["expected_winner"] != "none":
                assert item["expected_winner"] in item["candidates"]

    prompts = "\n".join(item["prompt"] for item in data)
    for fragment in (
        "cw.event.handle",
        "event_type=unknown",
        "next_action=manual",
        "cw.event.reconcile",
        "trail-cw-prep",
        "trail-cw-entry",
        "trail-cw-portal",
        "trail-hsr-advanced",
    ):
        assert fragment in prompts


def test_trail_cw_prep_documents_lv999_cost_protocol() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    text = CW_PREP_SKILL.read_text(encoding="utf-8")
    surface = CW_PREP_COMMAND_SURFACE.read_text(encoding="utf-8")

    assert "银狼LV.999 与普通 银狼 完全无关" in agents
    assert "cost 是 slots/shop/sell_plan 的 must-keep 事实" in agents
    assert "cost 只能作为默认文本既有 slot/item 行字段输出" in agents
    assert "guide/info/role_verification 默认文本不得输出 cost" in agents
    assert "银狼LV.999 不使用普通 star-equivalent 1/3/9" in agents
    assert "银狼LV.999 默认文本身份使用 name + cost + star" in text
    assert "cost 是 slots/shop/sell_plan 的 must-keep 事实" in text
    assert "role_id 只作内部资源/诊断，不作 Agent 业务身份" in text
    assert "银狼LV.999 不使用普通 star-equivalent 1/3/9" in surface
    assert "确认升费后确定性同步当前 shop snapshot，不刷新商店、不标 stale" in surface
    assert "trail cw event handle --session <id> --variable-cost-choice cost_up|equipment" in surface
    assert "variable_cost_choice" in surface
    combined = agents + "\n" + text + "\n" + surface
    lv999_lines = [line for line in combined.splitlines() if "银狼LV.999" in line]
    assert lv999_lines
    assert all("1/3/9" not in line or "不使用普通 star-equivalent 1/3/9" in line for line in lv999_lines)


def test_cw_prep_stage_boundaries_keep_boss_preview_and_layer_transition_split() -> None:
    stage_boundaries = CW_PREP_STAGE_BOUNDARIES.read_text(encoding="utf-8")

    boss_preview_lines = _lines_with_tokens(stage_boundaries, "boss_preview")
    layer_transition_lines = _lines_with_tokens(stage_boundaries, "layer_transition")

    assert boss_preview_lines
    assert layer_transition_lines
    assert any("本场对局首领" in line or "BOSS" in line for line in boss_preview_lines)
    assert any("停止" in line or "交给后续" in line for line in boss_preview_lines)
    assert not any("battle flow" in line for line in boss_preview_lines)
    assert not any("trail cw battle run --session <id>" in line for line in boss_preview_lines)
    assert any("battle flow" in line for line in layer_transition_lines)
    assert any("trail cw battle run --session <id>" in line for line in layer_transition_lines)
    assert any("不是普通稳定阶段" in line or "手工中断点" in line for line in layer_transition_lines)
    assert not any("本场对局首领" in line for line in layer_transition_lines)


def test_cw_prep_trigger_fixture_has_required_boundary_cases() -> None:
    assert CW_PREP_TRIGGERS.exists(), f"missing trigger fixture: {CW_PREP_TRIGGERS}"
    data = json.loads(CW_PREP_TRIGGERS.read_text(encoding="utf-8"))
    counts = Counter(item["sample_type"] for item in data)

    assert counts["should-trigger"] >= 4
    assert counts["should-not-trigger"] >= 10
    assert counts["competition"] >= 6
    for item in data:
        assert {"prompt", "sample_type", "expected_winner"} <= item.keys()
        assert item["sample_type"] in {
            "should-trigger",
            "should-not-trigger",
            "competition",
        }
        if item["sample_type"] == "should-trigger":
            assert item["expected_winner"] == "trail-cw-prep"
        elif item["sample_type"] == "should-not-trigger":
            assert item["expected_winner"] == "none"
        else:
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


def test_trail_hsr_advanced_frontmatter_stays_internal_recovery_only() -> None:
    text = TRAIL_HSR_ADVANCED_SKILL.read_text(encoding="utf-8")
    frontmatter, _ = _frontmatter_markdown(TRAIL_HSR_ADVANCED_SKILL)

    description = frontmatter["description"]
    assert frontmatter["name"] == "trail-hsr-advanced"
    assert "description: 当上层 Trail 技能在自动游玩过程中遇到启动失败" in text
    assert "当上层 Trail 技能在自动游玩过程中遇到启动失败" in description
    for forbidden in ("用户希望", "trail start"):
        assert forbidden not in description


def test_trail_hsr_advanced_skill_has_required_sections() -> None:
    text = TRAIL_HSR_ADVANCED_SKILL.read_text(encoding="utf-8")

    for section in (
        "## Role",
        "## When To Use",
        "## Recovery Ladder",
        "## Command Families",
        "## Stop Conditions",
        "## Reference Map",
    ):
        assert section in text

    assert "不作为用户直达入口" in text
    assert "只允许 `root_entry` 或 `scene_entry` 按 `skills/shared/escalation-contract.md` 升级调用" in text
    assert "expected_return_owner_skill" in text
    assert "具体 skill owner" in text


def test_trail_hsr_advanced_describes_verbose_trace_guidance() -> None:
    text = TRAIL_HSR_ADVANCED_SKILL.read_text(encoding="utf-8")

    assert "--verbose" in text
    assert "major action trace" in text
    assert "shared helper 执行证据" in text
    assert "ts=<UTC RFC3339 毫秒时间戳>" in text
    assert "ok=0|1" in text
    assert "没有显式开启 `--verbose` 时，不能假定 stdout 含这些行" in text


def test_trail_hsr_advanced_reference_files_exist_with_required_content() -> None:
    reference_expectations = {
        ADVANCED_COMMAND_SURFACE: [
            "不负责替上层决定 scene owner",
            "只服务内部恢复；一旦状态稳定，就把控制权交回 `expected_return_owner`",
        ],
        RECOVERY_LADDER: [
            "先做 request-status / state 回读，再决定要恢复启动、窗口还是 session",
            "把控制权交回 `expected_return_owner`，并按 `expected_return_owner_skill` 交回具体 skill",
        ],
        REQUEST_STATUS_AND_TAINT: [
            "先记录默认文本里的 `request id`，再查询 request-status",
            "如果 `tainted` 在 reconcile 后仍存在，advanced 应停止自动重试",
        ],
        WINDOW_LAUNCH: [
            "显式提供的 `game path` 应被当作最高优先级",
            "不能在失败后静默切换 channel 继续试",
        ],
    }

    for path, expected_fragments in reference_expectations.items():
        text = path.read_text(encoding="utf-8")
        for fragment in expected_fragments:
            assert fragment in text


def test_trail_hsr_advanced_reference_describes_verbose_trace_guidance() -> None:
    text = ADVANCED_COMMAND_SURFACE.read_text(encoding="utf-8")

    assert "major action trace" in text
    assert "--verbose" in text
    assert "UTC RFC3339" in text
    assert "ok=0|1" in text
    assert "shared helper 执行证据" in text
    assert "trace/context" in text
    assert "`trace` 只承载 finalized helper 动作事件" in text
    assert "`context` 只承载跨动作请求级事实" in text
    assert "debug kind=trace step=ocr ..." in text
