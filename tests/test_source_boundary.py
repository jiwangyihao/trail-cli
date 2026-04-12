from pathlib import Path

from trail.runtime.resources import load_scene_aliases, resolve_scene_asset


def test_trail_runtime_does_not_import_sra_task_modules():
    root = Path("trail")
    forbidden = ["tasks.currency_wars", "SRACore.cli", "TaskManager"]

    for file in root.rglob("*.py"):
        text = file.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text


def test_cw_resources_are_resolved_from_local_package_assets():
    path = resolve_scene_asset("cw", "stage.shop")

    assert "trail/scenes/cw/assets" in path.as_posix()


def test_cw_stage_aliases_cover_minimum_stage_set():
    aliases = load_scene_aliases("cw")
    required = {
        "stage.preparation",
        "stage.shop",
        "stage.replenish",
        "stage.encounter",
        "stage.invest",
        "stage.boss_preview",
        "stage.fortune",
        "stage.event",
        "stage.settle",
        "stage.game_over",
    }

    assert required.issubset(set(aliases))
