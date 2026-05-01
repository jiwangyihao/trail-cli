from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    load_cw_resource_bundle_from_path,
    write_bundle_manifest,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_content_digest(manifest: dict) -> str:
    payload = {key: value for key, value in manifest.items() if key != "content_digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=None)
def _zero_pixel_data(length: int) -> tuple[int, ...]:
    return (0,) * length


def _pixel_payload(mode: str, size: list[int]) -> dict:
    channels = 4 if mode == "RGBA" else 1
    return {"mode": mode, "size": size, "data": _zero_pixel_data(size[0] * size[1] * channels)}


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


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "generated" / "3.2"
    _write_json(root / "raw_config.json", _valid_raw_config())
    guide_config = _valid_guide_config()
    _write_json(root / "guide_config.json", guide_config)
    _write_json(root / "guide_config_enriched.json", guide_config)
    _write_json(root / "indexes.json", _valid_indexes())
    _write_valid_equipment_manifest_item(root)
    _write_json(root / "equipment" / "features.json", _valid_feature_payload())
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
    return root


def test_load_cw_resource_bundle_validates_manifest_files(tmp_path):
    bundle = load_cw_resource_bundle_from_path(_bundle(tmp_path))

    assert bundle.big_version == "3.2"
    assert bundle.raw_config["rpg_game_big_version"] == "3.2"
    assert bundle.guide_config["meta"]["big_version"] == "3.2"


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


def _write_equipment_override(root: Path, *, base_identity: str, min_score: float = 0.33) -> Path:
    override_root = root / ".trail" / "cache" / "cw-equipment-resource"
    _write_valid_equipment_manifest_item(override_root, cache_key="icon-a")
    features = _valid_feature_payload("icon-a")
    features["min_score"] = min_score
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
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    _write_equipment_override(tmp_path / "workspace", base_identity=_bundle_identity(package_root), min_score=0.33)
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.raw_config == _valid_raw_config()
    assert bundle.guide_config == _valid_guide_config()
    assert bundle.guide_config_enriched == _valid_guide_config()
    assert bundle.indexes == _valid_indexes()
    assert bundle.equipment_features["min_score"] == 0.33
    assert bundle.source_kind == "package+workspace_equipment"
    assert bundle.identity != _bundle_identity(package_root)


def test_load_default_cw_resource_bundle_ignores_stale_workspace_equipment_override(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    _write_equipment_override(tmp_path / "workspace", base_identity="old-package", min_score=0.33)
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.source_kind == "package"
    assert bundle.equipment_features["min_score"] == 0.72


def test_load_default_cw_resource_bundle_rejects_matching_incomplete_workspace_equipment_override(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    override_root = _write_equipment_override(tmp_path / "workspace", base_identity=_bundle_identity(package_root), min_score=0.33)
    (override_root / "equipment" / "icons" / "icon-a.png").unlink()
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    with pytest.raises(TrailError) as exc_info:
        static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_default_cw_resource_bundle_rejects_workspace_equipment_override_config_files(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    override_root = _write_equipment_override(tmp_path / "workspace", base_identity=_bundle_identity(package_root), min_score=0.33)
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
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    target = _write_equipment_override(tmp_path / "target", base_identity=_bundle_identity(package_root), min_score=0.33)
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


def test_load_cw_resource_bundle_rejects_bool_manifest_file_size(tmp_path):
    root = _bundle(tmp_path)
    icon_path = root / "equipment" / "icons" / "icon-a.png"
    icon_path.write_bytes(b"x")
    equipment_manifest_path = root / "equipment" / "manifest.json"
    equipment_manifest = json.loads(equipment_manifest_path.read_text(encoding="utf-8"))
    equipment_manifest["items"][0]["sha256"] = _sha256(icon_path)
    equipment_manifest["items"][0]["size"] = icon_path.stat().st_size
    _write_json(equipment_manifest_path, equipment_manifest)
    _refresh_manifest_entry(root, "equipment/manifest.json")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "equipment/icons/icon-a.png":
            entry["sha256"] = _sha256(icon_path)
            entry["size"] = True
            break
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

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


def test_load_cw_resource_bundle_rejects_raw_config_missing_required_field(tmp_path):
    root = _bundle(tmp_path)
    payload = _valid_raw_config()
    payload.pop("trait_info_list")
    _write_json(root / "raw_config.json", payload)
    _refresh_manifest_entry(root, "raw_config.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

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


def test_load_cw_resource_bundle_allows_empty_strategy_catalogs(tmp_path):
    root = _bundle(tmp_path)
    raw_config = _valid_raw_config()
    raw_config["fight_augment_list"] = []
    guide_config = _valid_guide_config()
    guide_config["strategy_list"] = []
    indexes = _valid_indexes()
    indexes["strategies_by_title"] = {}
    _write_json(root / "raw_config.json", raw_config)
    _write_json(root / "guide_config.json", guide_config)
    _write_json(root / "guide_config_enriched.json", guide_config)
    _write_json(root / "indexes.json", indexes)
    for relative in [
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
    ]:
        _refresh_manifest_entry(root, relative)

    bundle = load_cw_resource_bundle_from_path(root)

    assert bundle.raw_config["fight_augment_list"] == []
    assert bundle.guide_config["strategy_list"] == []
    assert bundle.indexes["strategies_by_title"] == {}


@pytest.mark.parametrize("missing_key", ["lineup_levels", "traits", "roles", "role_tags", "portal_list", "strategy_list"])
def test_validate_guide_config_rejects_missing_required_structure(missing_key):
    from trail.scenes.cw import static_resources

    payload = _valid_guide_config()
    payload.pop(missing_key)

    with pytest.raises(TrailError) as exc_info:
        static_resources._validate_guide_config(payload, "guide config")

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


@pytest.mark.parametrize("relative", ["guide_config.json", "guide_config_enriched.json"])
def test_load_cw_resource_bundle_rejects_guide_config_missing_big_version(tmp_path, relative):
    root = _bundle(tmp_path)
    payload = _valid_guide_config()
    payload["meta"] = {}
    _write_json(root / relative, payload)
    _refresh_manifest_entry(root, relative)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

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


def test_load_cw_resource_bundle_requires_equipment_icon_paths_in_manifest_files(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "equipment/icons/icon-a.png"]
    manifest["content_digest"] = _bundle_content_digest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

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
    root = _bundle(tmp_path)
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
                    "sha256": _sha256(icon_path),
                    "size": icon_path.stat().st_size,
                }
            ]
        },
    )
    _write_json(root / "equipment" / "features.json", _valid_feature_payload())

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
