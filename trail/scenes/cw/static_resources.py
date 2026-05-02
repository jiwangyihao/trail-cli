from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
from shutil import copy2
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_resources import (
    EQUIPMENT_ICON_CACHE_RELATIVE,
    EquipmentCatalogEntry,
    safe_equipment_cache_segment,
)


CW_RESOURCE_BUNDLE_SCHEMA_VERSION = 1
CW_EQUIPMENT_FEATURE_SCHEMA_VERSION = 1
CW_ROLE_MANIFEST_SCHEMA_VERSION = 1
CW_ROLE_FEATURE_SCHEMA_VERSION = 1
CW_ROLE_RECOGNIZER_ALGORITHM_VERSION = "role-card-mask-v1"
CW_SLOT_GEOMETRY_VERSION = "cw-slots-1920x1080-v2"
CW_SLOT_EMPTY_TEMPLATE_VERSION = "cw-slots-empty-v1"
CW_GENERATED_RELATIVE = Path("scenes") / "cw" / "generated"

_FIXED_BUNDLE_RELATIVES = (
    "raw_config.json",
    "guide_config.json",
    "guide_config_enriched.json",
    "indexes.json",
    "equipment/manifest.json",
    "equipment/features.json",
    "roles/manifest.json",
    "roles/features.json",
    "roles/empty/field-v1.png",
    "roles/empty/hand-v1.png",
)
_FEATURE_ITEM_REQUIRED_KEYS = (
    "cache_key",
    "name",
    "feature_rgba",
    "match_rgba",
    "feature_mask",
    "match_mask",
)
_EQUIPMENT_MANIFEST_REQUIRED_KEYS = (
    "cache_key",
    "name",
    "kind",
    "icon_url",
    "big_version",
    "local_path",
    "sha256",
    "size",
)
_ROLE_MANIFEST_REQUIRED_KEYS = (
    "role_id",
    "name",
    "normalized_name",
    "icon_url",
    "local_path",
    "sha256",
    "size",
    "front_back_type",
    "trait_ids",
)
_ROLE_FEATURE_ITEM_REQUIRED_KEYS = (
    "role_id",
    "name",
    "normalized_name",
    "front_back_type",
    "trait_ids",
    "icon_rgba",
    "icon_mask",
    "histogram",
)
_ROLE_EMPTY_TEMPLATE_RELATIVES = {
    "field": "roles/empty/field-v1.png",
    "hand": "roles/empty/hand-v1.png",
}
_ROLE_EMPTY_TEMPLATE_SIZE = (103, 120)


@dataclass(frozen=True)
class CwResourceBundle:
    root: Path
    manifest: dict[str, Any]
    raw_config: dict[str, Any]
    guide_config: dict[str, Any]
    guide_config_enriched: dict[str, Any]
    indexes: dict[str, Any]
    equipment_manifest: dict[str, Any]
    equipment_features: dict[str, Any]
    role_manifest: dict[str, Any]
    role_features: dict[str, Any]
    source_kind: str = "package"
    manifest_path: Path | None = None
    manifest_mtime: float = 0.0
    equipment_manifest_path: Path | None = None
    equipment_manifest_mtime: float = 0.0
    role_manifest_path: Path | None = None
    role_manifest_mtime: float = 0.0
    override_manifest_path: Path | None = None
    override_manifest_mtime: float = 0.0
    override_identity: str = ""

    @property
    def big_version(self) -> str:
        return str(self.manifest.get("rpg_game_big_version") or "")

    @property
    def identity(self) -> str:
        content_digest = self.manifest.get("content_digest")
        if self.override_identity:
            base = content_digest if isinstance(content_digest, str) and content_digest else _manifest_digest(self.manifest)
            return _manifest_digest({"base_content_digest": base, "equipment_override_digest": self.override_identity})
        if isinstance(content_digest, str) and content_digest:
            return content_digest
        return _manifest_digest(self.manifest)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file must not be symlink: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", f"missing cw resource bundle file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"invalid cw resource bundle file: {path}") from exc
    if not isinstance(payload, dict):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file must be object: {path}")
    return payload


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _manifest_without_digest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "content_digest"}


def _clean_bundle_relative(value: object, *, source: str = "cw resource bundle file") -> str:
    if not isinstance(value, str) or not value:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} path invalid")
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (
        normalized in {"", "."}
        or path.is_absolute()
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
        or ".." in path.parts
    ):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} escapes root: {value}")
    return path.as_posix()


def _clean_equipment_icon_relative(value: object) -> str:
    relative = _clean_bundle_relative(value, source="cw equipment icon")
    parts = PurePosixPath(relative).parts
    if len(parts) != 3 or parts[:2] != ("equipment", "icons") or not parts[2].endswith(".png"):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon path invalid: {value}")
    return relative


