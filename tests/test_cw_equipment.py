from __future__ import annotations

import importlib
import json
from functools import lru_cache
from io import BytesIO

import pytest
from PIL import Image, ImageDraw


def load_equipment_resources_module():
    return importlib.import_module("trail.scenes.cw.equipment_resources")


def test_build_equipment_catalog_includes_advanced_and_basic_without_kind_collision():
    resources = load_equipment_resources_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "进阶装甲",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "category": "4",
                "category_name": "进阶",
                "compose_list": [
                    {
                        "childrens": [
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic.png",
                                "category": "1",
                                "category_name": "基础",
                            },
                            {
                                "id": "base-2",
                                "name": "基础电池",
                                "icon": "https://act-webstatic.mihoyo.com/battery.png",
                                "category": "1",
                                "category_name": "基础",
                            },
                        ]
                    }
                ],
            }
        ],
    }

    catalog = resources.build_cw_equipment_catalog(raw_config)

    assert [item.cache_key for item in catalog] == ["advanced-same-id", "basic-same-id", "basic-base-2"]
    assert [item.kind for item in catalog] == ["advanced", "basic", "basic"]
    assert [item.name for item in catalog] == ["进阶装甲", "基础装甲", "基础电池"]
    assert catalog[0].big_version == "3.2"


def test_build_equipment_recipes_preserves_basic_identity_and_counts():
    resources = load_equipment_resources_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic-a.png",
                            },
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic-a.png",
                            },
                            {
                                "id": "battery",
                                "name": "光能电池",
                                "icon": "https://act-webstatic.mihoyo.com/basic-b.png",
                            },
                        ]
                    }
                ],
            }
        ],
    }

    recipes = resources.build_cw_equipment_recipes(raw_config)

    assert list(recipes) == ["高周波电锯"]
    recipe = recipes["高周波电锯"]
    assert recipe.name == "高周波电锯"
    assert recipe.cache_key == "advanced-same-id"
    assert [(child.name, child.cache_key, child.need) for child in recipe.basics] == [
        ("基础装甲", "basic-same-id", 2),
        ("光能电池", "basic-battery", 1),
    ]


def test_build_equipment_recipes_requires_valid_equipment_list():
    resources = load_equipment_resources_module()

    with pytest.raises(Exception) as exc_info:
        resources.build_cw_equipment_recipes({"rpg_game_big_version": "3.2", "equipment_list": None})

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_CONFIG_INVALID"


def test_build_equipment_catalog_uses_stable_noid_key_for_missing_id():
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [
                {
                    "name": "无 ID 装备",
                    "icon": "https://act-webstatic.mihoyo.com/no-id.png",
                    "compose_list": [],
                }
            ],
        }
    )

    assert len(catalog) == 1
    assert catalog[0].cache_key.startswith("advanced-noid-")
    assert catalog[0].id is None


@lru_cache(maxsize=None)
def _png_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    Image.new("RGBA", (16, 16), color=color).save(output, format="PNG")
    return output.getvalue()


def test_prepare_equipment_icon_cache_downloads_missing_and_reuses_complete_cache(tmp_path):
    resources = load_equipment_resources_module()
    calls: list[str] = []
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    def fetcher(url: str, *, timeout: float, max_bytes: int) -> bytes:
        calls.append(url)
        assert timeout == resources.EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS
        assert max_bytes == resources.EQUIPMENT_ICON_MAX_BYTES
        return _png_bytes()

    first = resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=fetcher)
    second = resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=fetcher)

    assert first == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": False}
    assert second == {"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": False}
    assert calls == ["https://act-webstatic.mihoyo.com/e1.png"]
    manifest = json.loads(
        (tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["items"][0]["cache_key"] == "advanced-e1"
    assert manifest["items"][0]["local_path"] == "icons/advanced-e1.png"


def test_prepare_equipment_icon_cache_requires_safe_big_version(tmp_path):
    resources = load_equipment_resources_module()
    with pytest.raises(Exception) as exc_info:
        resources.build_cw_equipment_catalog({"equipment_list": []})

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_VERSION_MISSING"


def test_prepare_equipment_icon_cache_rejects_cache_root_symlink_escape(tmp_path):
    resources = load_equipment_resources_module()
    external_cache = tmp_path / "external-cache"
    external_cache.mkdir()
    cache_parent = tmp_path / ".trail" / "cache"
    cache_parent.mkdir(parents=True)
    cache_link = cache_parent / "cw-equipment-icons"
    try:
        cache_link.symlink_to(external_cache, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"
    assert not (external_cache / "3.2").exists()


@pytest.mark.parametrize("link_parts", [("3.2",), ("3.2", "icons")])
def test_prepare_equipment_icon_cache_rejects_nested_cache_symlink_escape(tmp_path, link_parts):
    resources = load_equipment_resources_module()
    external_cache = tmp_path / "external-cache"
    external_cache.mkdir()
    cache_root = tmp_path / ".trail" / "cache" / "cw-equipment-icons"
    link_path = cache_root.joinpath(*link_parts)
    link_path.parent.mkdir(parents=True)
    try:
        link_path.symlink_to(external_cache, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"
    assert not any(external_cache.iterdir())


def test_prepare_equipment_icon_cache_rejects_icon_file_symlink_escape(tmp_path):
    resources = load_equipment_resources_module()
    external_icon = tmp_path / "external.png"
    external_icon.write_bytes(_png_bytes())
    icon_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "advanced-e1.png"
    icon_path.parent.mkdir(parents=True)
    try:
        icon_path.symlink_to(external_icon)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes("blue"),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"
    assert external_icon.read_bytes() == _png_bytes()


def test_prepare_equipment_icon_cache_rejects_manifest_file_symlink_escape(tmp_path):
    resources = load_equipment_resources_module()
    external_manifest = tmp_path / "external-manifest.json"
    external_manifest.write_text('{"items": []}', encoding="utf-8")
    manifest_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    try:
        manifest_path.symlink_to(external_manifest)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"


def test_prepare_equipment_icon_cache_rejects_icon_temp_symlink_escape(tmp_path):
    resources = load_equipment_resources_module()
    external_icon = tmp_path / "external.png"
    external_icon.write_bytes(_png_bytes("green"))
    tmp_icon_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "advanced-e1.png.tmp"
    tmp_icon_path.parent.mkdir(parents=True)
    try:
        tmp_icon_path.symlink_to(external_icon)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes("blue"),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"
    assert external_icon.read_bytes() == _png_bytes("green")


def test_prepare_equipment_icon_cache_rejects_manifest_temp_symlink_escape(tmp_path):
    resources = load_equipment_resources_module()
    external_manifest = tmp_path / "external-manifest.json"
    external_manifest.write_text('{"items": []}', encoding="utf-8")
    tmp_manifest_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "manifest.json.tmp"
    tmp_manifest_path.parent.mkdir(parents=True)
    try:
        tmp_manifest_path.symlink_to(external_manifest)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        )

    assert getattr(exc_info.value, "code", None) == "CW_RESOURCE_BUNDLE_INVALID"


def test_prepare_equipment_icon_cache_refresh_redownloads_changed_url_only_with_refresh(tmp_path):
    resources = load_equipment_resources_module()
    first_catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/a.png"}],
        }
    )
    second_catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/b.png"}],
        }
    )
    calls: list[str] = []

    def fetcher(url: str, *, timeout: float, max_bytes: int) -> bytes:
        del timeout, max_bytes
        calls.append(url)
        return _png_bytes("blue")

    resources.prepare_equipment_icon_cache(first_catalog, workspace_root=tmp_path, fetcher=fetcher)
    no_refresh = resources.prepare_equipment_icon_cache(second_catalog, workspace_root=tmp_path, fetcher=fetcher)
    refreshed = resources.prepare_equipment_icon_cache(second_catalog, workspace_root=tmp_path, fetcher=fetcher, refresh=True)

    assert no_refresh["downloaded"] == 0
    assert refreshed["downloaded"] == 1
    assert refreshed["refreshed"] is True
    assert calls == ["https://act-webstatic.mihoyo.com/a.png", "https://act-webstatic.mihoyo.com/b.png"]


