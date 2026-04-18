from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
import json
import re
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import CwSceneState, ensure_cw_state
from trail.session.models import SessionModel


CW_GUIDE_DETAIL_API = "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/detail"
CW_GUIDE_LIST_API = "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/index"
CW_GUIDE_CONFIG_API = "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/config?game=hkrpg"
CW_GUIDE_LINEUP_URL_TEMPLATE = "https://act.miyoushe.com/sr/event/e20241220rpg-3Tii9M/index.html#/lineup/{lineup_id}"
CW_GUIDE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "x-rpc-currencywar-tourn": "tourn",
    "x-rpc-platform": "pc",
}
LINEUP_ID_PATTERN = re.compile(r"#/lineup/([^/?]+)")
LINEUP_ID_ONLY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SHARE_CODE_PATTERN = re.compile(r"##[^#\s]+##")
LINEUP_LEVEL_PATTERN = re.compile(r"(\d+)级搜牌")
PURCHASE_COUNT_BY_STAR = {
    1: 1,
    2: 3,
    3: 9,
}
CW_GUIDE_PORTAL_FILTER_LIMIT = 60

CW_WIDTH = 1920
CW_HEIGHT = 1080
GUIDE_INPUT_POINT = (int(CW_WIDTH * 0.5), int(CW_HEIGHT * 0.5))
GUIDE_ESC_PRESSES = 3
GUIDE_ESC_INTERVAL = 1.0
GUIDE_UI_WAIT_TIMEOUT = 10
GUIDE_INPUT_FOCUS_DELAY = 0.2
GUIDE_TEXT_SETTLE_DELAY = 0.2
GUIDE_APPLY_SETTLE_TIMEOUT = 1.5
GUIDE_APPLY_SETTLE_INTERVAL = 0.2


class GuidePortalLookupError(TrailError):
    def __init__(self, portal: str, *, candidates: list[dict[str, object]]):
        super().__init__("GUIDE_PORTAL_INVALID", f"guide portal invalid: {portal}")
        self.candidates = candidates


def _guide_artifact_invalid(target: str) -> TrailError:
    return TrailError("GUIDE_ARTIFACT_INVALID", f"guide artifact invalid: {target}")


