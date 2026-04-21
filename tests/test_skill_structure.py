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


def _frontmatter_markdown(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, f"missing frontmatter in {path}"
    return yaml.safe_load(match.group(1)), match.group(2)


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
    assert "skills/shared/escalation-contract.md" in text
    assert "archive" in text


def test_trail_hsr_reference_files_exist_with_required_content() -> None:
    reference_expectations = {
        SIMPLE_COMMAND_SURFACE: ["trail start", "trail ocr read", "trail input"],
        OCR_AND_SCREENSHOT: ["shot path=", "info read_image_first=1"],
        SCENE_ENTRY_INDEX: ["scene-entries.yaml", "status=active", "exposure=public"],
    }

    for path, expected_fragments in reference_expectations.items():
        text = path.read_text(encoding="utf-8")
        for fragment in expected_fragments:
            assert fragment in text


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
