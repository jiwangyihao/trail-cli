from __future__ import annotations

import importlib
import json
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
    assert cells[1].x == 1740.25
    assert cells[2].x == 1660.5
    assert cells[0].box == {"left": 1820, "top": 240, "width": 70, "height": 70}
    assert cells[1].box == {"left": 1740, "top": 240, "width": 70, "height": 70}
    assert cells[2].box == {"left": 1660, "top": 240, "width": 70, "height": 70}
    assert cells[9].row == 4
    assert cells[9].col == 1
    assert cells[9].box["top"] == 473
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


def test_runtime_image_rejects_non_canonical_large_capture():
    scene = load_equipment_scene_module()

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": False}
            return Image.new("RGBA", (1921, 1080), "black")

    with pytest.raises(Exception) as exc_info:
        scene._runtime_image(Runtime())

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_LAYOUT_MISMATCH"


def test_runtime_image_rejects_invalid_image_bytes_with_fixed_error_code():
    scene = load_equipment_scene_module()

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": False}
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
            assert kwargs == {"normalize": False}
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
    monkeypatch.setattr(
        scene,
        "iter_equipment_grid_cells",
        lambda profile, columns=3, rows=6: [
            grid.EquipmentGridCell(1, 1, 1, 1820.0, 240, {"left": 1820, "top": 240, "width": 70, "height": 70}),
            grid.EquipmentGridCell(2, 1, 2, 1740.25, 240, {"left": 1740, "top": 240, "width": 70, "height": 70}),
        ],
    )
    monkeypatch.setattr(
        scene,
        "crop_equipment_cells",
        lambda image, cells: [grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "exact") for cell in cells],
    )

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["uncertain"] == 0
    assert result["empty"] == 1
    assert result["items"][0]["idx"] == 1
    assert result["items"][0]["name"] == "幸运星"
    assert result["backend"] == "vector"
    assert result["layout"] == "default"


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
            assert kwargs == {"normalize": False}
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
            assert kwargs == {"normalize": False}
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

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["empty"] == 0
    assert result["uncertain"] == 1
    item = result["items"][0]
    assert item["idx"] == 1
    assert item["row"] == 1
    assert item["col"] == 1
    assert item["box"] == {"left": 1820, "top": 240, "width": 70, "height": 70}
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
            assert kwargs == {"normalize": False}
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

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["empty"] == 0
    assert result["uncertain"] == 1
    item = result["items"][0]
    assert item["name"] == "幸运星"
    assert item["equipment_id"] == "e1"
    assert item["cache_key"] == "advanced-e1"
    assert item["score"] == 0.0
