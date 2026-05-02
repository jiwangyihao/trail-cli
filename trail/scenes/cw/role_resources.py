from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import os
import re
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_resources import download_equipment_icon_bytes, safe_equipment_cache_segment


ROLE_ICON_MAX_BYTES = 512_000


@dataclass(frozen=True)
class RoleCatalogEntry:
    role_id: str
    name: str
    normalized_name: str
    icon_url: str
    rarity: str | None
    cost: str | None
    front_back_type: str | None
    trait_ids: list[str]


def normalize_role_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lower()).strip()


def safe_role_cache_segment(value: object) -> str:
    return safe_equipment_cache_segment(str(value or "role"))


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _role_trait_ids(role: dict[str, Any]) -> list[str]:
    trait_details = role.get("trait_details")
    if isinstance(trait_details, list):
        trait_ids = [str(item["id"]) for item in trait_details if isinstance(item, dict) and item.get("id") is not None]
        if trait_ids:
            return trait_ids
    raw_trait_ids = role.get("trait_ids")
    return [str(value) for value in raw_trait_ids] if isinstance(raw_trait_ids, list) else []


def build_cw_role_catalog(raw_config: dict[str, Any]) -> list[RoleCatalogEntry]:
    items = raw_config.get("role_list")
    if not isinstance(items, list):
        return []
    result: list[RoleCatalogEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        icon_url = str(item.get("icon") or "").strip()
        if not role_id or not name or not icon_url:
            continue
        result.append(
            RoleCatalogEntry(
                role_id=role_id,
                name=name,
                normalized_name=normalize_role_name(name),
                icon_url=icon_url,
                rarity=_text_or_none(item.get("rarity")),
                cost=_text_or_none(item.get("cost")),
                front_back_type=_text_or_none(item.get("front_back_type")),
                trait_ids=_role_trait_ids(item),
            )
        )
    return result


def download_role_icon_bytes(url: str, *, timeout: float, max_bytes: int = ROLE_ICON_MAX_BYTES) -> bytes:
    return download_equipment_icon_bytes(url, timeout=timeout, max_bytes=max_bytes)


def write_verified_role_icon(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with Image.open(BytesIO(data)) as image:
            image.convert("RGBA").save(tmp_path, format="PNG")
        with Image.open(tmp_path) as image:
            image.verify()
        os.replace(tmp_path, path)
    except TrailError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise TrailError("CW_ROLE_ICON_INVALID", f"invalid cw role icon: {path}") from exc
