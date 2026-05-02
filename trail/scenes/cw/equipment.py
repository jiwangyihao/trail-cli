from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import nullcontext
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
    equipment_slot_center,
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
from trail.scenes.cw.slots import (
    SLOT_POINTS_BY_AREA,
    canonical_cw_role_slots,
    format_cw_agent_slot_reference,
    parse_cw_slot_reference,
    read_cw_slots,
)


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


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _item_identity_keys(item: Any) -> list[tuple[str, str]]:
    if not isinstance(item, Mapping):
        return []

    keys: list[tuple[str, str]] = []
    cache_key = item.get("cache_key")
    equipment_id = item.get("equipment_id")
    prefix = None
    if isinstance(cache_key, str) and cache_key:
        keys.append(("cache_key", cache_key))
        prefix = cache_key.split("-", 1)[0]
        if prefix in {"basic", "advanced"} and isinstance(equipment_id, str) and equipment_id:
            keys.append(("kind_id", f"{prefix}:{equipment_id}"))
    name = item.get("name")
    if prefix != "advanced" and isinstance(name, str) and name:
        keys.append(("name", name))
    return keys


def _recipe_child_match_key(child) -> tuple[str, str]:
    if child.cache_key:
        return ("cache_key", child.cache_key)
    if child.id:
        return ("kind_id", f"basic:{child.id}")
    return ("name", child.name)


def _equipment_identity_counter(items: list[Any]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for item in items:
        counts.update(_item_identity_keys(item))
    return counts


def _basic_have_count(counts: dict[tuple[str, str], int], child) -> int:
    if child.cache_key and ("cache_key", child.cache_key) in counts:
        return counts[("cache_key", child.cache_key)]
    if child.id and ("kind_id", f"basic:{child.id}") in counts:
        return counts[("kind_id", f"basic:{child.id}")]
    if child.cache_key or child.id:
        return 0
    return counts.get(("name", child.name), 0)


def _equipment_item_idx(item: Any) -> int:
    if not isinstance(item, Mapping):
        return 10**9
    try:
        return int(item.get("idx"))
    except (TypeError, ValueError):
        return 10**9


def _held_compose_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": item.get("idx"),
        "pos": item.get("pos"),
        "name": item.get("name"),
        "equipment_id": item.get("equipment_id"),
        "cache_key": item.get("cache_key"),
    }


def select_cw_equipment_compose_materials(
    snapshot: dict[str, Any],
    *,
    name: str,
    raw_config: dict[str, Any],
) -> dict[str, Any]:
    equipment_name = str(name or "").strip()
    recipes = build_cw_equipment_recipes(raw_config)
    recipe = recipes.get(equipment_name)
    if recipe is None:
        raise TrailError("CW_EQUIPMENT_RECIPE_MISSING", f"missing equipment recipe: {equipment_name}")

    total_basic_need = sum(child.need for child in recipe.basics)
    if total_basic_need != 2:
        raise TrailError("CW_EQUIPMENT_RECIPE_UNSUPPORTED", f"unsupported equipment recipe: {equipment_name}")

    items = sorted(
        [item for item in _as_list(snapshot.get("items")) if isinstance(item, Mapping)],
        key=_equipment_item_idx,
    )
    counts = _equipment_identity_counter(items)
    needed = [
        {"name": child.name, "need": child.need, "have": counts.get(_recipe_child_match_key(child), 0)}
        for child in recipe.basics
    ]
    held = [_held_compose_item(item) for item in items]

    available = list(items)
    materials: list[Mapping[str, Any]] = []
    for child in recipe.basics:
        match_key = _recipe_child_match_key(child)
        for _ in range(child.need):
            selected_index = next(
                (idx for idx, item in enumerate(available) if match_key in _item_identity_keys(item)),
                None,
            )
            if selected_index is None:
                need_text = "|".join(f"{item['name']}:{item['have']}/{item['need']}" for item in needed)
                held_text = "|".join(f"{item.get('pos')}:{item.get('name')}" for item in held)
                error = TrailError("CW_EQUIPMENT_MATERIALS_MISSING", f"合成 {equipment_name} 的基础装备不足")
                error.data = {"needed": needed, "held": held}
                error.warnings = [
                    {
                        "code": "CW_EQUIPMENT_MATERIALS_MISSING",
                        "message": f"合成 {equipment_name} 的基础装备不足",
                        "需求": need_text,
                        "持有": held_text,
                    }
                ]
                raise error
            materials.append(available.pop(selected_index))

    for material in materials:
        if material.get("uncertain") is True:
            error = TrailError("CW_EQUIPMENT_MATERIAL_UNCERTAIN", f"基础装备识别低置信: {material.get('pos')}")
            error.data = {"material": material}
            raise error

    return {"equipment": equipment_name, "materials": list(materials), "needed": needed, "held": held}


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