def _clean_role_icon_relative(value: object) -> str:
    relative = _clean_bundle_relative(value, source="cw role icon")
    parts = PurePosixPath(relative).parts
    if len(parts) != 3 or parts[:2] != ("roles", "icons") or not parts[2].endswith(".png"):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role icon path invalid: {value}")
    return relative


def _clean_role_empty_relative(value: object) -> str:
    relative = _clean_bundle_relative(value, source="cw role empty template")
    if relative not in set(_ROLE_EMPTY_TEMPLATE_RELATIVES.values()):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template path invalid: {value}")
    return relative


def _validate_role_empty_template_bytes(data: bytes, source: str) -> None:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.mode != "RGBA":
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template mode invalid: {source}")
            if image.size != _ROLE_EMPTY_TEMPLATE_SIZE:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template size invalid: {source}")
            image.load()
    except TrailError:
        raise
    except OSError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template invalid: {source}") from exc


def _validate_role_empty_template_path(path: Path, source: str | None = None) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template invalid: {source or path}") from exc
    _validate_role_empty_template_bytes(data, source or str(path))


def _is_path_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _bundle_path(
    root: Path,
    relative: str,
    *,
    missing_code: str = "CW_RESOURCE_BUNDLE_MISSING",
    root_resolved: Path | None = None,
) -> Path:
    clean_relative = _clean_bundle_relative(relative)
    root_resolved = root_resolved or root.resolve()
    candidate = root
    for part in PurePosixPath(clean_relative).parts:
        candidate = candidate / part
        if _is_path_link(candidate):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file must not be symlink: {relative}")
    path = (root / clean_relative).resolve()
    try:
        is_inside_root = path.is_relative_to(root_resolved)
    except ValueError:
        is_inside_root = False
    if not is_inside_root:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file escapes root: {relative}")
    if not path.is_file():
        raise TrailError(missing_code, f"missing cw resource bundle file: {relative}")
    return path


def _bundle_relative(root: Path, path: Path, *, root_resolved: Path | None = None) -> str:
    return path.relative_to(root_resolved or root.resolve()).as_posix()


def write_bundle_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def bundle_file_entry(root: Path, relative: str) -> dict[str, Any]:
    root_resolved = root.resolve()
    path = _bundle_path(root, relative, root_resolved=root_resolved)
    return {
        "path": _bundle_relative(root, path, root_resolved=root_resolved),
        "sha256": _file_sha256(path),
        "size": path.stat().st_size,
    }


def write_bundle_manifest(
    root: Path,
    *,
    source_manifest: dict[str, Any],
    source_kind: str = "package",
) -> dict[str, Any]:
    root_resolved = root.resolve()
    file_relatives = [*_FIXED_BUNDLE_RELATIVES]
    equipment_manifest = _read_json(root / "equipment" / "manifest.json")
    role_manifest = _read_json(root / "roles" / "manifest.json")
    _validate_equipment_manifest(root, equipment_manifest)
    _validate_role_manifest(root, role_manifest)
    items = equipment_manifest.get("items")
    seen_local_paths: set[str] = set(_FIXED_BUNDLE_RELATIVES)
    for item in items:
        local_path = _clean_equipment_icon_relative(item["local_path"])
        normalized_local_path = _bundle_relative(
            root,
            _bundle_path(root, local_path, root_resolved=root_resolved),
            root_resolved=root_resolved,
        )
        if normalized_local_path in seen_local_paths:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment manifest duplicate local_path: {local_path}")
        seen_local_paths.add(normalized_local_path)
        file_relatives.append(normalized_local_path)
    for item in role_manifest["items"]:
        local_path = _clean_role_icon_relative(item["local_path"])
        normalized_local_path = _bundle_relative(
            root,
            _bundle_path(root, local_path, root_resolved=root_resolved),
            root_resolved=root_resolved,
        )
        if normalized_local_path in seen_local_paths:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest duplicate local_path: {local_path}")
        seen_local_paths.add(normalized_local_path)
        file_relatives.append(normalized_local_path)
    manifest = {
        **source_manifest,
        "source_kind": source_kind,
        "files": [bundle_file_entry(root, relative) for relative in file_relatives],
    }
    manifest["content_digest"] = _manifest_digest(_manifest_without_digest(manifest))
    write_bundle_json(root / "manifest.json", manifest)
    return manifest


def equipment_catalog_from_manifest(manifest: dict[str, Any]) -> list[EquipmentCatalogEntry]:
    catalog: list[EquipmentCatalogEntry] = []
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if not isinstance(items, list):
        return catalog
    for item in items:
        if not isinstance(item, dict):
            continue
        catalog.append(
            EquipmentCatalogEntry(
                cache_key=str(item["cache_key"]),
                id=None if item.get("id") is None else str(item.get("id")),
                name=str(item["name"]),
                kind=str(item.get("kind") or "advanced"),
                category=None if item.get("category") is None else str(item.get("category")),
                category_name=None if item.get("category_name") is None else str(item.get("category_name")),
                icon_url=str(item.get("icon_url") or ""),
                big_version=str(item.get("big_version") or ""),
            )
        )
    return catalog


