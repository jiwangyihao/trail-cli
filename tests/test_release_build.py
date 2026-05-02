from __future__ import annotations

import hashlib
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import stat
import tarfile
import tomllib
import zipfile

from PIL import Image
import pytest

from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    CW_ROLE_FEATURE_SCHEMA_VERSION,
    CW_ROLE_MANIFEST_SCHEMA_VERSION,
    CW_ROLE_RECOGNIZER_ALGORITHM_VERSION,
    CW_SLOT_EMPTY_TEMPLATE_VERSION,
    CW_SLOT_GEOMETRY_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_SCRIPT = ROOT / "scripts" / "verify-cw-resource-bundle-artifacts.py"


def _pixel_payload(mode: str, size: list[int]) -> dict:
    channels = 4 if mode == "RGBA" else 1
    return {"mode": mode, "size": size, "data": [0] * (size[0] * size[1] * channels)}


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


def _valid_feature_payload() -> dict:
    return {
        "items": [_valid_feature_item()],
        "equipment_feature_schema_version": 1,
        "recognizer_algorithm_version": "vector-mask-v1",
        "feature_size": [32, 32],
        "match_size": [64, 64],
        "min_score": 0.72,
        "min_gap": 0.05,
    }


def _valid_role_manifest_payload(
    *,
    role_icon_data: bytes = b"role-icon",
    field_empty_data: bytes | None = None,
    hand_empty_data: bytes | None = None,
) -> dict:
    field_empty_data = _png_bytes() if field_empty_data is None else field_empty_data
    hand_empty_data = _png_bytes() if hand_empty_data is None else hand_empty_data
    return {
        "role_manifest_schema_version": CW_ROLE_MANIFEST_SCHEMA_VERSION,
        "resource_version": "3.2",
        "empty_template_version": CW_SLOT_EMPTY_TEMPLATE_VERSION,
        "empty_templates": {
            "field": {
                "local_path": "roles/empty/field-v1.png",
                "sha256": hashlib.sha256(field_empty_data).hexdigest(),
                "size": len(field_empty_data),
            },
            "hand": {
                "local_path": "roles/empty/hand-v1.png",
                "sha256": hashlib.sha256(hand_empty_data).hexdigest(),
                "size": len(hand_empty_data),
            },
        },
        "items": [
            {
                "role_id": "r1",
                "name": "Role A",
                "normalized_name": "role a",
                "icon_url": "https://example.test/r1.png",
                "local_path": "roles/icons/r1.png",
                "sha256": hashlib.sha256(role_icon_data).hexdigest(),
                "size": len(role_icon_data),
                "front_back_type": "front",
                "trait_ids": ["t1"],
                "rarity": "5",
            }
        ],
    }


def _valid_role_feature_item(role_id: str = "r1", name: str = "Role A", normalized_name: str = "role a") -> dict:
    return {
        "role_id": role_id,
        "name": name,
        "normalized_name": normalized_name,
        "front_back_type": "front",
        "trait_ids": ["t1"],
        "rarity": "5",
        "icon_rgba": _pixel_payload("RGBA", [64, 64]),
        "icon_mask": _pixel_payload("L", [64, 64]),
        "histogram": [0.0] * 4096,
    }


def _valid_role_feature_payload() -> dict:
    return {
        "role_feature_schema_version": CW_ROLE_FEATURE_SCHEMA_VERSION,
        "recognizer_algorithm_version": CW_ROLE_RECOGNIZER_ALGORITHM_VERSION,
        "geometry_version": CW_SLOT_GEOMETRY_VERSION,
        "empty_template_version": CW_SLOT_EMPTY_TEMPLATE_VERSION,
        "target_size": [103, 120],
        "avatar_roi": [5, 4, 98, 108],
        "feature_size": [64, 64],
        "hist_bins": [16, 16, 16],
        "min_score": 0.58,
        "low_score": 0.50,
        "min_gap": 0.035,
        "empty_min_score": 0.82,
        "empty_min_gap": 0.08,
        "items": [_valid_role_feature_item()],
    }


