from __future__ import annotations

from collections.abc import Mapping
import json
import re
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trail.artifacts.models import ArtifactMeta
from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import CwSceneState, ensure_cw_state
from trail.session.models import SessionModel


CW_GUIDE_DETAIL_API = "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/detail"
CW_GUIDE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "x-rpc-currencywar-tourn": "tourn",
    "x-rpc-platform": "pc",
}
LINEUP_ID_PATTERN = re.compile(r"#/lineup/([^/?]+)")
SHARE_CODE_PATTERN = re.compile(r"##[^#\s]+##")
LINEUP_LEVEL_PATTERN = re.compile(r"(\d+)级搜牌")
PURCHASE_COUNT_BY_STAR = {
    1: 1,
    2: 3,
    3: 9,
}

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


def _wait_for_template_to_clear(runtime, *, alias: str, timeout: float, interval: float) -> None:
    template = str(resolve_scene_asset("cw", alias))
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if runtime.locate(template) is None:
            return
        sleep(interval)
    raise TrailError("GUIDE_APPLY_NOT_CONFIRMED", f"guide ui element still visible: {alias}")


def apply_cw_guide_via_ui(runtime, *, share_code: str) -> None:
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


def _extract_lineup_id(url: str) -> str:
    match = LINEUP_ID_PATTERN.search(url)
    if match is None:
        raise TrailError("GUIDE_URL_INVALID", f"unsupported cw guide url: {url}")
    return match.group(1)


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
    lineup_id = _extract_lineup_id(url)
    request = Request(f"{CW_GUIDE_DETAIL_API}?id={lineup_id}&game=hkrpg", headers=CW_GUIDE_HEADERS)
    payload = _read_json_response(request, timeout=timeout)
    data = payload.get("data")
    lineup = data.get("lineup") if isinstance(data, Mapping) else None
    if payload.get("retcode") != 0 or not isinstance(lineup, Mapping):
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {payload.get('message', 'unknown error')}")
    return lineup_id, dict(lineup)


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
    lineup_id, lineup = _fetch_lineup_detail(url)
    on_field, off_field = _build_roles_from_lineup(lineup)
    min_level = _get_lineup_level(lineup)
    tourn_detail = lineup.get("tourn_detail") if isinstance(lineup.get("tourn_detail"), Mapping) else {}
    payload = {
        "scene": "cw",
        "kind": "guide",
        "source_url": url,
        "lineup_id": lineup_id,
        "title": str(lineup.get("title") or "货币战争攻略码"),
        "author": str(lineup.get("nickname") or ""),
        "uploader": str(lineup.get("nickname") or ""),
        "share_code": _wrap_share_code(tourn_detail.get("share_code"), source_url=url),
        "min_coins": 40,
        "min_level": min_level,
        "mid_level": _get_mid_level(min_level),
        "on_field": on_field,
        "off_field": off_field,
    }
    return payload


def fetch_cw_guide(url: str, *, artifact_store: ArtifactStore, fetcher) -> ArtifactMeta:
    payload = normalize_cw_guide_payload(fetcher(url))
    return artifact_store.create(scene="cw", kind="guide", payload=payload)


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
        "share_code": guide_payload["share_code"],
        "source_url": guide_payload.get("source_url"),
        "article_id": guide_payload.get("article_id"),
        "title": guide_payload.get("title"),
        "author": guide_payload.get("author"),
        "uploader": guide_payload.get("uploader"),
        "on_field": on_field,
        "off_field": off_field,
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
