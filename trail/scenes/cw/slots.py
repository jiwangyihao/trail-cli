from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from time import sleep
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.batch_ocr import BatchOcrTarget, run_batch_ocr
from trail.scenes.cw import stage
from trail.scenes.cw.catalog import CwCatalog, build_cw_catalog, resolve_cw_role_name, summarize_cw_field_traits
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.role_recognition import CANONICAL_SCREENSHOT_SIZE, iter_slot_specs, warp_slot_crop
from trail.runtime.resources import resolve_scene_asset
from trail.session.models import SessionModel


@dataclass(frozen=True)
class CwStatusReadResult:
    stage: str | None
    stage_status: dict[str, Any]


@dataclass(frozen=True)
class CwSlotsReadResult:
    front: list[Any]
    back: list[Any]
    hand: list[Any]
    stage_status: dict[str, Any] | None = None
    stage: str | None = None
    screenshot: str | None = None


class CwSlotsRecognitionUncertainError(TrailError):
    def __init__(self, code: str, message: str, *, screenshot: str | None = None, warnings: list[dict] | None = None):
        super().__init__(code, message)
        self.screenshot = screenshot
        self.warnings = deepcopy(warnings or [])


@dataclass(frozen=True)
class CwSlotsReadApplied:
    session: SessionModel
    response_snapshot: dict[str, Any]

    @property
    def scene_state(self) -> dict[str, dict[str, Any]]:
        return self.session.scene_state


SlotsSnapshotReader = Callable[[], CwSlotsReadResult | tuple[list[Any], list[Any], list[Any]]]
SlotMover = Callable[[str, str], object]
HandSeller = Callable[[int], object]
CrystalCollector = Callable[[], object]

CW_WIDTH = 1920
CW_HEIGHT = 1080


def _point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
    return int(CW_WIDTH * x_ratio), int(CW_HEIGHT * y_ratio)


def _region(from_x: float, from_y: float, to_x: float, to_y: float) -> dict[str, int]:
    return {
        "from_x": int(CW_WIDTH * from_x),
        "from_y": int(CW_HEIGHT * from_y),
        "to_x": int(CW_WIDTH * to_x),
        "to_y": int(CW_HEIGHT * to_y),
    }


FRONT_SLOT_POINTS = [
    _point(0.386, 0.365),
    _point(0.464, 0.365),
    _point(0.536, 0.365),
    _point(0.611, 0.365),
]
BACK_SLOT_POINTS = [
    _point(0.3056, 0.620),
    _point(0.3806, 0.620),
    _point(0.4525, 0.620),
    _point(0.5264, 0.620),
    _point(0.6004, 0.620),
    _point(0.6738, 0.620),
]
HAND_SLOT_POINTS = [
    _point(0.229, 0.844),
    _point(0.297, 0.844),
    _point(0.358, 0.844),
    _point(0.426, 0.844),
    _point(0.488, 0.844),
    _point(0.556, 0.844),
    _point(0.618, 0.844),
    _point(0.684, 0.844),
    _point(0.749, 0.844),
]
SLOT_POINTS_BY_AREA = {
    "front": FRONT_SLOT_POINTS,
    "back": BACK_SLOT_POINTS,
    "hand": HAND_SLOT_POINTS,
}

INFO_DISMISS_POINT = _point(0.5, 0.5)
HAND_EXPAND_DISMISS_POINT = _point(0.35, 0.20)
SELL_SLOT_POINT = _point(0.05, 0.86)
SLOT_NAME_REGION = _region(0.78, 0.175, 0.880, 0.2315)
SLOT_STAR_REGION = _region(0.83, 0.15, 0.87, 0.17)
CANNOT_BE_FIELDED_REGION = _region(0.25, 0.25, 0.75, 0.75)
CRYSTAL_DRAG_DURATION_SECONDS = 0.2
CRYSTAL_DRAG_PATHS = [
    (*_point(0.68, 0.18), *_point(0.82, 0.18)),
    (*_point(0.68, 0.25), *_point(0.82, 0.25)),
    (*_point(0.68, 0.30), *_point(0.83, 0.30)),
    (*_point(0.68, 0.35), *_point(0.84, 0.35)),
    (*_point(0.68, 0.40), *_point(0.83, 0.40)),
]

OPEN_TEMPLATE_ALIAS = "slots.open"
CANNOT_BE_FIELDED_ALIAS = "slots.cannot_be_fielded"
HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS = 5
SLOT_PANEL_SETTLE_SECONDS = 0.2
INITIAL_UI_DISMISS_SETTLE_SECONDS = 1.0
SLOT_MATCH_DIAGNOSTIC_KEYS = {
    "raw_name",
    "match_score",
    "score",
    "match_kind",
    "candidates",
    "empty_score",
    "fee_color",
    "star_boxes",
    "confidence_reason",
}


def _clear_sell_plan(cw_state: dict) -> None:
    cw_state["sell_plan"] = {}


def _mark_slots_stale(session: SessionModel) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    stage.mark_cw_stage_status_stale(session)
    _clear_sell_plan(cw_state)
    return session


def _snapshot_has_any_name(*areas: list[Any]) -> bool:
    return any(_slot_value_name(value) for area in areas for value in area)


def _slot_value_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


CW_ROLE_SLOT_AREAS = ("front", "back", "hand")


def format_cw_agent_slot_reference(area: str, index: int) -> str:
    return _format_agent_slot_reference(f"{area}:{index}")


def parse_cw_slot_reference(value: str, *, allowed_areas: set[str] | None = None) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {value}")
    return _parse_slot_reference(value, allowed_areas=allowed_areas)


