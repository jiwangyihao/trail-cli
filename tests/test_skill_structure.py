from collections import Counter
from pathlib import Path
import json
import re

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


def _frontmatter_markdown(path: Path) -> tuple[dict, str]:
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


def _load_triggers() -> list[dict]:
    return json.loads(TRIGGERS.read_text(encoding="utf-8"))


def _load_registry() -> dict:
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
    assert "shot path=" in text
    assert "info read_image_first=1" in text
    assert "launched_needs_check" in text
    assert "launched_clicked_enter" in text
    assert "skills/shared/escalation-contract.md" in text
    assert "archive" in text
    assert "references/start-run-status-handling.md" in text


def test_trail_hsr_reference_files_exist_with_required_content() -> None:
    reference_expectations = {
        SIMPLE_COMMAND_SURFACE: [
            "trail start",
            "trail ocr read",
            "trail input",
            "status=",
            "shot path=",
        ],
        OCR_AND_SCREENSHOT: [
            "shot path=",
            "info read_image_first=1",
            "attached",
            "launched_needs_check",
            "launched_clicked_enter",
        ],
        START_RUN_STATUS_HANDLING: [
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
    assert "handoff_skill=trail-cw-entry" in text
    assert "workflow handoff" in text
    assert "registry 是唯一索引来源。" in text
    assert "不要凭 archive、旧文档或历史习惯推断当前入口。" in text
    assert (
        "当 `cw.enter` success 返回 `info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered` 时，应把它视为切到对应 scene entry 的强提示，不是继续沿用旧总入口语义。"
        in text
    )


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
    confirm_section = _markdown_section(text, "What To Confirm First")
    confirm_items = _markdown_bullets(confirm_section)

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
    for expected_tokens in (
        ("开新局", "继续上一局"),
        ("上分", "速刷奖励"),
        ("标准", "超频"),
        ("继续当前职级", "更低", "最高职级", "AX-X"),
        ("攻略/阵容", "投资环境/词条"),
        ("刷开局",),
        ("未收尾进度", "先结算"),
    ):
        assert any(all(token in item for token in expected_tokens) for item in confirm_items)
    assert "A0-1..A8-40" in text
    assert "A7-3" in text
    for forbidden in ("battle_mode=", "difficulty=", "portal refresh", "strategy="):
        assert forbidden not in text


def test_cw_entry_reference_files_exist_with_required_content() -> None:
    gameplay_text = CW_ENTRY_GAMEPLAY_CONCEPTS.read_text(encoding="utf-8")
    mapping_text = CW_ENTRY_PLAYER_LANGUAGE_MAPPING.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    mapping_rows = _markdown_table_rows(mapping_text)
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "开局前必问清单"))
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

    assert mapping_rows[0] == ["玩家常用说法", "官方化名词", "项目内命令或字段"]
    assert len(mapping_body_rows) >= 10
    for expected_phrase in ("A8", "A7-3", "上分", "周常", "奖励", "投资环境", "攻略开局", "最高职级"):
        assert any(expected_phrase in term for term in player_terms)

    assert any("cw enter" in target for target in action_targets)
    assert any("cw start" in target for target in action_targets)
    assert any("portal" in target for target in action_targets)
    assert any("guide" in target for target in action_targets)
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
        ("开新局", "继续上一局"),
        ("上分", "速刷奖励"),
        ("标准", "超频"),
        ("继续当前职级", "更低", "最高职级", "AX-X"),
        ("攻略/阵容", "投资环境/词条"),
        ("刷开局",),
        ("未收尾进度", "先结算"),
    ):
        assert any(all(token in item for token in expected_tokens) for item in checklist_items)

    for fragment in ("cw enter", "cw start", "portal", "guide"):
        assert fragment in checklist_text
    assert "A0-1..A8-40" in mapping_text
    assert "A0-1..A8-40" in checklist_text


def test_cw_entry_top_level_and_checklist_confirmations_stay_in_sync() -> None:
    skill_text = CW_ENTRY_SKILL.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST.read_text(encoding="utf-8")
    skill_items = _markdown_bullets(_markdown_section(skill_text, "What To Confirm First"))
    checklist_items = _markdown_bullets(_markdown_section(checklist_text, "开局前必问清单"))

    confirmation_groups = (
        ("开新局", "继续上一局"),
        ("上分", "速刷奖励"),
        ("标准", "超频"),
        ("继续当前职级", "更低", "最高职级", "AX-X"),
        ("攻略/阵容", "投资环境/词条"),
        ("刷开局",),
        ("未收尾进度", "先结算"),
    )

    for expected_tokens in confirmation_groups:
        assert any(all(token in item for token in expected_tokens) for item in skill_items)
        assert any(all(token in item for token in expected_tokens) for item in checklist_items)

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
    assert any("攻略" in item["prompt"] for item in should_trigger_prompts)

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
        and "货币战争攻略" in item["prompt"]
        and any(keyword in item["prompt"] for keyword in ("解释", "先讲", "不急着开"))
        for item in competition_prompts
    )


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