def write_workspace_equipment_icons_from_cache(
    *,
    override_root: Path,
    catalog: list[EquipmentCatalogEntry],
    workspace_root: str | Path,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not catalog:
        return {"items": items}
    cache_version_dir = Path(workspace_root) / EQUIPMENT_ICON_CACHE_RELATIVE / safe_equipment_cache_segment(catalog[0].big_version)
    for entry in catalog:
        safe_key = safe_equipment_cache_segment(entry.cache_key)
        source = cache_version_dir / "icons" / f"{safe_key}.png"
        relative = f"equipment/icons/{safe_key}.png"
        target = override_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target)
        items.append(
            {
                "cache_key": entry.cache_key,
                "id": entry.id,
                "name": entry.name,
                "kind": entry.kind,
                "category": entry.category,
                "category_name": entry.category_name,
                "icon_url": entry.icon_url,
                "big_version": entry.big_version,
                "local_path": relative,
                "sha256": _file_sha256(target),
                "size": target.stat().st_size,
            }
        )
    return {"items": items}


def write_equipment_override_manifest(override_root: Path, *, base_bundle: CwResourceBundle) -> dict[str, Any]:
    files = [
        bundle_file_entry(override_root, "equipment/manifest.json"),
        bundle_file_entry(override_root, "equipment/features.json"),
    ]
    equipment_manifest = _read_json(override_root / "equipment" / "manifest.json")
    items = equipment_manifest.get("items") if isinstance(equipment_manifest, dict) else []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("local_path"), str):
            files.append(bundle_file_entry(override_root, str(item["local_path"])))
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "source_kind": "workspace_equipment",
        "base_content_digest": base_bundle.identity,
        "base_resource_version": base_bundle.manifest.get("resource_version"),
        "rpg_game_big_version": base_bundle.big_version,
        "files": files,
    }
    manifest["content_digest"] = _manifest_digest(_manifest_without_digest(manifest))
    write_bundle_json(override_root / "manifest.json", manifest)
    return manifest


def _validate_manifest(
    root: Path,
    manifest: dict[str, Any],
    *,
    required_relatives: tuple[str, ...] = _FIXED_BUNDLE_RELATIVES,
    root_resolved: Path | None = None,
) -> set[str]:
    if manifest.get("bundle_schema_version") != CW_RESOURCE_BUNDLE_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle schema version unsupported")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle manifest missing files")
    root_resolved = root_resolved or root.resolve()
    seen_paths: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle file entry invalid")
        relative = entry.get("path")
        expected_hash = entry.get("sha256")
        expected_size = entry.get("size")
        if (
            not isinstance(relative, str)
            or not isinstance(expected_hash, str)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
        ):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle file entry incomplete")
        clean_relative = _clean_bundle_relative(relative)
        path = _bundle_path(root, clean_relative, root_resolved=root_resolved)
        normalized_relative = _bundle_relative(root, path, root_resolved=root_resolved)
        if normalized_relative in seen_paths:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle duplicate file entry: {relative}")
        seen_paths.add(normalized_relative)
        if path.stat().st_size != expected_size or _file_sha256(path) != expected_hash:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file checksum mismatch: {relative}")
    missing_fixed = [relative for relative in required_relatives if relative not in seen_paths]
    if missing_fixed:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle manifest missing fixed file: {missing_fixed[0]}")
    expected_digest = manifest.get("content_digest")
    if not isinstance(expected_digest, str) or not expected_digest:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle manifest missing content_digest")
    actual_digest = _manifest_digest(_manifest_without_digest(manifest))
    if actual_digest != expected_digest:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle manifest digest mismatch")
    return seen_paths


def _validate_raw_config(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("rpg_game_big_version"), str) or not payload.get("rpg_game_big_version"):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw raw config missing rpg_game_big_version")
    for key in ("trait_info_list", "role_list", "portal_list", "equipment_list"):
        _validate_non_empty_list(payload, key, "cw raw config")
    if not any(isinstance(payload.get(key), list) for key in ("fight_augment_list", "strategy_list")):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw raw config missing fight_augment_list or strategy_list")


def _validate_guide_config(payload: dict[str, Any], source: str) -> None:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw {source} missing meta")
    if not isinstance(meta.get("big_version"), str) or not meta.get("big_version"):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw {source} missing meta.big_version")
    for key in ("lineup_levels", "role_tags"):
        _validate_list(payload, key, f"cw {source}")
    for key in ("traits", "roles", "portal_list"):
        _validate_non_empty_list(payload, key, f"cw {source}")
    _validate_list(payload, "strategy_list", f"cw {source}")


