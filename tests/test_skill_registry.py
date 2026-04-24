from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
SKILLS_ROOT = PROJECT_ROOT / "skills"
CORE_ACTIVE_SKILL_DIRS = {"trail-hsr", "registry", "shared"}
ACTIVE_PUBLIC_SCENE_ENTRY_SKILLS = {"trail-cw-entry"}
ACTIVE_PUBLIC_HELPER_SKILL_DIRS = {"trail-cw-guide"}
ACTIVE_INTERNAL_SKILL_DIRS = {"trail-hsr-advanced", "trail-cw-portal"}
ARCHIVE_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "superpowers"
    / "archive"
    / "skills"
    / "2026-04-21-cw-skill-snapshot"
)
ARCHIVED_LEGACY_CW_SKILL_DIRS = [
    "trail-cw",
    "trail-cw-battle-advanced",
    "trail-cw-guide",
    "trail-cw-events",
    "trail-cw-replenish",
    "trail-cw-shop",
    "trail-cw-slots",
]
DISALLOWED_ACTIVE_LEGACY_CW_SKILL_DIRS = {
    "trail-cw",
    "trail-cw-battle-advanced",
    "trail-cw-events",
    "trail-cw-replenish",
    "trail-cw-shop",
    "trail-cw-slots",
}
EXPECTED_ARCHIVE_ROOT_ENTRIES = {"README.md", *ARCHIVED_LEGACY_CW_SKILL_DIRS}


def _load_registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _active_public_scene_entries(registry_data: dict) -> list[dict]:
    return [
        entry
        for entry in registry_data.get("entries", [])
        if entry.get("status") == "active" and entry.get("exposure") == "public"
    ]


def _active_internal_skills(registry_data: dict) -> dict[str, dict]:
    return {
        skill["name"]: skill
        for skill in registry_data.get("internal_skills", [])
        if skill.get("status") == "active" and skill.get("exposure") == "internal"
    }


def test_scene_entries_registry_declares_root_entry_and_only_active_public_scene_entry() -> None:
    data = _load_registry()

    assert data["root_entry_skill"] == "trail-hsr"
    assert _active_public_scene_entries(data) == [
        {
            "scene": "cw",
            "entry_skill": "trail-cw-entry",
            "status": "active",
            "exposure": "public",
            "aliases": ["货币战争", "Currency Wars", "cw"],
        }
    ]


def test_registry_keeps_cw_guide_as_active_public_helper_outside_scene_entries() -> None:
    data = _load_registry()
    entry_skills = {entry["entry_skill"] for entry in data.get("entries", [])}

    assert (SKILLS_ROOT / "trail-cw-guide").is_dir()
    assert ACTIVE_PUBLIC_SCENE_ENTRY_SKILLS == {"trail-cw-entry"}
    assert "trail-cw-guide" not in entry_skills
    assert "trail-cw-guide" not in _active_internal_skills(data)


def test_registry_keeps_cw_portal_as_active_internal_skill() -> None:
    data = _load_registry()
    internal_skills = _active_internal_skills(data)

    assert {"trail-hsr-advanced", "trail-cw-portal"} <= set(internal_skills)
    assert {"root_entry", "scene_entry"} <= set(internal_skills["trail-hsr-advanced"]["caller_roles"])
    assert "scene_entry" in internal_skills["trail-cw-portal"]["caller_roles"]
    assert all(entry["entry_skill"] != "trail-cw-portal" for entry in data.get("entries", []))


def test_active_skill_directories_match_current_topology_without_legacy_cw_skills() -> None:
    active_skill_dirs = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}

    assert active_skill_dirs == (
        CORE_ACTIVE_SKILL_DIRS
        | ACTIVE_PUBLIC_SCENE_ENTRY_SKILLS
        | ACTIVE_PUBLIC_HELPER_SKILL_DIRS
        | ACTIVE_INTERNAL_SKILL_DIRS
    )
    assert active_skill_dirs.isdisjoint(DISALLOWED_ACTIVE_LEGACY_CW_SKILL_DIRS)


def test_archive_snapshot_keeps_all_legacy_cw_skill_dirs() -> None:
    assert ARCHIVE_ROOT.is_dir()
    assert {path.name for path in ARCHIVE_ROOT.iterdir()} == EXPECTED_ARCHIVE_ROOT_ENTRIES

    readme = (ARCHIVE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "快照日期：2026-04-21" in readme
    assert "来源：原 skills/trail-cw* active skill 集合" in readme
    assert "本目录仅供历史参考" in readme
    assert "不代表当前 active skill 拓扑" in readme

    for legacy_dir in ARCHIVED_LEGACY_CW_SKILL_DIRS:
        skill_dir = ARCHIVE_ROOT / legacy_dir
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").is_file()
