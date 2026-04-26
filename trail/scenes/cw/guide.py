from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from difflib import SequenceMatcher
import json
import math
import re
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import CwSceneState, ensure_cw_state
from trail.scenes.cw.stage import _replace_stage_fields
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
CW_GUIDE_STATE_INVALID_MESSAGE = "当前攻略不完整，请重新执行 guide.fetch.cw --select"
LOOKUP_WHITESPACE_PATTERN = re.compile(r"\s+")
CW_GUIDE_UPSTREAM_PAGE_SIZE = 10
CW_GUIDE_PORTAL_MAX_PAGES = 30
GUIDE_ROLE_HIGH_RISK_SIMILARITY_THRESHOLD = 0.75
CW_GUIDE_CONFIG_CACHE_RELATIVE = Path(".trail") / "cache" / "cw-guide-config.json"

CW_WIDTH = 1920
CW_HEIGHT = 1080
GUIDE_INPUT_POINT = (int(CW_WIDTH * 0.5), int(CW_HEIGHT * 0.5))
GUIDE_ESC_PRESSES = 3
GUIDE_ESC_INTERVAL = 1.0
GUIDE_UI_WAIT_TIMEOUT = 10
GUIDE_INPUT_FOCUS_DELAY = 0.2
GUIDE_TEXT_SETTLE_DELAY = 0.2
GUIDE_CONFIRM_SETTLE_DELAY = 1.0
GUIDE_APPLY_READY_DELAY = 1.0
GUIDE_APPLY_SETTLE_TIMEOUT = 1.5
GUIDE_APPLY_SETTLE_INTERVAL = 0.2
GUIDE_POST_APPLY_SETTLE_DELAY = 1.0
GUIDE_ENTER_CODE_OCR_INTERVAL = 0.5
GUIDE_ENTER_CODE_OCR_KEYWORDS = ("输入攻略码", "攻略码", "输入策略码", "策略码")


class GuidePortalLookupError(TrailError):
    def __init__(self, portal: str, *, candidates: list[dict[str, object]]):
        super().__init__("GUIDE_PORTAL_INVALID", f"guide portal invalid: {portal}")
        self.candidates = candidates


class GuideTraitLookupError(TrailError):
    def __init__(self, trait: str, *, candidates: list[dict[str, object]]):
        super().__init__("GUIDE_TRAIT_INVALID", f"guide trait invalid: {trait}")
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
    box = _wait_required_template(runtime, alias=alias)
    runtime.click_point(*_box_center(box))


def _normalize_guide_ocr_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", "", text).strip()


def _extract_guide_polygon_box(points: object) -> dict[str, int] | None:
    if not isinstance(points, (list, tuple)):
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except (TypeError, ValueError):
            return None
    if not xs or not ys:
        return None
    left = min(xs)
    top = min(ys)
    right = max(xs)
    bottom = max(ys)
    return {
        "left": int(left),
        "top": int(top),
        "width": int(right - left),
        "height": int(bottom - top),
    }


def _normalize_guide_ocr_piece(piece: object) -> dict[str, int | str] | None:
    if isinstance(piece, Mapping):
        text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        box_source = piece.get("box") if isinstance(piece.get("box"), Mapping) else piece
        try:
            left = int(box_source["left"])
            top = int(box_source["top"])
            width = int(box_source["width"])
            height = int(box_source["height"])
        except (KeyError, TypeError, ValueError):
            return None
        box = {"left": left, "top": top, "width": width, "height": height}
    elif isinstance(piece, (list, tuple)) and len(piece) >= 2:
        text = str(piece[1] or "").strip()
        box = _extract_guide_polygon_box(piece[0])
    else:
        return None
    if not text or box is None:
        return None
    return {"text": text, **box}


