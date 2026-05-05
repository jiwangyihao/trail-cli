from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any


LOOKUP_WHITESPACE_PATTERN = re.compile(r"\s+")
LOW_CONFIDENCE_THRESHOLD = 0.72
AMBIGUOUS_DELTA_THRESHOLD = 0.12


@dataclass(frozen=True)
class CwCatalog:
    roles: list[dict[str, Any]]
    traits: list[dict[str, Any]]
    trait_name_by_id: dict[str, str]
    trait_tiers_by_name: dict[str, list[int]]
    role_traits_by_name: dict[str, list[str]]


@dataclass(frozen=True)
class CwRoleMatch:
    name: str
    role_id: str | None
    traits: list[str]
    match_kind: str
    match_score: float
    raw_name: str | None = None
    warning: dict[str, Any] | None = None


def normalize_cw_lookup_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return LOOKUP_WHITESPACE_PATTERN.sub(" ", text.strip().lower()).strip()


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal():
            number = int(stripped)
            return number if number > 0 else None
    return None


def _is_present(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _iter_mappings(value: object) -> Iterable[Mapping[str, Any]]:
    for item in _as_list(value):
        if isinstance(item, Mapping):
            yield item


def _dedupe_items(items: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item in result:
            continue
        result.append(item)
    return result


def _layer_completeness(layer: Mapping[str, Any]) -> int:
    return sum(1 for value in layer.values() if _is_present(value))


def _clean_layer(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    layer = _coerce_positive_int(raw.get("layer"))
    if layer is None:
        return None
    cleaned: dict[str, Any] = {"layer": layer}
    if raw.get("quality") is not None:
        cleaned["quality"] = raw.get("quality")
    if raw.get("trait_desc"):
        cleaned["trait_desc"] = raw.get("trait_desc")
    return cleaned


def _merge_layers(left: object, right: object) -> list[dict[str, Any]]:
    by_layer: dict[int, dict[str, Any]] = {}
    for raw in [*_iter_mappings(left), *_iter_mappings(right)]:
        layer = _coerce_positive_int(raw.get("layer"))
        if layer is None:
            continue
        cleaned = {"layer": layer}
        for key in ("quality", "trait_desc"):
            if _is_present(raw.get(key)):
                cleaned[key] = raw[key]
        existing = by_layer.get(layer)
        if existing is None or _layer_completeness(cleaned) > _layer_completeness(existing):
            by_layer[layer] = cleaned
    return [by_layer[layer] for layer in sorted(by_layer)]


def clean_cw_trait_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    name = str(entry.get("trait_name") or entry.get("name") or "").strip()
    if not name:
        return None

    cleaned: dict[str, Any] = {}
    trait_id = entry.get("trait_id", entry.get("id"))
    if trait_id is not None:
        cleaned["id"] = str(trait_id)
    cleaned["name"] = name

    icon = entry.get("trait_icon", entry.get("icon"))
    if _is_present(icon):
        cleaned["icon"] = icon
    trait_type = entry.get("trait_type", entry.get("type"))
    if trait_type is not None:
        cleaned["type"] = trait_type
    if _is_present(entry.get("simple_desc")):
        cleaned["simple_desc"] = entry["simple_desc"]
    remarks = _dedupe_items(_as_list(entry.get("remarks")))
    if remarks:
        cleaned["remarks"] = remarks

    role_ids = [str(item) for item in _as_list(entry.get("role_ids")) if item is not None]
    if role_ids:
        cleaned["role_ids"] = sorted(set(role_ids))

    layers = []
    for raw in _iter_mappings(entry.get("layers")):
        layer = _clean_layer(raw)
        if layer is not None:
            layers.append(layer)
    merged_layers = _merge_layers([], layers)
    if merged_layers:
        cleaned["layers"] = merged_layers
    return cleaned


def merge_cw_trait_entries(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key in ("id", "name", "icon", "type", "simple_desc"):
        if not _is_present(merged.get(key)) and _is_present(right.get(key)):
            merged[key] = right[key]

    role_ids = [str(item) for item in _as_list(merged.get("role_ids")) if item is not None]
    role_ids.extend(str(item) for item in _as_list(right.get("role_ids")) if item is not None)
    if role_ids:
        merged["role_ids"] = sorted(set(role_ids))

    remarks = _dedupe_items([*_as_list(merged.get("remarks")), *_as_list(right.get("remarks"))])
    if remarks:
        merged["remarks"] = remarks

    layers = _merge_layers(merged.get("layers") or [], right.get("layers") or [])
    if layers:
        merged["layers"] = layers
    return merged


def build_cw_catalog(guide_config: Mapping[str, Any] | None) -> CwCatalog:
    data = guide_config if isinstance(guide_config, Mapping) else {}
    trait_map: dict[str, dict[str, Any]] = {}
    for raw in _iter_mappings(data.get("traits")):
        cleaned = clean_cw_trait_entry(raw)
        if cleaned is None:
            continue
        key = str(cleaned.get("id") or cleaned["name"])
        trait_map[key] = merge_cw_trait_entries(trait_map[key], cleaned) if key in trait_map else cleaned

    traits = list(trait_map.values())
    trait_name_by_id = {str(item["id"]): str(item["name"]) for item in traits if item.get("id") is not None}
    trait_tiers_by_name = {
        str(item["name"]): [int(layer["layer"]) for layer in item.get("layers") or []]
        for item in traits
        if item.get("layers")
    }

    roles: list[dict[str, Any]] = []
    role_traits_by_name: dict[str, list[str]] = {}
    for raw in _iter_mappings(data.get("roles")):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        role_id = raw.get("id")
        trait_ids = [str(item) for item in _as_list(raw.get("trait_ids")) if item is not None]
        traits_for_role = _dedupe_items(
            trait_name_by_id[trait_id] for trait_id in trait_ids if trait_id in trait_name_by_id
        )
        role: dict[str, Any] = {
            "id": str(role_id) if role_id is not None else None,
            "name": name,
            "normalized_name": normalize_cw_lookup_text(name),
            "trait_ids": trait_ids,
            "traits": traits_for_role,
        }
        roles.append(role)
        role_traits_by_name[name] = traits_for_role

    return CwCatalog(
        roles=roles,
        traits=traits,
        trait_name_by_id=trait_name_by_id,
        trait_tiers_by_name=trait_tiers_by_name,
        role_traits_by_name=role_traits_by_name,
    )


def _rank_role_matches(query: str, catalog: CwCatalog) -> list[tuple[float, str, dict[str, Any]]]:
    normalized_query = normalize_cw_lookup_text(query)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for role in catalog.roles:
        normalized_name = str(role.get("normalized_name") or "")
        score = SequenceMatcher(a=normalized_query, b=normalized_name).ratio() if normalized_query and normalized_name else 0.0
        ranked.append((score, str(role.get("name") or ""), role))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def resolve_cw_role_id(role_id: object, catalog: CwCatalog) -> CwRoleMatch | None:
    if role_id is None:
        return None
    role_id_text = str(role_id).strip()
    if not role_id_text:
        return None
    for role in catalog.roles:
        if role.get("id") != role_id_text:
            continue
        return CwRoleMatch(
            name=str(role["name"]),
            role_id=role_id_text,
            traits=list(role.get("traits") or []),
            match_kind="exact",
            match_score=1.0,
        )
    return None


def _role_warning(
    *,
    code: str,
    position: Mapping[str, Any] | None,
    query: str,
    resolved: str,
    score: float,
    candidates: list[str],
) -> dict[str, Any]:
    return {
        "code": code,
        "position": dict(position or {}),
        "query": query,
        "resolved": resolved,
        "score": round(score, 2),
        "candidates": candidates,
        "message": "角色名未精确命中，请先看截图确认",
    }


def resolve_cw_role_name(
    raw_name: object,
    catalog: CwCatalog,
    *,
    position: Mapping[str, Any] | None = None,
) -> CwRoleMatch | None:
    if not isinstance(raw_name, str):
        return None
    text = raw_name.strip()
    if not text:
        return None
    ranked = _rank_role_matches(text, catalog)
    if not ranked:
        return None

    best_score, _, best_role = ranked[0]
    normalized = normalize_cw_lookup_text(text)
    if normalized == best_role.get("normalized_name"):
        return CwRoleMatch(
            name=str(best_role["name"]),
            role_id=best_role.get("id"),
            traits=list(best_role.get("traits") or []),
            match_kind="exact",
            match_score=1.0,
        )

    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    is_ambiguous = second_score > 0 and best_score - second_score < AMBIGUOUS_DELTA_THRESHOLD
    if is_ambiguous:
        match_kind = "ambiguous"
        code = "CW_ROLE_MATCH_AMBIGUOUS"
    elif best_score < LOW_CONFIDENCE_THRESHOLD:
        match_kind = "low_confidence"
        code = "CW_ROLE_MATCH_LOW_CONFIDENCE"
    else:
        match_kind = "fuzzy"
        code = "CW_ROLE_MATCH_FUZZY"

    score = round(best_score, 2)
    candidates = [f"{role['name']}:{candidate_score:.2f}" for candidate_score, _, role in ranked[:3]]
    warning = _role_warning(
        code=code,
        position=position,
        query=text,
        resolved=str(best_role["name"]),
        score=score,
        candidates=candidates,
    )
    return CwRoleMatch(
        name=str(best_role["name"]),
        role_id=best_role.get("id"),
        traits=list(best_role.get("traits") or []),
        match_kind=match_kind,
        match_score=score,
        raw_name=text,
        warning=warning,
    )


def summarize_cw_field_traits(*, front: list[Any], back: list[Any], catalog: CwCatalog) -> list[dict[str, Any]]:
    owned_counts: dict[str, int] = {}
    for value in [*front, *back]:
        if not isinstance(value, Mapping):
            continue
        for trait in _as_list(value.get("traits")):
            trait_name = str(trait or "").strip()
            if not trait_name:
                continue
            owned_counts[trait_name] = owned_counts.get(trait_name, 0) + 1

    summary: list[dict[str, Any]] = []
    for trait, owned_roles in owned_counts.items():
        tiers = catalog.trait_tiers_by_name.get(trait)
        if not tiers:
            continue
        active_tier = 0
        for tier in tiers:
            if owned_roles >= tier:
                active_tier = tier
        total_tiers = len(tiers)
        max_tier = max(tiers)
        ratio = 0.0 if max_tier <= 0 else round(active_tier / max_tier, 2)
        summary.append(
            {
                "trait": trait,
                "tiers": list(tiers),
                "owned_roles": owned_roles,
                "active_tier": active_tier,
                "total_tiers": total_tiers,
                "ratio": ratio,
            }
        )
    summary.sort(key=lambda item: (-float(item["ratio"]), -int(item["owned_roles"]), str(item["trait"])))
    return summary[:10]