def canonical_cw_role_slots(slots: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(slots, dict):
        return []
    seen: set[str] = set()
    roles: list[dict[str, Any]] = []
    for area in CW_ROLE_SLOT_AREAS:
        values = slots.get(area)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            name = _slot_value_name(value)
            if not name or name in seen:
                continue
            seen.add(name)
            roles.append(
                {
                    "area": area,
                    "index": index,
                    "pos": format_cw_agent_slot_reference(area, index),
                    "name": name,
                    "value": value,
                }
            )
    return roles


def _template(scene_alias: str) -> str:
    return str(resolve_scene_asset("cw", scene_alias))


def _star_template_path() -> str:
    return str((Path(__file__).resolve().parent / "assets" / "star.png").resolve())


def _box_center(box: Any) -> tuple[int, int]:
    center = getattr(box, "center", None)
    if isinstance(center, tuple) and len(center) == 2:
        return int(center[0]), int(center[1])

    if isinstance(box, dict):
        return int(box["left"]) + int(box["width"]) // 2, int(box["top"]) + int(box["height"]) // 2

    raise TrailError("SLOTS_UI_INVALID", "slot ui match result missing box coordinates")


def _capture_slot_panel_images(runtime, *, point: tuple[int, int]):
    runtime.click_point(*point)
    sleep(SLOT_PANEL_SETTLE_SECONDS * 2)
    try:
        return {
            "name_image": runtime.capture_image(**SLOT_NAME_REGION, normalize=False),
            "star_image": runtime.capture_image(**SLOT_STAR_REGION, normalize=False),
        }
    finally:
        runtime.click_point(*INFO_DISMISS_POINT)
        sleep(SLOT_PANEL_SETTLE_SECONDS)


def _capture_slot_name_panel_image(runtime, *, point: tuple[int, int]):
    return _capture_slot_panel_images(runtime, point=point)["name_image"]


def _count_slot_stars_in_image(image) -> int | None:
    try:
        import pyscreeze  # type: ignore
    except Exception as exc:
        raise TrailError("IMAGE_BACKEND_UNAVAILABLE", "pyscreeze backend unavailable") from exc

    try:
        boxes = list(pyscreeze.locateAll(_star_template_path(), image, confidence=0.9))
    except Exception as exc:
        image_not_found = getattr(pyscreeze, "ImageNotFoundException", None)
        if image_not_found is not None and isinstance(exc, image_not_found):
            return None
        raise
    if not boxes:
        return None

    centers: list[tuple[int, int]] = []
    for left, top, width, height in boxes:
        center = (int(left) + int(width) // 2, int(top) + int(height) // 2)
        if any(abs(center[0] - prev[0]) < 3 and abs(center[1] - prev[1]) < 3 for prev in centers):
            continue
        centers.append(center)
    return len(centers) or None


def _read_slot_star_counts(captures: list[dict[str, Any]]) -> dict[tuple[str, int], int | None]:
    return {
        (capture["area"], capture["index"]): _count_slot_stars_in_image(capture["star_image"]) if "star_image" in capture else None
        for capture in captures
    }


def _collapse_expanded_hand_card(runtime) -> None:
    template = _template(OPEN_TEMPLATE_ALIAS)
    for _ in range(HAND_EXPAND_COLLAPSE_MAX_ATTEMPTS):
        box = runtime.locate(template)
        if box is None:
            return
        runtime.click_point(*_box_center(box))
        runtime.click_point(*HAND_EXPAND_DISMISS_POINT)
        sleep(SLOT_PANEL_SETTLE_SECONDS)
    raise TrailError("SLOTS_OPEN_STUCK", "手牌展开卡片未关闭，请确认当前在编队界面")


def _slot_points(area: str) -> list[tuple[int, int]]:
    points = SLOT_POINTS_BY_AREA.get(area)
    if points is None:
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {area}")
    return points


def _empty_slots_snapshot() -> tuple[list[Any], list[Any], list[Any]]:
    return [None] * len(FRONT_SLOT_POINTS), [None] * len(BACK_SLOT_POINTS), [None] * len(HAND_SLOT_POINTS)


def _parse_slot_targets(targets: list[str] | None = None) -> dict[str, set[int]] | None:
    if not targets:
        return None

    parsed = {"front": set(), "back": set(), "hand": set()}
    for target in targets:
        area, index = _parse_slot_reference(target, allowed_areas={"front", "back", "hand"})
        parsed[area].add(index)
    return parsed


def _merge_area_snapshot(previous: Any, current: list[Any], *, size: int, targets: set[int] | None) -> list[Any]:
    if targets is None:
        return list(current)

    merged = list(previous) if isinstance(previous, list) else [None] * size
    if len(merged) < size:
        merged.extend([None] * (size - len(merged)))
    else:
        merged = merged[:size]

    for index in targets:
        merged[index] = current[index]
    return merged


def _normalized_slot_equipments(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    raw = value.get("equipments")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _role_value_with_equipments(value: Any, equipments: list[str] | None) -> Any:
    if not equipments:
        if isinstance(value, dict) and "equipments" in value:
            cleaned = deepcopy(value)
            cleaned.pop("equipments", None)
            return cleaned
        return value
    if isinstance(value, dict):
        return {**deepcopy(value), "equipments": list(equipments)}
    name = _slot_value_name(value)
    return {"name": name, "equipments": list(equipments)} if name else value


def _preserve_canonical_slot_equipments(previous: dict[str, Any], merged: dict[str, list[Any]]) -> dict[str, list[Any]]:
    previous_by_name: dict[str, list[str]] = {}
    for role in canonical_cw_role_slots(previous):
        equipments = _normalized_slot_equipments(role.get("value"))
        if equipments:
            previous_by_name[role["name"]] = equipments

    canonical_positions = {(role["area"], role["index"]): role["name"] for role in canonical_cw_role_slots(merged)}
    preserved = {area: list(values) for area, values in merged.items()}
    for area in CW_ROLE_SLOT_AREAS:
        values = preserved.get(area)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            canonical_name = canonical_positions.get((area, index))
            equipments = previous_by_name.get(canonical_name or "") if canonical_name else None
            values[index] = _role_value_with_equipments(value, equipments)
    return preserved


def _merge_area_response_snapshot_with_equipments(
    preserved: list[Any],
    response: list[Any],
    *,
    size: int,
    targets: set[int] | None,
) -> list[Any]:
    output = _merge_area_snapshot(preserved, response, size=size, targets=targets)
    for index, preserved_value in enumerate(preserved[: len(output)]):
        equipments = _normalized_slot_equipments(preserved_value)
        output_value = output[index]
        if equipments:
            base_value = output_value if _slot_value_name(output_value) else preserved_value
            output[index] = _role_value_with_equipments(base_value, equipments)
        elif isinstance(output_value, dict) and "equipments" in output_value:
            output[index] = _role_value_with_equipments(output_value, None)
    return output


def _strip_slot_match_diagnostics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: deepcopy(item) for key, item in value.items() if key not in SLOT_MATCH_DIAGNOSTIC_KEYS}


def _strip_area_match_diagnostics(values: Any) -> Any:
    if not isinstance(values, list):
        return values
    return [_strip_slot_match_diagnostics(value) for value in values]


def _is_unknown_slot_value(value: Any) -> bool:
    return isinstance(value, dict) and value.get("match_kind") == "unknown"


def _unknown_slot_warning(area: str, index: int, value: dict[str, Any], *, preserved_previous: bool = False) -> dict[str, Any]:
    warning: dict[str, Any] = {
        "code": "SLOTS_RECOGNITION_UNCERTAIN",
        "position": {"kind": "slot", "area": area, "index": index},
        "match_kind": "unknown",
        "message": "槽位角色图标识别不确定，请先看截图确认",
    }
    raw_name = value.get("raw_name")
    if isinstance(raw_name, str):
        warning["raw_name"] = raw_name
    score = value.get("score")
    if score is not None:
        warning["score"] = score
    if preserved_previous:
        warning["preserved_previous"] = 1
    return warning


def _target_indexes_for_unknowns(area: str, values: list[Any], parsed_targets: dict[str, set[int]] | None) -> list[int]:
    if parsed_targets is None:
        return list(range(len(values)))
    return [index for index in sorted(parsed_targets[area]) if index < len(values)]


def _collect_unknown_slot_warnings(
    front: list[Any],
    back: list[Any],
    hand: list[Any],
    *,
    parsed_targets: dict[str, set[int]] | None,
    preserved_previous: bool = False,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for area, values in (("front", front), ("back", back), ("hand", hand)):
        for index in _target_indexes_for_unknowns(area, values, parsed_targets):
            value = values[index]
            if _is_unknown_slot_value(value):
                warnings.append(_unknown_slot_warning(area, index, value, preserved_previous=preserved_previous))
    return warnings


def _replace_unknown_targets_with_previous(
    current: list[Any],
    previous: Any,
    *,
    area: str,
    parsed_targets: dict[str, set[int]],
) -> list[Any]:
    output = list(current)
    previous_values = _strip_area_match_diagnostics(previous) if isinstance(previous, list) else []
    for index in _target_indexes_for_unknowns(area, output, parsed_targets):
        if not _is_unknown_slot_value(output[index]):
            continue
        output[index] = deepcopy(previous_values[index]) if index < len(previous_values) else None
    return output


def _unknown_targets_have_previous_values(
    front: list[Any],
    back: list[Any],
    hand: list[Any],
    *,
    previous: dict[str, Any],
    parsed_targets: dict[str, set[int]],
) -> bool:
    for area, values in (("front", front), ("back", back), ("hand", hand)):
        previous_values = _strip_area_match_diagnostics(previous.get(area))
        previous_values = previous_values if isinstance(previous_values, list) else []
        for index in _target_indexes_for_unknowns(area, values, parsed_targets):
            if not _is_unknown_slot_value(values[index]):
                continue
            if index >= len(previous_values) or not _slot_value_name(previous_values[index]):
                return False
    return True


def _score_slot_name_candidates(text: str, *, candidates: list[str]) -> list[tuple[str, float]]:
    scored = [(candidate, SequenceMatcher(a=text, b=candidate).ratio()) for candidate in candidates]
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _collect_role_stage_name_candidates(guide: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    role_stages = guide.get("role_stages")
    if not isinstance(role_stages, list):
        return candidates
    for stage in role_stages:
        if not isinstance(stage, dict):
            continue
        for key in ("front_roles", "back_roles"):
            roles = stage.get(key)
            if not isinstance(roles, list):
                continue
            for role in roles:
                if not isinstance(role, dict):
                    continue
                text = str(role.get("name") or "").strip()
                if text:
                    candidates.append(text)
    return candidates


def _dedupe_slot_name_candidates(candidates: list[str]) -> list[str]:
    return list(dict.fromkeys(candidates))


def _session_slot_name_candidates(cw_state: dict[str, Any]) -> tuple[list[str], list[str]]:
    guide = cw_state.get("guide") if isinstance(cw_state.get("guide"), dict) else {}
    authoritative_candidates = _collect_role_stage_name_candidates(guide)

    slot_candidates: list[str] = []
    slots = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    if slots.get("stale") is False:
        for area in ("front", "back", "hand"):
            for value in slots.get(area, []) or []:
                text = _slot_value_name(value)
                if text:
                    slot_candidates.append(text)
    return _dedupe_slot_name_candidates(authoritative_candidates), _dedupe_slot_name_candidates(slot_candidates)


def _is_short_slot_name_variant(left: str, right: str) -> bool:
    if not left or not right or left == right or len(left) != len(right) or len(left) > 4:
        return False
    same_positions = sum(1 for left_char, right_char in zip(left, right, strict=False) if left_char == right_char)
    return same_positions >= len(left) - 1


def _normalize_slot_name_against_candidates(text: str, *, candidates: list[str]) -> str | None:
    if text in candidates:
        return text

    scored = _score_slot_name_candidates(text, candidates=candidates)
    if not scored:
        return None
    best_candidate, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    if best_score >= 0.72 and (best_score - second_score) >= 0.12:
        return best_candidate

    short_variants = [candidate for candidate, _ in scored if _is_short_slot_name_variant(text, candidate)]
    if len(short_variants) == 1:
        return short_variants[0]
    return None


def _normalize_slot_name(raw: str | None, *, authoritative_candidates: list[str], slot_candidates: list[str]) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = _normalize_slot_name_against_candidates(text, candidates=authoritative_candidates)
    if normalized is not None:
        return normalized
    normalized = _normalize_slot_name_against_candidates(text, candidates=slot_candidates)
    if normalized is not None:
        return normalized
    return text


def _normalize_slot_value(raw: Any, *, authoritative_candidates: list[str], slot_candidates: list[str]) -> Any:
    if isinstance(raw, dict):
        normalized_name = _normalize_slot_name(
            raw.get("name"),
            authoritative_candidates=authoritative_candidates,
            slot_candidates=slot_candidates,
        )
        if normalized_name is None:
            return None
        return {**raw, "name": normalized_name}
    return _normalize_slot_name(raw, authoritative_candidates=authoritative_candidates, slot_candidates=slot_candidates)


def _slot_value_from_catalog_match(value: Any, match) -> dict[str, Any]:
    stable: dict[str, Any] = {"name": match.name}
    value_dict = value if isinstance(value, dict) else {}
    role_id = value_dict.get("role_id") if value_dict.get("role_id") is not None else match.role_id
    if role_id is not None:
        stable["role_id"] = role_id
    if "star" in value_dict:
        stable["star"] = value_dict.get("star")
    if value_dict.get("rarity") is not None:
        stable["rarity"] = value_dict.get("rarity")
    if value_dict.get("cost") is not None:
        stable["cost"] = value_dict.get("cost")
    if match.traits:
        stable["traits"] = list(match.traits)
    return stable


def _slot_response_from_catalog_match(value: Any, match) -> dict[str, Any]:
    response = _slot_value_from_catalog_match(value, match)
    if match.match_kind != "exact":
        if match.raw_name is not None:
            response["raw_name"] = match.raw_name
        response["match_score"] = match.match_score
        response["match_kind"] = match.match_kind
    return response


def _resolve_catalog_slot_value(
    value: Any,
    *,
    catalog: CwCatalog | None,
    position: dict[str, Any] | None,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    name = _slot_value_name(value)
    if catalog is None or not catalog.roles or not name:
        return None, None, []

    match = resolve_cw_role_name(name, catalog, position=position)
    if match is None:
        return None, None, []

    warnings = [match.warning] if isinstance(match.warning, dict) else []
    return _slot_value_from_catalog_match(value, match), _slot_response_from_catalog_match(value, match), warnings


def _icon_match_diagnostics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("match_kind") != "low_confidence":
        return {}
    keys = ("raw_name", "score", "match_kind", "candidates", "empty_score", "fee_color", "star_boxes", "confidence_reason")
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _response_with_icon_diagnostics(response: Any, value: Any) -> Any:
    diagnostics = _icon_match_diagnostics(value)
    if not diagnostics or not isinstance(response, dict):
        return response
    return {**response, **diagnostics}


def _icon_low_confidence_warning(value: Any, *, area: str, index: int, response: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("match_kind") != "low_confidence":
        return None
    warning: dict[str, Any] = {
        "code": "CW_ROLE_MATCH_LOW_CONFIDENCE",
        "position": {"kind": "slot", "area": area, "index": index},
        "query": str(value.get("raw_name") or value.get("name") or ""),
        "resolved": _slot_value_name(response),
        "message": "角色名未精确命中，请先看截图确认",
    }
    score = value.get("score")
    if score is not None:
        warning["score"] = score
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        warning["candidates"] = deepcopy(candidates)
    return warning


def _canonicalize_slot_value(
    value: Any,
    *,
    catalog: CwCatalog | None,
    area: str,
    index: int,
    authoritative_candidates: list[str],
    slot_candidates: list[str],
) -> tuple[Any, Any, list[dict[str, Any]]]:
    stable, response, warnings = _resolve_catalog_slot_value(
        value,
        catalog=catalog,
        position={"kind": "slot", "area": area, "index": index},
    )
    if stable is not None or response is not None:
        response = _response_with_icon_diagnostics(response, value)
        icon_warning = _icon_low_confidence_warning(value, area=area, index=index, response=response)
        if icon_warning is not None:
            warnings = [*warnings, icon_warning]
        return stable, response, warnings

    stable = _strip_slot_match_diagnostics(
        _normalize_slot_value(value, authoritative_candidates=authoritative_candidates, slot_candidates=slot_candidates)
    )
    response = _response_with_icon_diagnostics(deepcopy(stable), value)
    icon_warning = _icon_low_confidence_warning(value, area=area, index=index, response=response)
    return stable, response, [icon_warning] if icon_warning is not None else []


def _canonicalize_area_snapshot(
    values: list[Any],
    *,
    area: str,
    catalog: CwCatalog | None,
    authoritative_candidates: list[str],
    slot_candidates: list[str],
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    stable_values: list[Any] = []
    response_values: list[Any] = []
    warnings: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        stable, response, slot_warnings = _canonicalize_slot_value(
            value,
            catalog=catalog,
            area=area,
            index=index,
            authoritative_candidates=authoritative_candidates,
            slot_candidates=slot_candidates,
        )
        stable_values.append(stable)
        response_values.append(response)
        warnings.extend(slot_warnings)
    return stable_values, response_values, warnings


def _summarize_field_trait_status(front: list[Any], back: list[Any], *, guide_config: dict[str, Any] | None) -> list[dict[str, Any]]:
    return summarize_cw_field_traits(front=front, back=back, catalog=build_cw_catalog(guide_config))


def _next_slots_stale(previous: dict[str, Any], *, parsed_targets: dict[str, set[int]] | None) -> bool:
    if parsed_targets is None:
        return False
    return previous.get("stale", True) is not False


def _slots_read_empty_snapshot(
    previous: dict[str, Any],
    *,
    parsed_targets: dict[str, set[int]] | None,
    front: list[Any],
    back: list[Any],
    hand: list[Any],
    merged_front: list[Any],
    merged_back: list[Any],
    merged_hand: list[Any],
) -> bool:
    if parsed_targets is None:
        return not _snapshot_has_any_name(front, back, hand)
    if previous.get("stale", True) is False:
        return not _snapshot_has_any_name(merged_front, merged_back, merged_hand)
    return not _snapshot_has_any_name(front, back, hand)


def _slot_role_count(front: list[Any], back: list[Any], hand: list[Any]) -> dict[str, int]:
    front_count = sum(1 for item in front if item)
    back_count = sum(1 for item in back if item)
    hand_count = sum(1 for item in hand if item)
    return {
        "front": front_count,
        "back": back_count,
        "hand": hand_count,
        "field": front_count + back_count,
        "total": front_count + back_count + hand_count,
    }


def _format_agent_slot_reference(value: str) -> str:
    area, separator, raw_index = value.partition(":")
    if separator == ":" and area in SLOT_POINTS_BY_AREA and raw_index.isdecimal():
        return f"{area}:{int(raw_index) + 1}"
    return value


def _format_agent_hand_slot(slot: int) -> int:
    return slot + 1


def _parse_slot_reference(value: str, *, allowed_areas: set[str] | None = None) -> tuple[str, int]:
    area, separator, raw_index = value.partition(":")
    if separator != ":" or not raw_index.isdecimal():
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {_format_agent_slot_reference(value)}")
    if allowed_areas is not None and area not in allowed_areas:
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {_format_agent_slot_reference(value)}")

    index = int(raw_index)
    points = _slot_points(area)
    if index < 0 or index >= len(points):
        raise TrailError("SLOTS_POSITION_INVALID", f"invalid slot position: {_format_agent_slot_reference(value)}")
    return area, index


def _ensure_fieldable_target(runtime, *, target: str) -> None:
    box = runtime.locate(_template(CANNOT_BE_FIELDED_ALIAS), **CANNOT_BE_FIELDED_REGION)
    if box is None:
        return
    runtime.click_point(*INFO_DISMISS_POINT)
    raise TrailError("SLOTS_CANNOT_BE_FIELDED", f"target slot cannot field character: {_format_agent_slot_reference(target)}")


def dismiss_cw_slots_overlay(runtime) -> None:
    runtime.click_point(*INFO_DISMISS_POINT)
    sleep(INITIAL_UI_DISMISS_SETTLE_SECONDS)


def _runtime_slots_image(runtime) -> Image.Image:
    capture_image = getattr(runtime, "capture_image", None)
    if not callable(capture_image):
        raise TrailError("SLOTS_SCREENSHOT_INVALID", "slots icon reader requires PIL screenshot")
    try:
        image = capture_image(normalize=True)
    except TypeError:
        image = capture_image()

    if isinstance(image, bytes | bytearray):
        try:
            with Image.open(BytesIO(image)) as opened:
                image = opened.convert("RGBA")
        except Exception as exc:
            raise TrailError("SLOTS_SCREENSHOT_INVALID", "slots icon reader requires PIL screenshot") from exc

    if not isinstance(image, Image.Image):
        raise TrailError("SLOTS_SCREENSHOT_INVALID", "slots icon reader requires PIL screenshot")
    if image.size != CANONICAL_SCREENSHOT_SIZE:
        raise TrailError("SLOTS_LAYOUT_MISMATCH", "slots icon reader requires canonical 1920x1080 screenshot")
    return image.convert("RGBA")


def _save_reused_slots_screenshot(runtime, image: Image.Image, *, request_id: str | None = None) -> str | None:
    if request_id is None:
        return None
    save = getattr(runtime, "save_capture_image_to_workspace", None)
    if not callable(save):
        return None
    try:
        path = save(image, request_id=request_id)
    except Exception:
        return None
    return str(path) if path is not None else None


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    if is_dataclass(candidate):
        payload = asdict(candidate)
    elif isinstance(candidate, dict):
        payload = dict(candidate)
    else:
        payload = {
            key: getattr(candidate, key)
            for key in (
                "name",
                "role_id",
                "score",
                "rarity",
                "cost",
                "normalized_name",
                "front_back_type",
                "trait_ids",
                "diagnostics",
            )
            if hasattr(candidate, key)
        }
    return deepcopy(payload)


def _role_recognition_candidates(result: Any) -> list[dict[str, Any]]:
    diagnostics = getattr(result, "diagnostics", None)
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("candidates"), list):
        return deepcopy(diagnostics["candidates"])
    candidates = getattr(result, "candidates", None)
    if not isinstance(candidates, list):
        return []
    return [_candidate_payload(candidate) for candidate in candidates]


def _slot_value_from_role_recognition_result(result: Any) -> Any:
    diagnostics = getattr(result, "diagnostics", None)
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    match_kind = str(getattr(result, "match_kind", "") or "")
    common = {
        "match_kind": match_kind,
        "score": getattr(result, "score", None),
        "raw_name": str(getattr(result, "raw_name", getattr(result, "name", "")) or ""),
        "candidates": _role_recognition_candidates(result),
        "empty_score": diagnostics.get("empty_score"),
        "fee_color": getattr(result, "fee_color", None),
        "star_boxes": deepcopy(getattr(result, "star_boxes", [])),
        "confidence_reason": getattr(result, "confidence_reason", None),
    }
    if bool(getattr(result, "empty", False)):
        return None
    name = getattr(result, "name", None)
    if match_kind == "unknown" or not name:
        return common
    value: dict[str, Any] = {
        "name": str(name),
        "role_id": getattr(result, "role_id", None),
        "star": getattr(result, "star_count", None),
        **common,
    }
    rarity = getattr(result, "rarity", None)
    cost = getattr(result, "cost", None)
    if rarity is not None:
        value["rarity"] = rarity
    if cost is not None:
        value["cost"] = cost
    return value


def build_cw_slot_icon_reader(
    runtime,
    recognizer,
    targets: list[str] | None = None,
    *,
    request_id: str | None = None,
    dismiss_initial_overlay: bool = True,
) -> SlotsSnapshotReader:
    parsed_targets = _parse_slot_targets(targets)
    status_reader = build_cw_status_reader(runtime)

    def reader() -> CwSlotsReadResult:
        _collapse_expanded_hand_card(runtime)
        if dismiss_initial_overlay:
            dismiss_cw_slots_overlay(runtime)
        status_result = status_reader()
        image = _runtime_slots_image(runtime)
        screenshot = _save_reused_slots_screenshot(runtime, image, request_id=request_id)
        front, back, hand = _empty_slots_snapshot()
        targets_by_area = parsed_targets or {
            "front": set(range(len(FRONT_SLOT_POINTS))),
            "back": set(range(len(BACK_SLOT_POINTS))),
            "hand": set(range(len(HAND_SLOT_POINTS))),
        }
        for slot_spec in iter_slot_specs():
            if slot_spec.index not in targets_by_area[slot_spec.area]:
                continue
            crop = warp_slot_crop(image, slot_spec)
            value = _slot_value_from_role_recognition_result(recognizer.recognize_crop(crop, slot_spec.area))
            if slot_spec.area == "front":
                front[slot_spec.index] = value
            elif slot_spec.area == "back":
                back[slot_spec.index] = value
            else:
                hand[slot_spec.index] = value
        return CwSlotsReadResult(
            front=front,
            back=back,
            hand=hand,
            stage_status=status_result.stage_status,
            stage=status_result.stage,
            screenshot=screenshot,
        )

    return reader


def build_cw_slots_reader(
    runtime,
    targets: list[str] | None = None,
    *,
    dismiss_initial_overlay: bool = True,
) -> SlotsSnapshotReader:
    status_reader = build_cw_status_reader(runtime)
    roles_reader = build_cw_slot_roles_reader(runtime, targets=targets)

    def reader() -> CwSlotsReadResult:
        _collapse_expanded_hand_card(runtime)
        if dismiss_initial_overlay:
            dismiss_cw_slots_overlay(runtime)
        status_result = status_reader()
        front, back, hand = roles_reader()
        return CwSlotsReadResult(
            front=front,
            back=back,
            hand=hand,
            stage_status=status_result.stage_status,
            stage=status_result.stage,
        )

    return reader


def build_cw_status_reader(runtime) -> Callable[[], CwStatusReadResult]:
    stage_detector = stage.build_cw_stage_detector(runtime)

    def reader() -> CwStatusReadResult:
        detected_stage = stage_detector()
        batch_targets = [
            BatchOcrTarget(("stage_status", "level"), runtime.capture_image(**stage.CW_STATUS_LEVEL_REGION, normalize=False)),
            BatchOcrTarget(("stage_status", "exp"), runtime.capture_image(**stage.CW_STATUS_EXP_REGION, normalize=False)),
            BatchOcrTarget(("stage_status", "team_size"), runtime.capture_image(**stage.CW_STATUS_TEAM_SIZE_REGION, normalize=False)),
        ]
        batch_result = run_batch_ocr(runtime, batch_targets, trace_prefix="cw_slots_status_batch_ocr")
        return CwStatusReadResult(
            stage=detected_stage,
            stage_status=stage.parse_cw_stage_status(batch_result.by_key),
        )

    return reader


def build_cw_slot_roles_reader(runtime, targets: list[str] | None = None) -> Callable[[], tuple[list[Any], list[Any], list[Any]]]:
    parsed_targets = _parse_slot_targets(targets)

    def reader() -> tuple[list[Any], list[Any], list[Any]]:
        front, back, hand = _empty_slots_snapshot()
        targets_by_area = parsed_targets or {
            "front": set(range(len(FRONT_SLOT_POINTS))),
            "back": set(range(len(BACK_SLOT_POINTS))),
            "hand": set(range(len(HAND_SLOT_POINTS))),
        }

        captures: list[dict[str, Any]] = []
        batch_targets = []
        for area in ("front", "back", "hand"):
            points = SLOT_POINTS_BY_AREA[area]
            for index in sorted(targets_by_area[area]):
                capture = {
                    "area": area,
                    "index": index,
                    **_capture_slot_panel_images(runtime, point=points[index]),
                }
                captures.append(capture)
                batch_targets.append(BatchOcrTarget(("slot", area, index), capture["name_image"]))

        batch_result = run_batch_ocr(runtime, batch_targets, trace_prefix="cw_slots_batch_ocr")
        names_by_slot = {
            (capture["area"], capture["index"]): (batch_result.by_key[("slot", capture["area"], capture["index"])].text)
            for capture in captures
        }
        stars_by_slot = _read_slot_star_counts(captures)
        for (area, index), value in names_by_slot.items():
            slot_value = None if value is None else {"name": value, "star": stars_by_slot.get((area, index))}
            if area == "front":
                front[index] = slot_value
            elif area == "back":
                back[index] = slot_value
            else:
                hand[index] = slot_value
        return front, back, hand

    return reader


def build_cw_slot_swapper(runtime) -> SlotMover:
    def swapper(source: str, target: str) -> None:
        source_area, source_index = _parse_slot_reference(source)
        target_area, target_index = _parse_slot_reference(target)
        runtime.drag_to(*_slot_points(source_area)[source_index], *_slot_points(target_area)[target_index])
        if target_area != "hand":
            _ensure_fieldable_target(runtime, target=target)

    return swapper


def build_cw_hand_seller(runtime) -> HandSeller:
    def seller(slot: int) -> None:
        if slot < 0 or slot >= len(HAND_SLOT_POINTS):
            raise TrailError("SLOTS_POSITION_INVALID", f"invalid hand slot: {_format_agent_hand_slot(slot)}")
        runtime.drag_to(*HAND_SLOT_POINTS[slot], *SELL_SLOT_POINT)

    return seller


def build_cw_crystal_collector(runtime) -> CrystalCollector:
    def collector() -> None:
        for drag in CRYSTAL_DRAG_PATHS:
            runtime.drag_to(*drag, duration=CRYSTAL_DRAG_DURATION_SECONDS)

    return collector


def read_cw_slots(
    session: SessionModel,
    *,
    reader: SlotsSnapshotReader,
    targets: list[str] | None = None,
    guide_config: dict[str, Any] | None = None,
) -> CwSlotsReadApplied:
    try:
        result = reader()
    except TrailError as exc:
        if exc.code == "STAGE_AMBIGUOUS":
            stage._invalidate_cw_stage(session, code=exc.code, message=str(exc))
            session.last_stage = None
            exc.known_failure_after_save = True
        raise
    if isinstance(result, CwSlotsReadResult):
        front, back, hand = result.front, result.back, result.hand
        stage_status = result.stage_status
        detected_stage = result.stage
        screenshot = result.screenshot
    else:
        front, back, hand = result
        stage_status = None
        detected_stage = None
        screenshot = None
    cw_state = ensure_cw_state(session)
    previous = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    parsed_targets = _parse_slot_targets(targets)
    preserved_unknowns = False
    unknown_warnings = _collect_unknown_slot_warnings(front, back, hand, parsed_targets=parsed_targets)
    if unknown_warnings:
        if parsed_targets is None or previous.get("stale") is not False:
            raise CwSlotsRecognitionUncertainError(
                "SLOTS_RECOGNITION_UNCERTAIN",
                "槽位角色图标识别不确定，请先看截图确认",
                screenshot=screenshot,
                warnings=unknown_warnings,
            )
        if not _unknown_targets_have_previous_values(
            front,
            back,
            hand,
            previous=previous,
            parsed_targets=parsed_targets,
        ):
            raise CwSlotsRecognitionUncertainError(
                "SLOTS_RECOGNITION_UNCERTAIN",
                "槽位角色图标识别不确定，请先看截图确认",
                screenshot=screenshot,
                warnings=unknown_warnings,
            )
        preserved_unknowns = True
        unknown_warnings = _collect_unknown_slot_warnings(
            front,
            back,
            hand,
            parsed_targets=parsed_targets,
            preserved_previous=True,
        )
        front = _replace_unknown_targets_with_previous(front, previous.get("front"), area="front", parsed_targets=parsed_targets)
        back = _replace_unknown_targets_with_previous(back, previous.get("back"), area="back", parsed_targets=parsed_targets)
        hand = _replace_unknown_targets_with_previous(hand, previous.get("hand"), area="hand", parsed_targets=parsed_targets)
    catalog = build_cw_catalog(guide_config) if isinstance(guide_config, dict) else None
    authoritative_candidates, slot_candidates = _session_slot_name_candidates(cw_state)
    front, response_front, front_warnings = _canonicalize_area_snapshot(
        front,
        area="front",
        catalog=catalog,
        authoritative_candidates=authoritative_candidates,
        slot_candidates=slot_candidates,
    )
    back, response_back, back_warnings = _canonicalize_area_snapshot(
        back,
        area="back",
        catalog=catalog,
        authoritative_candidates=authoritative_candidates,
        slot_candidates=slot_candidates,
    )
    hand, response_hand, hand_warnings = _canonicalize_area_snapshot(
        hand,
        area="hand",
        catalog=catalog,
        authoritative_candidates=authoritative_candidates,
        slot_candidates=slot_candidates,
    )
    match_warnings = [*unknown_warnings, *front_warnings, *back_warnings, *hand_warnings]
    previous_front = _strip_area_match_diagnostics(previous.get("front"))
    previous_back = _strip_area_match_diagnostics(previous.get("back"))
    previous_hand = _strip_area_match_diagnostics(previous.get("hand"))
    merged_front = _merge_area_snapshot(previous_front, front, size=len(FRONT_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["front"])
    merged_back = _merge_area_snapshot(previous_back, back, size=len(BACK_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["back"])
    merged_hand = _merge_area_snapshot(previous_hand, hand, size=len(HAND_SLOT_POINTS), targets=None if parsed_targets is None else parsed_targets["hand"])
    preserved = _preserve_canonical_slot_equipments(
        previous,
        {"front": merged_front, "back": merged_back, "hand": merged_hand},
    )
    merged_front = preserved["front"]
    merged_back = preserved["back"]
    merged_hand = preserved["hand"]
    if _slots_read_empty_snapshot(
        previous,
        parsed_targets=parsed_targets,
        front=front,
        back=back,
        hand=hand,
        merged_front=merged_front,
        merged_back=merged_back,
        merged_hand=merged_hand,
    ) and not preserved_unknowns:
        raise TrailError("SLOTS_READ_EMPTY", "未读取到任何货币战争槽位角色，请确认当前在编队界面")
    fact_front, fact_back, fact_hand = merged_front, merged_back, merged_hand
    if parsed_targets is not None and previous.get("stale", True) is not False:
        fact_front, fact_back, fact_hand = front, back, hand
    output_front = _merge_area_response_snapshot_with_equipments(
        merged_front,
        response_front,
        size=len(FRONT_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["front"],
    )
    output_back = _merge_area_response_snapshot_with_equipments(
        merged_back,
        response_back,
        size=len(BACK_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["back"],
    )
    output_hand = _merge_area_response_snapshot_with_equipments(
        merged_hand,
        response_hand,
        size=len(HAND_SLOT_POINTS),
        targets=None if parsed_targets is None else parsed_targets["hand"],
    )
    cw_state["slots"] = {
        "front": deepcopy(merged_front),
        "back": deepcopy(merged_back),
        "hand": deepcopy(merged_hand),
        "stale": _next_slots_stale(previous, parsed_targets=parsed_targets),
    }
    response_snapshot = {
        "front": deepcopy(output_front),
        "back": deepcopy(output_back),
        "hand": deepcopy(output_hand),
        "stale": cw_state["slots"]["stale"],
    }
    if screenshot is not None:
        response_snapshot["_screenshot"] = screenshot
    if catalog is not None:
        trait_summary = summarize_cw_field_traits(front=fact_front, back=fact_back, catalog=catalog)
        if trait_summary:
            cw_state["slots"]["trait_summary"] = deepcopy(trait_summary)
            response_snapshot["trait_summary"] = deepcopy(trait_summary)
    if match_warnings:
        response_snapshot["warnings"] = deepcopy(match_warnings)
    if stage_status is not None:
        status_with_role_count = {**stage_status, "role_count": _slot_role_count(fact_front, fact_back, fact_hand)}
        if detected_stage is not None:
            stage._replace_stage_fields(session, value=detected_stage, stale=False, status=status_with_role_count)
            session.last_stage = {"scene": "cw", "value": detected_stage}
        else:
            stage._replace_stage_fields(session, status=status_with_role_count)
        cw_stage = cw_state.get("stage") if isinstance(cw_state.get("stage"), dict) else {}
        cw_state["slots"]["stage"] = detected_stage
        cw_state["slots"]["stage_stale"] = False if detected_stage is not None else bool(cw_stage.get("stale", True))
        cw_state["slots"]["stage_status"] = deepcopy(status_with_role_count)
        cw_state["slots"]["stage_status_stale"] = bool(status_with_role_count.get("stale", True))
        if detected_stage is not None:
            response_snapshot["stage"] = detected_stage
            response_snapshot["stage_stale"] = False
        response_snapshot["stage_status"] = deepcopy(status_with_role_count)
        response_snapshot["stage_status_stale"] = bool(status_with_role_count.get("stale", True))
    _clear_sell_plan(cw_state)
    return CwSlotsReadApplied(session=session, response_snapshot=response_snapshot)


def _normalize_place_actions(actions: list[dict[str, str]]) -> list[tuple[str, str]]:
    if not actions:
        raise TrailError("CW_OPTION_INVALID", "cw slots place requires at least one --action")

    normalized: list[tuple[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            raise TrailError("CW_OPTION_INVALID", f"cw slots place action invalid: {action!r}")
        source = action.get("source")
        target = action.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise TrailError("CW_OPTION_INVALID", f"cw slots place action invalid: {action!r}")
        _parse_slot_reference(source)
        _parse_slot_reference(target)
        normalized.append((source, target))
    return normalized


def _normalize_sell_slots(slots: list[int]) -> list[int]:
    if not slots:
        raise TrailError("CW_OPTION_INVALID", "cw hand sell requires at least one --slot")

    normalized: list[int] = []
    for slot in slots:
        if not isinstance(slot, int) or isinstance(slot, bool):
            raise TrailError("CW_OPTION_INVALID", f"cw hand sell slot invalid: {slot!r}")
        if slot < 0 or slot >= len(HAND_SLOT_POINTS):
            raise TrailError("SLOTS_POSITION_INVALID", f"invalid hand slot: {_format_agent_hand_slot(slot)}")
        normalized.append(slot)
    return normalized


def swap_cw_slots(session: SessionModel, *, source: str, target: str, swapper: SlotMover | None = None) -> SessionModel:
    try:
        if swapper is not None:
            swapper(source, target)
    except TrailError as error:
        _mark_slots_stale(session)
        error.known_failure_after_save = True
        raise
    del source, target
    return _mark_slots_stale(session)


def place_cw_slots(session: SessionModel, *, actions: list[dict[str, str]], placer: SlotMover | None = None) -> SessionModel:
    normalized = _normalize_place_actions(actions)
    for source, target in normalized:
        try:
            if placer is not None:
                placer(source, target)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)


def place_one_cw_slot(session: SessionModel, *, source: str, target: str, placer: SlotMover | None = None) -> SessionModel:
    try:
        if placer is not None:
            placer(source, target)
    except TrailError as error:
        _mark_slots_stale(session)
        error.known_failure_after_save = True
        raise
    del source, target
    return _mark_slots_stale(session)


def collect_cw_crystals(session: SessionModel, *, collector: CrystalCollector | None = None) -> SessionModel:
    if collector is not None:
        collector()
    cw_state = ensure_cw_state(session)
    metrics = cw_state.setdefault("metrics", {})
    metrics["last_crystal_collection"] = "done"
    return session


SELL_PLAN_CATEGORY_PRIORITY = {
    "非攻略": 10,
    "前期": 20,
    "中期": 30,
    "后期超买": 40,
    "后期": 50,
}


def _slot_star(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    star = value.get("star")
    parsed: int | None = None
    if isinstance(star, bool):
        return None
    if isinstance(star, int):
        parsed = star
    if isinstance(star, str) and star.strip().isdigit():
        parsed = int(star.strip())
    if parsed in {1, 2, 3}:
        return parsed
    return None


def _iter_stage_roles(stage: dict[str, Any]) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for key in ("front_roles", "back_roles"):
        values = stage.get(key)
        if not isinstance(values, list):
            continue
        roles.extend(dict(item) for item in values if isinstance(item, dict) and _slot_value_name(item))
    return roles


def _parse_team_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        left, separator, right = text.partition("/")
        if separator:
            left_text = left.strip()
            right_text = right.strip()
            if left_text.isdigit() and right_text.isdigit():
                current_size = int(left_text)
                max_size = int(right_text)
                if current_size > 0 and max_size > 0:
                    return max_size
            return None
        if text.isdigit():
            parsed = int(text)
            return parsed if parsed > 0 else None
    return None


def _add_sell_plan_todo(todos: list[str], value: str) -> None:
    if value not in todos:
        todos.append(value)


def _is_final_role_stage(stage: dict[str, Any]) -> bool:
    return str(stage.get("stage") or "").strip().casefold() == "final"


def _sell_plan_stage_reference(guide: dict[str, Any], *, todos: list[str]) -> tuple[dict[str, str], dict[str, int | None]]:
    role_stages = guide.get("role_stages") if isinstance(guide, dict) else None
    if not isinstance(role_stages, list):
        _add_sell_plan_todo(todos, "stage_granularity")
        return {}, {}
    stages = [stage for stage in role_stages if isinstance(stage, dict)]
    if not stages:
        _add_sell_plan_todo(todos, "stage_granularity")
        return {}, {}

    explicit_final_indexes = [index for index, stage in enumerate(stages) if _is_final_role_stage(stage)]
    if explicit_final_indexes:
        final_indexes = explicit_final_indexes
        final_index = explicit_final_indexes[-1]
    else:
        final_index = len(stages) - 1
        final_indexes = [final_index]
        _add_sell_plan_todo(todos, "missing_final")
    if len(stages) < 3:
        _add_sell_plan_todo(todos, "stage_granularity")

    non_final_before = [
        index for index, stage in enumerate(stages[:final_index]) if not _is_final_role_stage(stage)
    ]
    first_non_final = non_final_before[0] if non_final_before else None
    role_categories: dict[str, str] = {}
    for index, stage in enumerate(stages):
        if index in final_indexes:
            category = "后期"
        elif first_non_final is not None and index == first_non_final:
            category = "前期"
        elif index < final_index:
            category = "中期"
        else:
            category = "后期"
        for role in _iter_stage_roles(stage):
            name = _slot_value_name(role)
            current = role_categories.get(name)
            if current is None or SELL_PLAN_CATEGORY_PRIORITY[category] < SELL_PLAN_CATEGORY_PRIORITY[current]:
                role_categories[name] = category

    final_targets: dict[str, int | None] = {}
    for index in final_indexes:
        for role in _iter_stage_roles(stages[index]):
            name = _slot_value_name(role)
            star = _slot_star(role)
            if name not in final_targets or star is not None:
                final_targets[name] = star
    return role_categories, final_targets


def _parse_sell_plan_stage(cw_state: dict[str, Any], *, todos: list[str]) -> tuple[int | None, bool]:
    stage = cw_state.get("stage") if isinstance(cw_state.get("stage"), dict) else None
    if stage is None or stage.get("stale") is not False:
        _add_sell_plan_todo(todos, "stage")
        return None, False
    value = str(stage.get("value") or "").strip()
    layer, separator, section = value.partition("-")
    if separator != "-" or layer not in {"1", "2", "3"} or not section:
        _add_sell_plan_todo(todos, "stage")
        return None, False
    boss_preview = stage.get("boss_preview")
    if not isinstance(boss_preview, bool):
        _add_sell_plan_todo(todos, "boss_preview")
        boss_preview = False
    return int(layer), boss_preview


def _sell_plan_recommendation(category: str, *, layer: int | None, boss_preview: bool) -> str:
    if layer is None:
        return "不推荐"
    if category == "后期":
        return "不推荐"
    if layer == 1:
        return "可以" if category == "非攻略" else "不推荐"
    if layer == 2:
        if boss_preview:
            return "推荐" if category in {"非攻略", "前期"} else "不推荐"
        if category == "非攻略":
            return "推荐"
        if category == "前期":
            return "可以"
        return "不推荐"
    if layer == 3:
        if boss_preview:
            if category in {"非攻略", "前期", "中期"}:
                return "推荐"
            if category == "后期超买":
                return "可以"
            return "不推荐"
        if category in {"非攻略", "前期"}:
            return "推荐"
        if category == "中期":
            return "可以"
    return "不推荐"


def _field_star_status(slots: dict[str, Any], *, name: str) -> tuple[bool, int | None]:
    field_present = False
    stars: list[int] = []
    for area in ("front", "back"):
        values = slots.get(area)
        if not isinstance(values, list):
            continue
        for value in values:
            if _slot_value_name(value) != name:
                continue
            field_present = True
            star = _slot_star(value)
            if star is not None:
                stars.append(star)
    return field_present, max(stars) if stars else None


def _non_empty_slot_count(values: Any) -> int:
    if not isinstance(values, list):
        return 0
    return sum(1 for value in values if _slot_value_name(value))


def _sell_plan_reason(category: str, *, protected: bool, layer: int | None, under_team_size: bool) -> str:
    if under_team_size:
        return "人口不足，保守不推荐"
    if protected:
        return "Final目标未达成，保护"
    if layer is None:
        return "缺少当前阶段，仅提供参考"
    if category == "后期超买":
        return "Final目标已达成，手牌为超买参考"
    return f"{category}角色"


def plan_cw_hand_sell(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    slots = cw_state.get("slots")
    if not isinstance(slots, dict) or slots.get("stale") is not False:
        raise TrailError("SLOTS_STALE", "槽位快照已失效，请先执行 trail cw slots read")
    hand = slots.get("hand", [])
    if not isinstance(hand, list):
        hand = []

    todos: list[str] = []
    guide = cw_state.get("guide") if isinstance(cw_state.get("guide"), dict) else {}
    role_categories, final_targets = _sell_plan_stage_reference(guide, todos=todos)
    layer, boss_preview = _parse_sell_plan_stage(cw_state, todos=todos)

    shop = cw_state.get("shop") if isinstance(cw_state.get("shop"), dict) else {}
    team_size = _parse_team_size(shop.get("team_size")) if shop.get("stale") is False else None
    if team_size is None:
        _add_sell_plan_todo(todos, "team_size")
    field_count = _non_empty_slot_count(slots.get("front")) + _non_empty_slot_count(slots.get("back"))
    hand_count = _non_empty_slot_count(hand)
    under_team_size = team_size is not None and field_count + hand_count < team_size

    items: list[dict[str, Any]] = []
    for index, value in enumerate(hand):
        name = _slot_value_name(value)
        if not name:
            continue
        star = _slot_star(value)
        target_star = final_targets.get(name)
        field_present, current_star = _field_star_status(slots, name=name)
        protected = False
        category = role_categories.get(name, "非攻略")

        if name in final_targets:
            if target_star is None:
                protected = True
                category = "后期"
                _add_sell_plan_todo(todos, "star")
            elif not field_present:
                protected = True
                category = "后期"
            elif current_star is None:
                protected = True
                category = "后期"
                _add_sell_plan_todo(todos, "star")
            elif current_star < target_star:
                protected = True
                category = "后期"
            else:
                category = "后期超买"

        recommendation = _sell_plan_recommendation(category, layer=layer, boss_preview=boss_preview)
        if protected or under_team_size:
            recommendation = "不推荐"
        priority = SELL_PLAN_CATEGORY_PRIORITY[category]
        items.append(
            {
                "slot": index,
                "name": name,
                "star": star,
                "target_star": target_star,
                "current_star": current_star,
                "category": category,
                "recommendation": recommendation,
                "priority": priority,
                "protected": protected,
                "reason": _sell_plan_reason(category, protected=protected, layer=layer, under_team_size=under_team_size),
            }
        )

    items.sort(key=lambda item: (int(item["priority"]), int(item["slot"])))
    cw_state["sell_plan"] = {
        "reference_only": True,
        "candidates": [],
        "items": deepcopy(items),
        "todos": list(todos),
    }
    return deepcopy(cw_state["sell_plan"])


def sell_cw_hand_slots(session: SessionModel, *, slots: list[int], seller: HandSeller | None = None) -> SessionModel:
    normalized = _normalize_sell_slots(slots)
    for slot in normalized:
        try:
            if seller is not None:
                seller(slot)
        except TrailError as error:
            _mark_slots_stale(session)
            error.known_failure_after_save = True
            raise
    return _mark_slots_stale(session)


def sell_one_cw_hand(session: SessionModel, *, slot: int, seller: HandSeller | None = None) -> SessionModel:
    try:
        if seller is not None:
            seller(slot)
    except TrailError as error:
        _mark_slots_stale(session)
        error.known_failure_after_save = True
        raise
    del slot
    return _mark_slots_stale(session)