def _validate_indexes(payload: dict[str, Any]) -> None:
    required_keys = (
        "traits_by_name",
        "roles_by_name",
        "portals_by_title",
        "strategies_by_title",
        "equipment_by_cache_key",
    )
    for key in required_keys:
        if not isinstance(payload.get(key), dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw indexes missing {key}")
    for key in ("traits_by_name", "roles_by_name", "portals_by_title", "equipment_by_cache_key"):
        if not payload[key]:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw indexes empty {key}")


def _validate_list(payload: dict[str, Any], key: str, source: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} missing {key}")
    return value


def _validate_non_empty_list(payload: dict[str, Any], key: str, source: str) -> list[Any]:
    value = _validate_list(payload, key, source)
    if not value:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} empty {key}")
    return value


def _validate_equipment_features(payload: dict[str, Any]) -> None:
    if payload.get("equipment_feature_schema_version") != CW_EQUIPMENT_FEATURE_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature schema version unsupported")
    if payload.get("recognizer_algorithm_version") != "vector-mask-v1":
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment recognizer algorithm unsupported")
    if payload.get("feature_size") != [32, 32] or payload.get("match_size") != [64, 64]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature sizes unsupported")
    _validate_finite_float(payload.get("min_score"), "min_score")
    _validate_finite_float(payload.get("min_gap"), "min_gap")
    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment features missing items")
    if not items:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment features empty items")
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature item invalid")
        for key in _FEATURE_ITEM_REQUIRED_KEYS:
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment feature item missing {key}")
        _validate_pixel_payload(item["feature_rgba"], "feature_rgba", "RGBA", [32, 32], 4)
        _validate_pixel_payload(item["match_rgba"], "match_rgba", "RGBA", [64, 64], 4)
        _validate_pixel_payload(item["feature_mask"], "feature_mask", "L", [32, 32], 1)
        _validate_pixel_payload(item["match_mask"], "match_mask", "L", [64, 64], 1)


