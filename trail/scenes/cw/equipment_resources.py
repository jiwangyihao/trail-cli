from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha1
from io import BytesIO
import json
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image

from trail.core.errors import TrailError


EQUIPMENT_ICON_CACHE_RELATIVE = Path(".trail") / "cache" / "cw-equipment-icons"
EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS = 10.0
EQUIPMENT_ICON_MAX_BYTES = 2_000_000
EQUIPMENT_ICON_ALLOWED_HOSTS = {"act-webstatic.mihoyo.com"}
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class EquipmentCatalogEntry:
    cache_key: str
    id: str | None
    name: str
    kind: str
    category: str | None
    category_name: str | None
    icon_url: str
    big_version: str


@dataclass(frozen=True)
class EquipmentRecipeChild:
    cache_key: str | None
    id: str | None
    name: str
    kind: str
    need: int


@dataclass(frozen=True)
class EquipmentRecipe:
    cache_key: str | None
    id: str | None
    name: str
    basics: tuple[EquipmentRecipeChild, ...]


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cache_key(*, kind: str, equipment_id: str | None, name: str, icon_url: str) -> str:
    if equipment_id:
        return f"{kind}-{equipment_id}"
    digest = sha1(f"{name}\n{icon_url}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-noid-{digest}"


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip(". ")
    cleaned_upper = cleaned.upper()
    reserved_stem = cleaned_upper.rsplit(".", 1)[0]
    reserved_prefix = cleaned_upper.split(".", 1)[0]
    if (
        not cleaned
        or cleaned in {".", ".."}
        or cleaned_upper in _WINDOWS_RESERVED_NAMES
        or reserved_stem in _WINDOWS_RESERVED_NAMES
        or reserved_prefix in _WINDOWS_RESERVED_NAMES
    ):
        return sha1(value.encode("utf-8")).hexdigest()[:16]
    return cleaned


def safe_equipment_cache_segment(value: str) -> str:
    return _safe_segment(value)


def _is_path_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _ensure_cache_path_inside_workspace(workspace_root: str | Path | None, path: Path) -> None:
    root = Path.cwd() if workspace_root is None else Path(workspace_root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache path escapes workspace: {path}") from exc
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if _is_path_link(candidate):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache path must not be symlink: {candidate}")
    try:
        if not path.resolve(strict=False).is_relative_to(root.resolve()):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache path escapes workspace: {path}")
    except ValueError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache path escapes workspace: {path}") from exc


def _cache_root(workspace_root: str | Path | None) -> Path:
    root = Path.cwd() if workspace_root is None else Path(workspace_root)
    cache_root = root / EQUIPMENT_ICON_CACHE_RELATIVE
    _ensure_cache_path_inside_workspace(workspace_root, cache_root)
    return cache_root


def _version_dir(big_version: str, *, workspace_root: str | Path | None) -> Path:
    version_dir = _cache_root(workspace_root) / _safe_segment(big_version)
    _ensure_cache_path_inside_workspace(workspace_root, version_dir)
    return version_dir


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in EQUIPMENT_ICON_ALLOWED_HOSTS:
        raise TrailError("CW_EQUIPMENT_ICON_URL_UNSUPPORTED", f"unsupported equipment icon url: {url}")


def _download_icon(url: str, *, timeout: float, max_bytes: int) -> bytes:
    _validate_https_url(url)
    request = Request(url, headers={"user-agent": "trail-cli"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TrailError("CW_EQUIPMENT_ICON_TOO_LARGE", "equipment icon response too large")
    return data


def download_equipment_icon_bytes(url: str, *, timeout: float, max_bytes: int) -> bytes:
    try:
        return _download_icon(url, timeout=timeout, max_bytes=max_bytes)
    except TrailError:
        raise
    except Exception as exc:
        raise TrailError("CW_EQUIPMENT_ICON_DOWNLOAD_FAILED", f"failed to download equipment icon: {url}") from exc


def _open_verified_png(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.convert("RGBA").load()
        return True
    except Exception:
        return False


def _write_verified_icon(path: Path, data: bytes) -> None:
    if _is_path_link(path):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache file must not be symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if _is_path_link(tmp_path):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache temp file must not be symlink: {tmp_path}")
    try:
        with Image.open(BytesIO(data)) as image:
            image.convert("RGBA").save(tmp_path, format="PNG")
        if not _open_verified_png(tmp_path):
            raise TrailError("CW_EQUIPMENT_ICON_INVALID", "equipment icon cannot be opened by PIL")
    except TrailError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise TrailError("CW_EQUIPMENT_ICON_INVALID", "equipment icon cannot be opened by PIL") from exc
    try:
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def write_verified_equipment_icon(path: Path, data: bytes) -> None:
    _write_verified_icon(path, data)


def _fetch_icon_bytes(fetcher, url: str) -> bytes:
    try:
        return fetcher(url, timeout=EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS, max_bytes=EQUIPMENT_ICON_MAX_BYTES)
    except TrailError:
        raise
    except Exception as exc:
        raise TrailError("CW_EQUIPMENT_ICON_DOWNLOAD_FAILED", f"failed to download equipment icon: {url}") from exc


def _entry_from_mapping(item: Mapping[str, Any], *, kind: str, big_version: str) -> EquipmentCatalogEntry | None:
    name = _text_or_none(item.get("name"))
    icon_url = _text_or_none(item.get("icon"))
    if name is None or icon_url is None:
        return None
    equipment_id = _text_or_none(item.get("id"))
    return EquipmentCatalogEntry(
        cache_key=_cache_key(kind=kind, equipment_id=equipment_id, name=name, icon_url=icon_url),
        id=equipment_id,
        name=name,
        kind=kind,
        category=_text_or_none(item.get("category")),
        category_name=_text_or_none(item.get("category_name")),
        icon_url=icon_url,
        big_version=big_version,
    )


def _append_unique(target: list[EquipmentCatalogEntry], seen: set[str], entry: EquipmentCatalogEntry | None) -> None:
    if entry is None or entry.cache_key in seen:
        return
    seen.add(entry.cache_key)
    target.append(entry)


def build_cw_equipment_catalog(raw_config: Mapping[str, Any]) -> list[EquipmentCatalogEntry]:
    big_version = _text_or_none(raw_config.get("rpg_game_big_version"))
    if big_version is None:
        raise TrailError("CW_EQUIPMENT_VERSION_MISSING", "cw equipment config missing rpg_game_big_version")
    equipment_list = raw_config.get("equipment_list")
    if not isinstance(equipment_list, list):
        raise TrailError("CW_EQUIPMENT_CONFIG_INVALID", "cw equipment config missing equipment_list")

    catalog: list[EquipmentCatalogEntry] = []
    seen: set[str] = set()
    for item in equipment_list:
        if not isinstance(item, Mapping):
            continue
        _append_unique(catalog, seen, _entry_from_mapping(item, kind="advanced", big_version=big_version))
        compose_list = item.get("compose_list")
        if not isinstance(compose_list, list):
            continue
        for compose in compose_list:
            if not isinstance(compose, Mapping):
                continue
            childrens = compose.get("childrens")
            if not isinstance(childrens, list):
                continue
            for child in childrens:
                if isinstance(child, Mapping):
                    _append_unique(catalog, seen, _entry_from_mapping(child, kind="basic", big_version=big_version))
    return catalog


def _recipe_child_identity(child: EquipmentRecipeChild) -> tuple[str, str]:
    if child.cache_key:
        return ("cache_key", child.cache_key)
    if child.id:
        return ("kind_id", f"{child.kind}:{child.id}")
    return ("name", child.name)


def _recipe_child_from_mapping(item: Mapping[str, Any], *, big_version: str) -> EquipmentRecipeChild | None:
    name = _text_or_none(item.get("name"))
    if name is None:
        return None
    entry = _entry_from_mapping(item, kind="basic", big_version=big_version)
    equipment_id = _text_or_none(item.get("id"))
    return EquipmentRecipeChild(
        cache_key=None if entry is None else entry.cache_key,
        id=equipment_id,
        name=name,
        kind="basic",
        need=1,
    )


def build_cw_equipment_recipes(raw_config: Mapping[str, Any]) -> dict[str, EquipmentRecipe]:
    big_version = _text_or_none(raw_config.get("rpg_game_big_version"))
    if big_version is None:
        raise TrailError("CW_EQUIPMENT_VERSION_MISSING", "cw equipment config missing rpg_game_big_version")
    equipment_list = raw_config.get("equipment_list")
    if not isinstance(equipment_list, list):
        raise TrailError("CW_EQUIPMENT_CONFIG_INVALID", "cw equipment config missing equipment_list")

    recipes: dict[str, EquipmentRecipe] = {}
    for item in equipment_list:
        if not isinstance(item, Mapping):
            continue
        advanced = _entry_from_mapping(item, kind="advanced", big_version=big_version)
        if advanced is None:
            continue

        children_by_identity: dict[tuple[str, str], EquipmentRecipeChild] = {}
        child_order: list[tuple[str, str]] = []
        compose_list = item.get("compose_list")
        if isinstance(compose_list, list):
            for compose in compose_list:
                if not isinstance(compose, Mapping):
                    continue
                childrens = compose.get("childrens")
                if not isinstance(childrens, list):
                    continue
                for child_item in childrens:
                    if not isinstance(child_item, Mapping):
                        continue
                    child = _recipe_child_from_mapping(child_item, big_version=big_version)
                    if child is None:
                        continue
                    identity = _recipe_child_identity(child)
                    if identity not in children_by_identity:
                        child_order.append(identity)
                        children_by_identity[identity] = child
                    else:
                        previous = children_by_identity[identity]
                        children_by_identity[identity] = EquipmentRecipeChild(
                            cache_key=previous.cache_key,
                            id=previous.id,
                            name=previous.name,
                            kind=previous.kind,
                            need=previous.need + 1,
                        )

        recipes[advanced.name] = EquipmentRecipe(
            cache_key=advanced.cache_key,
            id=advanced.id,
            name=advanced.name,
            basics=tuple(children_by_identity[key] for key in child_order),
        )
    return recipes


@contextmanager
def _version_lock(version_dir: Path):
    version_dir.mkdir(parents=True, exist_ok=True)
    lock_path = version_dir / ".prepare.lock"
    deadline = monotonic() + 30.0
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if monotonic() >= deadline:
                raise TrailError("CW_EQUIPMENT_CACHE_LOCK_TIMEOUT", "equipment icon cache lock timeout")
            sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def _manifest_path(version_dir: Path) -> Path:
    return version_dir / "manifest.json"


def _workspace_root_from_version_dir(version_dir: Path) -> Path:
    return version_dir.parents[3]


def _load_manifest(version_dir: Path) -> dict[str, Any]:
    path = _manifest_path(version_dir)
    _ensure_cache_path_inside_workspace(_workspace_root_from_version_dir(version_dir), path)
    if not path.is_file():
        return {"items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    return payload if isinstance(payload, dict) else {"items": []}


def _write_manifest(version_dir: Path, manifest: dict[str, Any]) -> None:
    _ensure_cache_path_inside_workspace(_workspace_root_from_version_dir(version_dir), _manifest_path(version_dir))
    tmp_path = _manifest_path(version_dir).with_suffix(".json.tmp")
    if _is_path_link(_manifest_path(version_dir)) or _is_path_link(tmp_path):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon cache manifest must not be symlink: {version_dir}")
    tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, _manifest_path(version_dir))


def _manifest_items_by_key(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = manifest.get("items")
    if not isinstance(items, list):
        return {}
    return {str(item.get("cache_key")): dict(item) for item in items if isinstance(item, dict) and item.get("cache_key")}


def _manifest_entry(entry: EquipmentCatalogEntry, *, local_path: str) -> dict[str, Any]:
    return {
        "cache_key": entry.cache_key,
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "category": entry.category,
        "category_name": entry.category_name,
        "icon_url": entry.icon_url,
        "local_path": local_path,
    }


def equipment_bundle_manifest_entry(
    entry: EquipmentCatalogEntry,
    *,
    local_path: str,
    sha256: str | None = None,
    size: int | None = None,
) -> dict[str, Any]:
    payload = _manifest_entry(entry, local_path=local_path)
    payload["big_version"] = entry.big_version
    if sha256 is not None:
        payload["sha256"] = sha256
    if size is not None:
        payload["size"] = size
    return payload


def prepare_equipment_icon_cache(
    catalog: list[EquipmentCatalogEntry],
    *,
    workspace_root: str | Path | None = None,
    refresh: bool = False,
    fetcher=_download_icon,
) -> dict[str, Any]:
    if not catalog:
        return {"big_version": "", "count": 0, "cached": 0, "downloaded": 0, "refreshed": bool(refresh)}
    big_version = catalog[0].big_version
    version_dir = _version_dir(big_version, workspace_root=workspace_root)
    downloaded = 0
    cached = 0
    with _version_lock(version_dir):
        manifest = _load_manifest(version_dir)
        previous_by_key = _manifest_items_by_key(manifest)
        next_items: list[dict[str, Any]] = []
        for entry in catalog:
            safe_key = _safe_segment(entry.cache_key)
            local_rel = f"icons/{safe_key}.png"
            local_path = version_dir / "icons" / f"{safe_key}.png"
            _ensure_cache_path_inside_workspace(workspace_root, local_path.parent)
            _ensure_cache_path_inside_workspace(workspace_root, local_path)
            previous = previous_by_key.get(entry.cache_key)
            url_changed = bool(previous and previous.get("icon_url") != entry.icon_url)
            locked_to_previous = bool(previous and url_changed and not refresh)
            download_url = previous.get("icon_url") if locked_to_previous else entry.icon_url
            if not isinstance(download_url, str) or not download_url:
                download_url = entry.icon_url
            needs_download = not local_path.is_file() or not _open_verified_png(local_path) or (refresh and url_changed)
            if needs_download:
                data = _fetch_icon_bytes(fetcher, download_url)
                _write_verified_icon(local_path, data)
                downloaded += 1
                next_items.append(previous if locked_to_previous and previous is not None else _manifest_entry(entry, local_path=local_rel))
            else:
                cached += 1
                if locked_to_previous and previous is not None:
                    next_items.append(previous)
                else:
                    next_items.append(_manifest_entry(entry, local_path=local_rel))
        _write_manifest(version_dir, {"big_version": big_version, "items": next_items})
    return {"big_version": big_version, "count": len(catalog), "cached": cached, "downloaded": downloaded, "refreshed": bool(refresh)}


def load_cached_equipment_icons(
    catalog: list[EquipmentCatalogEntry],
    *,
    workspace_root: str | Path | None = None,
) -> list[tuple[EquipmentCatalogEntry, Image.Image]]:
    if not catalog:
        return []
    version_dir = _version_dir(catalog[0].big_version, workspace_root=workspace_root)
    loaded: list[tuple[EquipmentCatalogEntry, Image.Image]] = []
    for entry in catalog:
        path = version_dir / "icons" / f"{_safe_segment(entry.cache_key)}.png"
        _ensure_cache_path_inside_workspace(workspace_root, path.parent)
        _ensure_cache_path_inside_workspace(workspace_root, path)
        if not path.is_file() or not _open_verified_png(path):
            raise TrailError("CW_EQUIPMENT_ICON_CACHE_INCOMPLETE", f"equipment icon cache incomplete: {entry.cache_key}")
        with Image.open(path) as image:
            loaded.append((entry, image.convert("RGBA")))
    return loaded