def _find_enter_code_box_by_ocr(runtime) -> dict[str, int] | None:
    ocr = getattr(runtime, "ocr", None)
    if not callable(ocr):
        return None
    normalized_keywords = tuple(_normalize_guide_ocr_text(keyword) for keyword in GUIDE_ENTER_CODE_OCR_KEYWORDS)
    try:
        pieces = ocr() or []
    except Exception:
        return None
    for piece in pieces:
        normalized_piece = _normalize_guide_ocr_piece(piece)
        if normalized_piece is None:
            continue
        normalized_text = _normalize_guide_ocr_text(normalized_piece["text"])
        if not any(keyword in normalized_text for keyword in normalized_keywords):
            continue
        return {
            "left": int(normalized_piece["left"]),
            "top": int(normalized_piece["top"]),
            "width": int(normalized_piece["width"]),
            "height": int(normalized_piece["height"]),
        }
    return None


def _is_enter_code_visible(runtime) -> bool:
    return _find_enter_code_box_by_ocr(runtime) is not None


def _wait_for_enter_code_box(runtime):
    deadline = monotonic() + GUIDE_UI_WAIT_TIMEOUT
    while True:
        ocr_box = _find_enter_code_box_by_ocr(runtime)
        if ocr_box is not None:
            return ocr_box
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(GUIDE_ENTER_CODE_OCR_INTERVAL, remaining))
    raise TrailError("GUIDE_UI_NOT_FOUND", "guide ui element not found: guide.enter_code")


def _wait_required_template(runtime, *, alias: str):
    template = str(resolve_scene_asset("cw", alias))
    box = runtime.wait_img(template, timeout=GUIDE_UI_WAIT_TIMEOUT)
    if box is None:
        raise TrailError("GUIDE_UI_NOT_FOUND", f"guide ui element not found: {alias}")
    return box


def _wait_for_template_to_stabilize(runtime, *, alias: str, timeout: float, settle_delay: float, interval: float):
    if timeout <= 0:
        raise AssertionError(f"{alias} wait timeout must stay > 0")
    if settle_delay <= 0:
        raise AssertionError(f"{alias} settle delay must stay > 0")
    if interval <= 0:
        raise AssertionError(f"{alias} settle interval must stay > 0")

    template = str(resolve_scene_asset("cw", alias))
    stable_checks = max(1, math.ceil(settle_delay / interval))
    box = _wait_required_template(runtime, alias=alias)
    stable_count = 0
    deadline = monotonic() + timeout
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
        current = runtime.locate(template)
        if current is None:
            box = None
            stable_count = 0
            continue
        if box is not None and _box_center(current) == _box_center(box):
            stable_count += 1
        else:
            stable_count = 0
        box = current
        if stable_count >= stable_checks:
            return box
    raise TrailError("GUIDE_UI_NOT_FOUND", f"guide ui element not found: {alias}")


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
    if not _is_enter_code_visible(runtime):
        _click_required_template(runtime, alias="guide.strategy")
    runtime.click_point(*_box_center(_wait_for_enter_code_box(runtime)))
    runtime.click_point(*GUIDE_INPUT_POINT)
    sleep(GUIDE_INPUT_FOCUS_DELAY)
    runtime.type_text(share_code)
    sleep(GUIDE_TEXT_SETTLE_DELAY)
    _click_required_template(runtime, alias="guide.confirm")
    sleep(GUIDE_CONFIRM_SETTLE_DELAY)
    # Wait until the apply button stops shifting while the fetched lineup details settle.
    apply_box = _wait_for_template_to_stabilize(
        runtime,
        alias="guide.apply",
        timeout=GUIDE_UI_WAIT_TIMEOUT,
        settle_delay=GUIDE_APPLY_READY_DELAY,
        interval=GUIDE_APPLY_SETTLE_INTERVAL,
    )
    runtime.click_point(*_box_center(apply_box))
    _wait_for_template_to_clear(
        runtime,
        alias="guide.apply",
        timeout=GUIDE_APPLY_SETTLE_TIMEOUT,
        interval=GUIDE_APPLY_SETTLE_INTERVAL,
    )
    sleep(GUIDE_POST_APPLY_SETTLE_DELAY)
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


