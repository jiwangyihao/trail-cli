from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.session.models import SessionModel
from trail.scenes.cw.equipment_grid import (
    DEFAULT_EQUIPMENT_GRID_PROFILE,
    crop_equipment_cells,
    crop_has_equipment_slot_markers,
    equipment_cell_center,
    iter_equipment_grid_cells,
)
from trail.scenes.cw.equipment_recognition import EquipmentRecognitionResult, VectorEquipmentIconRecognizer
from trail.scenes.cw.equipment_resources import (
    build_cw_equipment_catalog,
    build_cw_equipment_recipes,
    load_cached_equipment_icons,
    prepare_equipment_icon_cache,
)
from trail.scenes.cw.guide import complete_cw_guide_or_none, fetch_cw_raw_guide_config, require_complete_cw_guide
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.slots import canonical_cw_role_slots, format_cw_agent_slot_reference, parse_cw_slot_reference


def prepare_cw_equipment(*, workspace_root: str | Path | None = None, refresh: bool = False) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(raw_config)
    return prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=refresh)


def _runtime_image(runtime) -> Image.Image:
    capture_image = getattr(runtime, "capture_image", None)
    if callable(capture_image):
        try:
            image = capture_image(normalize=True)
        except TypeError:
            image = capture_image()
    else:
        screenshot = getattr(runtime, "screenshot", None)
        if not callable(screenshot):
            raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot")
        image = screenshot()

    if isinstance(image, bytes | bytearray):
        try:
            with Image.open(BytesIO(image)) as opened:
                image = opened.convert("RGBA")
        except Exception as exc:
            raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot") from exc

    if not isinstance(image, Image.Image):
        raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot")
    if image.size != (1920, 1080):
        raise TrailError("CW_EQUIPMENT_LAYOUT_MISMATCH", "equipment grid requires canonical 1920x1080 screenshot")
    return image.convert("RGBA")


def _save_reused_screenshot(runtime, image: Image.Image, *, request_id: str | None = None) -> str | None:
    save = getattr(runtime, "save_capture_image_to_workspace", None)
    if not callable(save):
        return None
    try:
        path = save(image, request_id=request_id)
    except Exception:
        return None
    return str(path) if path is not None else None


def _item_from_result(crop, result: EquipmentRecognitionResult) -> dict[str, Any] | None:
    if result.empty:
        return None
    top = result.candidates[0] if result.candidates else None
    alt = result.candidates[1] if len(result.candidates) > 1 else None
    center_x, center_y = equipment_cell_center(crop.cell)
    item = {
        "idx": crop.cell.idx,
        "pos": f"equipment:{crop.cell.idx}",
        "row": crop.cell.row,
        "col": crop.cell.col,
        "center": {"x": center_x, "y": center_y},
        "name": top.name if top is not None else None,
        "equipment_id": top.equipment_id if top is not None else None,
        "cache_key": top.cache_key if top is not None else None,
        "score": result.score,
        "gap": result.gap,
        "uncertain": bool(result.uncertain),
        "alt": alt.name if alt is not None else None,
        "alt_score": alt.score if alt is not None else None,
        "candidates": [asdict(candidate) for candidate in result.candidates],
    }
    return item


def _filter_isolated_equipment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idxs = {int(item["idx"]) for item in items}
    return [
        item
        for item in items
        if int(item["idx"]) == 1
        or int(item["idx"]) - 1 in idxs
        or int(item["idx"]) + 1 in idxs
    ]


