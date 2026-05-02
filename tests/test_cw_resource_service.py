from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from trail.daemon.cw_resource_service import CwResourceService


def _write_empty_template(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (103, 120), (0, 0, 0, 0)).save(path)


def _role_service_fixture(tmp_path):
    _write_empty_template(tmp_path / "roles" / "empty" / "field-v1.png")
    _write_empty_template(tmp_path / "roles" / "empty" / "hand-v1.png")
    bundle = SimpleNamespace(
        raw_config={"rpg_game_big_version": "3.2"},
        guide_config_enriched={"roles": [], "traits": []},
        role_features={"items": []},
        role_manifest={
            "empty_templates": {
                "field": {"local_path": "roles/empty/field-v1.png"},
                "hand": {"local_path": "roles/empty/hand-v1.png"},
            },
            "items": [],
        },
        root=tmp_path,
        equipment_features={"items": []},
        equipment_manifest={"items": []},
        source_kind="package",
        manifest={"bundle_schema_version": 1, "resource_version": "3.2"},
        manifest_path=tmp_path / "manifest.json",
        equipment_manifest_path="equipment/manifest.json",
        role_manifest_path="roles/manifest.json",
        big_version="3.2",
        manifest_mtime=1.0,
        equipment_manifest_mtime=1.0,
        role_manifest_mtime=1.0,
        override_manifest_path="",
        override_manifest_mtime=0.0,
        override_identity="",
        identity="bundle-id",
    )
    calls = []

    class RoleRecognizer:
        @classmethod
        def from_precomputed_features(cls, payload, *, empty_templates):
            assert payload is bundle.role_features
            assert set(empty_templates) == {"field", "hand"}
            assert empty_templates["field"].size == (103, 120)
            assert empty_templates["hand"].size == (103, 120)
            assert empty_templates["field"].mode == "RGBA"
            assert empty_templates["hand"].mode == "RGBA"
            calls.append((payload, empty_templates))
            return {"recognizer": len(calls)}

    service = CwResourceService(
        bundle_loader=lambda workspace_root=None: bundle,
        source_signature=lambda workspace_root=None: ("sig",),
        role_recognizer_cls=RoleRecognizer,
    )
    return service, bundle, calls


def test_cw_resource_service_caches_role_recognizer(tmp_path):
    service, _bundle, calls = _role_service_fixture(tmp_path)

    first = service.slots_read_resources(workspace_root=tmp_path)[2]
    second = service.slots_read_resources(workspace_root=tmp_path)[2]

    assert first is second
    assert len(calls) == 1


def test_cw_resource_service_invalidates_role_recognizer(tmp_path):
    service, _bundle, calls = _role_service_fixture(tmp_path)

    first = service.slots_read_resources(workspace_root=tmp_path)[2]
    service.invalidate_workspace(workspace_root=tmp_path)
    second = service.slots_read_resources(workspace_root=tmp_path)[2]

    assert first is not second
    assert len(calls) == 2


def test_cw_resource_service_slots_resources_do_not_prepare_or_download(monkeypatch, tmp_path):
    service, bundle, calls = _role_service_fixture(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("slots resources must use bundled role features")

    monkeypatch.setenv("TRAIL_CW_RESOURCE_DEV_FALLBACK", "1")
    monkeypatch.setattr("trail.daemon.cw_resource_service.prepare_equipment_icon_cache", fail)
    monkeypatch.setattr("trail.daemon.cw_resource_service.load_cached_equipment_icons", fail)
    monkeypatch.setattr("trail.scenes.cw.role_resources.download_role_icon_bytes", fail)

    raw_config, guide_config, recognizer = service.slots_read_resources(workspace_root=tmp_path)

    assert raw_config is bundle.raw_config
    assert guide_config is bundle.guide_config_enriched
    assert recognizer == {"recognizer": 1}
    assert len(calls) == 1