def test_prepare_equipment_icon_cache_redownloads_corrupt_file(tmp_path):
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )
    resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=lambda url, timeout, max_bytes: _png_bytes("red"))
    icon_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "advanced-e1.png"
    icon_path.write_text("broken", encoding="utf-8")
    calls: list[str] = []

    result = resources.prepare_equipment_icon_cache(
        catalog,
        workspace_root=tmp_path,
        fetcher=lambda url, timeout, max_bytes: calls.append(url) or _png_bytes("green"),
    )

    assert result["downloaded"] == 1
    assert calls == ["https://act-webstatic.mihoyo.com/e1.png"]


def test_prepare_equipment_icon_cache_no_refresh_reuses_previous_url_when_changed_icon_missing(tmp_path):
    resources = load_equipment_resources_module()
    first_catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/a.png"}],
        }
    )
    second_catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/b.png"}],
        }
    )
    calls: list[str] = []

    def fetcher(url: str, *, timeout: float, max_bytes: int) -> bytes:
        del timeout, max_bytes
        calls.append(url)
        return _png_bytes("blue")

    resources.prepare_equipment_icon_cache(first_catalog, workspace_root=tmp_path, fetcher=fetcher)
    icon_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "advanced-e1.png"
    icon_path.unlink()

    no_refresh = resources.prepare_equipment_icon_cache(second_catalog, workspace_root=tmp_path, fetcher=fetcher)

    assert no_refresh["downloaded"] == 1
    assert calls == ["https://act-webstatic.mihoyo.com/a.png", "https://act-webstatic.mihoyo.com/a.png"]
    manifest = json.loads(
        (tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["items"][0]["icon_url"] == "https://act-webstatic.mihoyo.com/a.png"


def test_read_cw_equipment_with_prebuilt_recognizer_skips_icon_cache(monkeypatch, tmp_path):
    scene = importlib.import_module("trail.scenes.cw.equipment")

    monkeypatch.setattr(
        scene,
        "fetch_cw_raw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("config network must not be called")),
    )
    monkeypatch.setattr(
        scene,
        "prepare_equipment_icon_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("icon cache must not be prepared")),
    )
    monkeypatch.setattr(
        scene,
        "load_cached_equipment_icons",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("icon cache must not be loaded")),
    )
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda *args, **kwargs: [])

    class Runtime:
        def capture_image(self, **kwargs):
            del kwargs
            return Image.new("RGBA", (1920, 1080), "black")

    result = scene.read_cw_equipment(
        Runtime(),
        workspace_root=tmp_path,
        raw_config={"rpg_game_big_version": "3.2", "equipment_list": []},
        recognizer=object(),
    )

    assert result == {
        "count": 0,
        "uncertain": 0,
        "empty": 0,
        "items": [],
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }


def test_read_cw_equipment_with_injected_recognizer_does_not_prepare_or_load_icons(monkeypatch, tmp_path):
    scene = importlib.import_module("trail.scenes.cw.equipment")
    recognition = importlib.import_module("trail.scenes.cw.equipment_recognition")

    class Runtime:
        def capture_image(self, **kwargs):
            del kwargs
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def recognize(self, image):
            del image
            return recognition.EquipmentRecognitionResult(
                candidates=[],
                score=None,
                gap=None,
                uncertain=False,
                empty=True,
            )

    monkeypatch.setattr(
        scene,
        "prepare_equipment_icon_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prepare must not be called")),
    )
    monkeypatch.setattr(
        scene,
        "load_cached_equipment_icons",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("load icons must not be called")),
    )
    monkeypatch.setattr(
        scene,
        "fetch_cw_raw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("raw config must not be fetched")),
    )

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path, recognizer=Recognizer())

    assert result["count"] == 0
