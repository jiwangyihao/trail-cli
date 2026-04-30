from __future__ import annotations

import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Any

from trail.scenes.cw.equipment_recognition import build_precomputed_equipment_features
from trail.scenes.cw.equipment_recognition import VectorEquipmentIconRecognizer
from trail.scenes.cw.equipment_resources import load_cached_equipment_icons, prepare_equipment_icon_cache
from trail.scenes.cw.static_resources import (
    cw_resource_source_signature,
    equipment_catalog_from_manifest,
    load_cw_resource_bundle_from_path,
    load_default_cw_package_resource_bundle,
    load_default_cw_resource_bundle,
    workspace_equipment_override_write_root,
    write_bundle_json,
    write_equipment_override_manifest,
    write_workspace_equipment_icons_from_cache,
)


class CwResourceService:
    def __init__(
        self,
        *,
        bundle_loader=load_default_cw_resource_bundle,
        package_bundle_loader=load_default_cw_package_resource_bundle,
        recognizer_cls=VectorEquipmentIconRecognizer,
        source_signature=cw_resource_source_signature,
    ) -> None:
        self._bundle_loader = bundle_loader
        self._package_bundle_loader = package_bundle_loader
        self._recognizer_cls = recognizer_cls
        self._source_signature = source_signature
        self._bundle_by_workspace: dict[str, Any] = {}
        self._source_signature_by_workspace: dict[str, tuple[Any, ...]] = {}
        self._bundles: dict[tuple[Any, ...], Any] = {}
        self._recognizers: dict[tuple[Any, ...], Any] = {}
        self._lock = RLock()

    def _workspace_key(self, *, workspace_root: str | Path) -> str:
        return str(Path(workspace_root).resolve())

    def _bundle_key(self, *, workspace_root: str | Path, bundle: Any) -> tuple[Any, ...]:
        manifest = getattr(bundle, "manifest", {})
        if not isinstance(manifest, dict):
            manifest = {}
        return (
            self._workspace_key(workspace_root=workspace_root),
            getattr(bundle, "source_kind", "package"),
            str(getattr(bundle, "manifest_path", getattr(bundle, "root", ""))),
            str(getattr(bundle, "equipment_manifest_path", "")),
            manifest.get("bundle_schema_version"),
            manifest.get("resource_version"),
            getattr(bundle, "big_version", ""),
            getattr(bundle, "manifest_mtime", 0.0),
            getattr(bundle, "equipment_manifest_mtime", 0.0),
            str(getattr(bundle, "override_manifest_path", "")),
            getattr(bundle, "override_manifest_mtime", 0.0),
            getattr(bundle, "override_identity", ""),
            getattr(bundle, "identity", ""),
        )

    def bundle(self, *, workspace_root: str | Path):
        workspace_key = self._workspace_key(workspace_root=workspace_root)
        with self._lock:
            source_signature = self._source_signature(workspace_root=workspace_root)
            cached = self._bundle_by_workspace.get(workspace_key)
            if cached is not None and self._source_signature_by_workspace.get(workspace_key) == source_signature:
                return cached
            bundle = self._bundle_loader(workspace_root=workspace_root)
            key = self._bundle_key(workspace_root=workspace_root, bundle=bundle)
            cached = self._bundles.get(key)
            if cached is None:
                cached = bundle
                self._bundles[key] = cached
            self._bundle_by_workspace[workspace_key] = cached
            self._source_signature_by_workspace[workspace_key] = source_signature
            return cached

    def equipment_recognizer_for_bundle(self, *, workspace_root: str | Path, bundle: Any):
        key = self._bundle_key(workspace_root=workspace_root, bundle=bundle)
        with self._lock:
            cached = self._recognizers.get(key)
            if cached is None:
                cached = self._recognizer_cls.from_precomputed_features(bundle.equipment_features)
                self._recognizers[key] = cached
            return cached

    def equipment_read_resources(self, *, workspace_root: str | Path) -> tuple[dict[str, Any], Any]:
        bundle = self.bundle(workspace_root=workspace_root)
        return bundle.raw_config, self.equipment_recognizer_for_bundle(workspace_root=workspace_root, bundle=bundle)

    def equipment_recognizer(self, *, workspace_root: str | Path):
        bundle = self.bundle(workspace_root=workspace_root)
        return self.equipment_recognizer_for_bundle(workspace_root=workspace_root, bundle=bundle)

    def refresh_equipment_workspace_override(self, *, workspace_root: str | Path) -> dict[str, Any]:
        workspace_path = Path(workspace_root)
        with self._lock:
            base_bundle = self._package_bundle_loader()
            if getattr(base_bundle, "source_kind", "package") != "package" and getattr(base_bundle, "root", None) is not None:
                base_bundle = load_cw_resource_bundle_from_path(base_bundle.root, source_kind="package")

            override_root = workspace_equipment_override_write_root(workspace_path)
            staging_root = override_root.with_name("cw-equipment-resource.tmp")
            if staging_root.exists():
                shutil.rmtree(staging_root)
            staging_root.mkdir(parents=True, exist_ok=True)
            try:
                catalog = equipment_catalog_from_manifest(base_bundle.equipment_manifest)
                summary = prepare_equipment_icon_cache(catalog, workspace_root=workspace_path, refresh=True)
                icons = load_cached_equipment_icons(catalog, workspace_root=workspace_path)
                write_bundle_json(staging_root / "equipment" / "features.json", build_precomputed_equipment_features(icons))
                equipment_manifest = write_workspace_equipment_icons_from_cache(
                    override_root=staging_root,
                    catalog=catalog,
                    workspace_root=workspace_path,
                )
                write_bundle_json(staging_root / "equipment" / "manifest.json", equipment_manifest)
                write_equipment_override_manifest(staging_root, base_bundle=base_bundle)
                if override_root.exists():
                    shutil.rmtree(override_root)
                os.replace(staging_root, override_root)
                self.invalidate_workspace(workspace_root=workspace_path)
                return summary
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

    def equipment_prepare_summary(self, *, workspace_root: str | Path) -> dict[str, Any]:
        bundle = self.bundle(workspace_root=workspace_root)
        items = bundle.equipment_manifest.get("items") if isinstance(bundle.equipment_manifest, dict) else []
        count = len(items) if isinstance(items, list) else 0
        return {"big_version": bundle.big_version, "count": count, "cached": count, "downloaded": 0, "refreshed": False}

    def invalidate_workspace(self, *, workspace_root: str | Path) -> None:
        workspace_key = self._workspace_key(workspace_root=workspace_root)
        with self._lock:
            self._bundle_by_workspace.pop(workspace_key, None)
            self._source_signature_by_workspace.pop(workspace_key, None)
            self._bundles = {key: value for key, value in self._bundles.items() if key[0] != workspace_key}
            self._recognizers = {key: value for key, value in self._recognizers.items() if key[0] != workspace_key}