def _validate_role_manifest(root: Path, payload: dict[str, Any], *, root_resolved: Path | None = None) -> None:
    if payload.get("role_manifest_schema_version") != CW_ROLE_MANIFEST_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest schema version unsupported")
    if not isinstance(payload.get("resource_version"), str) or not payload.get("resource_version"):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest missing resource_version")
    if payload.get("empty_template_version") != CW_SLOT_EMPTY_TEMPLATE_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role empty template version unsupported")
    empty_templates = payload.get("empty_templates")
    if not isinstance(empty_templates, dict):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest missing empty_templates")
    root_resolved = root_resolved or root.resolve()
    for key, expected_relative in _ROLE_EMPTY_TEMPLATE_RELATIVES.items():
        entry = empty_templates.get(key)
        if not isinstance(entry, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest missing empty template: {key}")
        local_path = _clean_role_empty_relative(entry.get("local_path"))
        if local_path != expected_relative:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template path invalid: {local_path}")
        expected_hash = entry.get("sha256")
        expected_size = entry.get("size")
        if not isinstance(expected_hash, str) or not expected_hash:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template sha256 invalid: {key}")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template size invalid: {key}")
        template_path = _bundle_path(root, local_path, root_resolved=root_resolved)
        if template_path.stat().st_size != expected_size or _file_sha256(template_path) != expected_hash:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role empty template checksum mismatch: {local_path}")
        _validate_role_empty_template_path(template_path, local_path)

    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest missing items")
    if not items:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest empty items")
    seen_role_ids: set[str] = set()
    seen_local_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest item invalid")
        for key in _ROLE_MANIFEST_REQUIRED_KEYS:
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest item missing {key}")
        if "rarity" not in item and "cost" not in item:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest item missing rarity or cost")
        for key in ("role_id", "name", "normalized_name", "icon_url", "local_path", "sha256"):
            if not isinstance(item.get(key), str) or not item.get(key):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest item {key} invalid")
        role_id = item["role_id"]
        if role_id in seen_role_ids:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest duplicate role_id: {role_id}")
        seen_role_ids.add(role_id)
        if item.get("front_back_type") is not None and not isinstance(item.get("front_back_type"), str):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest item front_back_type invalid")
        if not isinstance(item.get("trait_ids"), list) or not all(isinstance(value, str) for value in item["trait_ids"]):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest item trait_ids invalid")
        for key in ("rarity", "cost"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest item {key} invalid")
        if not isinstance(item.get("size"), int) or isinstance(item.get("size"), bool):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role manifest item size invalid")
        local_path = _clean_role_icon_relative(item["local_path"])
        icon_path = _bundle_path(root, local_path, root_resolved=root_resolved)
        normalized_local_path = _bundle_relative(root, icon_path, root_resolved=root_resolved)
        if normalized_local_path in seen_local_paths:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role manifest duplicate local_path: {local_path}")
        seen_local_paths.add(normalized_local_path)
        if icon_path.stat().st_size != item["size"] or _file_sha256(icon_path) != item["sha256"]:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role icon checksum mismatch: {local_path}")


def _validate_role_features(payload: dict[str, Any]) -> None:
    if payload.get("role_feature_schema_version") != CW_ROLE_FEATURE_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature schema version unsupported")
    if payload.get("recognizer_algorithm_version") != CW_ROLE_RECOGNIZER_ALGORITHM_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role recognizer algorithm unsupported")
    if payload.get("geometry_version") != CW_SLOT_GEOMETRY_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role geometry version unsupported")
    if payload.get("empty_template_version") != CW_SLOT_EMPTY_TEMPLATE_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role empty template version unsupported")
    if payload.get("target_size") != [103, 120]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role target size unsupported")
    if payload.get("avatar_roi") != [5, 4, 98, 108]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role avatar roi unsupported")
    if payload.get("feature_size") != [64, 64]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature size unsupported")
    if payload.get("hist_bins") != [16, 16, 16]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role histogram bins unsupported")
    for key in ("min_score", "low_score", "min_gap", "empty_min_score", "empty_min_gap"):
        _validate_finite_float(payload.get(key), key, source="cw role feature")
    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role features missing items")
    if not items:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role features empty items")
    seen_role_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature item invalid")
        for key in _ROLE_FEATURE_ITEM_REQUIRED_KEYS:
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role feature item missing {key}")
        if "rarity" not in item and "cost" not in item:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature item missing rarity or cost")
        for key in ("role_id", "name", "normalized_name"):
            if not isinstance(item.get(key), str) or not item.get(key):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role feature item {key} invalid")
        role_id = item["role_id"]
        if role_id in seen_role_ids:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role feature duplicate role_id: {role_id}")
        seen_role_ids.add(role_id)
        if item.get("front_back_type") is not None and not isinstance(item.get("front_back_type"), str):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature item front_back_type invalid")
        if not isinstance(item.get("trait_ids"), list) or not all(isinstance(value, str) for value in item["trait_ids"]):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature item trait_ids invalid")
        for key in ("rarity", "cost"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role feature item {key} invalid")
        _validate_pixel_payload(item["icon_rgba"], "icon_rgba", "RGBA", [64, 64], 4, source="cw role feature")
        _validate_pixel_payload(item["icon_mask"], "icon_mask", "L", [64, 64], 1, source="cw role feature")
        histogram = item.get("histogram")
        if not isinstance(histogram, list) or len(histogram) != 4096:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature histogram length invalid")
        for value in histogram:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw role feature histogram value invalid")


def _equipment_cache_keys(payload: dict[str, Any], source: str) -> set[str]:
    keys: set[str] = set()
    for item in payload["items"]:
        cache_key = item.get("cache_key")
        if not isinstance(cache_key, str):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment {source} item cache_key invalid")
        if cache_key in keys:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment {source} duplicate cache_key: {cache_key}")
        keys.add(cache_key)
    return keys


def _role_ids(payload: dict[str, Any], source: str) -> set[str]:
    role_ids: set[str] = set()
    for item in payload["items"]:
        role_id = item.get("role_id")
        if not isinstance(role_id, str):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role {source} item role_id invalid")
        if role_id in role_ids:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role {source} duplicate role_id: {role_id}")
        role_ids.add(role_id)
    return role_ids


def _validate_equipment_feature_manifest_keys(
    equipment_manifest: dict[str, Any], equipment_features: dict[str, Any]
) -> None:
    manifest_keys = _equipment_cache_keys(equipment_manifest, "manifest")
    feature_keys = _equipment_cache_keys(equipment_features, "features")
    if manifest_keys == feature_keys:
        return
    missing = sorted(manifest_keys - feature_keys)
    extra = sorted(feature_keys - manifest_keys)
    details = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if extra:
        details.append(f"extra={','.join(extra)}")
    suffix = f": {' '.join(details)}" if details else ""
    raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment feature cache_key mismatch{suffix}")


def _validate_role_feature_manifest_keys(role_manifest: dict[str, Any], role_features: dict[str, Any]) -> None:
    manifest_role_ids = _role_ids(role_manifest, "manifest")
    feature_role_ids = _role_ids(role_features, "features")
    if manifest_role_ids == feature_role_ids:
        return
    missing = sorted(manifest_role_ids - feature_role_ids)
    extra = sorted(feature_role_ids - manifest_role_ids)
    details = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if extra:
        details.append(f"extra={','.join(extra)}")
    suffix = f": {' '.join(details)}" if details else ""
    raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role feature role_id mismatch{suffix}")


def _validate_finite_float(value: Any, key: str, *, source: str = "cw equipment feature") -> None:
    if isinstance(value, bool):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} invalid") from exc
    if not math.isfinite(number):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} invalid")