def test_safe_segment_rejects_windows_reserved_names_with_extensions():
    resources = load_equipment_resources_module()

    assert resources._safe_segment("CON.txt") != "CON.txt"
    assert resources._safe_segment("LPT1.png") != "LPT1.png"


def test_prepare_equipment_icon_cache_preserves_replace_errors(monkeypatch, tmp_path):
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    def fail_replace(src, dst):
        del src, dst
        raise OSError("replace failed")

    monkeypatch.setattr(resources.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        resources.prepare_equipment_icon_cache(
            catalog,
            workspace_root=tmp_path,
            fetcher=lambda url, timeout, max_bytes: _png_bytes("red"),
        )


def test_prepare_equipment_icon_cache_wraps_download_failures(tmp_path):
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    def fail_fetch(url, *, timeout, max_bytes):
        del url, timeout, max_bytes
        raise OSError("network down")

    with pytest.raises(Exception) as exc_info:
        resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=fail_fetch)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_ICON_DOWNLOAD_FAILED"
    assert isinstance(exc_info.value.__cause__, OSError)


def load_equipment_grid_module():
    return importlib.import_module("trail.scenes.cw.equipment_grid")


def test_equipment_slot_center_uses_agent_visible_equipment_position():
    grid = load_equipment_grid_module()

    assert grid.equipment_slot_center("equipment:1") == (1855, 275)
    assert grid.equipment_slot_center("equipment:6") == (1855, 663)
    assert grid.equipment_slot_center("equipment:7") == (1775, 275)
    assert grid.equipment_slot_center("equipment:60") == (1137, 663)


@pytest.mark.parametrize("value", ["1", "front:1", "equipment:0", "equipment:61", "equipment:x", "equipment:"])
def test_equipment_slot_center_rejects_invalid_agent_positions(value):
    grid = load_equipment_grid_module()

    with pytest.raises(Exception) as exc_info:
        grid.equipment_slot_center(value)

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_SLOT_INVALID"


def test_default_equipment_grid_profile_locks_confirmed_coordinates():
    grid = load_equipment_grid_module()
    profile = grid.DEFAULT_EQUIPMENT_GRID_PROFILE
    cells = list(grid.iter_equipment_grid_cells(profile, columns=3, rows=6))

    assert profile.crop_width == 70
    assert profile.crop_height == 70
    assert profile.col1_left == 1820.0
    assert profile.col_step == 79.75
    assert profile.row_origin_top == 240.0
    assert profile.row_step == 77.5
    assert profile.tracked_top_slot_count == 2
    assert profile.tracked_top_slots_excluded is True
    assert cells[0].idx == 1
    assert cells[0].row == 1
    assert cells[0].col == 1
    assert cells[0].x == 1820.0
    assert cells[1].idx == 2
    assert cells[1].row == 2
    assert cells[1].col == 1
    assert cells[1].x == 1820.0
    assert cells[5].row == 6
    assert cells[5].col == 1
    assert cells[5].box["top"] == 628
    assert cells[6].idx == 7
    assert cells[6].row == 1
    assert cells[6].col == 2
    assert cells[6].x == 1740.25
    assert cells[0].box == {"left": 1820, "top": 240, "width": 70, "height": 70}
    assert cells[1].box == {"left": 1820, "top": 318, "width": 70, "height": 70}
    assert cells[6].box == {"left": 1740, "top": 240, "width": 70, "height": 70}
    assert len(cells) == 18


def test_crop_equipment_cells_returns_70x70_images_and_float_candidates():
    grid = load_equipment_grid_module()
    source = Image.new("RGB", (1920, 1080), color="black")
    cells = list(grid.iter_equipment_grid_cells(grid.DEFAULT_EQUIPMENT_GRID_PROFILE, columns=2, rows=1))

    crops = grid.crop_equipment_cells(source, cells)

    assert [crop.cell.idx for crop in crops] == [1, 2, 2]
    assert [crop.image.size for crop in crops] == [(70, 70), (70, 70), (70, 70)]
    assert [crop.variant for crop in crops] == ["exact", "floor", "ceil"]
    assert crops[1].cell == crops[2].cell


def test_equipment_slot_marker_requires_dark_corners_and_light_frame():
    grid = load_equipment_grid_module()
    slot = Image.new("RGBA", (70, 70), (25, 25, 25, 255))
    draw = ImageDraw.Draw(slot)
    draw.line((18, 2, 52, 2), fill=(170, 170, 180, 255), width=3)
    draw.line((18, 67, 52, 67), fill=(170, 170, 180, 255), width=3)
    draw.line((2, 18, 2, 52), fill=(170, 170, 180, 255), width=3)
    draw.line((67, 18, 67, 52), fill=(170, 170, 180, 255), width=3)

    dark_line_only = Image.new("RGBA", (70, 70), (80, 80, 110, 255))
    draw = ImageDraw.Draw(dark_line_only)
    draw.rectangle((0, 0, 14, 14), fill=(15, 15, 18, 255))
    draw.rectangle((56, 56, 69, 69), fill=(15, 15, 18, 255))

    frame_only = Image.new("RGBA", (70, 70), (95, 95, 120, 255))
    draw = ImageDraw.Draw(frame_only)
    draw.line((18, 2, 52, 2), fill=(170, 170, 180, 255), width=3)
    draw.line((18, 67, 52, 67), fill=(170, 170, 180, 255), width=3)
    draw.line((2, 18, 2, 52), fill=(170, 170, 180, 255), width=3)
    draw.line((67, 18, 67, 52), fill=(170, 170, 180, 255), width=3)

    assert grid.crop_has_equipment_slot_markers(slot) is True
    assert grid.crop_has_equipment_slot_markers(dark_line_only) is False
    assert grid.crop_has_equipment_slot_markers(frame_only) is False


