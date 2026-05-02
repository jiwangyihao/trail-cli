from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
from tempfile import TemporaryDirectory
from typing import TypeVar
import zipfile

from trail.core.errors import TrailError
from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    _clean_equipment_icon_relative,
    _clean_role_empty_relative,
    _clean_role_icon_relative,
    _manifest_digest,
    _manifest_without_digest,
    _validate_equipment_feature_manifest_keys,
    _validate_equipment_features,
    _validate_equipment_manifest_payload,
    _validate_guide_config,
    _validate_indexes,
    _validate_raw_config,
    _validate_role_feature_manifest_keys,
    _validate_role_features,
    _validate_role_manifest,
)


GENERATED_PARTS = ("trail", "scenes", "cw", "generated")
REQUIRED_RELATIVES = (
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
EQUIPMENT_ICON_PREFIX = "equipment/icons/"
ROLE_ICON_PREFIX = "roles/icons/"
ArchiveMember = TypeVar("ArchiveMember")
ExpectedLayout = tuple[str | None, ...]
WHEEL_LAYOUT: ExpectedLayout = ("trail", "scenes", "cw", "generated", None)
SDIST_LAYOUT: ExpectedLayout = (None, "trail", "scenes", "cw", "generated", None)
WINDOWS_ZIP_LAYOUT: ExpectedLayout = ("trail", "bin", "_internal", "trail", "scenes", "cw", "generated", None)
PYINSTALLER_TREE_LAYOUT: ExpectedLayout = ("_internal", "trail", "scenes", "cw", "generated", None)


def _clean_relative_path(value: str, escape_prefix: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
        or ".." in path.parts
    ):
        raise ValueError(f"{escape_prefix}: {value}")
    return path.as_posix()


def _clean_member_name(value: str) -> str:
    return _clean_relative_path(value, "archive member escapes bundle")


def _clean_manifest_relative(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest file entry path invalid")
    normalized = _clean_relative_path(value, "manifest file entry escapes bundle")
    if normalized in {"", "."}:
        raise ValueError("manifest file entry path invalid")
    return normalized


def _normalized_originals(
    members: Iterable[ArchiveMember],
    name_of: Callable[[ArchiveMember], str],
) -> dict[str, ArchiveMember]:
    originals: dict[str, ArchiveMember] = {}
    for member in members:
        normalized = _clean_member_name(name_of(member))
        if normalized in originals:
            raise ValueError(f"duplicate normalized archive member: {normalized}")
        originals[normalized] = member
    return originals


def _matches_expected_layout(bundle_root: str, expected_layout: ExpectedLayout | None) -> bool:
    if expected_layout is None:
        return True
    parts = PurePosixPath(bundle_root).parts
    if len(parts) != len(expected_layout):
        return False
    return all(expected is None or actual == expected for actual, expected in zip(parts, expected_layout))


def _find_bundle_root(members: tuple[str, ...], expected_layout: ExpectedLayout | None = None) -> str:
    manifests: list[str] = []
    for member in members:
        parts = PurePosixPath(member).parts
        for index in range(0, len(parts) - 5):
            if tuple(parts[index : index + 4]) != GENERATED_PARTS:
                continue
            if parts[index + 5] == "manifest.json":
                manifests.append("/".join(parts[: index + 5]))
    if not manifests:
        raise ValueError("missing trail/scenes/cw/generated/<version>/manifest.json")
    unique = sorted(set(manifests))
    if len(unique) != 1:
        raise ValueError("expected exactly one cw generated resource bundle")
    bundle_root = unique[0]
    if not _matches_expected_layout(bundle_root, expected_layout):
        raise ValueError(f"unexpected bundle layout: {bundle_root}")
    return bundle_root


def _bundle_member(bundle_root: str, relative: str) -> str:
    return f"{bundle_root}/{relative}" if bundle_root else relative


def _generated_root(bundle_root: str) -> str:
    parts = PurePosixPath(bundle_root).parts
    for index in range(0, len(parts) - len(GENERATED_PARTS) + 1):
        if tuple(parts[index : index + len(GENERATED_PARTS)]) == GENERATED_PARTS:
            return "/".join(parts[: index + len(GENERATED_PARTS)])
    raise ValueError("missing trail/scenes/cw/generated bundle root")


def _load_json_member(read_member: Callable[[str], bytes], member: str) -> dict:
    try:
        payload = json.loads(read_member(member).decode("utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid json member: {member}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"json member must be object: {member}")
    return payload


def _verify_equipment_manifest_icons(
    label: str,
    bundle_root: str,
    referenced: set[str],
    read_member: Callable[[str], bytes],
    equipment_manifest: dict,
) -> set[str]:
    local_paths: set[str] = set()
    for item in equipment_manifest["items"]:
        try:
            relative = _clean_equipment_icon_relative(item.get("local_path"))
        except TrailError as exc:
            raise ValueError(f"{label}: equipment manifest icon path invalid: {item.get('local_path')}") from exc
        if relative not in referenced:
            raise ValueError(f"{label}: equipment manifest references unlisted icon: {relative}")
        data = read_member(_bundle_member(bundle_root, relative))
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"{label}: equipment manifest icon checksum mismatch: {relative}")
        local_paths.add(relative)
    return local_paths


def _write_temp_bundle_member(root: Path, relative: str, data: bytes) -> None:
    target = root.joinpath(*PurePosixPath(relative).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _verify_role_manifest_resources(
    label: str,
    bundle_root: str,
    referenced: set[str],
    read_member: Callable[[str], bytes],
    role_manifest: dict,
) -> set[str]:
    local_paths: set[str] = set()
    temp_files: list[tuple[str, bytes]] = []

    def check_resource(entry: dict, *, kind: str) -> None:
        try:
            relative = (
                _clean_role_empty_relative(entry.get("local_path"))
                if kind == "empty template"
                else _clean_role_icon_relative(entry.get("local_path"))
            )
        except TrailError as exc:
            raise ValueError(f"{label}: role manifest {kind} path invalid: {entry.get('local_path')}") from exc
        if relative not in referenced:
            raise ValueError(f"{label}: role manifest references unlisted {kind}: {relative}")
        data = read_member(_bundle_member(bundle_root, relative))
        if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise ValueError(f"{label}: role {kind} checksum mismatch: {relative}")
        local_paths.add(relative)
        temp_files.append((relative, data))

    empty_templates = role_manifest.get("empty_templates")
    if isinstance(empty_templates, dict):
        for key in ("field", "hand"):
            entry = empty_templates.get(key)
            if isinstance(entry, dict):
                check_resource(entry, kind="empty template")

    items = role_manifest.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                check_resource(item, kind="icon")

    with TemporaryDirectory(prefix="trail-cw-role-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        for relative, data in temp_files:
            _write_temp_bundle_member(temp_root, relative, data)
        try:
            _validate_role_manifest(temp_root, role_manifest)
        except TrailError as exc:
            raise ValueError(f"{label}: runtime schema invalid: {exc}") from exc
    return local_paths


def _verify_runtime_payload_schema(
    label: str,
    bundle_root: str,
    referenced: set[str],
    read_member: Callable[[str], bytes],
) -> set[str]:
    raw_config = _load_json_member(read_member, _bundle_member(bundle_root, "raw_config.json"))
    guide_config = _load_json_member(read_member, _bundle_member(bundle_root, "guide_config.json"))
    guide_config_enriched = _load_json_member(read_member, _bundle_member(bundle_root, "guide_config_enriched.json"))
    indexes = _load_json_member(read_member, _bundle_member(bundle_root, "indexes.json"))
    equipment_manifest = _load_json_member(read_member, _bundle_member(bundle_root, "equipment/manifest.json"))
    equipment_features = _load_json_member(read_member, _bundle_member(bundle_root, "equipment/features.json"))
    role_manifest = _load_json_member(read_member, _bundle_member(bundle_root, "roles/manifest.json"))
    role_features = _load_json_member(read_member, _bundle_member(bundle_root, "roles/features.json"))
    try:
        _validate_raw_config(raw_config)
        _validate_guide_config(guide_config, "guide config")
        _validate_guide_config(guide_config_enriched, "guide config enriched")
        _validate_indexes(indexes)
        _validate_equipment_manifest_payload(equipment_manifest)
        _validate_equipment_features(equipment_features)
        _validate_equipment_feature_manifest_keys(equipment_manifest, equipment_features)
        _validate_role_features(role_features)
    except TrailError as exc:
        raise ValueError(f"{label}: runtime schema invalid: {exc}") from exc
    role_paths = _verify_role_manifest_resources(label, bundle_root, referenced, read_member, role_manifest)
    try:
        _validate_role_feature_manifest_keys(role_manifest, role_features)
    except TrailError as exc:
        raise ValueError(f"{label}: runtime schema invalid: {exc}") from exc
    return {
        *_verify_equipment_manifest_icons(label, bundle_root, referenced, read_member, equipment_manifest),
        *role_paths,
    }


def _verify_members(
    label: str,
    members: tuple[str, ...],
    read_member: Callable[[str], bytes],
    expected_layout: ExpectedLayout | None = None,
    directories: tuple[str, ...] = (),
) -> None:
    member_set = set(members)
    bundle_root = _find_bundle_root(members, expected_layout)
    generated_root = _generated_root(bundle_root)
    allowed_directories = {
        generated_root,
        bundle_root,
        f"{bundle_root}/equipment",
        f"{bundle_root}/equipment/icons",
        f"{bundle_root}/roles",
        f"{bundle_root}/roles/icons",
        f"{bundle_root}/roles/empty",
    }
    for directory in directories:
        if not directory.startswith(generated_root + "/"):
            continue
        if directory not in allowed_directories:
            raise ValueError(f"{label}: unexpected generated directory: {directory}")
    manifest_member = _bundle_member(bundle_root, "manifest.json")
    manifest = _load_json_member(read_member, manifest_member)
    if manifest.get("bundle_schema_version") != CW_RESOURCE_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"{label}: bundle schema version unsupported")
    expected_digest = manifest.get("content_digest")
    if not isinstance(expected_digest, str) or not expected_digest:
        raise ValueError(f"{label}: manifest missing content_digest")
    actual_digest = _manifest_digest(_manifest_without_digest(manifest))
    if actual_digest != expected_digest:
        raise ValueError(f"{label}: manifest digest mismatch")

    missing_required = [relative for relative in REQUIRED_RELATIVES if _bundle_member(bundle_root, relative) not in member_set]
    if missing_required:
        raise ValueError(f"{label}: missing required bundle file {missing_required[0]}")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError(f"{label}: manifest missing files")

    referenced: set[str] = set()
    allowed_members = {manifest_member, _bundle_member(generated_root, ".gitkeep")}
    icon_count = 0
    role_icon_count = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: manifest file entry invalid")
        relative = _clean_manifest_relative(entry.get("path"))
        expected_hash = entry.get("sha256")
        expected_size = entry.get("size")
        if not isinstance(expected_hash, str) or not isinstance(expected_size, int) or isinstance(expected_size, bool):
            raise ValueError(f"{label}: manifest file entry incomplete: {relative}")
        if relative in referenced:
            raise ValueError(f"{label}: duplicate manifest file entry: {relative}")
        referenced.add(relative)
        if relative.startswith(EQUIPMENT_ICON_PREFIX):
            icon_count += 1
        if relative.startswith(ROLE_ICON_PREFIX):
            role_icon_count += 1

        member = _bundle_member(bundle_root, relative)
        if member not in member_set:
            raise ValueError(f"{label}: manifest references missing file: {relative}")
        allowed_members.add(member)
        data = read_member(member)
        actual_hash = hashlib.sha256(data).hexdigest()
        if len(data) != expected_size or actual_hash != expected_hash:
            raise ValueError(f"{label}: checksum mismatch: {relative}")

    missing_references = [relative for relative in REQUIRED_RELATIVES if relative not in referenced]
    if missing_references:
        raise ValueError(f"{label}: manifest missing required file entry {missing_references[0]}")
    if icon_count == 0:
        raise ValueError(f"{label}: manifest missing equipment/icons/ entry")
    if role_icon_count == 0:
        raise ValueError(f"{label}: manifest missing roles/icons/ entry")
    equipment_paths = _verify_runtime_payload_schema(label, bundle_root, referenced, read_member)
    allowed_references = {*REQUIRED_RELATIVES, *equipment_paths}
    extra_references = sorted(referenced - allowed_references)
    if extra_references:
        raise ValueError(f"{label}: manifest references unexpected file: {extra_references[0]}")

    generated_prefix = _generated_root(bundle_root) + "/"
    for member in members:
        if member.startswith(generated_prefix) and member not in allowed_members:
            raise ValueError(f"{label}: unreferenced generated file: {member}")


def verify_tree(path: Path, expected_layout: ExpectedLayout | None = None) -> None:
    root = path.resolve()
    unsupported = sorted(_clean_member_name(str(item.relative_to(root))) for item in root.rglob("*") if item.is_symlink())
    if unsupported:
        raise ValueError(f"{path}: unsupported tree member: {unsupported[0]}")
    files = tuple(
        sorted(_clean_member_name(str(item.relative_to(root))) for item in root.rglob("*") if item.is_file())
    )
    directories = tuple(
        sorted(_clean_member_name(str(item.relative_to(root))) for item in root.rglob("*") if item.is_dir())
    )

    def read_member(member: str) -> bytes:
        return (root / member).read_bytes()

    _verify_members(str(path), files, read_member, expected_layout, directories)


def verify_zip(path: Path, expected_layout: ExpectedLayout | None = None) -> None:
    with zipfile.ZipFile(path) as archive:
        originals: dict[str, zipfile.ZipInfo] = {}
        directories: set[str] = set()
        seen: set[str] = set()
        for info in archive.infolist():
            normalized = _clean_member_name(info.filename).rstrip("/")
            if normalized in seen:
                raise ValueError(f"duplicate normalized archive member: {normalized}")
            seen.add(normalized)
            unix_type = stat.S_IFMT(info.external_attr >> 16)
            is_dir = info.is_dir()
            if unix_type and ((is_dir and unix_type != stat.S_IFDIR) or (not is_dir and unix_type != stat.S_IFREG)):
                raise ValueError(f"unsupported zip member: {info.filename}")
            if is_dir:
                directories.add(normalized)
                continue
            originals[normalized] = info

        def read_member(member: str) -> bytes:
            return archive.read(originals[member])

        _verify_members(str(path), tuple(sorted(originals)), read_member, expected_layout, tuple(sorted(directories)))


def verify_tar(path: Path, expected_layout: ExpectedLayout | None = None) -> None:
    with tarfile.open(path, "r:*") as archive:
        originals: dict[str, tarfile.TarInfo] = {}
        directories: set[str] = set()
        seen: set[str] = set()
        for member in archive.getmembers():
            normalized = _clean_member_name(member.name).rstrip("/")
            if member.linkname:
                _clean_member_name(member.linkname)
            if normalized in seen:
                raise ValueError(f"duplicate normalized archive member: {normalized}")
            seen.add(normalized)
            if member.isdir():
                directories.add(normalized)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported tar member: {member.name}")
            originals[normalized] = member

        def read_member(member: str) -> bytes:
            extracted = archive.extractfile(originals[member])
            if extracted is None:
                raise KeyError(member)
            return extracted.read()

        _verify_members(str(path), tuple(sorted(originals)), read_member, expected_layout, tuple(sorted(directories)))


def verify_target(path: Path) -> None:
    if path.is_dir():
        verify_tree(path, PYINSTALLER_TREE_LAYOUT)
        return
    if path.suffix == ".whl":
        verify_zip(path, WHEEL_LAYOUT)
        return
    if path.suffix == ".zip":
        verify_zip(path, WINDOWS_ZIP_LAYOUT)
        return
    if path.name.endswith(".tar.gz"):
        verify_tar(path, SDIST_LAYOUT)
        return
    raise ValueError(f"unsupported build artifact: {path}")


def _dist_targets(path: Path) -> list[Path]:
    targets = [*sorted(path.glob("*.whl")), *sorted(path.glob("*.tar.gz")), *sorted(path.glob("*.zip"))]
    tree = path / "trail"
    if tree.is_dir():
        targets.append(tree)
    return targets


def _targets(argv: list[str]) -> list[Path]:
    values = argv or ["dist"]
    targets: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value)
        expanded = _dist_targets(path) if path.is_dir() and path.name == "dist" else [path]
        for target in expanded:
            if not target.exists():
                continue
            resolved = target.resolve()
            if resolved not in seen:
                targets.append(target)
                seen.add(resolved)
    return targets


def main(argv: list[str] | None = None) -> None:
    targets = _targets(list(sys.argv[1:] if argv is None else argv))
    if not targets:
        raise SystemExit("no build artifacts found")
    for target in targets:
        try:
            verify_target(target)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    print(f"ok cw_resource_bundle_artifacts count={len(targets)}")


if __name__ == "__main__":
    main()