def _validate_pixel_payload(
    payload: Any,
    key: str,
    mode: str,
    size: list[int],
    channels: int,
    *,
    source: str = "cw equipment feature",
) -> None:
    if not isinstance(payload, dict):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} payload invalid")
    data = payload.get("data")
    if payload.get("mode") != mode or payload.get("size") != size or not isinstance(data, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} payload invalid")
    if len(data) != size[0] * size[1] * channels:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} data length invalid")
    try:
        bytes(data)
    except (TypeError, ValueError) as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"{source} {key} data invalid") from exc


def _validate_equipment_manifest_payload(payload: dict[str, Any]) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest missing items")
    if not items:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest empty items")
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest item invalid")
        for key in _EQUIPMENT_MANIFEST_REQUIRED_KEYS:
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment manifest item missing {key}")
        for key in ("cache_key", "name", "kind", "icon_url", "big_version", "local_path", "sha256"):
            if not isinstance(item.get(key), str) or not item.get(key):
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment manifest item {key} invalid")
        _clean_equipment_icon_relative(item["local_path"])
        if not isinstance(item.get("size"), int) or isinstance(item.get("size"), bool):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest item size invalid")


def _validate_equipment_manifest(root: Path, payload: dict[str, Any], *, root_resolved: Path | None = None) -> None:
    _validate_equipment_manifest_payload(payload)
    root_resolved = root_resolved or root.resolve()
    items = payload["items"]
    for item in items:
        local_path = _clean_equipment_icon_relative(item.get("local_path"))
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        icon_path = _bundle_path(root, local_path, root_resolved=root_resolved)
        if icon_path.stat().st_size != expected_size or _file_sha256(icon_path) != expected_hash:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon checksum mismatch: {local_path}")


def _validate_equipment_manifest_paths_listed(
    root: Path,
    payload: dict[str, Any],
    manifest_files: set[str],
    *,
    root_resolved: Path | None = None,
) -> None:
    root_resolved = root_resolved or root.resolve()
    for item in payload["items"]:
        local_path = _clean_equipment_icon_relative(item["local_path"])
        normalized_local_path = _bundle_relative(
            root,
            _bundle_path(root, local_path, root_resolved=root_resolved),
            root_resolved=root_resolved,
        )
        if normalized_local_path not in manifest_files:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon missing from manifest files: {local_path}")