def test_equipment_slot_marker_accepts_inset_light_frame():
    grid = load_equipment_grid_module()
    slot = Image.new("RGBA", (70, 70), (25, 25, 25, 255))
    draw = ImageDraw.Draw(slot)
    draw.line((16, 10, 54, 10), fill=(190, 190, 200, 255), width=3)
    draw.line((16, 59, 54, 59), fill=(190, 190, 200, 255), width=3)
    draw.line((10, 16, 10, 54), fill=(190, 190, 200, 255), width=3)
    draw.line((59, 16, 59, 54), fill=(190, 190, 200, 255), width=3)

    assert grid.crop_has_equipment_slot_markers(slot) is True


def load_equipment_recognition_module():
    return importlib.import_module("trail.scenes.cw.equipment_recognition")


def test_vector_equipment_recognizer_picks_best_candidate_with_gap():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    red_entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    blue_entry = resources.EquipmentCatalogEntry(
        "advanced-blue", "blue", "蓝装", "advanced", None, None, "https://act-webstatic.mihoyo.com/blue.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [(red_entry, Image.new("RGBA", (128, 128), "red")), (blue_entry, Image.new("RGBA", (128, 128), "blue"))]
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert result.empty is False
    assert result.uncertain is False
    assert result.candidates[0].name == "红装"
    assert result.candidates[0].score > 0.95
    assert result.gap > 0.1


def test_vector_recognizer_from_precomputed_features_matches_png_constructor():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", "4", "进阶", "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    alt_entry = resources.EquipmentCatalogEntry(
        "advanced-ruby", "ruby", "红宝", "advanced", "4", "进阶", "https://act-webstatic.mihoyo.com/ruby.png", "3.2"
    )
    icon = Image.new("RGBA", (128, 128), (224, 20, 18, 255))
    alt_icon = Image.new("RGBA", (128, 128), (180, 22, 24, 255))
    query = Image.new("RGBA", (70, 70), (220, 20, 18, 255))

    expected = recognition.VectorEquipmentIconRecognizer([(entry, icon), (alt_entry, alt_icon)]).recognize(query)
    payload = recognition.build_precomputed_equipment_features([(entry, icon), (alt_entry, alt_icon)])
    actual = recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload).recognize(query)

    assert payload["equipment_feature_schema_version"] == 1
    assert payload["recognizer_algorithm_version"] == "vector-mask-v1"
    assert payload["feature_size"] == [32, 32]
    assert payload["match_size"] == [64, 64]
    assert payload["min_score"] == recognition.DEFAULT_MIN_SCORE
    assert payload["min_gap"] == recognition.DEFAULT_MIN_GAP
    assert len(payload["items"]) == 2
    assert set(payload["items"][0]) >= {
        "cache_key",
        "id",
        "name",
        "kind",
        "category",
        "category_name",
        "icon_url",
        "big_version",
        "feature_rgba",
        "match_rgba",
        "feature_mask",
        "match_mask",
    }
    assert payload["items"][0]["feature_rgba"]["mode"] == "RGBA"
    assert payload["items"][0]["match_rgba"]["size"] == [64, 64]
    assert payload["items"][0]["feature_mask"]["mode"] == "L"
    assert payload["items"][0]["match_mask"]["size"] == [64, 64]
    assert [(candidate.cache_key, candidate.score) for candidate in actual.candidates] == [
        (candidate.cache_key, candidate.score) for candidate in expected.candidates
    ]
    assert actual.score == expected.score
    assert actual.gap == expected.gap
    assert actual.uncertain is expected.uncertain
    assert actual.empty is expected.empty


def _sample_precomputed_feature_payload():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    return recognition, recognition.build_precomputed_equipment_features([(entry, Image.new("RGBA", (128, 128), "red"))])


def _replace_payload_value(payload, path, value):
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        pytest.param(("min_score",), float("nan"), "min_score", id="min-score-nan"),
        pytest.param(("min_gap",), float("inf"), "min_gap", id="min-gap-inf"),
        pytest.param(("min_score",), "bad", "min_score", id="threshold-non-numeric"),
        pytest.param(("items",), {"cache_key": "advanced-red"}, "items", id="items-not-list"),
        pytest.param(("feature_size",), [31, 32], "sizes", id="top-level-feature-size"),
        pytest.param(("match_size",), [64, 63], "sizes", id="top-level-match-size"),
        pytest.param(("items", 0, "feature_rgba", "mode"), "RGB", "feature_rgba", id="payload-mode"),
        pytest.param(("items", 0, "feature_rgba", "size"), [1, 1], "feature_rgba", id="payload-size"),
        pytest.param(("items", 0, "feature_rgba", "data"), [0], "feature_rgba", id="payload-data-length"),
        pytest.param(("items", 0, "feature_rgba", "data"), [999] * (32 * 32 * 4), "feature_rgba", id="payload-data-element"),
    ],
)
def test_vector_recognizer_from_precomputed_features_rejects_invalid_payloads(path, value, match):
    recognition, payload = _sample_precomputed_feature_payload()
    _replace_payload_value(payload, path, value)

    with pytest.raises(ValueError, match=match):
        recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload)


def test_vector_recognizer_from_precomputed_features_rejects_bad_algorithm():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    payload = recognition.build_precomputed_equipment_features([(entry, Image.new("RGBA", (128, 128), "red"))])
    payload["recognizer_algorithm_version"] = "vector-mask-v0"

    with pytest.raises(ValueError, match="algorithm"):
        recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload)


def test_vector_recognizer_from_precomputed_features_rejects_wrong_payload_size():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    payload = recognition.build_precomputed_equipment_features([(entry, Image.new("RGBA", (128, 128), "red"))])
    payload["items"][0]["feature_rgba"] = {"mode": "RGBA", "size": [1, 1], "data": [255, 0, 0, 255]}

    with pytest.raises(ValueError, match="feature_rgba"):
        recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload)


