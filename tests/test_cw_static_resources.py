from __future__ import annotations

from io import BytesIO
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    load_cw_resource_bundle_from_path,
    write_bundle_manifest,
)


def _write_json_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write_json_text(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_content_digest(manifest: dict) -> str:
    payload = {key: value for key, value in manifest.items() if key != "content_digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=None)
def _zero_pixel_data(length: int) -> tuple[int, ...]:
    return (0,) * length


@lru_cache(maxsize=None)
def _zero_histogram_data(length: int) -> tuple[float, ...]:
    return (0.0,) * length


def _pixel_payload(mode: str, size: list[int]) -> dict:
    channels = 4 if mode == "RGBA" else 1
    return {"mode": mode, "size": size, "data": _zero_pixel_data(size[0] * size[1] * channels)}


@lru_cache(maxsize=None)
def _png_bytes(*, mode: str = "RGBA", size: tuple[int, int] = (103, 120)) -> bytes:
    buffer = BytesIO()
    Image.new(mode, size).save(buffer, format="PNG")
    return buffer.getvalue()


def _truncated_empty_png_bytes() -> bytes:
    return _png_bytes()[:-40]


def _valid_feature_item(cache_key: str = "icon-a", name: str = "Icon A") -> dict:
    return {
        "cache_key": cache_key,
        "name": name,
        "feature_rgba": _pixel_payload("RGBA", [32, 32]),
        "match_rgba": _pixel_payload("RGBA", [64, 64]),
        "feature_mask": _pixel_payload("L", [32, 32]),
        "match_mask": _pixel_payload("L", [64, 64]),
    }


def _valid_raw_config() -> dict:
    return {
        "rpg_game_big_version": "3.2",
        "trait_info_list": [{"id": "t1", "name": "Trait A", "type": "faction"}],
        "role_list": [{"id": "r1", "name": "Role A", "trait_ids": ["t1"]}],
        "portal_list": [{"portal_id": "p1", "title": "Portal A", "description": "desc"}],
        "fight_augment_list": [{"id": "a1", "name": "Strategy A", "desc": "desc"}],
        "equipment_list": [{"id": "e1", "name": "Equipment A", "icon": "https://example.test/icon-a.png"}],
    }


def _valid_guide_config() -> dict:
    return {
        "meta": {"big_version": "3.2"},
        "lineup_levels": [{"id": "7", "name": "Level 7"}],
        "traits": [{"id": "t1", "name": "Trait A"}],
        "roles": [{"id": "r1", "name": "Role A", "trait_ids": ["t1"], "front_back_type": "front"}],
        "role_tags": ["damage"],
        "portal_list": [{"portal_id": "p1", "title": "Portal A", "description": "desc"}],
        "strategy_list": [{"id": "a1", "title": "Strategy A"}],
    }


def _valid_indexes() -> dict:
    return {
        "traits_by_name": {"Trait A": {"id": "t1", "name": "Trait A"}},
        "roles_by_name": {"Role A": {"id": "r1", "name": "Role A"}},
        "portals_by_title": {"Portal A": {"portal_id": "p1", "title": "Portal A"}},
        "strategies_by_title": {"Strategy A": {"id": "a1", "title": "Strategy A"}},
        "equipment_by_cache_key": {"icon-a": {"cache_key": "icon-a", "name": "Icon A"}},
    }


def _valid_feature_payload(cache_key: str = "icon-a") -> dict:
    return {
        "items": [_valid_feature_item(cache_key)],
        "equipment_feature_schema_version": 1,
        "recognizer_algorithm_version": "vector-mask-v1",
        "feature_size": [32, 32],
        "match_size": [64, 64],
        "min_score": 0.72,
        "min_gap": 0.05,
    }


@lru_cache(maxsize=None)
def _valid_feature_payload_json(cache_key: str = "icon-a") -> str:
    return json.dumps(_valid_feature_payload(cache_key), ensure_ascii=False, separators=(",", ":"))


def _light_feature_payload(cache_key: str = "icon-a", *, min_score: float = 0.72) -> dict:
    return {"items": [{"cache_key": cache_key}], "min_score": min_score}


def _valid_role_manifest(root: Path) -> dict:
    icon_relative = "roles/icons/r1.png"
    field_relative = "roles/empty/field-v1.png"
    hand_relative = "roles/empty/hand-v1.png"
    for relative, data in {
        icon_relative: b"role-icon-r1",
        field_relative: _png_bytes(),
        hand_relative: _png_bytes(),
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {
        "role_manifest_schema_version": 1,
        "resource_version": "3.2",
        "empty_template_version": "cw-slots-empty-v1",
        "empty_templates": {
            "field": {
                "local_path": field_relative,
                "sha256": _sha256(root / field_relative),
                "size": (root / field_relative).stat().st_size,
            },
            "hand": {
                "local_path": hand_relative,
                "sha256": _sha256(root / hand_relative),
                "size": (root / hand_relative).stat().st_size,
            },
        },
        "items": [
            {
                "role_id": "r1",
                "name": "Role A",
                "normalized_name": "role a",
                "icon_url": "https://example.test/r1.png",
                "local_path": icon_relative,
                "sha256": _sha256(root / icon_relative),
                "size": (root / icon_relative).stat().st_size,
                "rarity": "2",
                "front_back_type": "Common",
                "trait_ids": ["t1"],
            }
        ],
    }


def _valid_role_features() -> dict:
    return {
        "role_feature_schema_version": 1,
        "recognizer_algorithm_version": "role-card-mask-v1",
        "geometry_version": "cw-slots-1920x1080-v2",
        "empty_template_version": "cw-slots-empty-v1",
        "target_size": [103, 120],
        "avatar_roi": [5, 4, 98, 108],
        "feature_size": [64, 64],
        "hist_bins": [16, 16, 16],
        "min_score": 0.58,
        "low_score": 0.50,
        "min_gap": 0.035,
        "empty_min_score": 0.82,
        "empty_min_gap": 0.08,
        "items": [
            {
                "role_id": "r1",
                "name": "Role A",
                "normalized_name": "role a",
                "rarity": "2",
                "front_back_type": "Common",
                "trait_ids": ["t1"],
                "icon_rgba": _pixel_payload("RGBA", [64, 64]),
                "icon_mask": _pixel_payload("L", [64, 64]),
                "histogram": _zero_histogram_data(4096),
            }
        ],
    }


@lru_cache(maxsize=1)
def _valid_role_features_json() -> str:
    return json.dumps(_valid_role_features(), ensure_ascii=False, separators=(",", ":"))


def _write_valid_equipment_manifest_item(root: Path, cache_key: str = "icon-a") -> None:
    icon_relative = f"equipment/icons/{cache_key}.png"
    icon_path = root / icon_relative
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(cache_key.encode("utf-8"))
    _write_json(
        root / "equipment" / "manifest.json",
        {
            "items": [
                {
                    "cache_key": cache_key,
                    "name": "Icon A",
                    "kind": "basic",
                    "icon_url": f"https://example.test/{cache_key}.png",
                    "big_version": "3.2",
                    "local_path": icon_relative,
                    "sha256": _sha256(icon_path),
                    "size": icon_path.stat().st_size,
                }
            ]
        },
    )


def _refresh_manifest_entry(root: Path, relative: str) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = root / relative
    for entry in manifest["files"]:
        if entry["path"] == relative:
            entry["sha256"] = _sha256(path)
            entry["size"] = path.stat().st_size
            break
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)


def _replace_role_empty_template(root: Path, key: str, data: bytes) -> None:
    role_manifest_path = root / "roles" / "manifest.json"
    role_manifest = json.loads(role_manifest_path.read_text(encoding="utf-8"))
    relative = role_manifest["empty_templates"][key]["local_path"]
    path = root / relative
    path.write_bytes(data)
    role_manifest["empty_templates"][key]["sha256"] = _sha256(path)
    role_manifest["empty_templates"][key]["size"] = path.stat().st_size
    _write_json(role_manifest_path, role_manifest)
    _refresh_manifest_entry(root, "roles/manifest.json")
    _refresh_manifest_entry(root, relative)


def _bundle_files_root(tmp_path: Path, *, equipment_features: dict | None = None) -> Path:
    root = tmp_path / "generated" / "3.2"
    _write_json(root / "raw_config.json", _valid_raw_config())
    guide_config = _valid_guide_config()
    _write_json(root / "guide_config.json", guide_config)
    _write_json(root / "guide_config_enriched.json", guide_config)
    _write_json(root / "indexes.json", _valid_indexes())
    _write_valid_equipment_manifest_item(root)
    if equipment_features is None:
        _write_json_text(root / "equipment" / "features.json", _valid_feature_payload_json())
    else:
        _write_json(root / "equipment" / "features.json", equipment_features)
    _write_json(root / "roles" / "manifest.json", _valid_role_manifest(root))
    _write_json_text(root / "roles" / "features.json", _valid_role_features_json())
    return root


def _bundle(tmp_path: Path, *, equipment_features: dict | None = None) -> Path:
    root = _bundle_files_root(tmp_path, equipment_features=equipment_features)
    write_bundle_manifest(
        root,
        source_manifest={
            "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
            "generator_schema_version": 1,
            "resource_version": "test",
            "season_id": "s1",
            "sub_season_id": "sub1",
            "rpg_game_big_version": "3.2",
            "rpg_game_lineup_tourn_filter": "filter1",
        },
    )
    return root


def _bundle_identity(root: Path) -> str:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["content_digest"])


def _manifest_writer_root(tmp_path: Path) -> Path:
    root = tmp_path / "generated" / "3.2"
    for relative in (
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/features.json",
    ):
        _write_json(root / relative, {})
    _write_json(root / "roles" / "manifest.json", _valid_role_manifest(root))
    _write_json_text(root / "roles" / "features.json", _valid_role_features_json())
    return root


def _skip_equipment_feature_payload_validation(monkeypatch):
    from trail.scenes.cw import static_resources

    monkeypatch.setattr(static_resources, "_validate_equipment_features", lambda payload: None)
    return static_resources


def test_load_cw_resource_bundle_validates_manifest_files(tmp_path):
    bundle = load_cw_resource_bundle_from_path(_bundle(tmp_path))

    assert bundle.big_version == "3.2"
    assert bundle.raw_config["rpg_game_big_version"] == "3.2"
    assert bundle.guide_config["meta"]["big_version"] == "3.2"


def test_load_cw_resource_bundle_validates_role_resources(tmp_path):
    bundle = load_cw_resource_bundle_from_path(_bundle(tmp_path))

    assert bundle.role_manifest["items"][0]["role_id"] == "r1"
    assert bundle.role_features["recognizer_algorithm_version"] == "role-card-mask-v1"


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (_truncated_empty_png_bytes(), "invalid"),
        (_png_bytes(size=(102, 120)), "size"),
        (_png_bytes(mode="RGB"), "mode"),
    ],
)
def test_load_cw_resource_bundle_rejects_bad_role_empty_templates(tmp_path, data: bytes, match: str):
    root = _bundle(tmp_path)
    _replace_role_empty_template(root, "field", data)

    with pytest.raises(TrailError, match=match) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_role_icon_path_escape(tmp_path):
    root = _bundle(tmp_path)
    role_manifest = json.loads((root / "roles" / "manifest.json").read_text(encoding="utf-8"))
    role_manifest["items"][0]["local_path"] = "roles/icons/../escape.png"
    _write_json(root / "roles" / "manifest.json", role_manifest)
    _refresh_manifest_entry(root, "roles/manifest.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_role_feature_manifest_mismatch(tmp_path):
    root = _bundle(tmp_path)
    features = _valid_role_features()
    features["items"][0]["role_id"] = "missing-from-manifest"
    _write_json(root / "roles" / "features.json", features)
    _refresh_manifest_entry(root, "roles/features.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_duplicate_role_icon_local_path(tmp_path):
    root = _bundle(tmp_path)
    role_manifest = json.loads((root / "roles" / "manifest.json").read_text(encoding="utf-8"))
    role_manifest["items"].append({**role_manifest["items"][0], "role_id": "r2", "name": "Role B", "normalized_name": "role b"})
    _write_json(root / "roles" / "manifest.json", role_manifest)
    _refresh_manifest_entry(root, "roles/manifest.json")
    features = _valid_role_features()
    features["items"].append({**features["items"][0], "role_id": "r2", "name": "Role B", "normalized_name": "role b"})
    _write_json(root / "roles" / "features.json", features)
    _refresh_manifest_entry(root, "roles/features.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_bool_role_feature_threshold(tmp_path):
    root = _bundle(tmp_path)
    features = _valid_role_features()
    features["min_score"] = True
    _write_json(root / "roles" / "features.json", features)
    _refresh_manifest_entry(root, "roles/features.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_role_feature_payload_error_does_not_mention_equipment(tmp_path):
    root = _bundle(tmp_path)
    features = _valid_role_features()
    features["items"][0]["icon_rgba"] = []
    _write_json(root / "roles" / "features.json", features)
    _refresh_manifest_entry(root, "roles/features.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"
    assert "cw role feature icon_rgba payload invalid" in str(exc_info.value)
    assert "equipment" not in str(exc_info.value)


def test_role_resource_helpers_build_catalog_and_normalize_names():
    from trail.scenes.cw.role_resources import build_cw_role_catalog, normalize_role_name, safe_role_cache_segment

    catalog = build_cw_role_catalog(
        {
            "role_list": [
                {
                    "id": " r1 ",
                    "name": " Role   A ",
                    "icon": " https://example.test/r1.png ",
                    "rarity": 2,
                    "cost": "3",
                    "front_back_type": "Common",
                    "trait_ids": ["t1", 2],
                },
                {"id": "missing-icon", "name": "Missing Icon"},
            ]
        }
    )

    assert normalize_role_name(" Role\t A  ") == "role a"
    assert catalog[0].role_id == "r1"
    assert catalog[0].name == "Role   A"
    assert catalog[0].normalized_name == "role a"
    assert catalog[0].icon_url == "https://example.test/r1.png"
    assert catalog[0].rarity == "2"
    assert catalog[0].cost == "3"
    assert catalog[0].front_back_type == "Common"
    assert catalog[0].trait_ids == ["t1", "2"]
    assert len(catalog) == 1
    assert safe_role_cache_segment("Role/A") == "Role-A"


def test_write_verified_role_icon_writes_rgba_png_and_rejects_invalid_data(tmp_path):
    from trail.scenes.cw.role_resources import write_verified_role_icon

    source = BytesIO()
    Image.new("RGB", (1, 1), "red").save(source, format="PNG")
    icon_path = tmp_path / "roles" / "icons" / "r1.png"

    write_verified_role_icon(icon_path, source.getvalue())

    with Image.open(icon_path) as image:
        assert image.mode == "RGBA"
        assert image.size == (1, 1)
    with pytest.raises(TrailError) as exc_info:
        write_verified_role_icon(tmp_path / "bad.png", b"not a png")
    assert exc_info.value.code == "CW_ROLE_ICON_INVALID"


def test_cw_resource_source_signature_changes_when_manifest_digest_changes(monkeypatch, tmp_path):
    import trail.scenes.cw.static_resources as static_resources

    fake_file = tmp_path / "trail" / "scenes" / "cw" / "static_resources.py"
    generated = tmp_path / "trail" / "scenes" / "cw" / "generated" / "3.2"
    generated.mkdir(parents=True)
    manifest = generated / "manifest.json"
    first_payload = b'{"content_digest":"a"}'
    second_payload = b'{"content_digest":"b"}'
    assert len(first_payload) == len(second_payload)
    fixed_mtime = 1_700_000_000
    manifest.write_bytes(first_payload)
    os.utime(manifest, (fixed_mtime, fixed_mtime))
    monkeypatch.setattr(static_resources, "__file__", str(fake_file))
    first = static_resources.cw_resource_source_signature(workspace_root=tmp_path)

    manifest.write_bytes(second_payload)
    os.utime(manifest, (fixed_mtime, fixed_mtime))
    second = static_resources.cw_resource_source_signature(workspace_root=tmp_path)

    assert first != second


def _write_equipment_override(
    root: Path,
    *,
    base_identity: str,
    min_score: float = 0.33,
    equipment_features: dict | None = None,
) -> Path:
    override_root = root / ".trail" / "cache" / "cw-equipment-resource"
    _write_valid_equipment_manifest_item(override_root, cache_key="icon-a")
    features = {**(equipment_features or _valid_feature_payload("icon-a")), "min_score": min_score}
    _write_json(override_root / "equipment" / "features.json", features)
    files = []
    for relative in ("equipment/manifest.json", "equipment/features.json", "equipment/icons/icon-a.png"):
        path = override_root / relative
        files.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "base_content_digest": base_identity,
        "files": files,
    }
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(override_root / "manifest.json", manifest)
    return override_root


def test_load_default_cw_resource_bundle_overlays_workspace_equipment_without_replacing_config(monkeypatch, tmp_path):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package", equipment_features=_light_feature_payload())
    base_bundle = static_resources.load_cw_resource_bundle_from_path(package_root)
    _write_equipment_override(
        tmp_path / "workspace",
        base_identity=base_bundle.identity,
        min_score=0.33,
        equipment_features=_light_feature_payload(min_score=0.33),
    )
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.raw_config == _valid_raw_config()
    assert bundle.guide_config == _valid_guide_config()
    assert bundle.guide_config_enriched == _valid_guide_config()
    assert bundle.indexes == _valid_indexes()
    assert bundle.equipment_features["min_score"] == 0.33
    assert bundle.role_manifest == base_bundle.role_manifest
    assert bundle.role_features == base_bundle.role_features
    assert bundle.source_kind == "package+workspace_equipment"
    assert bundle.identity != _bundle_identity(package_root)


def test_load_default_cw_resource_bundle_rejects_matching_workspace_equipment_override_role_files(
    monkeypatch, tmp_path
):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package")
    override_root = _write_equipment_override(tmp_path / "workspace", base_identity=_bundle_identity(package_root), min_score=0.33)
    _write_json(override_root / "roles" / "features.json", _valid_role_features())
    manifest_path = override_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    role_path = override_root / "roles" / "features.json"
    manifest["files"].append({"path": "roles/features.json", "sha256": _sha256(role_path), "size": role_path.stat().st_size})
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)

    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_default_cw_resource_bundle_ignores_stale_workspace_equipment_override(monkeypatch, tmp_path):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package", equipment_features=_light_feature_payload())
    _write_equipment_override(
        tmp_path / "workspace",
        base_identity="old-package",
        min_score=0.33,
        equipment_features=_light_feature_payload(min_score=0.33),
    )
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.source_kind == "package"
    assert bundle.equipment_features["min_score"] == 0.72


def test_load_default_cw_resource_bundle_rejects_matching_incomplete_workspace_equipment_override(monkeypatch, tmp_path):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package", equipment_features=_light_feature_payload())
    override_root = _write_equipment_override(
        tmp_path / "workspace",
        base_identity=_bundle_identity(package_root),
        min_score=0.33,
        equipment_features=_light_feature_payload(min_score=0.33),
    )
    (override_root / "equipment" / "icons" / "icon-a.png").unlink()
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_default_cw_resource_bundle_rejects_workspace_equipment_override_config_files(monkeypatch, tmp_path):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package", equipment_features=_light_feature_payload())
    override_root = _write_equipment_override(
        tmp_path / "workspace",
        base_identity=_bundle_identity(package_root),
        min_score=0.33,
        equipment_features=_light_feature_payload(min_score=0.33),
    )
    _write_json(override_root / "raw_config.json", {"rpg_game_big_version": "bad"})
    manifest_path = override_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = override_root / "raw_config.json"
    manifest["files"].append({"path": "raw_config.json", "sha256": _sha256(raw_path), "size": raw_path.stat().st_size})
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_default_cw_resource_bundle_rejects_workspace_equipment_override_root_symlink(monkeypatch, tmp_path):
    static_resources = _skip_equipment_feature_payload_validation(monkeypatch)

    package_root = _bundle(tmp_path / "package", equipment_features=_light_feature_payload())
    target = _write_equipment_override(
        tmp_path / "target",
        base_identity=_bundle_identity(package_root),
        min_score=0.33,
        equipment_features=_light_feature_payload(min_score=0.33),
    )
    workspace_root = tmp_path / "workspace"
    link = workspace_root / ".trail" / "cache" / "cw-equipment-resource"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=workspace_root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_old_schema(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_schema_version"] = 0
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_hash_mismatch(tmp_path):
    root = _bundle(tmp_path)
    _write_json(root / "raw_config.json", {"rpg_game_big_version": "changed"})

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_manifest_rejects_bool_file_size(tmp_path):
    from trail.scenes.cw import static_resources

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_manifest(
            tmp_path,
            {
                "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
                "files": [{"path": "raw_config.json", "sha256": "abc", "size": True}],
                "content_digest": "unused",
            },
            required_relatives=(),
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_symlink_manifest(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest_target = tmp_path / "manifest-target.json"
    manifest_target.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    try:
        manifest_path.symlink_to(manifest_target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_symlink_bundle_root(tmp_path):
    root = _bundle(tmp_path)
    symlink_root = tmp_path / "bundle-link"
    try:
        symlink_root.symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(symlink_root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_default_cw_resource_bundle_rejects_generated_root_symlink(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    fake_file = tmp_path / "package" / "trail" / "scenes" / "cw" / "static_resources.py"
    generated_link = fake_file.parent / "generated"
    generated_link.parent.mkdir(parents=True)
    external_generated = tmp_path / "external" / "generated"
    package_root = _bundle(tmp_path / "external")
    assert package_root.parent == external_generated
    try:
        generated_link.symlink_to(external_generated, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(static_resources, "__file__", str(fake_file))

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_requires_fixed_files_in_manifest(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "raw_config.json"]
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_requires_manifest_content_digest(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("content_digest", None)
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_raw_config_rejects_missing_required_field():
    from trail.scenes.cw import static_resources

    payload = _valid_raw_config()
    payload.pop("trait_info_list")

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_raw_config(payload)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize("empty_key", ["trait_info_list", "role_list", "portal_list", "equipment_list"])
def test_validate_raw_config_rejects_empty_catalog(empty_key):
    from trail.scenes.cw import static_resources

    payload = _valid_raw_config()
    payload[empty_key] = []

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_raw_config(payload)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_raw_config_rejects_missing_strategy_catalog_list():
    from trail.scenes.cw import static_resources

    payload = _valid_raw_config()
    payload.pop("fight_augment_list")
    payload.pop("strategy_list", None)

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_raw_config(payload)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_resource_catalogs_allow_empty_strategy_catalogs():
    from trail.scenes.cw import static_resources

    raw_config = _valid_raw_config()
    raw_config["fight_augment_list"] = []
    guide_config = _valid_guide_config()
    guide_config["strategy_list"] = []
    indexes = _valid_indexes()
    indexes["strategies_by_title"] = {}

    static_resources._validate_raw_config(raw_config)
    static_resources._validate_guide_config(guide_config, "guide config")
    static_resources._validate_indexes(indexes)


@pytest.mark.parametrize("missing_key", ["lineup_levels", "traits", "roles", "role_tags", "portal_list", "strategy_list"])
def test_validate_guide_config_rejects_missing_required_structure(missing_key):
    from trail.scenes.cw import static_resources

    payload = _valid_guide_config()
    payload.pop(missing_key)

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_guide_config(payload, "guide config")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_guide_config_rejects_missing_big_version():
    from trail.scenes.cw import static_resources

    payload = _valid_guide_config()
    payload["meta"] = {}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_guide_config(payload, "guide config")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize("empty_key", ["traits", "roles", "portal_list"])
def test_validate_guide_config_rejects_empty_catalog(empty_key):
    from trail.scenes.cw import static_resources

    payload = _valid_guide_config()
    payload[empty_key] = []

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_guide_config(payload, "guide config")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize(
    "missing_key",
    [
        "traits_by_name",
        "roles_by_name",
        "portals_by_title",
        "strategies_by_title",
        "equipment_by_cache_key",
    ],
)
def test_validate_indexes_rejects_missing_required_key(missing_key):
    from trail.scenes.cw import static_resources

    indexes = _valid_indexes()
    indexes.pop(missing_key)

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_indexes(indexes)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize(
    "bad_key",
    [
        "traits_by_name",
        "roles_by_name",
        "portals_by_title",
        "strategies_by_title",
        "equipment_by_cache_key",
    ],
)
def test_validate_indexes_rejects_required_key_that_is_not_object(bad_key):
    from trail.scenes.cw import static_resources

    indexes = _valid_indexes()
    indexes[bad_key] = []

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_indexes(indexes)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize(
    "empty_key",
    ["traits_by_name", "roles_by_name", "portals_by_title", "equipment_by_cache_key"],
)
def test_validate_indexes_rejects_empty_core_indexes(empty_key):
    from trail.scenes.cw import static_resources

    indexes = _valid_indexes()
    indexes[empty_key] = {}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_indexes(indexes)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_manifest_paths_requires_icon_paths_in_manifest_files(tmp_path):
    from trail.scenes.cw import static_resources

    icon_path = tmp_path / "equipment" / "icons" / "icon-a.png"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_bytes(b"icon-a")
    equipment_manifest = {"items": [{"local_path": "equipment/icons/icon-a.png"}]}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_manifest_paths_listed(tmp_path, equipment_manifest, set())

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_unreferenced_generated_files(tmp_path):
    root = _bundle(tmp_path)
    (root / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_extra_manifest_files(tmp_path):
    root = _bundle(tmp_path)
    extra_path = root / "semantic-extra.json"
    extra_path.write_text("{}", encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "semantic-extra.json",
            "sha256": _sha256(extra_path),
            "size": extra_path.stat().st_size,
        }
    )
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_manifest_file_paths_with_parent_segments(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "equipment/../raw_config.json"
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_manifest_rejects_local_paths_with_parent_segments():
    from trail.scenes.cw import static_resources

    equipment_manifest = {
        "items": [
            {
                "cache_key": "icon-a",
                "name": "Icon A",
                "kind": "basic",
                "icon_url": "https://example.test/icon-a.png",
                "big_version": "3.2",
                "local_path": "equipment/icons/../icons/icon-a.png",
                "sha256": "placeholder",
                "size": 1,
            }
        ]
    }

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_manifest_payload(equipment_manifest)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_manifest_rejects_local_paths_outside_icon_dir():
    from trail.scenes.cw import static_resources

    equipment_manifest = {
        "items": [
            {
                "cache_key": "icon-a",
                "name": "Icon A",
                "kind": "basic",
                "icon_url": "https://example.test/icon-a.png",
                "big_version": "3.2",
                "local_path": "semantic-extra.json",
                "sha256": "placeholder",
                "size": 1,
            }
        ]
    }

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_manifest_payload(equipment_manifest)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_features_rejects_bad_pixel_payload():
    from trail.scenes.cw import static_resources

    item = _valid_feature_item()
    item["feature_rgba"] = []
    payload = {**_valid_feature_payload(), "items": [item]}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_features(payload)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_features_rejects_empty_items():
    from trail.scenes.cw import static_resources

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_features({**_valid_feature_payload(), "items": []})

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_validate_equipment_manifest_rejects_empty_items():
    from trail.scenes.cw import static_resources

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_manifest_payload({"items": []})

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize("bad_value", ["x", 999])
def test_validate_equipment_features_rejects_bad_pixel_data_element(bad_value):
    from trail.scenes.cw import static_resources

    item = _valid_feature_item()
    item["feature_rgba"]["data"] = [bad_value] * (32 * 32 * 4)
    payload = {**_valid_feature_payload(), "items": [item]}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_features(payload)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize("feature_items", [[], [_valid_feature_item("icon-b", "Icon B")]])
def test_validate_equipment_feature_manifest_keys_rejects_cache_key_mismatch(feature_items):
    from trail.scenes.cw import static_resources

    equipment_manifest = {"items": [{"cache_key": "icon-a"}]}
    equipment_features = {"items": feature_items}

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_equipment_feature_manifest_keys(equipment_manifest, equipment_features)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize(
    "equipment_manifest",
    [
        {"items": {"local_path": "equipment/icons/a.png"}},
        {"items": ["equipment/icons/a.png"]},
        {"items": [{"name": "invalid"}]},
        {"items": [{"local_path": 1}]},
    ],
)
def test_write_bundle_manifest_rejects_equipment_items_without_local_path(tmp_path, equipment_manifest):
    root = _manifest_writer_root(tmp_path)
    _write_json(root / "equipment" / "manifest.json", equipment_manifest)

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_rejects_incomplete_equipment_items(tmp_path):
    root = _manifest_writer_root(tmp_path)
    icon_relative = "equipment/icons/icon-a.png"
    icon_path = root / icon_relative
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(b"icon-a")
    _write_json(
        root / "equipment" / "manifest.json",
        {
            "items": [
                {
                    "cache_key": "icon-a",
                    "name": "Icon A",
                    "kind": "basic",
                    "icon_url": "https://example.test/icon-a.png",
                    "big_version": "3.2",
                    "local_path": icon_relative,
                }
            ]
        },
    )

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_rejects_equipment_icon_checksum_mismatch(tmp_path):
    root = _manifest_writer_root(tmp_path)
    icon_relative = "equipment/icons/icon-a.png"
    icon_path = root / icon_relative
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(b"icon-a")
    _write_json(
        root / "equipment" / "manifest.json",
        {
            "items": [
                {
                    "cache_key": "icon-a",
                    "name": "Icon A",
                    "kind": "basic",
                    "icon_url": "https://example.test/icon-a.png",
                    "big_version": "3.2",
                    "local_path": icon_relative,
                    "sha256": "bad",
                    "size": icon_path.stat().st_size,
                }
            ]
        },
    )

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_rejects_duplicate_equipment_icon_paths(tmp_path):
    root = _manifest_writer_root(tmp_path)
    icon_relative = "equipment/icons/icon-a.png"
    icon_path = root / icon_relative
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(b"icon-a")
    item = {
        "cache_key": "icon-a",
        "name": "Icon A",
        "kind": "basic",
        "icon_url": "https://example.test/icon-a.png",
        "big_version": "3.2",
        "local_path": icon_relative,
        "sha256": _sha256(icon_path),
        "size": icon_path.stat().st_size,
    }
    _write_json(root / "equipment" / "manifest.json", {"items": [item, {**item, "cache_key": "icon-b"}]})

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_rejects_equipment_icon_path_conflicting_with_fixed_file(tmp_path):
    root = _manifest_writer_root(tmp_path)
    feature_path = root / "equipment" / "features.json"
    _write_json(
        root / "equipment" / "manifest.json",
        {
            "items": [
                {
                    "cache_key": "features",
                    "name": "Features",
                    "kind": "basic",
                    "icon_url": "https://example.test/features.png",
                    "big_version": "3.2",
                    "local_path": "equipment/features.json",
                    "sha256": _sha256(feature_path),
                    "size": feature_path.stat().st_size,
                }
            ]
        },
    )

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_rejects_normalized_equipment_icon_path_conflict(tmp_path):
    root = _manifest_writer_root(tmp_path)
    raw_config_path = root / "raw_config.json"
    _write_json(
        root / "equipment" / "manifest.json",
        {
            "items": [
                {
                    "cache_key": "raw-config",
                    "name": "Raw Config",
                    "kind": "basic",
                    "icon_url": "https://example.test/raw-config.png",
                    "big_version": "3.2",
                    "local_path": "equipment/../raw_config.json",
                    "sha256": _sha256(raw_config_path),
                    "size": raw_config_path.stat().st_size,
                }
            ]
        },
    )

    with pytest.raises(TrailError) as exc_info:
        write_bundle_manifest(
            root,
            source_manifest={"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION},
        )

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_write_bundle_manifest_includes_equipment_icons_and_loads(tmp_path):
    root = _bundle_files_root(tmp_path)
    icon_relative = "equipment/icons/icon-a.png"

    manifest = write_bundle_manifest(
        root,
        source_manifest={
            "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
            "generator_schema_version": 1,
            "resource_version": "test",
            "season_id": "s1",
            "sub_season_id": "sub1",
            "rpg_game_big_version": "3.2",
            "rpg_game_lineup_tourn_filter": "filter1",
        },
    )
    bundle = load_cw_resource_bundle_from_path(root)

    assert icon_relative in {entry["path"] for entry in manifest["files"]}
    assert bundle.equipment_manifest["items"][0]["local_path"] == icon_relative
