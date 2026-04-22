from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "skills" / "registry" / "scene-entries.yaml"
SKILLS_ROOT = PROJECT_ROOT / "skills"
CORE_ACTIVE_SKILL_DIRS = {"trail-hsr", "trail-hsr-advanced", "registry", "shared"}
ARCHIVE_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "superpowers"
    / "archive"
    / "skills"
    / "2026-04-21-cw-skill-snapshot"
)
LEGACY_CW_SKILL_DIRS = [
    "trail-cw",
    "trail-cw-battle-advanced",
    "trail-cw-guide",
    "trail-cw-events",
    "trail-cw-replenish",
    "trail-cw-shop",
    "trail-cw-slots",
]
EXPECTED_ARCHIVE_ROOT_ENTRIES = {"README.md", *LEGACY_CW_SKILL_DIRS}


def _load_registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _active_public_scene_entry_skills(registry_data: dict) -> set[str]:
    return {
        entry["entry_skill"]
        for entry in registry_data.get("entries", [])
        if entry.get("status") == "active" and entry.get("exposure") == "public"
    }


def test_scene_entries_registry_declares_root_entry_and_internal_roles() -> None:
    data = _load_registry()

    assert data["root_entry_skill"] == "trail-hsr"
    assert data["entries"] == [
        {
            "scene": "cw",
            "entry_skill": "trail-cw-entry",
            "status": "active",
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


def test_archive_snapshot_keeps_all_legacy_cw_skill_dirs() -> None:
    assert ARCHIVE_ROOT.is_dir()

    assert {path.name for path in ARCHIVE_ROOT.iterdir()} == EXPECTED_ARCHIVE_ROOT_ENTRIES

    readme = (ARCHIVE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "快照日期：2026-04-21" in readme
    assert "来源：原 skills/trail-cw* active skill 集合" in readme
    assert "本目录仅供历史参考" in readme
    assert "不代表当前 active skill 拓扑" in readme

    for legacy_dir in LEGACY_CW_SKILL_DIRS:
        skill_dir = ARCHIVE_ROOT / legacy_dir
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").is_file()


def test_no_longer_contains_legacy_cw_dirs_allows_active_public_scene_entries_from_registry() -> None:
    registry_data = {
        "entries": [
            {"entry_skill": "trail-cw-entry", "status": "active", "exposure": "public"},
            {"entry_skill": "trail-hsr-sidecar", "status": "active", "exposure": "internal"},
            {"entry_skill": "trail-future-entry", "status": "planned", "exposure": "public"},
        ]
    }

    assert _active_public_scene_entry_skills(registry_data) == {"trail-cw-entry"}


def test_active_skills_directory_no_longer_contains_legacy_cw_dirs() -> None:
    registry_data = _load_registry()
    allowed_active_skill_dirs = CORE_ACTIVE_SKILL_DIRS | _active_public_scene_entry_skills(
        registry_data
    )
    active_skill_dirs = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}

    assert _active_public_scene_entry_skills(registry_data).isdisjoint(LEGACY_CW_SKILL_DIRS)
    assert active_skill_dirs.isdisjoint(LEGACY_CW_SKILL_DIRS)
    assert active_skill_dirs == allowed_active_skill_dirs


def test_cw_entry_registry_is_active_public_scene_entry() -> None:
    data = _load_registry()

    assert data["entries"] == [
        {
            "scene": "cw",
            "entry_skill": "trail-cw-entry",
            "status": "active",
            "exposure": "public",
            "aliases": ["货币战争", "Currency Wars", "cw"],
        }
    ]
    assert _active_public_scene_entry_skills(data) == {"trail-cw-entry"}


def test_cw_entry_active_skill_directory_contract_allows_only_registry_public_entries() -> None:
    registry_data = _load_registry()
    active_skill_dirs = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}

    assert "trail-cw-entry" in _active_public_scene_entry_skills(registry_data)
    assert "trail-cw-entry" in active_skill_dirs
    assert active_skill_dirs == CORE_ACTIVE_SKILL_DIRS | {"trail-cw-entry"}
    assert not any(name.startswith("trail-cw") and name != "trail-cw-entry" for name in active_skill_dirs)
