import ast
from pathlib import Path

import pytest

from trail.runtime.resources import load_scene_aliases, resolve_scene_asset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "trail"
CW_ASSET_ROOT = (SOURCE_ROOT / "scenes" / "cw" / "assets").resolve()
FORBIDDEN_MODULE_PREFIXES = (
    "SRACore",
    "tasks",
    "StarRailAssistant",
)
FORBIDDEN_IMPORTED_NAMES = {"TaskManager"}
FORBIDDEN_SOURCE_TOKENS = (*FORBIDDEN_MODULE_PREFIXES, *FORBIDDEN_IMPORTED_NAMES)


def _iter_python_sources():
    assert SOURCE_ROOT.is_dir()
    return SOURCE_ROOT.rglob("*.py")


def _iter_import_references(file: Path):
    source = file.read_text(encoding="utf-8")
    if not any(token in source for token in FORBIDDEN_SOURCE_TOKENS):
        return
    tree = ast.parse(source, filename=str(file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", {alias.name for alias in node.names}


def test_trail_runtime_does_not_import_sra_runtime_modules():
    for file in _iter_python_sources():
        for module_name, imported_names in _iter_import_references(file):
            assert not module_name.startswith(FORBIDDEN_MODULE_PREFIXES), (
                f"forbidden import {module_name!r} found in {file}"
            )
            if imported_names is not None:
                assert imported_names.isdisjoint(FORBIDDEN_IMPORTED_NAMES), (
                    f"forbidden imported names {imported_names!r} found in {file}"
                )


def test_cw_resources_are_resolved_from_local_package_assets():
    path = resolve_scene_asset("cw", "stage.shop")

    assert path.is_file()
    assert path.is_relative_to(CW_ASSET_ROOT)


def test_all_cw_aliases_point_to_existing_local_assets():
    aliases = load_scene_aliases("cw")

    for _alias, relative_path in aliases.items():
        resolved = (CW_ASSET_ROOT / relative_path).resolve()

        assert resolved.is_file()
        assert resolved.is_relative_to(CW_ASSET_ROOT)


def test_resolve_scene_asset_rejects_assets_outside_local_package_root(monkeypatch):
    monkeypatch.setattr(
        "trail.runtime.resources.load_scene_aliases",
        lambda scene: {"stage.escape": "../escape.png"},
    )

    with pytest.raises(ValueError, match="outside local asset root"):
        resolve_scene_asset("cw", "stage.escape")


def test_resolve_scene_asset_requires_existing_local_asset(monkeypatch):
    monkeypatch.setattr(
        "trail.runtime.resources.load_scene_aliases",
        lambda scene: {"stage.missing": "missing.png"},
    )

    with pytest.raises(FileNotFoundError, match="missing local asset"):
        resolve_scene_asset("cw", "stage.missing")


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


def test_cw_guide_aliases_cover_import_chain():
    aliases = load_scene_aliases("cw")
    required = {
        "guide.strategy",
        "guide.enter_code",
        "guide.confirm",
        "guide.apply",
    }

    assert required.issubset(set(aliases))