def _cw_guide_config_cache_path(*, workspace_root: str | Path | None = None) -> Path:
    root = Path.cwd() if workspace_root is None else Path(workspace_root)
    return root / CW_GUIDE_CONFIG_CACHE_RELATIVE


def _load_cached_cw_config_data(*, workspace_root: str | Path | None = None) -> dict | None:
    path = _cw_guide_config_cache_path(workspace_root=workspace_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _write_cached_cw_config_data(data: Mapping[str, object], *, workspace_root: str | Path | None = None) -> None:
    path = _cw_guide_config_cache_path(workspace_root=workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cw_config_data(*, timeout: int = 10, workspace_root: str | Path | None = None) -> dict:
    cached = _load_cached_cw_config_data(workspace_root=workspace_root)
    if cached is not None:
        return cached
    data = _fetch_cw_config_data(timeout=timeout)
    _write_cached_cw_config_data(data, workspace_root=workspace_root)
    return data


def _build_guide_list_request_payload(
    *,
    page: int,
    limit: int,
    trait_id: int | None,
    role_ids: list[str],
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
        "role_ids": role_ids,
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
    role_ids: list[str],
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
            role_ids=role_ids,
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


def _normalize_strategy_list(strategy_list: object) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(strategy_list, list):
        return result
    for item in strategy_list:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        result.append(
            {
                "strategy_id": str(item.get("strategy_id") or item.get("id") or title),
                "title": title,
                "description": str(item.get("description") or item.get("desc") or "").strip(),
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


def _normalize_lookup_text(text: str) -> str:
    return LOOKUP_WHITESPACE_PATTERN.sub(" ", text.strip().lower()).strip()


def _normalize_string_filters(value: str | list[str] | None, *, option_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise TrailError("GUIDE_INPUT_INVALID", f"guide {option_name} filter must be a string or list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TrailError("GUIDE_INPUT_INVALID", f"guide {option_name} filter must be a string or list of strings")
        result.append(item)
    return result


def _build_trait_name_lookup(raw_config: Mapping[str, object]) -> tuple[list[dict[str, object]], dict[str, str]]:
    traits = _normalize_traits(raw_config.get("trait_info_list"))
    trait_name_by_id = {
        str(item["id"]): str(item["name"])
        for item in traits
        if item.get("id") is not None and isinstance(item.get("name"), str) and item.get("name")
    }
    return traits, trait_name_by_id


def _trait_similarity(query: str, trait_name: str) -> float:
    normalized_query = _normalize_lookup_text(query)
    normalized_name = _normalize_lookup_text(trait_name)
    if not normalized_query or not normalized_name:
        return 0.0
    return SequenceMatcher(a=normalized_query, b=normalized_name).ratio()


def _trait_candidates(query: str, traits: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for trait in traits:
        name = str(trait.get("name") or "")
        trait_id = trait.get("id")
        if not name or trait_id is None:
            continue
        score = _trait_similarity(query, name)
        ranked.append({
            "trait": name,
            "trait_id": str(trait_id),
            "score": round(score, 2),
            "_score": score,
        })
    ranked.sort(key=lambda item: (-float(item["_score"]), str(item["trait"])))
    return [{key: value for key, value in item.items() if key != "_score"} for item in ranked[:3]]


def _resolve_guide_trait_id(*, raw_config: Mapping[str, object], trait: str | None, trait_id: int | None) -> int | None:
    if trait is None:
        return trait_id
    traits, _ = _build_trait_name_lookup(raw_config)
    normalized_query = _normalize_lookup_text(trait)
    for item in traits:
        if _normalize_lookup_text(str(item.get("name") or "")) != normalized_query:
            continue
        resolved_id = item.get("id")
        return int(resolved_id) if resolved_id is not None else None
    raise GuideTraitLookupError(trait, candidates=_trait_candidates(trait, traits))


def _normalize_role_item_tags(role: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    role_tags = role.get("role_tags")
    if not isinstance(role_tags, list):
        return result
    for item in role_tags:
        name = item.get("name") if isinstance(item, Mapping) else item
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _build_role_catalog(raw_config: Mapping[str, object]) -> list[dict[str, object]]:
    role_list = raw_config.get("role_list")
    if not isinstance(role_list, list):
        return []
    result: list[dict[str, object]] = []
    for item in role_list:
        if not isinstance(item, Mapping):
            continue
        role_id = item.get("id")
        name = str(item.get("name") or "")
        if role_id is None or not name:
            continue
        result.append({
            "id": str(role_id),
            "name": name,
            "normalized_name": _normalize_lookup_text(name),
            "front_back": str(item.get("front_back_type") or ""),
            "trait_ids": [str(trait_id) for trait_id in _normalize_role_trait_ids(item) if trait_id is not None],
            "role_tags": _normalize_role_item_tags(item),
        })
    return result


def _is_character_rearrangement(left: str, right: str) -> bool:
    return bool(left and right and left != right and len(left) == len(right) and sorted(left) == sorted(right))


def _has_contains_relation(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))


def _rank_role_matches(query: str, role_catalog: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized_query = _normalize_lookup_text(query)
    ranked: list[dict[str, object]] = []
    for role in role_catalog:
        normalized_name = str(role.get("normalized_name") or "")
        similarity = SequenceMatcher(a=normalized_query, b=normalized_name).ratio() if normalized_query and normalized_name else 0.0
        ranked.append({
            "role": role,
            "score": similarity,
            "is_exact": normalized_query == normalized_name,
            "is_rearranged": _is_character_rearrangement(normalized_query, normalized_name),
            "has_contains": _has_contains_relation(normalized_query, normalized_name),
        })
    ranked.sort(
        key=lambda item: (
            -int(bool(item["is_rearranged"])),
            -int(bool(item["has_contains"])),
            -float(item["score"]),
            str(item["role"].get("name") or ""),
        )
    )
    return ranked


def _pick_highest_similarity_role_match(ranked: list[dict[str, object]]) -> dict[str, object] | None:
    if not ranked:
        return None
    return min(ranked, key=lambda item: (-float(item["score"]), str(item["role"].get("name") or "")))


def _build_resolved_first_role_matches(
    resolved_match: Mapping[str, object],
    ranked: list[dict[str, object]],
) -> list[dict[str, object]]:
    ordered = [dict(resolved_match)]
    ordered.extend(match for match in ranked if match is not resolved_match)
    return ordered


def _role_traits_from_ids(trait_ids: list[str], *, trait_name_by_id: Mapping[str, str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for trait_id in trait_ids:
        name = trait_name_by_id.get(str(trait_id))
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _build_role_candidate_entry(
    *,
    query: str,
    match: Mapping[str, object],
    selected: bool,
    trait_name_by_id: Mapping[str, str],
) -> dict[str, object]:
    role = match["role"]
    return {
        "query": query,
        "role": role["name"],
        "id": role["id"],
        "selected": selected,
        "score": round(float(match["score"]), 2),
        "front_back": role["front_back"],
        "traits": _role_traits_from_ids(list(role.get("trait_ids") or []), trait_name_by_id=trait_name_by_id),
        "role_tags": list(role.get("role_tags") or []),
    }


def _is_high_risk_role_match(match: Mapping[str, object]) -> bool:
    return bool(
        match.get("is_rearranged")
        or match.get("has_contains")
        or float(match.get("score") or 0.0) >= GUIDE_ROLE_HIGH_RISK_SIMILARITY_THRESHOLD
    )


def _resolve_guide_role_filters(*, raw_config: Mapping[str, object], role: str | list[str] | None) -> dict[str, object]:
    role_queries = _normalize_string_filters(role, option_name="role")
    if not role_queries:
        return {
            "role_ids": [],
            "role_candidates": [],
            "role_warnings": [],
        }

    _, trait_name_by_id = _build_trait_name_lookup(raw_config)
    role_catalog = _build_role_catalog(raw_config)
    role_ids: list[str] = []
    seen_role_ids: set[str] = set()
    role_candidates: list[dict[str, object]] = []
    role_warnings: list[dict[str, object]] = []

    for query in role_queries:
        ranked = _rank_role_matches(query, role_catalog)
        if not ranked:
            continue

        resolved_match = next((item for item in ranked if item["is_exact"]), None)
        if resolved_match is None:
            resolved_match = _pick_highest_similarity_role_match(ranked)
        if resolved_match is None:
            continue
        resolved_role = resolved_match["role"]
        resolved_id = str(resolved_role["id"])
        if resolved_id not in seen_role_ids:
            seen_role_ids.add(resolved_id)
            role_ids.append(resolved_id)

        exact_matches = [item for item in ranked if item["is_exact"]]
        if exact_matches:
            risky_matches = [item for item in ranked if item is not resolved_match and _is_high_risk_role_match(item)]
            if not risky_matches:
                continue
            selected_matches = [resolved_match, *risky_matches[:2]]
            role_candidates.append(
                {
                    "query": query,
                    "role_resolution": "exact_ambiguous",
                    "resolved": resolved_role["name"],
                    "candidates": [
                        _build_role_candidate_entry(
                            query=query,
                            match=match,
                            selected=index == 0,
                            trait_name_by_id=trait_name_by_id,
                        )
                        for index, match in enumerate(selected_matches)
                    ],
                }
            )
            role_warnings.append(
                {
                    "code": "GUIDE_ROLE_SIMILAR_CANDIDATES",
                    "query": query,
                    "resolved": resolved_role["name"],
                    "message": "角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
                }
            )
            continue

        selected_matches = _build_resolved_first_role_matches(resolved_match, ranked)[:3]
        role_candidates.append(
            {
                "query": query,
                "role_resolution": "fuzzy",
                "resolved": resolved_role["name"],
                "candidates": [
                    _build_role_candidate_entry(
                        query=query,
                        match=match,
                        selected=index == 0,
                        trait_name_by_id=trait_name_by_id,
                    )
                    for index, match in enumerate(selected_matches)
                ],
            }
        )
        role_warnings.append(
            {
                "code": "GUIDE_ROLE_FUZZY_MATCH",
                "query": query,
                "resolved": resolved_role["name"],
                "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
            }
        )

    return {
        "role_ids": role_ids,
        "role_candidates": role_candidates,
        "role_warnings": role_warnings,
    }


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
        name = _extract_named_text(item)
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


def _core_interact(summary: Mapping[str, object]) -> dict[str, int]:
    interact = summary.get("interact") if isinstance(summary.get("interact"), Mapping) else {}
    return {
        "like": int(interact.get("like") or 0),
        "favour": int(interact.get("favour") or 0),
    }


def _normalize_named_list(values: object) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    seen: set[str] = set()
    for item in values:
        name = _extract_named_text(item)
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _normalize_trait_list(values: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for item in values:
        name = _extract_named_text(item)
        if not isinstance(name, str) or not name:
            continue
        count = None
        if isinstance(item, Mapping):
            raw_count = item.get("current_role_count")
            if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0:
                count = raw_count
            elif isinstance(raw_count, str) and raw_count.isdigit() and int(raw_count) > 0:
                count = int(raw_count)
        normalized = f"{count}{name}" if count is not None else name
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _extract_named_text(item: object) -> str | None:
    if isinstance(item, Mapping):
        for key in ("name", "title", "text", "trait_name"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return None
    return item if isinstance(item, str) and item else None


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
        role = {
            "name": name,
            "star": item.get("star"),
            "rarity": item.get("rarity"),
            "is_carry": bool(item.get("is_carry")),
        }
        first_equipments = _normalize_named_list(item.get("first_equipments"))
        second_equipments = _normalize_named_list(item.get("second_equipments"))
        if first_equipments:
            role["first_equipments"] = first_equipments
        if second_equipments:
            role["second_equipments"] = second_equipments
        target.append(role)


def _normalize_role_stage_list(role_stages: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if not isinstance(role_stages, list):
        return result
    for stage in role_stages:
        if not isinstance(stage, Mapping):
            continue
        front_roles: list[dict[str, object]] = []
        back_roles: list[dict[str, object]] = []
        _append_role_cards(front_roles, stage.get("front_roles"))
        _append_role_cards(back_roles, stage.get("back_roles"))
        result.append(
            {
                "stage": str(stage.get("stage") or ""),
                "front_roles": front_roles,
                "back_roles": back_roles,
                "traits": _normalize_trait_list(stage.get("traits")),
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
            "like": 0,
            "favour": 0,
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

    interact = _normalize_interact(game_data.get("interact"))
    recent_interact = _normalize_interact(game_data.get("recent_interact"))

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
        "interact": interact,
        "recent_interact": recent_interact,
        "like": interact.get("like", 0),
        "favour": interact.get("favour", 0),
        "support_hard": bool(tourn_detail.get("support_hard")),
    }


def fetch_cw_guide_config(*, timeout: int = 10, workspace_root: str | Path | None = None) -> dict:
    data = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
    strategy_source = data.get("fight_augment_list")
    if not isinstance(strategy_source, list):
        strategy_source = data.get("strategy_list")
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
        "strategy_list": _normalize_strategy_list(strategy_source),
    }


def fetch_cw_guide_list(
    *,
    page: int,
    limit: int,
    trait: str | None = None,
    trait_id: int | None = None,
    role: str | list[str] | None = None,
    role_id: str | list[str] | None = None,
    order: str | None = None,
    next_page_token: str | None = None,
    match_change_job: bool | None = None,
    match_hard: bool | None = None,
    portal: str | list[str] | None = None,
    portal_id: str | list[str] | None = None,
    timeout: int = 10,
    workspace_root: str | Path | None = None,
) -> dict:
    has_portal_filter = portal is not None or portal_id is not None
    if has_portal_filter and (page > 1 or next_page_token):
        raise TrailError(
            "GUIDE_PORTAL_PAGINATION_UNSUPPORTED",
            "guide portal filter only supports first page without next_page_token",
        )

    raw_config: dict[str, object] | None = None
    needs_config = has_portal_filter or trait is not None or role is not None
    if needs_config:
        raw_config = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)

    resolved_trait_id = trait_id
    if trait is not None and raw_config is not None:
        resolved_trait_id = _resolve_guide_trait_id(raw_config=raw_config, trait=trait, trait_id=trait_id)

    role_ids = _normalize_string_filters(role_id, option_name="role")
    role_candidates: list[dict[str, object]] = []
    role_warnings: list[dict[str, object]] = []
    if role is not None and raw_config is not None:
        role_resolution = _resolve_guide_role_filters(raw_config=raw_config, role=role)
        role_ids = list(role_resolution["role_ids"])
        role_candidates = list(role_resolution["role_candidates"])
        role_warnings = list(role_resolution["role_warnings"])

    portal_filters: list[dict[str, str]] = []
    if has_portal_filter:
        portal_filters = _resolve_guide_portal_filters(
            portal_list=_normalize_portal_list(raw_config.get("portal_list") if raw_config is not None else None),
            portal=portal,
            portal_id=portal_id,
        )

    if portal_filters:
        result = _fetch_cw_guide_list_for_portals(
            portal_filters=portal_filters,
            limit=limit,
            trait_id=resolved_trait_id,
            role_ids=role_ids,
            order=order,
            match_change_job=match_change_job,
            match_hard=match_hard,
            timeout=timeout,
        )
    else:
        data = _fetch_cw_guide_list_data(
            page=page,
            limit=limit,
            trait_id=resolved_trait_id,
            role_ids=role_ids,
            order=order,
            next_page_token=next_page_token,
            match_change_job=match_change_job,
            match_hard=match_hard,
            timeout=timeout,
        )
        lineup_list = data.get("list")
        result = {
            "list": [_normalize_lineup_summary(item) for item in lineup_list] if isinstance(lineup_list, list) else [],
            "next_page_token": data.get("next_page_token"),
        }

    if role_candidates:
        result["role_candidates"] = role_candidates
    if role_warnings:
        result["role_warnings"] = role_warnings
    return result


def _normalize_portal_values(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise TrailError("GUIDE_INPUT_INVALID", "guide portal filter must be a string or list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TrailError("GUIDE_INPUT_INVALID", "guide portal filter must be a string or list of strings")
        result.append(item)
    return result


def _resolve_guide_portal_filters(
    *,
    portal_list: list[dict[str, str]],
    portal: str | list[str] | None,
    portal_id: str | list[str] | None,
) -> list[dict[str, str]]:
    portal_values = _normalize_portal_values(portal)
    portal_id_values = _normalize_portal_values(portal_id)
    if portal_values and portal_id_values:
        raise TrailError("GUIDE_INPUT_INVALID", "guide options '--portal' and '--portal-id' are mutually exclusive")

    resolved: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    if portal_values:
        for value in portal_values:
            item = _resolve_guide_portal_filter(portal_list=portal_list, portal=value, portal_id=None)
            if item["portal_id"] in seen_ids:
                continue
            resolved.append(item)
            seen_ids.add(item["portal_id"])
    else:
        for value in portal_id_values:
            item = _resolve_guide_portal_filter(portal_list=portal_list, portal=None, portal_id=value)
            if item["portal_id"] in seen_ids:
                continue
            resolved.append(item)
            seen_ids.add(item["portal_id"])
    return resolved


def _fetch_cw_guide_list_for_portals(
    *,
    portal_filters: list[dict[str, str]],
    limit: int,
    trait_id: int | None,
    role_ids: list[str],
    order: str | None,
    match_change_job: bool | None,
    match_hard: bool | None,
    timeout: int,
) -> dict:
    grouped = {
        portal["portal_id"]: {
            "portal_title": portal["title"],
            "list": [],
            "more": False,
            "next_page_token": None,
        }
        for portal in portal_filters
    }
    next_page_token: str | None = None
    pages_scanned = 0

    while pages_scanned < CW_GUIDE_PORTAL_MAX_PAGES:
        data = _fetch_cw_guide_list_data(
            page=1,
            limit=CW_GUIDE_UPSTREAM_PAGE_SIZE,
            trait_id=trait_id,
            role_ids=role_ids,
            order=order,
            next_page_token=next_page_token,
            match_change_job=match_change_job,
            match_hard=match_hard,
            timeout=timeout,
        )
        lineup_list = data.get("list") if isinstance(data.get("list"), list) else []
        if not lineup_list:
            next_page_token = None
            break

        normalized_items = [item for item in lineup_list if isinstance(item, Mapping)]
        for item in normalized_items:
            normalized = _normalize_lineup_summary(item)
            for portal_filter in portal_filters:
                bucket = grouped[portal_filter["portal_id"]]["list"]
                if len(bucket) >= limit:
                    continue
                if _matches_lineup_portal(item, portal=portal_filter):
                    bucket.append(normalized)

        pages_scanned += 1
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break
        if all(len(grouped[portal["portal_id"]]["list"]) >= limit for portal in portal_filters):
            break

    has_more = bool(next_page_token) and pages_scanned < CW_GUIDE_PORTAL_MAX_PAGES
    result_groups = []
    total_count = 0
    for portal_filter in portal_filters:
        group = grouped[portal_filter["portal_id"]]
        items = group["list"][:limit]
        total_count += len(items)
        result_groups.append(
            {
                "portal_title": group["portal_title"],
                "list": items,
                "more": has_more,
                "next_page_token": next_page_token if has_more else None,
            }
        )

    if len(result_groups) == 1:
        only = result_groups[0]
        return {
            "list": only["list"],
            "next_page_token": only["next_page_token"],
        }

    return {
        "portals": result_groups,
        "count": total_count,
        "more": has_more,
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
        "operation_guide": str(lineup.get("description") or ""),
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


def apply_cw_guide(session: SessionModel, guide_data: dict, *, reset_dependent_state: bool = True) -> SessionModel:
    guide_payload = normalize_cw_guide_payload(guide_data)
    cw_state = ensure_cw_state(session)
    cw_state["guide"] = deepcopy(guide_payload)
    cw_state["guide"].pop("artifact", None)
    cw_state["guide"].pop("artifact_id", None)
    cw_state["guide"].pop("on_field", None)
    cw_state["guide"].pop("off_field", None)
    cw_state["guide"].pop("remaining_purchases", None)
    cw_state["constraints"] = {
        "min_coins": guide_payload.get("min_coins", 40),
        "min_level": guide_payload.get("min_level", 7),
        "mid_level": guide_payload.get("mid_level", 7),
        "priority": guide_payload.get("priority", {}),
        "positioning": guide_payload.get("positioning", {}),
    }
    if reset_dependent_state:
        invalidate_cw_guide_runtime_state(session)
    return session


def select_cw_guide(session: SessionModel, *, guide_data: dict) -> SessionModel:
    return apply_cw_guide(session, guide_data=guide_data, reset_dependent_state=False)


def require_complete_cw_guide(cw_state: dict) -> dict:
    if not isinstance(cw_state, dict):
        raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    guide = cw_state.get("guide")
    if not isinstance(guide, dict):
        raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    for key in ("lineup_id", "title", "share_code"):
        value = guide.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    if SHARE_CODE_PATTERN.fullmatch(guide["share_code"]) is None:
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略码无效，请重新执行 guide.fetch.cw --select")
    if "version" not in guide or not isinstance(guide.get("version"), str):
        raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    if "operation_guide" not in guide or not isinstance(guide.get("operation_guide"), str):
        raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    for list_key in ("role_stages", "first_fight_augments", "second_fight_augments", "order_basic", "order_compose"):
        if not isinstance(guide.get(list_key), list):
            raise TrailError("CW_GUIDE_STATE_INVALID", CW_GUIDE_STATE_INVALID_MESSAGE)
    constraints = cw_state.get("constraints")
    if not isinstance(constraints, dict) or any(key not in constraints for key in ("min_coins", "min_level", "mid_level")):
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略约束不完整，请重新执行 guide.fetch.cw --select")
    if "artifact" in guide or "artifact_id" in guide:
        raise TrailError("CW_GUIDE_STATE_INVALID", "当前攻略来自旧 artifact 状态，请重新执行 guide.fetch.cw --select")
    return guide


def complete_cw_guide_or_none(cw_state: dict) -> dict | None:
    try:
        return require_complete_cw_guide(cw_state)
    except TrailError as error:
        if error.code == "CW_GUIDE_STATE_INVALID":
            return None
        raise


def invalidate_cw_guide_runtime_state(session: SessionModel) -> SessionModel:
    defaults = CwSceneState().model_dump()
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = defaults["slots"]
    cw_state["sell_plan"] = defaults["sell_plan"]
    cw_state["shop"] = defaults["shop"]
    _replace_stage_fields(session, **defaults["stage"])
    return session
