from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

from trail.core.errors import TrailError


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-cw-resource-bundle.py"


@lru_cache(maxsize=None)
def _load_script():
    spec = importlib.util.spec_from_file_location("build_cw_resource_bundle", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _png_bytes(color="red"):
    buffer = BytesIO()
    Image.new("RGBA", (24, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _raw_config() -> dict:
    return {
        "season_id": "s1",
        "sub_season_id": "sub1",
        "rpg_game_big_version": "3.2",
        "rpg_game_lineup_tourn_filter": "filter1",
        "trait_info_list": [{"id": "t1", "name": "贝洛伯格", "type": "faction"}],
        "role_list": [{"id": "r1", "name": "希儿", "trait_ids": ["t1"]}],
        "portal_list": [{"portal_id": "p1", "title": "机械城", "description": "desc"}],
        "fight_augment_list": [{"id": "a1", "name": "快攻", "desc": "说明"}],
        "equipment_list": [
            {"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}
        ],
    }


def _normalizer(data, *, timeout=10, workspace_root=None, enrich_traits=False):
    return {
        "meta": {"big_version": data["rpg_game_big_version"]},
        "lineup_levels": [{"id": "7", "name": "7级搜牌"}],
        "traits": [
            {"id": "t1", "name": "贝洛伯格", "layers": [{"layer": 2}, {"layer": 4}, {"layer": 6}]}
        ]
        if enrich_traits
        else [{"id": "t1", "name": "贝洛伯格"}],
        "roles": [{"id": "r1", "name": "希儿", "trait_ids": ["t1"], "front_back_type": "front"}],
        "role_tags": ["输出"],
        "portal_list": [{"portal_id": "p1", "title": "机械城", "description": "desc"}],
        "strategy_list": [{"id": "a1", "title": "快攻"}],
    }


def test_build_cw_resource_bundle_writes_manifest_and_loadable_bundle(tmp_path):
    module = _load_script()

    result = module.build_cw_resource_bundle(
        output_root=tmp_path / "generated",
        raw_config_fetcher=lambda timeout=10: _raw_config(),
        config_normalizer=_normalizer,
        icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        timeout=1,
    )

    bundle_root = Path(result["bundle_root"])
    assert result == {"bundle_root": str(bundle_root), "big_version": "3.2", "count": 1}
    for relative in [
        "manifest.json",
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/manifest.json",
        "equipment/features.json",
        "equipment/icons/advanced-e1.png",
    ]:
        assert (bundle_root / relative).is_file()

    manifest = json.loads((bundle_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rpg_game_big_version"] == "3.2"

    equipment = json.loads((bundle_root / "equipment" / "manifest.json").read_text(encoding="utf-8"))
    assert equipment["items"][0]["cache_key"] == "advanced-e1"
    assert equipment["items"][0]["big_version"] == "3.2"

    from trail.scenes.cw.static_resources import load_cw_resource_bundle_from_path

    loaded = load_cw_resource_bundle_from_path(bundle_root)
    assert loaded.big_version == "3.2"
    assert loaded.equipment_manifest["items"][0]["cache_key"] == "advanced-e1"
    assert loaded.indexes["equipment_by_cache_key"]["advanced-e1"]["big_version"] == "3.2"


def test_build_cw_resource_bundle_removes_stale_generated_content(tmp_path):
    module = _load_script()
    output_root = tmp_path / "generated"
    old_version = output_root / "old-version"
    root_stale_file = output_root / "stale.txt"
    gitkeep = output_root / ".gitkeep"
    stale_same_version = output_root / "3.2" / "stale.json"
    stale_icon = output_root / "3.2" / "equipment" / "icons" / "stale.png"
    old_version.mkdir(parents=True)
    (old_version / "manifest.json").write_text("{}", encoding="utf-8")
    root_stale_file.write_text("stale", encoding="utf-8")
    gitkeep.write_text("", encoding="utf-8")
    stale_same_version.parent.mkdir(parents=True)
    stale_same_version.write_text("{}", encoding="utf-8")
    stale_icon.parent.mkdir(parents=True, exist_ok=True)
    stale_icon.write_bytes(b"stale")

    result = module.build_cw_resource_bundle(
        output_root=output_root,
        raw_config_fetcher=lambda timeout=10: _raw_config(),
        config_normalizer=_normalizer,
        icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        timeout=1,
    )

    bundle_root = Path(result["bundle_root"])
    assert not old_version.exists()
    assert not root_stale_file.exists()
    assert gitkeep.is_file()
    assert not stale_same_version.exists()
    assert not stale_icon.exists()

    from trail.scenes.cw.static_resources import load_cw_resource_bundle_from_path

    assert load_cw_resource_bundle_from_path(bundle_root).big_version == "3.2"


def test_build_cw_resource_bundle_rejects_generated_output_symlink(tmp_path):
    module = _load_script()
    external_generated = tmp_path / "external-generated"
    external_generated.mkdir()
    output_root = tmp_path / "generated"
    try:
        output_root.symlink_to(external_generated, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")

    with pytest.raises(TrailError) as exc_info:
        module.build_cw_resource_bundle(
            output_root=output_root,
            raw_config_fetcher=lambda timeout=10: _raw_config(),
            config_normalizer=_normalizer,
            icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
            timeout=1,
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"
    assert not any(external_generated.iterdir())


def test_build_cw_resource_bundle_sanitizes_big_version_directory(tmp_path):
    module = _load_script()
    output_root = tmp_path / "generated"
    raw_config = _raw_config()
    raw_config["rpg_game_big_version"] = "../3.2"

    result = module.build_cw_resource_bundle(
        output_root=output_root,
        raw_config_fetcher=lambda timeout=10: raw_config,
        config_normalizer=_normalizer,
        icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        timeout=1,
    )

    bundle_root = Path(result["bundle_root"])
    assert result["big_version"] == "../3.2"
    assert bundle_root == output_root / module.safe_equipment_cache_segment("../3.2")
    assert bundle_root.resolve().is_relative_to(output_root.resolve())

    escaped_root = output_root / ".." / "3.2"
    assert not (escaped_root.resolve() / "manifest.json").exists()

    manifest = json.loads((bundle_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rpg_game_big_version"] == "../3.2"


def test_build_cw_resource_bundle_rejects_generated_invalid_features(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(
        module,
        "_build_precomputed_equipment_features",
        lambda icon_pairs: {
            "items": [],
            "equipment_feature_schema_version": 1,
            "recognizer_algorithm_version": "vector-mask-v1",
            "feature_size": [32, 32],
            "match_size": [64, 64],
            "min_score": 0.72,
            "min_gap": 0.05,
        },
    )

    with pytest.raises(TrailError) as exc_info:
        module.build_cw_resource_bundle(
            output_root=tmp_path / "generated",
            raw_config_fetcher=lambda timeout=10: _raw_config(),
            config_normalizer=_normalizer,
            icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
            timeout=1,
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_equipment_bundle_public_helpers_include_checksum_and_wrap_download_errors(monkeypatch):
    from trail.scenes.cw import equipment_resources

    entry = equipment_resources.EquipmentCatalogEntry(
        cache_key="advanced-e1",
        id="e1",
        name="幸运星",
        kind="advanced",
        category=None,
        category_name=None,
        icon_url="https://act-webstatic.mihoyo.com/e1.png",
        big_version="3.2",
    )

    payload = equipment_resources.equipment_bundle_manifest_entry(
        entry,
        local_path="equipment/icons/advanced-e1.png",
        sha256="abc",
        size=123,
    )
    assert payload["big_version"] == "3.2"
    assert payload["sha256"] == "abc"
    assert payload["size"] == 123
    assert equipment_resources.safe_equipment_cache_segment("bad/key") == "bad-key"

    def fail_download(url, *, timeout, max_bytes):
        raise RuntimeError("network down")

    monkeypatch.setattr(equipment_resources, "_download_icon", fail_download)
    with pytest.raises(TrailError) as exc_info:
        equipment_resources.download_equipment_icon_bytes("https://act-webstatic.mihoyo.com/e1.png", timeout=1, max_bytes=10)

    assert exc_info.value.code == "CW_EQUIPMENT_ICON_DOWNLOAD_FAILED"