def test_vector_equipment_recognizer_marks_low_gap_uncertain():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    dark = resources.EquipmentCatalogEntry(
        "advanced-dark", "dark", "暗红", "advanced", None, None, "https://act-webstatic.mihoyo.com/dark.png", "3.2"
    )
    light = resources.EquipmentCatalogEntry(
        "advanced-light", "light", "亮红", "advanced", None, None, "https://act-webstatic.mihoyo.com/light.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [
            (dark, Image.new("RGBA", (128, 128), (200, 0, 0, 255))),
            (light, Image.new("RGBA", (128, 128), (210, 0, 0, 255))),
        ],
        min_gap=0.5,
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), (205, 0, 0, 255)))

    assert result.uncertain is True
    assert len(result.candidates) == 2
    assert result.candidates[0].score >= result.candidates[1].score


def test_vector_equipment_recognizer_detects_empty_roi():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer([(entry, Image.new("RGBA", (128, 128), "red"))])

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "black"))

    assert result.empty is True
    assert result.score is None
    assert result.gap is None
    assert result.uncertain is False
    assert result.candidates == []


def test_vector_equipment_recognizer_returns_gap_for_single_candidate():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer([(entry, Image.new("RGBA", (128, 128), "red"))])

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert result.empty is False
    assert result.score == 1.0
    assert result.gap == result.score
    assert result.uncertain is False


def test_vector_equipment_recognizer_clamps_top_k_to_one():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    red_entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    blue_entry = resources.EquipmentCatalogEntry(
        "advanced-blue", "blue", "蓝装", "advanced", None, None, "https://act-webstatic.mihoyo.com/blue.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [(red_entry, Image.new("RGBA", (128, 128), "red")), (blue_entry, Image.new("RGBA", (128, 128), "blue"))],
        top_k=0,
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert len(result.candidates) == 1
    assert result.candidates[0].name == "红装"


def test_vector_equipment_recognizer_treats_dark_speckled_roi_as_empty():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry(
        "advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer([(entry, Image.new("RGBA", (128, 128), "red"))])
    image = Image.new("RGBA", (70, 70), "black")
    image.putpixel((0, 0), (255, 255, 255, 255))

    result = recognizer.recognize(image)

    assert result.empty is True
    assert result.candidates == []


def test_vector_equipment_recognizer_breaks_score_ties_by_name():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    later_entry = resources.EquipmentCatalogEntry(
        "advanced-later", "later", "zeta", "advanced", None, None, "https://act-webstatic.mihoyo.com/later.png", "3.2"
    )
    earlier_entry = resources.EquipmentCatalogEntry(
        "advanced-earlier", "earlier", "alpha", "advanced", None, None, "https://act-webstatic.mihoyo.com/earlier.png", "3.2"
    )
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [
            (later_entry, Image.new("RGBA", (128, 128), "red")),
            (earlier_entry, Image.new("RGBA", (128, 128), "red")),
        ]
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert [candidate.name for candidate in result.candidates[:2]] == ["alpha", "zeta"]
    assert result.candidates[0].score == result.candidates[1].score


def test_vector_equipment_recognizer_ignores_hidden_rgb_in_transparent_icon_background():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    correct_entry = resources.EquipmentCatalogEntry(
        "advanced-correct", "correct", "正确", "advanced", None, None, "https://act-webstatic.mihoyo.com/correct.png", "3.2"
    )
    wrong_entry = resources.EquipmentCatalogEntry(
        "advanced-wrong", "wrong", "错误", "advanced", None, None, "https://act-webstatic.mihoyo.com/wrong.png", "3.2"
    )
    correct_icon = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    ImageDraw.Draw(correct_icon).rectangle((24, 24, 39, 39), fill=(0, 0, 255, 255))
    wrong_icon = Image.new("RGBA", (64, 64), "black")
    ImageDraw.Draw(wrong_icon).rectangle((24, 24, 39, 39), fill="green")
    query = Image.new("RGBA", (64, 64), "black")
    ImageDraw.Draw(query).rectangle((24, 24, 39, 39), fill="blue")
    recognizer = recognition.VectorEquipmentIconRecognizer([(correct_entry, correct_icon), (wrong_entry, wrong_icon)])

    result = recognizer.recognize(query)

    assert result.candidates[0].name == "正确"
    assert result.candidates[0].score > result.candidates[1].score


def test_vector_equipment_recognizer_weights_transparent_icon_foreground_over_slot_background():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    correct_entry = resources.EquipmentCatalogEntry(
        "advanced-correct", "correct", "正确", "advanced", None, None, "https://act-webstatic.mihoyo.com/correct.png", "3.2"
    )
    wrong_entry = resources.EquipmentCatalogEntry(
        "advanced-wrong", "wrong", "错误", "advanced", None, None, "https://act-webstatic.mihoyo.com/wrong.png", "3.2"
    )
    correct_icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(correct_icon).rectangle((24, 24, 39, 39), fill=(255, 0, 0, 255))
    wrong_icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(wrong_icon).rectangle((24, 24, 39, 39), fill=(0, 0, 255, 255))
    query = Image.new("RGBA", (70, 70), (25, 25, 30, 255))
    draw = ImageDraw.Draw(query)
    draw.line((18, 2, 52, 2), fill=(170, 170, 180, 255), width=3)
    draw.line((18, 67, 52, 67), fill=(170, 170, 180, 255), width=3)
    draw.line((2, 18, 2, 52), fill=(170, 170, 180, 255), width=3)
    draw.line((67, 18, 67, 52), fill=(170, 170, 180, 255), width=3)
    draw.rectangle((27, 27, 42, 42), fill=(255, 0, 0, 255))
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [(correct_entry, correct_icon), (wrong_entry, wrong_icon)],
        min_gap=0.15,
    )

    result = recognizer.recognize(query)

    assert result.candidates[0].name == "正确"
    assert result.gap >= 0.15
    assert result.uncertain is False


def load_equipment_scene_module():
    return importlib.import_module("trail.scenes.cw.equipment")


def test_runtime_image_accepts_capture_image_without_normalize_and_bytes():
    scene = load_equipment_scene_module()
    output = BytesIO()
    Image.new("RGBA", (1920, 1080), "black").save(output, format="PNG")
    png_bytes = output.getvalue()

    class Runtime:
        def __init__(self):
            self.calls = 0

        def capture_image(self):
            self.calls += 1
            return png_bytes

    runtime = Runtime()

    image = scene._runtime_image(runtime)

    assert runtime.calls == 1
    assert image.mode == "RGBA"
    assert image.size == (1920, 1080)


def test_runtime_image_uses_normalized_capture_from_shared_window_helper():
    scene = load_equipment_scene_module()

    class Runtime:
        def __init__(self):
            self.calls = []

        def capture_image(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs == {"normalize": True}:
                return Image.new("RGBA", (1920, 1080), "black")
            return Image.new("RGBA", (1600, 900), "black")

    runtime = Runtime()

    image = scene._runtime_image(runtime)

    assert runtime.calls == [{"normalize": True}]
    assert image.size == (1920, 1080)


def test_runtime_image_rejects_non_canonical_large_capture():
    scene = load_equipment_scene_module()

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1921, 1080), "black")

    with pytest.raises(Exception) as exc_info:
        scene._runtime_image(Runtime())

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_LAYOUT_MISMATCH"