def validate_cw_equipment_compose_preflight(
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

    scene_state = session.scene_state.get("cw")
    cw_state = scene_state if isinstance(scene_state, dict) else {}
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

    return {
        "equipment_name": equipment_name,
        "role_name": role_name,
        "area": area,
        "index": index,
        "agent_slot": agent_slot,
        "values": values,
        "value": value,
        "equipments": equipments,
    }


def commit_cw_equipment_compose_record(session: SessionModel, preflight: dict[str, Any]) -> dict[str, Any]:
    equipments = list(preflight["equipments"])
    equipment_name = preflight["equipment_name"]
    role_name = preflight["role_name"]
    equipments.append(equipment_name)
    values = preflight["values"]
    index = preflight["index"]
    value = preflight["value"]
    values[index] = _role_value_for_compose(value, role=role_name, equipments=equipments)
    cw_state = ensure_cw_state(session)
    _mark_equipment_snapshot_stale(cw_state)
    return {
        "pos": preflight["agent_slot"],
        "name": role_name,
        "equipment": equipment_name,
        "count": len(equipments),
    }


def record_cw_equipment_compose(
    session: SessionModel,
    *,
    name: str,
    slot: str,
    role: str,
    raw_config: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    preflight = validate_cw_equipment_compose_preflight(
        session,
        name=name,
        slot=slot,
        role=role,
        raw_config=raw_config,
        workspace_root=workspace_root,
    )
    return commit_cw_equipment_compose_record(session, preflight)


def _snapshot_items(snapshot: dict[str, Any]) -> list[Any]:
    return _as_list(snapshot.get("items"))


def _snapshot_count(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("count")
    if isinstance(value, int):
        return value
    return len(_snapshot_items(snapshot))


def _item_at_equipment_idx(snapshot: dict[str, Any], idx: int) -> Mapping[str, Any] | None:
    for item in _snapshot_items(snapshot):
        if not isinstance(item, Mapping):
            continue
        if _equipment_item_idx(item) == idx:
            return item
    return None


def _counter_without_zeros(counts: Counter[tuple[str, str]]) -> Counter[tuple[str, str]]:
    return Counter({key: value for key, value in counts.items() if value > 0})


def _target_recipe_identity_counter(recipe) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    if recipe.cache_key:
        counts[("cache_key", recipe.cache_key)] += 1
    if recipe.id:
        counts[("kind_id", f"advanced:{recipe.id}")] += 1
    if not counts:
        counts[("name", recipe.name)] += 1
    return counts


def _item_matches_target_recipe(item: Mapping[str, Any], recipe) -> bool:
    item_keys = set(_item_identity_keys(item))
    target_keys = set(_target_recipe_identity_counter(recipe))
    return bool(item_keys & target_keys)


def select_existing_cw_equipment_target(snapshot: dict[str, Any], *, recipe) -> Mapping[str, Any] | None:
    items = sorted(
        [item for item in _as_list(snapshot.get("items")) if isinstance(item, Mapping)],
        key=_equipment_item_idx,
    )
    for item in items:
        if item.get("uncertain") is True:
            continue
        if _item_matches_target_recipe(item, recipe):
            return item
    return None


def _expected_post_compose_counter(
    initial_snapshot: dict[str, Any],
    materials: list[Mapping[str, Any]],
    recipe,
) -> Counter[tuple[str, str]]:
    expected = _equipment_identity_counter(_snapshot_items(initial_snapshot))
    for material in materials:
        expected.subtract(_equipment_identity_counter([material]))
    expected.update(_target_recipe_identity_counter(recipe))
    return _counter_without_zeros(expected)


def _recognizable_shift_keys(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    if item.get("uncertain") is True:
        return []
    return _item_identity_keys(item)


def _unique_shift_identity_key(
    item: Mapping[str, Any],
    *,
    initial_counts: Counter[tuple[str, str]],
    post_counts: Counter[tuple[str, str]],
) -> tuple[str, str] | None:
    keys = _recognizable_shift_keys(item)
    for kind in ("cache_key", "kind_id", "name"):
        for key in keys:
            if key[0] == kind and initial_counts[key] == 1 and post_counts[key] == 1:
                return key
    return None


def _shift_identity_indexes_by_key(items: list[Mapping[str, Any]]) -> dict[tuple[str, str], list[int]]:
    indexes: dict[tuple[str, str], list[int]] = {}
    for item in items:
        item_idx = _equipment_item_idx(item)
        if item_idx == 10**9:
            continue
        for key in set(_recognizable_shift_keys(item)):
            indexes.setdefault(key, []).append(item_idx)
    return indexes


def _verify_cw_equipment_post_compose_shift(
    *,
    initial_snapshot: dict[str, Any],
    post_compose_snapshot: dict[str, Any],
    materials: list[Mapping[str, Any]],
) -> int:
    material_idxs = [_equipment_item_idx(material) for material in materials]
    if not material_idxs:
        return 0

    from_idx = max(material_idxs)
    initial_items = [item for item in _snapshot_items(initial_snapshot) if isinstance(item, Mapping)]
    post_items = [item for item in _snapshot_items(post_compose_snapshot) if isinstance(item, Mapping)]
    initial_by_idx = {_equipment_item_idx(item): item for item in initial_items}
    post_indexes_by_identity = _shift_identity_indexes_by_key(post_items)
    initial_counts = _equipment_identity_counter(initial_items)
    post_counts = _equipment_identity_counter(post_items)
    verified_shift = 0

    current_idx = from_idx + 1
    while current_idx in initial_by_idx:
        initial_item = initial_by_idx[current_idx]
        identity_key = _unique_shift_identity_key(
            initial_item,
            initial_counts=initial_counts,
            post_counts=post_counts,
        )
        if identity_key is None:
            return verified_shift

        expected_idx = current_idx - 1
        post_indexes = post_indexes_by_identity.get(identity_key, [])
        if not post_indexes:
            return verified_shift
        if len(post_indexes) != 1:
            return verified_shift

        actual_idx = post_indexes[0]
        if actual_idx == expected_idx:
            verified_shift = 1
            current_idx += 1
            continue

        error = TrailError("CW_EQUIPMENT_COMPOSE_VERIFY_FAILED", "装备合成后背包顺移验证失败")
        error.data = {
            "expected_shift_from": f"equipment:{current_idx}",
            "expected_shift_to": f"equipment:{expected_idx}",
            "actual": f"equipment:{actual_idx}",
            "identity": f"{identity_key[0]}:{identity_key[1]}",
        }
        raise error

    return verified_shift


def _verify_cw_equipment_post_compose(
    *,
    initial_snapshot: dict[str, Any],
    post_compose_snapshot: dict[str, Any],
    materials: list[Mapping[str, Any]],
    recipe,
    to_idx: int,
) -> tuple[Mapping[str, Any], int]:
    result_item = _item_at_equipment_idx(post_compose_snapshot, to_idx)
    if result_item is not None and _item_matches_target_recipe(result_item, recipe) and result_item.get("uncertain") is True:
        error = TrailError("CW_EQUIPMENT_COMPOSE_VERIFY_UNCERTAIN", f"合成结果识别低置信: equipment:{to_idx}")
        error.data = {"item": dict(result_item)}
        raise error

    expected_count = _snapshot_count(initial_snapshot) - 1
    expected_counter = _expected_post_compose_counter(initial_snapshot, materials, recipe)
    actual_counter = _equipment_identity_counter(_snapshot_items(post_compose_snapshot))
    if (
        _snapshot_count(post_compose_snapshot) != expected_count
        or result_item is None
        or not _item_matches_target_recipe(result_item, recipe)
        or _counter_without_zeros(actual_counter) != expected_counter
    ):
        error = TrailError("CW_EQUIPMENT_COMPOSE_VERIFY_FAILED", "装备合成后背包验证失败")
        error.data = {
            "expected_count": expected_count,
            "actual_count": _snapshot_count(post_compose_snapshot),
            "target": f"equipment:{to_idx}",
        }
        raise error

    verified_shift = _verify_cw_equipment_post_compose_shift(
        initial_snapshot=initial_snapshot,
        post_compose_snapshot=post_compose_snapshot,
        materials=materials,
    )
    return result_item, verified_shift


def _verify_cw_equipment_post_equip(
    *,
    post_compose_snapshot: dict[str, Any],
    post_equip_snapshot: dict[str, Any],
    result_item: Mapping[str, Any],
) -> None:
    expected_count = _snapshot_count(post_compose_snapshot) - 1
    expected_counter = _equipment_identity_counter(_snapshot_items(post_compose_snapshot))
    expected_counter.subtract(_equipment_identity_counter([result_item]))
    actual_counter = _equipment_identity_counter(_snapshot_items(post_equip_snapshot))
    if _snapshot_count(post_equip_snapshot) != expected_count or _counter_without_zeros(actual_counter) != _counter_without_zeros(expected_counter):
        error = TrailError("CW_EQUIPMENT_COMPOSE_EQUIP_VERIFY_FAILED", "装备给角色后背包验证失败")
        error.data = {
            "expected_count": expected_count,
            "actual_count": _snapshot_count(post_equip_snapshot),
            "result_item": dict(result_item),
        }
        raise error


def _verify_cw_equipment_existing_target_removed(
    *,
    post_equip_snapshot: dict[str, Any],
    existing_item: Mapping[str, Any],
    recipe,
) -> None:
    existing_idx = _equipment_item_idx(existing_item)
    current_item = _item_at_equipment_idx(post_equip_snapshot, existing_idx)
    if current_item is not None and _item_matches_target_recipe(current_item, recipe):
        error = TrailError("CW_EQUIPMENT_COMPOSE_EQUIP_VERIFY_FAILED", "装备给角色后背包槽位验证失败")
        error.data = {
            "expected_removed": f"equipment:{existing_idx}",
            "actual_item": dict(current_item),
            "result_item": dict(existing_item),
        }
        raise error


def compose_and_equip_cw_equipment(
    session: SessionModel,
    runtime,
    *,
    name: str,
    slot: str,
    role: str,
    raw_config: dict[str, Any],
    recognizer=None,
    workspace_root: str | Path | None = None,
    request_id: str | None = None,
    slots_reader: Callable[[], Any],
    guide_config: dict[str, Any] | None = None,
    preflight_read_scope: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    read_scope = preflight_read_scope or nullcontext
    with read_scope():
        read_cw_slots(session, reader=slots_reader, guide_config=guide_config)

    preflight = validate_cw_equipment_compose_preflight(
        session,
        name=name,
        slot=slot,
        role=role,
        raw_config=raw_config,
        workspace_root=workspace_root,
    )

    with read_scope():
        initial_snapshot = apply_cw_equipment_read(
            session,
            runtime,
            workspace_root=workspace_root,
            raw_config=raw_config,
            recognizer=recognizer,
            request_id=request_id,
        )

    recipes = build_cw_equipment_recipes(raw_config)
    recipe = recipes.get(preflight["equipment_name"])
    if recipe is not None:
        existing_item = select_existing_cw_equipment_target(initial_snapshot, recipe=recipe)
        if existing_item is not None:
            equip_from = f"equipment:{_equipment_item_idx(existing_item)}"
            equip_to = preflight["agent_slot"]
            runtime.drag_to(*equipment_slot_center(equip_from), *SLOT_POINTS_BY_AREA[preflight["area"]][preflight["index"]])

            post_equip_snapshot = apply_cw_equipment_read(
                session,
                runtime,
                workspace_root=workspace_root,
                raw_config=raw_config,
                recognizer=recognizer,
                request_id=request_id,
            )
            _verify_cw_equipment_post_equip(
                post_compose_snapshot=initial_snapshot,
                post_equip_snapshot=post_equip_snapshot,
                result_item=existing_item,
            )
            _verify_cw_equipment_existing_target_removed(
                post_equip_snapshot=post_equip_snapshot,
                existing_item=existing_item,
                recipe=recipe,
            )

            result = commit_cw_equipment_compose_record(session, preflight)
            result.update(
                {
                    "action": "equip_existing",
                    "role": preflight["role_name"],
                    "slot": equip_to,
                    "equipment_name": preflight["equipment_name"],
                    "existing_item": deepcopy(dict(existing_item)),
                    "equip_action": {"drag_from": equip_from, "drag_to": equip_to},
                    "verified": True,
                    "consumed": 0,
                    "post_equip_equipment_count": _snapshot_count(post_equip_snapshot),
                    "equipment_stale": True,
                }
            )
            return result

    selection = select_cw_equipment_compose_materials(initial_snapshot, name=preflight["equipment_name"], raw_config=raw_config)
    materials = [material for material in selection["materials"] if isinstance(material, Mapping)]
    material_idxs = [_equipment_item_idx(material) for material in materials]
    from_idx = max(material_idxs)
    to_idx = min(material_idxs)
    drag_from = f"equipment:{from_idx}"
    drag_to = f"equipment:{to_idx}"
    runtime.drag_to(*equipment_slot_center(drag_from), *equipment_slot_center(drag_to))

    post_compose_snapshot = apply_cw_equipment_read(
        session,
        runtime,
        workspace_root=workspace_root,
        raw_config=raw_config,
        recognizer=recognizer,
        request_id=request_id,
    )
    recipe = recipes[preflight["equipment_name"]]
    result_item, verified_shift = _verify_cw_equipment_post_compose(
        initial_snapshot=initial_snapshot,
        post_compose_snapshot=post_compose_snapshot,
        materials=materials,
        recipe=recipe,
        to_idx=to_idx,
    )

    equip_from = f"equipment:{to_idx}"
    equip_to = preflight["agent_slot"]
    runtime.drag_to(*equipment_slot_center(equip_from), *SLOT_POINTS_BY_AREA[preflight["area"]][preflight["index"]])

    post_equip_snapshot = apply_cw_equipment_read(
        session,
        runtime,
        workspace_root=workspace_root,
        raw_config=raw_config,
        recognizer=recognizer,
        request_id=request_id,
    )
    _verify_cw_equipment_post_equip(
        post_compose_snapshot=post_compose_snapshot,
        post_equip_snapshot=post_equip_snapshot,
        result_item=result_item,
    )

    result = commit_cw_equipment_compose_record(session, preflight)
    result.update(
        {
            "materials": deepcopy(materials),
            "needed": deepcopy(selection["needed"]),
            "result_item": deepcopy(dict(result_item)),
            "compose_action": {"drag_from": drag_from, "drag_to": drag_to},
            "equip_action": {"drag_from": equip_from, "drag_to": equip_to},
            "verified": True,
            "consumed": 2,
            "post_compose_equipment_count": _snapshot_count(post_compose_snapshot),
            "post_equip_equipment_count": _snapshot_count(post_equip_snapshot),
            "verified_shift": verified_shift,
        }
    )
    return result


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