def _require_mapping(value: object, *, field_name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TrailError("GUIDE_PAYLOAD_INVALID", f"guide payload field '{field_name}' must be an object")
    return dict(value)


def normalize_cw_guide_payload(guide_data: dict) -> dict:
    if not isinstance(guide_data, Mapping):
        raise TrailError("GUIDE_PAYLOAD_INVALID", "guide payload must be an object")

    payload = dict(guide_data)
    scene = str(payload.get("scene", "cw"))
    kind = str(payload.get("kind", "guide"))
    if scene != "cw":
        raise TrailError("GUIDE_SCENE_MISMATCH", f"guide artifact scene must be cw, got: {scene}")
    if kind != "guide":
        raise TrailError("GUIDE_KIND_MISMATCH", f"guide artifact kind must be guide, got: {kind}")

    share_code = payload.get("share_code")
    if not isinstance(share_code, str) or not share_code:
        raise TrailError("GUIDE_PAYLOAD_INVALID", "guide payload missing share_code")

    payload["scene"] = scene
    payload["kind"] = kind
    payload["on_field"] = _require_mapping(payload.get("on_field"), field_name="on_field")
    payload["off_field"] = _require_mapping(payload.get("off_field"), field_name="off_field")
    payload["priority"] = _require_mapping(payload.get("priority"), field_name="priority")
    payload["positioning"] = _require_mapping(payload.get("positioning"), field_name="positioning")
    return payload


def _box_center(box: object) -> tuple[int, int]:
    if hasattr(box, "center"):
        center = box.center
        if isinstance(center, tuple) and len(center) == 2:
            return int(center[0]), int(center[1])

    if isinstance(box, Mapping):
        try:
            left = int(box["left"])
            top = int(box["top"])
            width = int(box["width"])
            height = int(box["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrailError("GUIDE_UI_INVALID", "guide ui match result missing box coordinates") from exc
        return left + width // 2, top + height // 2

    raise TrailError("GUIDE_UI_INVALID", "guide ui match result missing box coordinates")


def _click_required_template(runtime, *, alias: str) -> None:
    template = str(resolve_scene_asset("cw", alias))
    box = runtime.wait_img(template, timeout=GUIDE_UI_WAIT_TIMEOUT)
    if box is None:
        raise TrailError("GUIDE_UI_NOT_FOUND", f"guide ui element not found: {alias}")
    runtime.click_point(*_box_center(box))


def _is_template_visible(runtime, *, alias: str):
    return runtime.locate(str(resolve_scene_asset("cw", alias))) is not None


def _wait_for_template_to_clear(runtime, *, alias: str, timeout: float, interval: float) -> None:
    template = str(resolve_scene_asset("cw", alias))
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if runtime.locate(template) is None:
            return
        sleep(interval)
    raise TrailError("GUIDE_APPLY_NOT_CONFIRMED", f"guide ui element still visible: {alias}")


def apply_cw_guide_via_ui(runtime, *, share_code: str) -> None:
    if not _is_template_visible(runtime, alias="guide.enter_code"):
        _click_required_template(runtime, alias="guide.strategy")
    _click_required_template(runtime, alias="guide.enter_code")
    runtime.click_point(*GUIDE_INPUT_POINT)
    sleep(GUIDE_INPUT_FOCUS_DELAY)
    runtime.type_text(share_code)
    sleep(GUIDE_TEXT_SETTLE_DELAY)
    _click_required_template(runtime, alias="guide.confirm")
    _click_required_template(runtime, alias="guide.apply")
    _wait_for_template_to_clear(
        runtime,
        alias="guide.apply",
        timeout=GUIDE_APPLY_SETTLE_TIMEOUT,
        interval=GUIDE_APPLY_SETTLE_INTERVAL,
    )
    runtime.press_key("esc", presses=GUIDE_ESC_PRESSES, interval=GUIDE_ESC_INTERVAL)


def _build_lineup_url(lineup_id: str) -> str:
    return CW_GUIDE_LINEUP_URL_TEMPLATE.format(lineup_id=lineup_id)


def _resolve_lineup_reference(value: str) -> tuple[str, str]:
    match = LINEUP_ID_PATTERN.search(value)
    if match is not None:
        return match.group(1), value
    if LINEUP_ID_ONLY_PATTERN.fullmatch(value):
        return value, _build_lineup_url(value)
    raise TrailError("GUIDE_URL_INVALID", f"unsupported cw guide url: {value}")


def _read_json_response(request: Request, *, timeout: int) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed with http status {exc.code}") from exc
    except URLError as exc:
        reason = exc.reason if isinstance(exc.reason, str) else str(exc.reason)
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise TrailError("GUIDE_FETCH_FAILED", "guide fetch returned invalid json") from exc


def _fetch_lineup_detail(url: str, *, timeout: int = 10) -> tuple[str, dict]:
    lineup_id, source_url = _resolve_lineup_reference(url)
    request = Request(f"{CW_GUIDE_DETAIL_API}?id={lineup_id}&game=hkrpg", headers=CW_GUIDE_HEADERS)
    payload = _read_json_response(request, timeout=timeout)
    data = payload.get("data")
    lineup = data.get("lineup") if isinstance(data, Mapping) else None
    if payload.get("retcode") != 0 or not isinstance(lineup, Mapping):
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {payload.get('message', 'unknown error')}")
    return lineup_id, source_url, dict(lineup)


def _fetch_cw_config_data(*, timeout: int = 10) -> dict:
    request = Request(CW_GUIDE_CONFIG_API, headers=CW_GUIDE_HEADERS)
    payload = _read_json_response(request, timeout=timeout)
    data = payload.get("data")
    if payload.get("retcode") != 0 or not isinstance(data, Mapping):
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {payload.get('message', 'unknown error')}")
    return dict(data)


def _build_guide_list_request_payload(
    *,
    page: int,
    limit: int,
    trait_id: int | None,
    order: str | None,
    next_page_token: str | None,
    match_change_job: bool | None,
    match_hard: bool | None,
) -> bytes:
    normalized_order = None
    if isinstance(order, str) and order:
        normalized_order = order[:1].upper() + order[1:]

    trait_ids: list[str] = []
    if trait_id is not None:
        trait_ids = [str(trait_id), ""]

    payload: dict[str, object] = {
        "game": "hkrpg",
        "page": str(page),
        "limit": str(limit),
        "lineup_type": "Tourn",
        "role_ids": [],
        "trait_ids": trait_ids,
        "next_page_token": next_page_token or "",
        "match_change_job": False if match_change_job is None else match_change_job,
        "match_hard": False if match_hard is None else match_hard,
    }
    if normalized_order:
        payload["order"] = normalized_order
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _fetch_cw_guide_list_data(
    *,
    page: int,
    limit: int,
    trait_id: int | None,
    order: str | None,
    next_page_token: str | None,
    match_change_job: bool | None,
    match_hard: bool | None,
    timeout: int = 10,
) -> dict:
    headers = {
        **CW_GUIDE_HEADERS,
        "content-type": "application/json",
    }
    request = Request(
        CW_GUIDE_LIST_API,
        headers=headers,
        data=_build_guide_list_request_payload(
            page=page,
            limit=limit,
            trait_id=trait_id,
            order=order,
            next_page_token=next_page_token,
            match_change_job=match_change_job,
            match_hard=match_hard,
        ),
    )
    payload = _read_json_response(request, timeout=timeout)
    data = payload.get("data")
    lineup_list = data.get("list") if isinstance(data, Mapping) else None
    if payload.get("retcode") != 0 or not isinstance(lineup_list, list):
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {payload.get('message', 'unknown error')}")
    return dict(data)


def _normalize_lineup_levels(label_list: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if not isinstance(label_list, list):
        return result
    for item in label_list:
        if not isinstance(item, Mapping):
            continue
        result.append({
            "id": item.get("id"),
            "name": str(item.get("text") or item.get("name") or ""),
        })
    return result


def _normalize_traits(trait_info_list: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if not isinstance(trait_info_list, list):
        return result
    for item in trait_info_list:
        if not isinstance(item, Mapping):
            continue
        result.append({
            "id": item.get("trait_id", item.get("id")),
            "name": str(item.get("trait_name") or item.get("name") or ""),
            "type": item.get("trait_type", item.get("type")),
        })
    return result


def _normalize_role_trait_ids(role: Mapping) -> list[object]:
    trait_details = role.get("trait_details")
    if isinstance(trait_details, list):
        trait_ids: list[object] = []
        for item in trait_details:
            if not isinstance(item, Mapping):
                continue
            trait_id = item.get("id")
            if trait_id is None:
                continue
            trait_ids.append(trait_id)
        return trait_ids

    trait_ids = role.get("trait_ids")
    return list(trait_ids) if isinstance(trait_ids, list) else []


def _normalize_roles(role_list: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if not isinstance(role_list, list):
        return result
    for item in role_list:
        if not isinstance(item, Mapping):
            continue
        result.append({
            "id": item.get("id"),
            "name": str(item.get("name") or ""),
            "front_back_type": item.get("front_back_type"),
            "trait_ids": _normalize_role_trait_ids(item),
        })
    return result


def _normalize_role_tags(data: Mapping) -> list[object]:
    role_tag_list = data.get("role_tag_list")
    if isinstance(role_tag_list, list):
        tags: list[object] = []
        seen: set[str] = set()
        for item in role_tag_list:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            tags.append(name)
        if tags:
            return tags

    tags = []
    seen: set[str] = set()
    role_list = data.get("role_list")
    if not isinstance(role_list, list):
        return tags
    for item in role_list:
        if not isinstance(item, Mapping):
            continue
        role_tags = item.get("role_tags")
        if not isinstance(role_tags, list):
            continue
        for tag in role_tags:
            name = str(tag or "")
            if not name or name in seen:
                continue
            seen.add(name)
            tags.append(name)
    return tags


def _normalize_portal_list(portal_list: object, *, require_id: bool = True) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(portal_list, list):
        return result
    for item in portal_list:
        if not isinstance(item, Mapping):
            continue
        portal_id = str(item.get("portal_id") or item.get("id") or "")
        title = str(item.get("title") or item.get("name") or "")
        description = str(item.get("description") or item.get("desc") or "")
        if not title:
            continue
        if require_id and not portal_id:
            continue
        result.append(
            {
                "portal_id": portal_id,
                "title": title,
                "description": description,
            }
        )
    return result


def _portal_similarity(query: str, *, portal: Mapping[str, str]) -> float:
    normalized_query = query.strip().lower()
    title = str(portal.get("title") or "").strip().lower()
    portal_id = str(portal.get("portal_id") or "").strip().lower()
    title_score = SequenceMatcher(a=normalized_query, b=title).ratio() if title else 0.0
    id_score = SequenceMatcher(a=normalized_query, b=portal_id).ratio() if portal_id else 0.0
    return max(title_score, id_score)


def _portal_candidates(query: str, portal_list: list[dict[str, str]]) -> list[dict[str, object]]:
    ranked = [
        {
            "title": portal["title"],
            "score": round(_portal_similarity(query, portal=portal), 2),
        }
        for portal in portal_list
    ]
    ranked.sort(key=lambda item: (-float(item["score"]), str(item["title"])))
    return ranked[:3]


def _resolve_guide_portal_filter(
    *,
    portal_list: list[dict[str, str]],
    portal: str | None,
    portal_id: str | None,
) -> dict[str, str] | None:
    if portal is not None and portal_id is not None:
        raise TrailError("GUIDE_INPUT_INVALID", "guide options '--portal' and '--portal-id' are mutually exclusive")

    if portal is None and portal_id is None:
        return None

    if portal is not None:
        for item in portal_list:
            if item["title"] == portal:
                return item
        raise GuidePortalLookupError(portal, candidates=_portal_candidates(portal, portal_list))

    for item in portal_list:
        if item["portal_id"] == portal_id:
            return item
    raise GuidePortalLookupError(str(portal_id), candidates=_portal_candidates(str(portal_id), portal_list))


def _matches_lineup_portal(lineup: Mapping[str, object], *, portal: Mapping[str, str]) -> bool:
    tourn_detail = lineup.get("tourn_detail")
    if not isinstance(tourn_detail, Mapping):
        return False
    detail_portals = _normalize_portal_list(tourn_detail.get("portals"), require_id=False)
    for item in detail_portals:
        if item["portal_id"] == portal["portal_id"] or item["title"] == portal["title"]:
            return True
    return False


def _normalize_lineup_labels(labels: object) -> list[str]:
    result: list[str] = []
    if not isinstance(labels, list):
        return result
    for item in labels:
        if isinstance(item, Mapping):
            text = item.get("text") or item.get("name")
        else:
            text = item
        if not isinstance(text, str) or not text:
            continue
        result.append(text)
    return result


def _pick_final_stage(role_stages: object) -> Mapping | None:
    if not isinstance(role_stages, list) or not role_stages:
        return None
    for stage in role_stages:
        if isinstance(stage, Mapping) and stage.get("stage") == "Final":
            return stage
    fallback = role_stages[-1]
    return fallback if isinstance(fallback, Mapping) else None


def _append_unique_names(target: list[str], values: object) -> None:
    if not isinstance(values, list):
        return
    seen = set(target)
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        target.append(name)


def _read_interact_value(payload: Mapping, *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _normalize_interact(payload: object) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        return {"like": 0, "favour": 0, "view": 0, "use": 0}
    return {
        "like": _read_interact_value(payload, "like", "like_num", "like_count"),
        "favour": _read_interact_value(payload, "favour", "favour_num", "favour_count"),
        "view": _read_interact_value(payload, "view", "view_num", "view_count"),
        "use": _read_interact_value(payload, "use", "use_num", "use_count"),
    }


def _normalize_named_list(values: object) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    seen: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            name = item.get("name")
        else:
            name = item
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _append_role_cards(target: list[dict[str, object]], values: object) -> None:
    if not isinstance(values, list):
        return
    seen: set[str] = {str(item.get("name")) for item in target if isinstance(item, Mapping)}
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        target.append(
            {
                "name": name,
                "star": item.get("star"),
                "rarity": item.get("rarity"),
                "is_carry": bool(item.get("is_carry")),
            }
        )


def _normalize_role_stage_list(role_stages: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if not isinstance(role_stages, list):
        return result
    for stage in role_stages:
        if not isinstance(stage, Mapping):
            continue
        front_roles: list[dict[str, object]] = []
        back_roles: list[dict[str, object]] = []
        traits: list[str] = []
        _append_role_cards(front_roles, stage.get("front_roles"))
        _append_role_cards(back_roles, stage.get("back_roles"))
        _append_unique_names(traits, stage.get("traits"))
        result.append(
            {
                "stage": str(stage.get("stage") or ""),
                "front_roles": front_roles,
                "back_roles": back_roles,
                "traits": traits,
            }
        )
    return result


def _normalize_lineup_summary(lineup: object) -> dict[str, object]:
    if not isinstance(lineup, Mapping):
        return {
            "id": None,
            "title": "",
            "nickname": "",
            "description": "",
            "labels": [],
            "final_traits": [],
            "final_role_cards": [],
            "has_change_equip": False,
            "has_expert": False,
            "version": "",
            "created_at": None,
            "last_edit": None,
            "carry_roles": [],
            "interact": _normalize_interact(None),
            "recent_interact": _normalize_interact(None),
            "support_hard": False,
        }

    tourn_detail = lineup.get("tourn_detail") if isinstance(lineup.get("tourn_detail"), Mapping) else {}
    game_data = lineup.get("game_data") if isinstance(lineup.get("game_data"), Mapping) else {}
    final_stage = _pick_final_stage(tourn_detail.get("role_stages"))
    final_traits: list[str] = []
    final_role_cards: list[dict[str, object]] = []
    if isinstance(final_stage, Mapping):
        _append_unique_names(final_traits, final_stage.get("traits"))
        _append_role_cards(final_role_cards, final_stage.get("front_roles"))
        _append_role_cards(final_role_cards, final_stage.get("back_roles"))

    return {
        "id": lineup.get("id"),
        "title": str(lineup.get("title") or ""),
        "nickname": str(lineup.get("nickname") or ""),
        "description": str(lineup.get("description") or ""),
        "labels": _normalize_lineup_labels(tourn_detail.get("labels")),
        "final_traits": final_traits,
        "final_role_cards": final_role_cards,
        "has_change_equip": bool(lineup.get("has_change_equip")),
        "has_expert": bool(lineup.get("has_expert")),
        "version": str(tourn_detail.get("rpg_game_big_version") or ""),
        "created_at": lineup.get("created_at"),
        "last_edit": lineup.get("last_edit"),
        "carry_roles": _normalize_named_list(tourn_detail.get("carry_list")),
        "interact": _normalize_interact(game_data.get("interact")),
        "recent_interact": _normalize_interact(game_data.get("recent_interact")),
        "support_hard": bool(tourn_detail.get("support_hard")),
    }


def fetch_cw_guide_config(*, timeout: int = 10) -> dict:
    data = _fetch_cw_config_data(timeout=timeout)
    return {
        "meta": {
            "game": "hkrpg",
            "season_id": data.get("season_id"),
            "sub_season_id": data.get("sub_season_id"),
            "big_version": data.get("rpg_game_big_version"),
            "lineup_filter_version": data.get("rpg_game_lineup_tourn_filter"),
        },
        "lineup_levels": _normalize_lineup_levels(data.get("label_list")),
        "traits": _normalize_traits(data.get("trait_info_list")),
        "roles": _normalize_roles(data.get("role_list")),
        "role_tags": _normalize_role_tags(data),
        "portal_list": _normalize_portal_list(data.get("portal_list")),
    }


def fetch_cw_guide_list(
    *,
    page: int,
    limit: int,
    trait_id: int | None,
    order: str | None,
    next_page_token: str | None,
    match_change_job: bool | None,
    match_hard: bool | None,
    portal: str | None = None,
    portal_id: str | None = None,
    timeout: int = 10,
) -> dict:
    if (portal is not None or portal_id is not None) and (page > 1 or next_page_token):
        raise TrailError(
            "GUIDE_PORTAL_PAGINATION_UNSUPPORTED",
            "guide portal filter only supports first page without next_page_token",
        )

    portal_filter = None
    if portal is not None or portal_id is not None:
        portal_filter = _resolve_guide_portal_filter(
            portal_list=fetch_cw_guide_config(timeout=timeout).get("portal_list", []),
            portal=portal,
            portal_id=portal_id,
        )

    data = _fetch_cw_guide_list_data(
        page=1 if portal_filter is not None else page,
        limit=CW_GUIDE_PORTAL_FILTER_LIMIT if portal_filter is not None else limit,
        trait_id=trait_id,
        order=order,
        next_page_token=None if portal_filter is not None else next_page_token,
        match_change_job=match_change_job,
        match_hard=match_hard,
        timeout=timeout,
    )
    lineup_list = data.get("list")
    if portal_filter is not None:
        filtered: list[dict[str, object]] = []
        for item in lineup_list if isinstance(lineup_list, list) else []:
            if not isinstance(item, Mapping):
                continue
            lineup_id = item.get("id") or item.get("lineup_id")
            if lineup_id is None:
                continue
            _, _, lineup_detail = _fetch_lineup_detail(str(lineup_id), timeout=timeout)
            if _matches_lineup_portal(lineup_detail, portal=portal_filter):
                filtered.append(_normalize_lineup_summary(item))
        return {
            "list": filtered[:limit],
            "next_page_token": None,
        }

    return {
        "list": [_normalize_lineup_summary(item) for item in lineup_list] if isinstance(lineup_list, list) else [],
        "next_page_token": data.get("next_page_token"),
    }


def _wrap_share_code(share_code: object, *, source_url: str) -> str:
    if not isinstance(share_code, str) or not share_code:
        raise TrailError("GUIDE_SHARE_CODE_NOT_FOUND", f"guide share code not found: {source_url}")
    if SHARE_CODE_PATTERN.fullmatch(share_code):
        return share_code
    return f"##{share_code}##"


def _get_purchase_count(star: object) -> int:
    if isinstance(star, bool):
        return PURCHASE_COUNT_BY_STAR[1]
    if isinstance(star, int):
        return PURCHASE_COUNT_BY_STAR.get(star, PURCHASE_COUNT_BY_STAR[1])
    return PURCHASE_COUNT_BY_STAR[1]


def _append_roles(target: dict[str, int], roles: object, seen_names: set[str]) -> None:
    if not isinstance(roles, list):
        return
    for role in roles:
        if not isinstance(role, Mapping):
            continue
        name = role.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            continue
        target[name] = _get_purchase_count(role.get("star"))
        seen_names.add(name)


def _get_lineup_level(lineup: Mapping) -> int:
    tourn_detail = lineup.get("tourn_detail")
    if not isinstance(tourn_detail, Mapping):
        return 7
    labels = tourn_detail.get("labels")
    if not isinstance(labels, list):
        return 7
    for label in labels:
        if not isinstance(label, Mapping):
            continue
        text = label.get("text")
        if not isinstance(text, str):
            continue
        match = LINEUP_LEVEL_PATTERN.search(text)
        if match is not None:
            return int(match.group(1))
    return 7


def _get_mid_level(min_level: int) -> int:
    return max(min_level, 9)


def _build_roles_from_lineup(lineup: Mapping) -> tuple[dict[str, int], dict[str, int]]:
    tourn_detail = lineup.get("tourn_detail")
    if not isinstance(tourn_detail, Mapping):
        raise TrailError("GUIDE_FETCH_FAILED", "guide fetch response missing lineup detail")
    role_stages = tourn_detail.get("role_stages")
    if not isinstance(role_stages, list) or not role_stages:
        raise TrailError("GUIDE_FETCH_FAILED", "guide fetch response missing role stages")

    final_stage = None
    for stage in role_stages:
        if isinstance(stage, Mapping) and stage.get("stage") == "Final":
            final_stage = stage
            break
    if not isinstance(final_stage, Mapping):
        final_stage = role_stages[-1]
    if not isinstance(final_stage, Mapping):
        raise TrailError("GUIDE_FETCH_FAILED", "guide fetch response missing final lineup stage")

    on_field: dict[str, int] = {}
    off_field: dict[str, int] = {}
    seen_names: set[str] = set()
    _append_roles(on_field, final_stage.get("front_roles"), seen_names)
    _append_roles(off_field, final_stage.get("back_roles"), seen_names)
    return on_field, off_field


def fetch_cw_guide_payload(url: str) -> dict:
    lineup_id, source_url, lineup = _fetch_lineup_detail(url)
    on_field, off_field = _build_roles_from_lineup(lineup)
    min_level = _get_lineup_level(lineup)
    tourn_detail = lineup.get("tourn_detail") if isinstance(lineup.get("tourn_detail"), Mapping) else {}
    payload = {
        "scene": "cw",
        "kind": "guide",
        "source_url": source_url,
        "lineup_id": lineup_id,
        "title": str(lineup.get("title") or "货币战争攻略码"),
        "author": str(lineup.get("nickname") or ""),
        "uploader": str(lineup.get("nickname") or ""),
        "share_code": _wrap_share_code(tourn_detail.get("share_code"), source_url=url),
        "labels": _normalize_lineup_labels(tourn_detail.get("labels")),
        "support_hard": bool(tourn_detail.get("support_hard")),
        "has_change_equip": bool(lineup.get("has_change_equip")),
        "has_expert": bool(lineup.get("has_expert")),
        "version": str(tourn_detail.get("rpg_game_big_version") or ""),
        "min_coins": 40,
        "min_level": min_level,
        "mid_level": _get_mid_level(min_level),
        "on_field": on_field,
        "off_field": off_field,
        "role_stages": _normalize_role_stage_list(tourn_detail.get("role_stages")),
        "first_fight_augments": _normalize_named_list(tourn_detail.get("first_fight_augments")),
        "second_fight_augments": _normalize_named_list(tourn_detail.get("second_fight_augments")),
        "portals": _normalize_named_list(tourn_detail.get("portals")),
        "order_basic": _normalize_named_list(tourn_detail.get("order_basic")),
        "order_compose": _normalize_named_list(tourn_detail.get("order_compose")),
    }
    return payload


def fetch_cw_guide(url: str, *, fetcher) -> dict:
    return normalize_cw_guide_payload(fetcher(url))


def _read_guide_payload(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrailError("GUIDE_NOT_FOUND", f"guide artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise _guide_artifact_invalid(str(path)) from exc
    except OSError as exc:
        raise _guide_artifact_invalid(str(path)) from exc


def resolve_guide_input(value: str, *, artifact_store: ArtifactStore) -> dict:
    candidate = Path(value)
    if candidate.exists():
        payload = _read_guide_payload(candidate)
        payload.setdefault("artifact_id", candidate.stem)
        return normalize_cw_guide_payload(payload)

    try:
        meta = artifact_store.load(value)
    except FileNotFoundError as exc:
        raise TrailError("GUIDE_NOT_FOUND", f"guide artifact not found: {value}") from exc
    except json.JSONDecodeError as exc:
        raise _guide_artifact_invalid(value) from exc
    except (KeyError, OSError) as exc:
        raise _guide_artifact_invalid(value) from exc
    except ValueError as exc:
        if str(exc) == "invalid artifact_id":
            raise TrailError("GUIDE_INPUT_INVALID", f"guide artifact id invalid: {value}") from exc
        raise _guide_artifact_invalid(value) from exc

    payload = _read_guide_payload(meta.path)
    payload["artifact_id"] = meta.artifact_id
    payload.setdefault("scene", meta.scene)
    payload.setdefault("kind", meta.kind)
    return normalize_cw_guide_payload(payload)


def apply_cw_guide(session: SessionModel, guide_data: dict) -> SessionModel:
    guide_payload = normalize_cw_guide_payload(guide_data)
    cw_state = ensure_cw_state(session)
    defaults = CwSceneState().model_dump()
    on_field = dict(guide_payload.get("on_field", {}))
    off_field = dict(guide_payload.get("off_field", {}))
    cw_state["guide"] = {
        "artifact": guide_payload.get("artifact_id"),
        "lineup_id": guide_payload.get("lineup_id"),
        "share_code": guide_payload["share_code"],
        "source_url": guide_payload.get("source_url"),
        "title": guide_payload.get("title"),
        "author": guide_payload.get("author"),
        "uploader": guide_payload.get("uploader"),
        "labels": guide_payload.get("labels", []),
        "support_hard": bool(guide_payload.get("support_hard")),
        "has_change_equip": bool(guide_payload.get("has_change_equip")),
        "has_expert": bool(guide_payload.get("has_expert")),
        "version": guide_payload.get("version"),
        "on_field": on_field,
        "off_field": off_field,
        "role_stages": guide_payload.get("role_stages", []),
        "first_fight_augments": guide_payload.get("first_fight_augments", []),
        "second_fight_augments": guide_payload.get("second_fight_augments", []),
        "portals": guide_payload.get("portals", []),
        "order_basic": guide_payload.get("order_basic", []),
        "order_compose": guide_payload.get("order_compose", []),
        "remaining_purchases": {
            **on_field,
            **off_field,
        },
    }
    cw_state["constraints"] = {
        "min_coins": guide_payload.get("min_coins", 40),
        "min_level": guide_payload.get("min_level", 7),
        "mid_level": guide_payload.get("mid_level", 7),
        "priority": guide_payload.get("priority", {}),
        "positioning": guide_payload.get("positioning", {}),
    }
    cw_state["slots"] = defaults["slots"]
    cw_state["sell_plan"] = defaults["sell_plan"]
    cw_state["shop"] = defaults["shop"]
    cw_state["stage"] = defaults["stage"]
    return session