def test_runtime_image_rejects_invalid_image_bytes_with_fixed_error_code():
    scene = load_equipment_scene_module()

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return b"not a png"

    with pytest.raises(Exception) as exc_info:
        scene._runtime_image(Runtime())

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_SCREENSHOT_INVALID"


def test_prepare_cw_equipment_builds_catalog_and_cache(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(
        scene,
        "prepare_equipment_icon_cache",
        lambda catalog, workspace_root=None, refresh=False: {
            "big_version": "3.2",
            "count": len(catalog),
            "cached": 0,
            "downloaded": 1,
            "refreshed": refresh,
        },
    )

    result = scene.prepare_cw_equipment(workspace_root=tmp_path, refresh=True)

    assert result == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}


def test_filter_isolated_equipment_items_drops_remote_singletons():
    scene = load_equipment_scene_module()

    def item(idx):
        return {"idx": idx, "pos": f"equipment:{idx}"}

    result = scene._filter_isolated_equipment_items([item(1), item(2), item(3), item(4), item(10)])

    assert [entry["idx"] for entry in result] == [1, 2, 3, 4]


def test_filter_isolated_equipment_items_keeps_front_singleton_and_adjacent_blocks():
    scene = load_equipment_scene_module()

    def item(idx):
        return {"idx": idx, "pos": f"equipment:{idx}"}

    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(1)])] == [1]
    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(1), item(2), item(5), item(6)])] == [1, 2, 5, 6]
    assert scene._filter_isolated_equipment_items([item(5)]) == []
    assert [entry["idx"] for entry in scene._filter_isolated_equipment_items([item(7), item(8)])] == [7, 8]


def test_read_cw_equipment_recognizes_best_variants_and_counts(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            del image
            self.calls += 1
            if self.calls == 1:
                return recognition.EquipmentRecognitionResult(
                    candidates=[recognition.EquipmentCandidate("e1", "advanced-e1", "幸运星", 0.93)],
                    score=0.93,
                    gap=0.2,
                    uncertain=False,
                    empty=False,
                )
            return recognition.EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(
        scene,
        "prepare_equipment_icon_cache",
        lambda catalog, workspace_root=None, refresh=False: {
            "big_version": "3.2",
            "count": 1,
            "cached": 1,
            "downloaded": 0,
            "refreshed": False,
        },
    )
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    iter_calls = []

    def iter_cells(profile, columns=3, rows=6):
        iter_calls.append((columns, rows))
        return [
            grid.EquipmentGridCell(1, 1, 1, 1820.0, 240, {"left": 1820, "top": 240, "width": 70, "height": 70}),
            grid.EquipmentGridCell(2, 2, 1, 1820.0, 318, {"left": 1820, "top": 318, "width": 70, "height": 70}),
        ]

    monkeypatch.setattr(
        scene,
        "iter_equipment_grid_cells",
        iter_cells,
    )
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "exact") for cell in cells],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert iter_calls == [(10, 6)]
    assert result["count"] == 1
    assert result["uncertain"] == 0
    assert result["empty"] == 1
    assert result["items"][0]["idx"] == 1
    assert result["items"][0]["pos"] == "equipment:1"
    assert result["items"][0]["center"] == {"x": 1855, "y": 275}
    assert "box" not in result["items"][0]
    assert result["items"][0]["name"] == "幸运星"
    assert result["backend"] == "vector"
    assert result["layout"] == "default"


def test_read_cw_equipment_filters_isolated_remote_items(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]
    selected_idxs = {1, 2, 3, 4, 10}

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def recognize(self, image):
            idx = image.getpixel((0, 0))[0]
            return recognition.EquipmentRecognitionResult(
                candidates=[recognition.EquipmentCandidate("e1", f"advanced-e{idx}", f"装备{idx}", 0.9)],
                score=0.9,
                gap=0.2,
                uncertain=False,
                empty=False,
            )

    cells = list(grid.iter_equipment_grid_cells(grid.DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=10, rows=6: cells)
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), (cell.idx, 0, 0, 255)), "exact")
            for cell in cells
            if cell.idx in selected_idxs
        ],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 4
    assert result["uncertain"] == 0
    assert result["empty"] == 56
    assert [item["pos"] for item in result["items"]] == ["equipment:1", "equipment:2", "equipment:3", "equipment:4"]
    assert "equipment:10" not in [item["pos"] for item in result["items"]]