def _guide_name(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        name = value.get("name")
        if isinstance(name, str):
            text = name.strip()
            return text or None
    return None


def _ordered_names(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        name = _guide_name(value)
        if name is None or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _guide_role_equipment_requirements(guide: dict[str, Any]) -> dict[str, dict[str, str]]:
    by_role: dict[str, dict[str, str]] = {}
    stages = guide.get("role_stages")
    if not isinstance(stages, list):
        return by_role
    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        for area_key in ("front_roles", "back_roles"):
            roles = stage.get(area_key)
            if not isinstance(roles, list):
                continue
            for role in roles:
                if not isinstance(role, Mapping):
                    continue
                role_name = _guide_name(role)
                if role_name is None:
                    continue
                equipment_by_name = by_role.setdefault(role_name, {})
                for equipment_name in _ordered_names(role.get("first_equipments")):
                    equipment_by_name[equipment_name] = "优选"
    return by_role


def _snapshot_equipment_counts(snapshot: dict[str, Any]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    items = snapshot.get("items")
    if not isinstance(items, list):
        return counts
    for item in items:
        if not isinstance(item, Mapping):
            continue
        item_kind = None
        cache_key = item.get("cache_key")
        if isinstance(cache_key, str) and cache_key:
            counts[("cache_key", cache_key)] = counts.get(("cache_key", cache_key), 0) + 1
            prefix = cache_key.split("-", 1)[0]
            if prefix in {"advanced", "basic"}:
                item_kind = prefix
        equipment_id = item.get("equipment_id")
        if isinstance(equipment_id, str) and equipment_id and item_kind is not None:
            kind_id = f"{item_kind}:{equipment_id}"
            counts[("kind_id", kind_id)] = counts.get(("kind_id", kind_id), 0) + 1
        name = item.get("name")
        if isinstance(name, str) and name:
            counts[("name", name)] = counts.get(("name", name), 0) + 1
    return counts


def _basic_have_count(counts: dict[tuple[str, str], int], child) -> int:
    if child.cache_key and ("cache_key", child.cache_key) in counts:
        return counts[("cache_key", child.cache_key)]
    if child.id and ("kind_id", f"basic:{child.id}") in counts:
        return counts[("kind_id", f"basic:{child.id}")]
    if child.cache_key or child.id:
        return 0
    return counts.get(("name", child.name), 0)


def _role_equipments(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("equipments")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def build_cw_equipment_recommendations(
    session: SessionModel,
    *,
    snapshot: dict[str, Any],
    raw_config: dict[str, Any],
) -> dict[str, Any] | None:
    cw_state = ensure_cw_state(session)
    guide = complete_cw_guide_or_none(cw_state)
    if guide is None:
        return None

    recipes = build_cw_equipment_recipes(raw_config)
    role_requirements = _guide_role_equipment_requirements(guide)
    required_roles_by_equipment: dict[str, list[str]] = {}
    for role_name, equipments in role_requirements.items():
        for equipment_name in equipments:
            required_roles_by_equipment.setdefault(equipment_name, []).append(role_name)

    slots = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    slots_fresh = isinstance(slots, dict) and slots.get("stale") is False
    canonical_roles = canonical_cw_role_slots(slots if slots_fresh else {})
    canonical_by_name = {role["name"]: role for role in canonical_roles}
    counts = _snapshot_equipment_counts(snapshot)

    priority: list[dict[str, Any]] = []
    for idx, equipment_name in enumerate(_ordered_names(guide.get("order_compose")), start=1):
        recipe = recipes.get(equipment_name)
        item: dict[str, Any] = {
            "idx": idx,
            "name": equipment_name,
            "known": recipe is not None,
            "required_roles": required_roles_by_equipment.get(equipment_name, []),
        }
        if recipe is not None and recipe.basics:
            item["basics"] = [
                {"name": child.name, "have": _basic_have_count(counts, child), "need": child.need}
                for child in recipe.basics
            ]
        if slots_fresh:
            acquired: list[str] = []
            missing: list[str] = []
            for role_name in item["required_roles"]:
                role = canonical_by_name.get(role_name)
                if role is None:
                    continue
                if equipment_name in _role_equipments(role.get("value")):
                    acquired.append(role_name)
                else:
                    missing.append(role_name)
            item["acquired_roles"] = acquired
            item["missing_roles"] = missing
        priority.append(item)

    role_missing: list[dict[str, Any]] = []
    todos: list[str] = []
    if not slots_fresh:
        todos.append("slots")
    else:
        for role in canonical_roles:
            requirements = role_requirements.get(role["name"], {})
            held = set(_role_equipments(role.get("value")))
            for equipment_name, category in requirements.items():
                if equipment_name in held:
                    continue
                role_missing.append(
                    {
                        "pos": role["pos"],
                        "role": role["name"],
                        "equipment": equipment_name,
                        "category": category,
                    }
                )
    return {"priority": priority, "role_missing": role_missing, "todos": todos}


def _valid_compose_equipment_names(guide: dict[str, Any], raw_config: dict[str, Any]) -> set[str]:
    names = set(build_cw_equipment_recipes(raw_config))
    names.update(_ordered_names(guide.get("order_compose")))
    for equipments in _guide_role_equipment_requirements(guide).values():
        names.update(equipments)
    return names


def _normalize_existing_role_equipments(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("equipments")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID", "role equipments must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_STATE_INVALID", "role equipments must contain strings")
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _role_value_for_compose(value: Any, *, role: str, equipments: list[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        updated = deepcopy(dict(value))
        updated["name"] = role
        updated["equipments"] = list(equipments)
        return updated
    return {"name": role, "equipments": list(equipments)}


def _mark_equipment_snapshot_stale(cw_state: dict[str, Any]) -> None:
    equipment = cw_state.get("equipment")
    if isinstance(equipment, dict):
        equipment["stale"] = True
        equipment.pop("recommendations", None)


def record_cw_equipment_compose(
    session: SessionModel,
    *,
    name: str,
    slot: str,
    role: str,
    raw_config: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    equipment_name = str(name or "").strip()
    role_name = str(role or "").strip()
    if not equipment_name:
        raise TrailError("CW_EQUIPMENT_NAME_INVALID", "equipment name is required")
    if not role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_MISMATCH", "role name is required")

    cw_state = ensure_cw_state(session)
    guide = require_complete_cw_guide(cw_state)
    slots = cw_state.get("slots")
    if not isinstance(slots, dict) or slots.get("stale") is not False:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_STALE", "当前角色槽位已过期，请先执行 cw.slots.read")

    area, index = parse_cw_slot_reference(slot, allowed_areas={"front", "back", "hand"})
    agent_slot = format_cw_agent_slot_reference(area, index)
    values = slots.get(area)
    if not isinstance(values, list) or index >= len(values):
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_EMPTY", f"目标槽位没有角色: {agent_slot}")
    value = values[index]
    slot_role_name = _guide_name(value) or (str(value).strip() if value is not None else "")
    if not slot_role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_EMPTY", f"目标槽位没有角色: {agent_slot}")
    if slot_role_name != role_name:
        raise TrailError("CW_EQUIPMENT_ROLE_SLOT_MISMATCH", f"槽位角色为 {slot_role_name}，不是 {role_name}")

    canonical_by_name = {item["name"]: item for item in canonical_cw_role_slots(slots)}
    canonical_role = canonical_by_name.get(role_name)
    if canonical_role is None or canonical_role["area"] != area or canonical_role["index"] != index:
        expected = canonical_role.get("pos") if canonical_role else role_name
        raise TrailError("CW_EQUIPMENT_ROLE_DUPLICATE_SLOT", f"请使用 canonical slot: {expected}")

    resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
    if equipment_name not in _valid_compose_equipment_names(guide, resolved_raw_config):
        raise TrailError("CW_EQUIPMENT_NAME_INVALID", f"unknown equipment: {equipment_name}")

    equipments = _normalize_existing_role_equipments(value)
    if equipment_name in equipments:
        raise TrailError("CW_EQUIPMENT_ALREADY_HELD", f"{role_name} already has {equipment_name}")
    if len(equipments) >= 3:
        raise TrailError("CW_EQUIPMENT_ROLE_EQUIPMENT_FULL", f"{role_name} already has 3 equipments")

    equipments.append(equipment_name)
    values[index] = _role_value_for_compose(value, role=role_name, equipments=equipments)
    _mark_equipment_snapshot_stale(cw_state)
    return {
        "pos": agent_slot,
        "name": role_name,
        "equipment": equipment_name,
        "count": len(equipments),
    }


def read_cw_equipment(
    runtime,
    *,
    workspace_root: str | Path | None = None,
    raw_config: dict[str, Any] | None = None,
    recognizer=None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if recognizer is None:
        resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
        catalog = build_cw_equipment_catalog(resolved_raw_config)
        prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
        recognizer = VectorEquipmentIconRecognizer(load_cached_equipment_icons(catalog, workspace_root=workspace_root))
    image = _runtime_image(runtime)
    screenshot = _save_reused_screenshot(runtime, image, request_id=request_id) if request_id is not None else None
    cells = list(iter_equipment_grid_cells(DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))
    best_by_idx: dict[int, dict[str, Any]] = {}

    for crop in crop_equipment_cells(image, cells):
        if not crop_has_equipment_slot_markers(crop.image):
            continue
        item = _item_from_result(crop, recognizer.recognize(crop.image))
        if item is None:
            continue
        previous = best_by_idx.get(crop.cell.idx)
        item_score = item.get("score")
        previous_score = None if previous is None else previous.get("score")
        if previous is None or float(item_score if item_score is not None else -1.0) > float(previous_score if previous_score is not None else -1.0):
            best_by_idx[crop.cell.idx] = item

    items = _filter_isolated_equipment_items([best_by_idx[idx] for idx in sorted(best_by_idx)])
    snapshot = {
        "count": len(items),
        "uncertain": sum(1 for item in items if item.get("uncertain")),
        "empty": len(cells) - len(items),
        "items": items,
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }
    if screenshot is not None:
        snapshot["_screenshot"] = screenshot
    return snapshot


def apply_cw_equipment_read(
    session: SessionModel,
    runtime,
    *,
    workspace_root: str | Path | None = None,
    raw_config: dict[str, Any] | None = None,
    recognizer=None,
    request_id: str | None = None,
) -> dict[str, Any]:
    resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
    snapshot = read_cw_equipment(
        runtime,
        workspace_root=workspace_root,
        raw_config=resolved_raw_config,
        recognizer=recognizer,
        request_id=request_id,
    )
    screenshot = snapshot.pop("_screenshot", None)
    recommendations = build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=resolved_raw_config)
    if recommendations is not None:
        snapshot["recommendations"] = recommendations
    ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
    if screenshot is not None:
        return {**deepcopy(snapshot), "_screenshot": screenshot}
    return snapshot