def test_valid_role_feature_payload_uses_spec_thresholds() -> None:
    payload = _valid_role_feature_payload()

    assert payload["min_score"] == 0.58
    assert payload["low_score"] == 0.50
    assert payload["min_gap"] == 0.035
    assert payload["empty_min_score"] == 0.82
    assert payload["empty_min_gap"] == 0.08


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_cw_resource_bundle_artifacts", VERIFIER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cw_bundle_entries(bundle_root: str, overrides: dict[str, object] | None = None) -> dict[str, bytes]:
    icon_data = b"icon"
    role_icon_data = b"role-icon"
    field_empty_data = _png_bytes()
    hand_empty_data = _png_bytes()
    payloads = {
        "raw_config.json": {
            "rpg_game_big_version": "3.2",
            "trait_info_list": [{"id": "t1", "name": "Trait A", "type": "faction"}],
            "role_list": [
                {
                    "id": "r1",
                    "name": "Role A",
                    "icon": "https://example.test/r1.png",
                    "rarity": "5",
                    "front_back_type": "front",
                    "trait_ids": ["t1"],
                }
            ],
            "portal_list": [{"portal_id": "p1", "title": "Portal A", "description": "desc"}],
            "fight_augment_list": [{"id": "a1", "name": "Strategy A", "desc": "desc"}],
            "equipment_list": [{"id": "e1", "name": "Equipment A", "icon": "https://example.test/icon-a.png"}],
        },
        "guide_config.json": {
            "meta": {"big_version": "3.2"},
            "lineup_levels": [{"id": "7", "name": "Level 7"}],
            "traits": [{"id": "t1", "name": "Trait A"}],
            "roles": [{"id": "r1", "name": "Role A", "trait_ids": ["t1"], "front_back_type": "front"}],
            "role_tags": ["damage"],
            "portal_list": [{"portal_id": "p1", "title": "Portal A", "description": "desc"}],
            "strategy_list": [{"id": "a1", "title": "Strategy A"}],
        },
        "guide_config_enriched.json": {
            "meta": {"big_version": "3.2"},
            "lineup_levels": [{"id": "7", "name": "Level 7"}],
            "traits": [{"id": "t1", "name": "Trait A", "layers": [{"layer": 2}]}],
            "roles": [{"id": "r1", "name": "Role A", "trait_ids": ["t1"], "front_back_type": "front"}],
            "role_tags": ["damage"],
            "portal_list": [{"portal_id": "p1", "title": "Portal A", "description": "desc"}],
            "strategy_list": [{"id": "a1", "title": "Strategy A"}],
        },
        "indexes.json": {
            "traits_by_name": {"Trait A": {"id": "t1", "name": "Trait A"}},
            "roles_by_name": {"Role A": {"id": "r1", "name": "Role A"}},
            "portals_by_title": {"Portal A": {"portal_id": "p1", "title": "Portal A"}},
            "strategies_by_title": {"Strategy A": {"id": "a1", "title": "Strategy A"}},
            "equipment_by_cache_key": {"icon-a": {"cache_key": "icon-a", "name": "Icon A"}},
        },
        "equipment/manifest.json": {
            "items": [
                {
                    "cache_key": "icon-a",
                    "name": "Icon A",
                    "kind": "basic",
                    "icon_url": "https://example.test/icon-a.png",
                    "big_version": "3.2",
                    "local_path": "equipment/icons/icon-a.png",
                    "sha256": hashlib.sha256(icon_data).hexdigest(),
                    "size": len(icon_data),
                }
            ]
        },
        "equipment/features.json": _valid_feature_payload(),
        "equipment/icons/icon-a.png": icon_data,
        "roles/manifest.json": _valid_role_manifest_payload(
            role_icon_data=role_icon_data,
            field_empty_data=field_empty_data,
            hand_empty_data=hand_empty_data,
        ),
        "roles/features.json": _valid_role_feature_payload(),
        "roles/icons/r1.png": role_icon_data,
        "roles/empty/field-v1.png": field_empty_data,
        "roles/empty/hand-v1.png": hand_empty_data,
    }
    payloads.update(overrides or {})
    encoded_payloads = {
        relative: data if isinstance(data, bytes) else _json_bytes(data)
        for relative, data in payloads.items()
    }
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
            for relative, data in encoded_payloads.items()
        ]
    }
    manifest["content_digest"] = _manifest_digest(manifest)
    entries = {f"{bundle_root}/{relative}": data for relative, data in encoded_payloads.items()}
    entries[f"{bundle_root}/manifest.json"] = _json_bytes(manifest)
    return entries