def test_read_cw_equipment_filters_crops_without_slot_markers(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]
    cells = [
        grid.EquipmentGridCell(1, 1, 1, 1820.0, 240, {"left": 1820, "top": 240, "width": 70, "height": 70}),
        grid.EquipmentGridCell(2, 2, 1, 1820.0, 318, {"left": 1820, "top": 318, "width": 70, "height": 70}),
    ]

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            del image
            self.calls += 1
            return recognition.EquipmentRecognitionResult(
                candidates=[recognition.EquipmentCandidate("e1", "advanced-e1", "幸运星", 0.93)],
                score=0.93,
                gap=0.2,
                uncertain=False,
                empty=False,
            )

    recognizer = Recognizer()
    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: recognizer)
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=10, rows=6: cells)
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [
            grid.EquipmentCrop(cells[0], Image.new("RGBA", (70, 70), "red"), "exact"),
            grid.EquipmentCrop(cells[1], Image.new("RGBA", (70, 70), "black"), "exact"),
        ],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: image.getpixel((0, 0)) == (255, 0, 0, 255))

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert recognizer.calls == 1
    assert result["count"] == 1
    assert result["empty"] == 1
    assert result["items"][0]["idx"] == 1


def test_read_cw_equipment_picks_non_empty_later_variant_for_same_cell(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            del image
            self.calls += 1
            if self.calls == 1:
                return recognition.EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)
            return recognition.EquipmentRecognitionResult(
                candidates=[recognition.EquipmentCandidate("e1", "advanced-e1", "幸运星", 0.91)],
                score=0.91,
                gap=0.15,
                uncertain=False,
                empty=False,
            )

    cell = grid.EquipmentGridCell(1, 1, 1, 1740.25, 240, {"left": 1740, "top": 240, "width": 70, "height": 70})
    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=3, rows=6: [cell])
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "black"), "floor"),
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "ceil"),
        ],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["empty"] == 0
    assert result["items"][0]["idx"] == 1
    assert result["items"][0]["score"] == 0.91


def test_read_cw_equipment_keeps_non_empty_uncertain_without_candidates(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]
    cell = grid.EquipmentGridCell(1, 1, 1, 1820.0, 240, {"left": 1820, "top": 240, "width": 70, "height": 70})

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def recognize(self, image):
            del image
            return recognition.EquipmentRecognitionResult(
                candidates=[],
                score=None,
                gap=None,
                uncertain=True,
                empty=False,
            )

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=3, rows=6: [cell])
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "exact")],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["empty"] == 0
    assert result["uncertain"] == 1
    item = result["items"][0]
    assert item["idx"] == 1
    assert item["pos"] == "equipment:1"
    assert item["row"] == 1
    assert item["col"] == 1
    assert item["center"] == {"x": 1855, "y": 275}
    assert "box" not in item
    assert item["name"] is None
    assert item["equipment_id"] is None
    assert item["cache_key"] is None
    assert item["alt"] is None
    assert item["alt_score"] is None
    assert item["score"] is None
    assert item["gap"] is None
    assert item["candidates"] == []


def test_read_cw_equipment_prefers_zero_score_candidate_over_unknown_variant(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }
    entry = resources.build_cw_equipment_catalog(raw_config)[0]
    cell = grid.EquipmentGridCell(1, 1, 1, 1740.25, 240, {"left": 1740, "top": 240, "width": 70, "height": 70})

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": True}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            del image
            self.calls += 1
            if self.calls == 1:
                return recognition.EquipmentRecognitionResult(
                    candidates=[],
                    score=None,
                    gap=None,
                    uncertain=True,
                    empty=False,
                )
            return recognition.EquipmentRecognitionResult(
                candidates=[recognition.EquipmentCandidate("e1", "advanced-e1", "幸运星", 0.0)],
                score=0.0,
                gap=0.0,
                uncertain=True,
                empty=False,
            )

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=3, rows=6: [cell])
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "black"), "floor"),
            grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "ceil"),
        ],
    )
    monkeypatch.setattr(scene, "crop_has_equipment_slot_markers", lambda image: True)

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["empty"] == 0
    assert result["uncertain"] == 1
    item = result["items"][0]
    assert item["name"] == "幸运星"
    assert item["equipment_id"] == "e1"
    assert item["cache_key"] == "advanced-e1"
    assert item["score"] == 0.0


def load_equipment_module():
    return importlib.import_module("trail.scenes.cw.equipment")


def _cw_session_with_equipment_guide(tmp_path, *, slots_stale: bool = False):
    from trail.scenes.cw.models import ensure_cw_state
    from trail.session.store import SessionStore

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = ensure_cw_state(session)
    cw_state["guide"] = {
        "lineup_id": "guide-equipment",
        "title": "装备攻略",
        "share_code": "##equipment##",
        "version": "4.0",
        "operation_guide": "测试运营",
        "role_stages": [
            {
                "front_roles": [
                    {"name": "希儿", "first_equipments": [{"name": "高周波电锯"}], "second_equipments": ["战场手册"]}
                ],
                "back_roles": [{"name": "佩拉", "first_equipments": ["战场手册"], "second_equipments": []}],
            }
        ],
        "first_fight_augments": [],
        "second_fight_augments": [],
        "order_basic": [],
        "order_compose": [{"name": "高周波电锯"}, "战场手册", {"name": "未知攻略装备"}],
    }
    cw_state["constraints"] = {"min_coins": 0, "min_level": 0, "mid_level": 0}
    cw_state["slots"] = {
        "front": [{"name": "希儿", "equipments": ["战场手册"]}],
        "back": ["佩拉"],
        "hand": ["希儿", "佩拉"],
        "stale": slots_stale,
    }
    return session


def _equipment_raw_config_for_recommendations():
    return {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "saw",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/saw.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/armor.png"},
                            {"id": "battery", "name": "光能电池", "icon": "https://act-webstatic.mihoyo.com/battery.png"},
                        ]
                    }
                ],
            },
            {"id": "manual", "name": "战场手册", "icon": "https://act-webstatic.mihoyo.com/manual.png", "compose_list": []},
        ],
    }


