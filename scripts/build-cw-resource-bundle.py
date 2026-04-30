from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_recognition import build_precomputed_equipment_features
from trail.scenes.cw.equipment_resources import (
    EQUIPMENT_ICON_MAX_BYTES,
    EquipmentCatalogEntry,
    build_cw_equipment_catalog,
    download_equipment_icon_bytes,
    equipment_bundle_manifest_entry,
    safe_equipment_cache_segment,
    write_verified_equipment_icon,
)
from trail.scenes.cw.guide import download_cw_guide_config_data, normalize_cw_guide_config_data
from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    load_cw_resource_bundle_from_path,
    write_bundle_json,
    write_bundle_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "trail" / "scenes" / "cw" / "generated"


def _is_path_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _iter_dicts(value: object):
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            yield item


def _normalized_name(value: object) -> str:
    return "".join(str(value or "").split()).lower()


def _by_key(items: Iterable[dict], key: str) -> dict[str, dict]:
    return {str(item[key]): item for item in items if item.get(key) is not None}


def _by_name(items: Iterable[dict], key: str = "name") -> dict[str, dict]:
    return {str(item[key]): item for item in items if item.get(key)}


def _indexes(config: dict, equipment_items: list[dict]) -> dict:
    traits = list(_iter_dicts(config.get("traits")))
    roles = list(_iter_dicts(config.get("roles")))
    portals = list(_iter_dicts(config.get("portal_list")))
    strategies = list(_iter_dicts(config.get("strategy_list")))
    role_lookup_candidates = []
    for item in roles:
        if not item.get("name"):
            continue
        role_lookup_candidates.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "normalized_name": _normalized_name(item.get("name")),
                "front_back_type": item.get("front_back_type"),
                "trait_ids": list(item.get("trait_ids") or []),
            }
        )

    return {
        "traits_by_name": _by_name(traits),
        "traits_by_id": _by_key(traits, "id"),
        "roles_by_name": _by_name(roles),
        "roles_by_id": _by_key(roles, "id"),
        "portals_by_title": _by_name(portals, "title"),
        "portals_by_id": _by_key(portals, "portal_id"),
        "strategies_by_title": _by_name(strategies, "title"),
        "role_lookup_candidates": role_lookup_candidates,
        "equipment_by_cache_key": _by_key(equipment_items, "cache_key"),
        "equipment_by_name": _by_name(equipment_items),
    }


def _build_precomputed_equipment_features(
    icon_pairs: Iterable[tuple[EquipmentCatalogEntry, Image.Image]],
) -> dict:
    return build_precomputed_equipment_features(icon_pairs)


def _has_enriched_trait_layers(config: Mapping[str, Any]) -> bool:
    return any(isinstance(item, dict) and item.get("layers") for item in _iter_dicts(config.get("traits")))


def _source_manifest(raw_config: Mapping[str, Any], *, big_version: str) -> dict[str, Any]:
    return {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "generator_schema_version": 1,
        "resource_version": big_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season_id": raw_config.get("season_id"),
        "sub_season_id": raw_config.get("sub_season_id"),
        "rpg_game_big_version": big_version,
        "rpg_game_lineup_tourn_filter": raw_config.get("rpg_game_lineup_tourn_filter"),
    }


def _ensure_generated_child(output_root: Path, path: Path) -> None:
    if _is_path_link(output_root):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle output root must not be symlink: {output_root}")
    try:
        path.resolve().is_relative_to(output_root.resolve())
    except ValueError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle output escapes generated root: {path}") from exc
    if not path.resolve().is_relative_to(output_root.resolve()):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle output escapes generated root: {path}")


def _replace_generated_bundle(output_root: Path, target_root: Path, staging_parent: Path, staging_root: Path) -> None:
    _ensure_generated_child(output_root, target_root)
    _ensure_generated_child(output_root, staging_parent)
    _ensure_generated_child(output_root, staging_root)
    staging_parent_resolved = staging_parent.resolve()
    for child in output_root.iterdir():
        if child.resolve() == staging_parent_resolved:
            continue
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    if target_root.exists():
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle target is not replaceable: {target_root}")
    shutil.move(str(staging_root), str(target_root))


def build_cw_resource_bundle(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    raw_config_fetcher=download_cw_guide_config_data,
    config_normalizer=normalize_cw_guide_config_data,
    icon_fetcher=download_equipment_icon_bytes,
    timeout: int = 10,
) -> dict:
    raw_config = dict(raw_config_fetcher(timeout=timeout))
    big_version = str(raw_config.get("rpg_game_big_version") or "")
    if not big_version:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle config missing rpg_game_big_version")
    output_root = Path(output_root)
    if _is_path_link(output_root):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle output root must not be symlink: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    safe_big_version = safe_equipment_cache_segment(big_version)
    bundle_root = output_root / safe_big_version

    with TemporaryDirectory(prefix="trail-cw-resource-build-") as cache_dir:
        guide_config = config_normalizer(
            raw_config,
            timeout=timeout,
            workspace_root=cache_dir,
            enrich_traits=False,
        )
        guide_config_enriched = config_normalizer(
            raw_config,
            timeout=timeout,
            workspace_root=cache_dir,
            enrich_traits=True,
        )
    if not _has_enriched_trait_layers(guide_config_enriched):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw enriched traits missing layer data")

    catalog = build_cw_equipment_catalog(raw_config)
    with TemporaryDirectory(prefix=f".{safe_big_version}-", dir=output_root) as staging_dir:
        staging_parent = Path(staging_dir)
        staging_root = staging_parent / safe_big_version
        equipment_items: list[dict[str, Any]] = []
        icon_pairs: list[tuple[EquipmentCatalogEntry, Image.Image]] = []
        for entry in catalog:
            safe_key = safe_equipment_cache_segment(entry.cache_key)
            relative = f"equipment/icons/{safe_key}.png"
            path = staging_root / relative
            data = icon_fetcher(entry.icon_url, timeout=timeout, max_bytes=EQUIPMENT_ICON_MAX_BYTES)
            write_verified_equipment_icon(path, data)
            equipment_items.append(
                equipment_bundle_manifest_entry(
                    entry,
                    local_path=relative,
                    sha256=sha256(path.read_bytes()).hexdigest(),
                    size=path.stat().st_size,
                )
            )
            with Image.open(path) as image:
                icon_pairs.append((entry, image.convert("RGBA").copy()))

        write_bundle_json(staging_root / "raw_config.json", raw_config)
        write_bundle_json(staging_root / "guide_config.json", dict(guide_config))
        write_bundle_json(staging_root / "guide_config_enriched.json", dict(guide_config_enriched))
        write_bundle_json(staging_root / "equipment" / "manifest.json", {"items": equipment_items})
        write_bundle_json(staging_root / "equipment" / "features.json", _build_precomputed_equipment_features(icon_pairs))
        write_bundle_json(staging_root / "indexes.json", _indexes(dict(guide_config_enriched), equipment_items))
        write_bundle_manifest(staging_root, source_manifest=_source_manifest(raw_config, big_version=big_version), source_kind="build")
        load_cw_resource_bundle_from_path(staging_root)
        count = len(equipment_items)
        _replace_generated_bundle(output_root, bundle_root, staging_parent, staging_root)

    return {"bundle_root": str(bundle_root), "big_version": big_version, "count": count}


def main() -> None:
    result = build_cw_resource_bundle()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