def _iter_role_manifest_local_paths(payload: dict[str, Any]):
    empty_templates = payload.get("empty_templates") if isinstance(payload, dict) else None
    if isinstance(empty_templates, dict):
        for key in ("field", "hand"):
            entry = empty_templates.get(key)
            if isinstance(entry, dict):
                yield _clean_role_empty_relative(entry.get("local_path"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                yield _clean_role_icon_relative(item.get("local_path"))


def _validate_role_manifest_paths_listed(
    root: Path,
    role_manifest: dict[str, Any],
    manifest_files: set[str],
    *,
    root_resolved: Path | None = None,
) -> None:
    root_resolved = root_resolved or root.resolve()
    for local_path in _iter_role_manifest_local_paths(role_manifest):
        normalized_local_path = _bundle_relative(
            root,
            _bundle_path(root, local_path, root_resolved=root_resolved),
            root_resolved=root_resolved,
        )
        if normalized_local_path not in manifest_files:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw role resource missing from manifest files: {local_path}")


def _validate_manifest_files_allowed(
    root: Path,
    equipment_manifest: dict[str, Any],
    role_manifest: dict[str, Any],
    manifest_files: set[str],
    *,
    root_resolved: Path | None = None,
) -> None:
    root_resolved = root_resolved or root.resolve()
    allowed = set(_FIXED_BUNDLE_RELATIVES)
    for item in equipment_manifest["items"]:
        local_path = _clean_equipment_icon_relative(item["local_path"])
        allowed.add(
            _bundle_relative(
                root,
                _bundle_path(root, local_path, root_resolved=root_resolved),
                root_resolved=root_resolved,
            )
        )
    for local_path in _iter_role_manifest_local_paths(role_manifest):
        allowed.add(
            _bundle_relative(
                root,
                _bundle_path(root, local_path, root_resolved=root_resolved),
                root_resolved=root_resolved,
            )
        )
    extra = sorted(manifest_files - allowed)
    if extra:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle manifest unexpected file: {extra[0]}")


def _validate_equipment_override_files_allowed(
    root: Path,
    payload: dict[str, Any],
    manifest_files: set[str],
    *,
    root_resolved: Path | None = None,
) -> None:
    root_resolved = root_resolved or root.resolve()
    allowed = {"equipment/manifest.json", "equipment/features.json"}
    for item in payload["items"]:
        local_path = _clean_equipment_icon_relative(item["local_path"])
        allowed.add(
            _bundle_relative(
                root,
                _bundle_path(root, local_path, root_resolved=root_resolved),
                root_resolved=root_resolved,
            )
        )
    extra = sorted(manifest_files - allowed)
    if extra:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override manifest unexpected file: {extra[0]}")


def _validate_no_unreferenced_bundle_files(
    root: Path,
    manifest_files: set[str],
    *,
    root_resolved: Path | None = None,
) -> None:
    allowed = {"manifest.json", *manifest_files}
    resolved_root = root_resolved or root.resolve()
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        relative = _bundle_relative(root, path, root_resolved=resolved_root)
        if relative not in allowed:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle unreferenced file: {relative}")


def load_cw_resource_bundle_from_path(root: str | Path, *, source_kind: str = "package") -> CwResourceBundle:
    bundle_root = Path(root)
    if _is_path_link(bundle_root):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle root must not be symlink: {bundle_root}")
    bundle_root_resolved = bundle_root.resolve()
    manifest_path = bundle_root / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest_files = _validate_manifest(bundle_root, manifest, root_resolved=bundle_root_resolved)

    raw_config = _read_json(bundle_root / "raw_config.json")
    guide_config = _read_json(bundle_root / "guide_config.json")
    guide_config_enriched = _read_json(bundle_root / "guide_config_enriched.json")
    indexes = _read_json(bundle_root / "indexes.json")
    _validate_raw_config(raw_config)
    _validate_guide_config(guide_config, "guide config")
    _validate_guide_config(guide_config_enriched, "guide config enriched")
    _validate_indexes(indexes)

    equipment_manifest_path = bundle_root / "equipment" / "manifest.json"
    equipment_manifest = _read_json(equipment_manifest_path)
    equipment_features = _read_json(bundle_root / "equipment" / "features.json")
    role_manifest_path = bundle_root / "roles" / "manifest.json"
    role_manifest = _read_json(role_manifest_path)
    role_features = _read_json(bundle_root / "roles" / "features.json")
    _validate_equipment_manifest(bundle_root, equipment_manifest, root_resolved=bundle_root_resolved)
    _validate_role_manifest(bundle_root, role_manifest, root_resolved=bundle_root_resolved)
    _validate_manifest_files_allowed(
        bundle_root,
        equipment_manifest,
        role_manifest,
        manifest_files,
        root_resolved=bundle_root_resolved,
    )
    _validate_equipment_manifest_paths_listed(
        bundle_root,
        equipment_manifest,
        manifest_files,
        root_resolved=bundle_root_resolved,
    )
    _validate_role_manifest_paths_listed(
        bundle_root,
        role_manifest,
        manifest_files,
        root_resolved=bundle_root_resolved,
    )
    _validate_equipment_features(equipment_features)
    _validate_equipment_feature_manifest_keys(equipment_manifest, equipment_features)
    _validate_role_features(role_features)
    _validate_role_feature_manifest_keys(role_manifest, role_features)
    _validate_no_unreferenced_bundle_files(bundle_root, manifest_files, root_resolved=bundle_root_resolved)

    return CwResourceBundle(
        root=bundle_root,
        manifest=manifest,
        raw_config=raw_config,
        guide_config=guide_config,
        guide_config_enriched=guide_config_enriched,
        indexes=indexes,
        equipment_manifest=equipment_manifest,
        equipment_features=equipment_features,
        role_manifest=role_manifest,
        role_features=role_features,
        source_kind=source_kind,
        manifest_path=manifest_path,
        manifest_mtime=manifest_path.stat().st_mtime,
        equipment_manifest_path=equipment_manifest_path,
        equipment_manifest_mtime=equipment_manifest_path.stat().st_mtime,
        role_manifest_path=role_manifest_path,
        role_manifest_mtime=role_manifest_path.stat().st_mtime,
    )


def allow_cw_resource_dev_fallback() -> bool:
    return os.environ.get("TRAIL_CW_RESOURCE_DEV_FALLBACK") == "1"


CW_EQUIPMENT_OVERRIDE_RELATIVE = Path(".trail") / "cache" / "cw-equipment-resource"


def _package_bundle_candidates() -> list[Path]:
    package_root = Path(__file__).resolve().parents[2]
    generated_root = package_root / "scenes" / "cw" / "generated"
    if _is_path_link(generated_root):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle generated root must not be symlink: {generated_root}")
    if not generated_root.is_dir():
        return []
    return [path for path in generated_root.iterdir() if path.is_dir() and not _is_path_link(path)]


def _workspace_equipment_override_root(workspace_root: str | Path | None) -> Path | None:
    if workspace_root is None:
        return None
    workspace_path = Path(workspace_root)
    root = workspace_path / CW_EQUIPMENT_OVERRIDE_RELATIVE
    if not (root / "manifest.json").is_file():
        return None
    if _is_path_link(root):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override root must not be symlink: {root}")
    try:
        if not root.resolve().is_relative_to(workspace_path.resolve()):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override root escapes workspace: {root}")
    except ValueError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override root escapes workspace: {root}") from exc
    return root


def workspace_equipment_override_write_root(workspace_root: str | Path) -> Path:
    workspace_path = Path(workspace_root)
    resolved_workspace = workspace_path.resolve()
    root = workspace_path / CW_EQUIPMENT_OVERRIDE_RELATIVE
    candidate = workspace_path
    for part in CW_EQUIPMENT_OVERRIDE_RELATIVE.parts:
        candidate = candidate / part
        if _is_path_link(candidate):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override path must not be symlink: {candidate}")
    try:
        if not root.resolve(strict=False).is_relative_to(resolved_workspace):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override path escapes workspace: {root}")
    except ValueError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment override path escapes workspace: {root}") from exc
    return root


def cw_resource_source_signature(*, workspace_root: str | Path | None = None) -> tuple[Any, ...]:
    package_root = Path(__file__).resolve().parents[2]
    generated_root = package_root / "scenes" / "cw" / "generated"
    try:
        root_stat = generated_root.stat()
    except OSError:
        package_sig = (str(generated_root), None)
    else:
        candidates: list[tuple[str, int, int, str | None]] = []
        for path in _package_bundle_candidates():
            try:
                manifest_path = path / "manifest.json"
                manifest_stat = manifest_path.stat()
                manifest_sha256 = _file_sha256(manifest_path)
            except OSError:
                manifest_stat = None
                manifest_sha256 = None
            candidates.append(
                (
                    path.name,
                    0 if manifest_stat is None else int(manifest_stat.st_mtime_ns),
                    0 if manifest_stat is None else int(manifest_stat.st_size),
                    manifest_sha256,
                )
            )
        package_sig = (str(generated_root), int(root_stat.st_mtime_ns), tuple(sorted(candidates)))

    override = _workspace_equipment_override_root(workspace_root)
    override_sig = None
    if override is not None:
        manifest_path = override / "manifest.json"
        try:
            stat = manifest_path.stat()
            override_sig = (str(manifest_path), int(stat.st_mtime_ns), int(stat.st_size), _file_sha256(manifest_path))
        except OSError:
            override_sig = (str(manifest_path), None)
    return (package_sig, override_sig)


def _overlay_workspace_equipment(bundle: CwResourceBundle, override_root: Path) -> CwResourceBundle:
    override_manifest_path = override_root / "manifest.json"
    override_manifest = _read_json(override_manifest_path)
    if override_manifest.get("base_content_digest") != bundle.identity:
        return bundle
    try:
        override_root_resolved = override_root.resolve()
        manifest_files = _validate_manifest(
            override_root,
            override_manifest,
            required_relatives=("equipment/manifest.json", "equipment/features.json"),
            root_resolved=override_root_resolved,
        )
        equipment_manifest_path = override_root / "equipment" / "manifest.json"
        equipment_manifest = _read_json(equipment_manifest_path)
        equipment_features = _read_json(override_root / "equipment" / "features.json")
        _validate_equipment_manifest(override_root, equipment_manifest, root_resolved=override_root_resolved)
        _validate_equipment_override_files_allowed(
            override_root,
            equipment_manifest,
            manifest_files,
            root_resolved=override_root_resolved,
        )
        _validate_equipment_manifest_paths_listed(
            override_root,
            equipment_manifest,
            manifest_files,
            root_resolved=override_root_resolved,
        )
        _validate_no_unreferenced_bundle_files(override_root, manifest_files, root_resolved=override_root_resolved)
        _validate_equipment_features(equipment_features)
        _validate_equipment_feature_manifest_keys(equipment_manifest, equipment_features)
    except TrailError as exc:
        if exc.code == "CW_RESOURCE_BUNDLE_MISSING":
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment override incomplete") from exc
        raise
    override_identity = str(override_manifest.get("content_digest") or _manifest_digest(_manifest_without_digest(override_manifest)))
    return replace(
        bundle,
        source_kind="package+workspace_equipment",
        equipment_manifest=equipment_manifest,
        equipment_features=equipment_features,
        equipment_manifest_path=equipment_manifest_path,
        equipment_manifest_mtime=equipment_manifest_path.stat().st_mtime,
        override_manifest_path=override_manifest_path,
        override_manifest_mtime=override_manifest_path.stat().st_mtime,
        override_identity=override_identity,
    )


def load_default_cw_package_resource_bundle() -> CwResourceBundle:
    candidates = _package_bundle_candidates()
    if not candidates:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    if len(candidates) > 1:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle ambiguous")
    try:
        bundle = load_cw_resource_bundle_from_path(candidates[0], source_kind="package")
    except TrailError as exc:
        if exc.code == "CW_RESOURCE_BUNDLE_MISSING":
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle incomplete") from exc
        raise

    return bundle


def load_default_cw_resource_bundle(*, workspace_root: str | Path | None = None) -> CwResourceBundle:
    bundle = load_default_cw_package_resource_bundle()
    override_root = _workspace_equipment_override_root(workspace_root)
    if override_root is None:
        return bundle
    return _overlay_workspace_equipment(bundle, override_root)