def test_build_equipment_recommendations_uses_guide_order_slots_and_basic_counts(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    snapshot = {
        "items": [
            {"name": "基础装甲", "equipment_id": "armor", "cache_key": "basic-armor"},
            {"name": "高周波电锯", "equipment_id": "saw", "cache_key": "advanced-saw"},
        ]
    }

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot=snapshot,
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert recommendations["priority"][0] == {
        "idx": 1,
        "name": "高周波电锯",
        "known": True,
        "basics": [
            {"name": "基础装甲", "have": 1, "need": 1},
            {"name": "光能电池", "have": 0, "need": 1},
        ],
        "required_roles": ["希儿"],
        "acquired_roles": [],
        "missing_roles": ["希儿"],
    }
    assert recommendations["priority"][1]["name"] == "战场手册"
    assert recommendations["priority"][2]["known"] is False
    assert recommendations["role_missing"] == [
        {"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"},
        {"pos": "back:1", "role": "佩拉", "equipment": "战场手册", "category": "优选"},
    ]
    assert recommendations["todos"] == []


def test_build_equipment_recommendations_with_stale_slots_omits_current_role_fields(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path, slots_stale=True)

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot={"items": []},
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    first = recommendations["priority"][0]
    assert first["required_roles"] == ["希儿"]
    assert "acquired_roles" not in first
    assert "missing_roles" not in first
    assert recommendations["role_missing"] == []
    assert recommendations["todos"] == ["slots"]


def test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_id_matches(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "same-id", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/basic.png"}
                        ]
                    }
                ],
            }
        ],
    }
    snapshot = {"items": [{"name": "高周波电锯", "equipment_id": "same-id", "cache_key": "advanced-same-id"}]}

    recommendations = equipment.build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=raw_config)

    assert recommendations["priority"][0]["basics"] == [{"name": "基础装甲", "have": 0, "need": 1}]


def test_build_equipment_recommendations_does_not_count_advanced_item_as_basic_when_name_matches(tmp_path):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "advanced-armor",
                "name": "高周波电锯",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "compose_list": [
                    {
                        "childrens": [
                            {"id": "basic-armor", "name": "基础装甲", "icon": "https://act-webstatic.mihoyo.com/basic.png"}
                        ]
                    }
                ],
            }
        ],
    }
    snapshot = {"items": [{"name": "基础装甲", "equipment_id": "advanced-armor", "cache_key": "advanced-advanced-armor"}]}

    recommendations = equipment.build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=raw_config)

    assert recommendations["priority"][0]["basics"] == [{"name": "基础装甲", "have": 0, "need": 1}]


def test_build_equipment_recommendations_prefers_first_equipment_category_across_stages(tmp_path):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["guide"]["role_stages"] = [
        {"front_roles": [{"name": "希儿", "first_equipments": [], "second_equipments": ["高周波电锯"]}], "back_roles": []},
        {"front_roles": [{"name": "希儿", "first_equipments": ["高周波电锯"], "second_equipments": []}], "back_roles": []},
    ]
    cw_state["slots"]["front"] = [{"name": "希儿", "equipments": []}]
    cw_state["slots"]["back"] = []
    cw_state["slots"]["hand"] = []

    recommendations = equipment.build_cw_equipment_recommendations(
        session,
        snapshot={"items": []},
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert recommendations["role_missing"] == [
        {"pos": "front:1", "role": "希儿", "equipment": "高周波电锯", "category": "优选"}
    ]


def test_record_equipment_compose_writes_role_object_and_marks_equipment_stale(tmp_path):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    ensure_cw_state(session)["equipment"] = {"stale": False, "items": [], "recommendations": {}}

    result = equipment.record_cw_equipment_compose(
        session,
        name="高周波电锯",
        slot="front:0",
        role="希儿",
        raw_config=_equipment_raw_config_for_recommendations(),
    )

    assert result == {"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 2}
    role = ensure_cw_state(session)["slots"]["front"][0]
    assert role["equipments"] == ["战场手册", "高周波电锯"]
    assert ensure_cw_state(session)["equipment"]["stale"] is True
    assert "recommendations" not in ensure_cw_state(session)["equipment"]


@pytest.mark.parametrize(
    ("mutate", "kwargs", "code"),
    [
        (lambda cw_state: cw_state["slots"].update(stale=True), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_STALE"),
        (lambda cw_state: cw_state.pop("guide", None), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_GUIDE_STATE_INVALID"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, None), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_EMPTY"),
        (lambda cw_state: None, {"slot": "front:0", "role": "佩拉", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_SLOT_MISMATCH"),
        (lambda cw_state: None, {"slot": "hand:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_DUPLICATE_SLOT"),
        (lambda cw_state: None, {"slot": "front:0", "role": "希儿", "name": "不存在装备"}, "CW_EQUIPMENT_NAME_INVALID"),
        (lambda cw_state: None, {"slot": "front:0", "role": "希儿", "name": "战场手册"}, "CW_EQUIPMENT_ALREADY_HELD"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, {"name": "希儿", "equipments": ["A", "B", "C"]}), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_EQUIPMENT_FULL"),
        (lambda cw_state: cw_state["slots"]["front"].__setitem__(0, {"name": "希儿", "equipments": "bad"}), {"slot": "front:0", "role": "希儿", "name": "高周波电锯"}, "CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID"),
    ],
)
def test_record_equipment_compose_rejects_invalid_state(tmp_path, mutate, kwargs, code):
    from trail.scenes.cw.models import ensure_cw_state

    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)
    cw_state = ensure_cw_state(session)
    mutate(cw_state)

    with pytest.raises(Exception) as exc_info:
        equipment.record_cw_equipment_compose(
            session,
            raw_config=_equipment_raw_config_for_recommendations(),
            **kwargs,
        )

    assert getattr(exc_info.value, "code", None) == code


@pytest.mark.parametrize("slot", ["front:99", "enemy:0", "bad", 123])
def test_record_equipment_compose_rejects_invalid_rpc_slot(tmp_path, slot):
    equipment = load_equipment_module()
    session = _cw_session_with_equipment_guide(tmp_path)

    with pytest.raises(Exception) as exc_info:
        equipment.record_cw_equipment_compose(
            session,
            name="高周波电锯",
            slot=slot,
            role="希儿",
            raw_config=_equipment_raw_config_for_recommendations(),
        )

    assert getattr(exc_info.value, "code", None) == "SLOTS_POSITION_INVALID"