def _manifest_digest(manifest: dict) -> str:
    payload = {key: value for key, value in manifest.items() if key != "content_digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _write_tar(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))


def _write_tree(path: Path, entries: dict[str, bytes]) -> None:
    for name, data in entries.items():
        target = path / Path(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _replace_manifest(
    entries: dict[str, bytes],
    bundle_root: str,
    manifest: dict,
) -> dict[str, bytes]:
    updated = dict(entries)
    updated[f"{bundle_root}/manifest.json"] = json.dumps(manifest).encode("utf-8")
    return updated


def test_windows_build_generates_cw_resource_bundle_before_python_build() -> None:
    build_script = (ROOT / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")

    build_bundle = "uv run python scripts\\build-cw-resource-bundle.py"
    python_build = "uv run python -m build"
    pyinstaller = "uv run pyinstaller packaging\\trail.spec --noconfirm"
    verify_bundle = "uv run python scripts\\verify-cw-resource-bundle-artifacts.py dist"
    compress_archive = "Compress-Archive -Path (Join-Path $Package '*') -DestinationPath $Zip -Force"
    sha256sums = "$artifacts = @()"

    assert build_bundle in build_script
    assert verify_bundle in build_script
    assert build_script.index(build_bundle) < build_script.index(python_build)
    assert build_script.index(pyinstaller) < build_script.index(verify_bundle)
    assert build_script.index(compress_archive) < build_script.index(verify_bundle)
    assert build_script.index(verify_bundle) < build_script.index(sha256sums)


def test_pyproject_includes_cw_generated_resources() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    hatch_targets = pyproject["tool"]["hatch"]["build"]["targets"]

    assert hatch_targets["wheel"]["packages"] == ["trail"]
    assert hatch_targets["sdist"]["force-include"]["trail/scenes/cw/generated"] == "trail/scenes/cw/generated"
    assert hatch_targets["sdist"]["force-include"]["trail/scenes/cw/assets/slots"] == "trail/scenes/cw/assets/slots"
    assert "force-include" not in hatch_targets["wheel"]


def test_verify_cw_resource_bundle_artifacts_script_exists() -> None:
    verifier = VERIFIER_SCRIPT.read_text(encoding="utf-8")

    for required in [
        "zipfile",
        "tarfile",
        "hashlib.sha256",
        'manifest.get("files")',
        "equipment/features.json",
        "equipment/icons/",
        "roles/features.json",
        "roles/icons/",
        "roles/empty/",
        "_validate_role_manifest",
        "_validate_role_features",
    ]:
        assert required in verifier


def test_dist_targets_includes_final_windows_zip(tmp_path: Path) -> None:
    verifier = _load_verifier()
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "trail_cli-0.1.0-py3-none-any.whl"
    sdist = dist / "trail_cli-0.1.0.tar.gz"
    zip_artifact = dist / "trail-cli-windows-x64-v0.1.0.zip"
    for artifact in (wheel, sdist, zip_artifact):
        artifact.write_bytes(b"artifact")

    assert zip_artifact in verifier._dist_targets(dist)


def test_verify_target_wheel_accepts_only_package_cw_bundle_layout(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail_cli-0.1.0-py3-none-any.whl"
    _write_zip(archive_path, _cw_bundle_entries("trail/scenes/cw/generated/3.2"))

    verifier.verify_target(archive_path)

    wrong_path = tmp_path / "trail_cli-0.1.1-py3-none-any.whl"
    _write_zip(wrong_path, _cw_bundle_entries("wrong/prefix/trail/scenes/cw/generated/3.2"))
    with pytest.raises(ValueError, match="bundle layout"):
        verifier.verify_target(wrong_path)


def test_verify_target_sdist_accepts_only_single_rooted_cw_bundle_layout(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail_cli-0.1.0.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries("trail_cli-0.1.0/trail/scenes/cw/generated/3.2"))

    verifier.verify_target(archive_path)


@pytest.mark.parametrize(
    "bundle_root",
    [
        "wrong/prefix/trail/scenes/cw/generated/3.2",
        "trail/scenes/cw/generated/3.2",
    ],
)
def test_verify_target_sdist_rejects_cw_bundle_outside_single_sdist_root(
    tmp_path: Path,
    bundle_root: str,
) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / f"bad-{bundle_root.count('/')}.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries(bundle_root))

    with pytest.raises(ValueError, match="bundle layout"):
        verifier.verify_target(archive_path)


def test_verify_target_windows_zip_accepts_only_packaged_pyinstaller_layout(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail-cli-windows-x64-v0.1.0.zip"
    _write_zip(archive_path, _cw_bundle_entries("trail/bin/_internal/trail/scenes/cw/generated/3.2"))

    verifier.verify_target(archive_path)


@pytest.mark.parametrize(
    "bundle_root",
    [
        "trail/scenes/cw/generated/3.2",
        "wrong/prefix/trail/scenes/cw/generated/3.2",
    ],
)
def test_verify_target_windows_zip_rejects_cw_bundle_outside_packaged_pyinstaller_layout(
    tmp_path: Path,
    bundle_root: str,
) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / f"trail-cli-windows-x64-v0.1.{bundle_root.count('/')}.zip"
    _write_zip(archive_path, _cw_bundle_entries(bundle_root))

    with pytest.raises(ValueError, match="bundle layout"):
        verifier.verify_target(archive_path)


def test_verify_target_pyinstaller_tree_accepts_only_internal_cw_bundle_layout(tmp_path: Path) -> None:
    verifier = _load_verifier()
    tree_path = tmp_path / "dist" / "trail"
    _write_tree(tree_path, _cw_bundle_entries("_internal/trail/scenes/cw/generated/3.2"))

    verifier.verify_target(tree_path)

    wrong_tree = tmp_path / "bad" / "trail"
    _write_tree(wrong_tree, _cw_bundle_entries("trail/scenes/cw/generated/3.2"))
    with pytest.raises(ValueError, match="bundle layout"):
        verifier.verify_target(wrong_tree)


def test_verify_zip_rejects_normalized_member_name_collisions(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("trail/scenes/cw/generated/3.2/manifest.json", "{}")
        archive.writestr("./trail/scenes/cw/generated/3.2/manifest.json", "{}")

    with pytest.raises(ValueError, match="duplicate normalized archive member"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_normalized_member_name_collisions(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in [
            "trail/scenes/cw/generated/3.2/manifest.json",
            "./trail/scenes/cw/generated/3.2/manifest.json",
        ]:
            data = b"{}"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))

    with pytest.raises(ValueError, match="duplicate normalized archive member"):
        verifier.verify_tar(archive_path)


def test_verify_zip_rejects_traversal_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in _cw_bundle_entries("../trail/scenes/cw/generated/3.2").items():
            archive.writestr(name, data)

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


@pytest.mark.parametrize("directory", ["../evil/", "/evil/"])
def test_verify_zip_rejects_dangerous_directory_member_paths(tmp_path: Path, directory: str) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    entries = _cw_bundle_entries("trail/scenes/cw/generated/3.2")
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr(directory, b"")

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_traversal_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in _cw_bundle_entries("../trail/scenes/cw/generated/3.2").items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


@pytest.mark.parametrize("directory", ["../evil", "/evil"])
def test_verify_tar_rejects_dangerous_directory_member_paths(tmp_path: Path, directory: str) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in _cw_bundle_entries("trail/scenes/cw/generated/3.2").items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
        directory_info = tarfile.TarInfo(directory)
        directory_info.type = tarfile.DIRTYPE
        archive.addfile(directory_info)

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


@pytest.mark.parametrize(
    ("member_type", "linkname"),
    [
        (tarfile.SYMTYPE, "raw_config.json"),
        (tarfile.LNKTYPE, "trail/scenes/cw/generated/3.2/raw_config.json"),
        (tarfile.FIFOTYPE, ""),
    ],
)
def test_verify_tar_rejects_generated_non_regular_members(
    tmp_path: Path,
    member_type: bytes,
    linkname: str,
) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in _cw_bundle_entries("trail/scenes/cw/generated/3.2").items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
        info = tarfile.TarInfo("trail/scenes/cw/generated/3.2/link")
        info.type = member_type
        info.linkname = linkname
        archive.addfile(info)

    with pytest.raises(ValueError, match="unsupported tar member"):
        verifier.verify_tar(archive_path)


def test_verify_tar_rejects_dangerous_linkname_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in _cw_bundle_entries("trail/scenes/cw/generated/3.2").items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
        info = tarfile.TarInfo("safe/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../evil"
        archive.addfile(info)

    with pytest.raises(ValueError, match="archive member escapes bundle|unsupported tar member"):
        verifier.verify_tar(archive_path)


def test_verify_zip_rejects_windows_drive_absolute_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _cw_bundle_entries("C:/trail/scenes/cw/generated/3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_windows_drive_absolute_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries("C:\\trail\\scenes\\cw\\generated\\3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


@pytest.mark.parametrize(
    "bundle_root",
    [
        "./C:/trail/scenes/cw/generated/3.2",
        ".\\C:\\trail\\scenes\\cw\\generated\\3.2",
    ],
)
def test_verify_zip_rejects_dot_prefixed_windows_drive_member_paths(
    tmp_path: Path,
    bundle_root: str,
) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _cw_bundle_entries(bundle_root))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


@pytest.mark.parametrize(
    "bundle_root",
    [
        "./C:/trail/scenes/cw/generated/3.2",
        ".\\C:\\trail\\scenes\\cw\\generated\\3.2",
    ],
)
def test_verify_tar_rejects_dot_prefixed_windows_drive_member_paths(
    tmp_path: Path,
    bundle_root: str,
) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries(bundle_root))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


def test_verify_zip_rejects_non_leading_parent_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _cw_bundle_entries("trail/../trail/scenes/cw/generated/3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_non_leading_parent_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries("trail/../trail/scenes/cw/generated/3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


def test_verify_zip_rejects_posix_absolute_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _cw_bundle_entries("/trail/scenes/cw/generated/3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_posix_absolute_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, _cw_bundle_entries("/trail/scenes/cw/generated/3.2"))

    with pytest.raises(ValueError, match="archive member escapes bundle"):
        verifier.verify_tar(archive_path)


def test_verify_zip_allows_current_directory_prefix_member_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _cw_bundle_entries("./trail/scenes/cw/generated/3.2"))

    verifier.verify_zip(archive_path)


@pytest.mark.parametrize(
    "manifest_path",
    ["C:/raw_config.json", "C:\\raw_config.json", "./C:/raw_config.json"],
)
def test_verify_zip_rejects_windows_drive_absolute_manifest_paths(
    tmp_path: Path,
    manifest_path: str,
) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["files"][0]["path"] = manifest_path
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError, match="manifest file entry escapes bundle"):
        verifier.verify_zip(archive_path)


def test_verify_zip_allows_current_directory_prefix_manifest_paths(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["files"][0]["path"] = "./raw_config.json"
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    verifier.verify_zip(archive_path)


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        ("raw_config.json", {}),
        (
            "guide_config.json",
            {
                "meta": {"big_version": "3.2"},
                "lineup_levels": [],
                "traits": [],
                "roles": [],
                "role_tags": [],
                "portal_list": [],
                "strategy_list": [],
            },
        ),
        (
            "guide_config_enriched.json",
            {
                "meta": {"big_version": "3.2"},
                "lineup_levels": [],
                "traits": [],
                "roles": [],
                "role_tags": [],
                "portal_list": [],
                "strategy_list": [],
            },
        ),
        ("indexes.json", {}),
        ("equipment/features.json", {**_valid_feature_payload(), "items": []}),
        ("equipment/features.json", {"items": [_valid_feature_item()]}),
    ],
)
def test_verify_zip_rejects_bad_runtime_schema_payloads(tmp_path: Path, relative: str, payload: dict) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    archive_path = tmp_path / f"bad-{relative.replace('/', '-')}.zip"
    _write_zip(archive_path, _cw_bundle_entries(bundle_root, overrides={relative: payload}))

    with pytest.raises(ValueError):
        verifier.verify_zip(archive_path)


def test_verify_zip_allows_empty_strategy_catalogs(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    raw_config = json.loads(entries[f"{bundle_root}/raw_config.json"].decode("utf-8"))
    guide_config = json.loads(entries[f"{bundle_root}/guide_config.json"].decode("utf-8"))
    guide_config_enriched = json.loads(entries[f"{bundle_root}/guide_config_enriched.json"].decode("utf-8"))
    indexes = json.loads(entries[f"{bundle_root}/indexes.json"].decode("utf-8"))
    raw_config["fight_augment_list"] = []
    guide_config["strategy_list"] = []
    guide_config_enriched["strategy_list"] = []
    indexes["strategies_by_title"] = {}
    archive_path = tmp_path / "empty-strategy.zip"
    _write_zip(
        archive_path,
        _cw_bundle_entries(
            bundle_root,
            overrides={
                "raw_config.json": raw_config,
                "guide_config.json": guide_config,
                "guide_config_enriched.json": guide_config_enriched,
                "indexes.json": indexes,
            },
        ),
    )

    verifier.verify_zip(archive_path)


def test_verify_zip_rejects_extra_manifest_files(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    extra_data = b"{}"
    entries[f"{bundle_root}/semantic-extra.json"] = extra_data
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["files"].append(
        {
            "path": "semantic-extra.json",
            "sha256": hashlib.sha256(extra_data).hexdigest(),
            "size": len(extra_data),
        }
    )
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "extra-manifest-file.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_equipment_local_paths_outside_icon_dir(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    extra_data = b"not-an-icon"
    equipment_manifest = {
        "items": [
            {
                "cache_key": "icon-a",
                "name": "Icon A",
                "kind": "basic",
                "icon_url": "https://example.test/icon-a.png",
                "big_version": "3.2",
                "local_path": "semantic-extra.json",
                "sha256": hashlib.sha256(extra_data).hexdigest(),
                "size": len(extra_data),
            }
        ]
    }
    entries = _cw_bundle_entries(
        bundle_root,
        overrides={
            "equipment/manifest.json": equipment_manifest,
            "semantic-extra.json": extra_data,
        },
    )
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "bad-equipment-local-path.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError, match="cw equipment icon path invalid"):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_unreferenced_role_files(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    entries[f"{bundle_root}/roles/icons/stale.png"] = b"stale"
    archive_path = tmp_path / "unreferenced-role-file.zip"
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="unreferenced generated file"):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_role_feature_manifest_mismatch(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    archive_path = tmp_path / "role-feature-mismatch.zip"
    _write_zip(
        archive_path,
        _cw_bundle_entries(
            bundle_root,
            overrides={"roles/features.json": {**_valid_role_feature_payload(), "items": [_valid_role_feature_item("r2")]}},
        ),
    )

    with pytest.raises(ValueError, match="cw role feature role_id mismatch"):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_role_icon_checksum_mismatch(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    bad_icon_data = b"wrong-role-icon"
    entries[f"{bundle_root}/roles/icons/r1.png"] = bad_icon_data
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "roles/icons/r1.png":
            entry["sha256"] = hashlib.sha256(bad_icon_data).hexdigest()
            entry["size"] = len(bad_icon_data)
            break
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "bad-role-icon-checksum.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError, match="role icon checksum mismatch"):
        verifier.verify_zip(archive_path)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (_truncated_empty_png_bytes(), "invalid"),
        (_png_bytes(size=(102, 120)), "size"),
        (_png_bytes(mode="RGB"), "mode"),
    ],
)
def test_verify_zip_rejects_bad_role_empty_templates(tmp_path: Path, data: bytes, match: str) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(
        bundle_root,
        overrides={
            "roles/manifest.json": _valid_role_manifest_payload(field_empty_data=data),
            "roles/empty/field-v1.png": data,
        },
    )
    archive_path = tmp_path / "bad-role-empty-template.zip"
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match=match):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_unreferenced_generated_files(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    entries[f"{bundle_root}/stale.json"] = b"{}"
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="unreferenced generated file"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_unreferenced_generated_files(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    entries[f"{bundle_root}/stale.json"] = b"{}"
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, entries)

    with pytest.raises(ValueError, match="unreferenced generated file"):
        verifier.verify_tar(archive_path)


def test_verify_zip_rejects_stale_generated_version_directory(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    archive_path = tmp_path / "stale-dir.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr("trail/scenes/cw/generated/old-version/", b"")

    with pytest.raises(ValueError, match="unexpected generated directory"):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_generated_symlink_members(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    archive_path = tmp_path / "symlink-member.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in _cw_bundle_entries(bundle_root).items():
            if name == f"{bundle_root}/equipment/icons/icon-a.png":
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
                continue
            archive.writestr(name, data)

    with pytest.raises(ValueError, match="unsupported zip member"):
        verifier.verify_zip(archive_path)


def test_verify_zip_rejects_directory_mode_file_members(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    archive_path = tmp_path / "directory-mode-file.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in _cw_bundle_entries(bundle_root).items():
            if name == f"{bundle_root}/equipment/icons/icon-a.png":
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, data)
                continue
            archive.writestr(name, data)

    with pytest.raises(ValueError, match="unsupported zip member"):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_stale_generated_version_directory(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    archive_path = tmp_path / "stale-dir.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in _cw_bundle_entries(bundle_root).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
        directory_info = tarfile.TarInfo("trail/scenes/cw/generated/old-version")
        directory_info.type = tarfile.DIRTYPE
        archive.addfile(directory_info)

    with pytest.raises(ValueError, match="unexpected generated directory"):
        verifier.verify_tar(archive_path)


def test_verify_tree_rejects_generated_symlink_files(tmp_path: Path) -> None:
    verifier = _load_verifier()
    tree_path = tmp_path / "dist" / "trail"
    bundle_root = "_internal/trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    _write_tree(tree_path, entries)
    raw_config_path = tree_path / "_internal" / "trail" / "scenes" / "cw" / "generated" / "3.2" / "raw_config.json"
    symlink_target = tmp_path / "raw_config_target.json"
    symlink_target.write_bytes(entries[f"{bundle_root}/raw_config.json"])
    raw_config_path.unlink()
    try:
        raw_config_path.symlink_to(symlink_target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="unsupported tree member"):
        verifier.verify_tree(tree_path, verifier.PYINSTALLER_TREE_LAYOUT)


def test_verify_zip_allows_generated_gitkeep(tmp_path: Path) -> None:
    verifier = _load_verifier()
    entries = _cw_bundle_entries("trail/scenes/cw/generated/3.2")
    entries["trail/scenes/cw/generated/.gitkeep"] = b""
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, entries)

    verifier.verify_zip(archive_path)


def test_verify_zip_rejects_corrupted_content_digest(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["content_digest"] = "0" * 64
    archive_path = tmp_path / "bundle.zip"
    _write_zip(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError):
        verifier.verify_zip(archive_path)


def test_verify_tar_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    verifier = _load_verifier()
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root)
    manifest = json.loads(entries[f"{bundle_root}/manifest.json"].decode("utf-8"))
    manifest["bundle_schema_version"] = 0
    manifest["content_digest"] = _manifest_digest(manifest)
    archive_path = tmp_path / "bundle.tar.gz"
    _write_tar(archive_path, _replace_manifest(entries, bundle_root, manifest))

    with pytest.raises(ValueError):
        verifier.verify_tar(archive_path)
