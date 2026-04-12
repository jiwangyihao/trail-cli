from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trail.artifacts.models import ArtifactMeta
from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.scenes.cw.models import CwSceneState, ensure_cw_state
from trail.session.models import SessionModel


CW_GUIDE_DETAIL_API = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
CW_GUIDE_GIDS = 6
CW_GUIDE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.miyoushe.com/",
}
ARTICLE_ID_PATTERN = re.compile(r"(?:/article/|post_id=)(\d+)")
SHARE_CODE_PATTERN = re.compile(r"##[^#\s]+##")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def normalize_cw_guide_payload(guide_data: dict) -> dict:
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
    return payload


def _extract_article_id(url: str) -> str:
    match = ARTICLE_ID_PATTERN.search(url)
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


def _fetch_miyoushe_post(url: str, *, timeout: int = 10) -> dict:
    article_id = _extract_article_id(url)
    query = urlencode({"gids": CW_GUIDE_GIDS, "post_id": article_id, "read": 1})
    request = Request(f"{CW_GUIDE_DETAIL_API}?{query}", headers=CW_GUIDE_HEADERS)
    payload = _read_json_response(request, timeout=timeout)
    if payload.get("retcode") != 0:
        raise TrailError("GUIDE_FETCH_FAILED", f"guide fetch failed: {payload.get('message', 'unknown error')}")

    try:
        return payload["data"]["post"]
    except KeyError as exc:
        raise TrailError("GUIDE_FETCH_FAILED", "guide fetch response missing post data") from exc


def _extract_post_text(post: dict) -> str:
    post_data = post.get("post") or {}
    structured_content = post_data.get("structured_content")
    if isinstance(structured_content, str) and structured_content:
        try:
            operations = json.loads(structured_content)
        except json.JSONDecodeError:
            operations = []
        parts: list[str] = []
        for operation in operations:
            insert = operation.get("insert")
            if isinstance(insert, str):
                parts.append(insert)
        if parts:
            return "".join(parts)

    content = post_data.get("content", "")
    stripped = HTML_TAG_PATTERN.sub("\n", content)
    return html.unescape(stripped)


def _extract_share_code(text: str, *, source_url: str) -> str:
    match = SHARE_CODE_PATTERN.search(text)
    if match is None:
        raise TrailError("GUIDE_SHARE_CODE_NOT_FOUND", f"guide share code not found: {source_url}")
    return match.group(0)


def fetch_cw_guide_payload(url: str) -> dict:
    post = _fetch_miyoushe_post(url)
    post_data = post.get("post") or {}
    user_data = post.get("user") or {}
    text = _extract_post_text(post)
    payload = {
        "scene": "cw",
        "kind": "guide",
        "source_url": url,
        "article_id": str(post_data.get("post_id", _extract_article_id(url))),
        "title": str(post_data.get("subject") or "货币战争攻略码"),
        "author": str(user_data.get("nickname") or ""),
        "uploader": str(user_data.get("nickname") or ""),
        "share_code": _extract_share_code(text, source_url=url),
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 7,
        "on_field": {},
        "off_field": {},
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
        raise TrailError("GUIDE_PAYLOAD_INVALID", f"guide payload invalid: {path}") from exc
    except OSError as exc:
        raise TrailError("GUIDE_PAYLOAD_INVALID", f"guide payload unreadable: {path}") from exc


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
    except ValueError as exc:
        raise TrailError("GUIDE_INPUT_INVALID", f"guide artifact id invalid: {value}") from exc

    payload = _read_guide_payload(meta.path)
    payload["artifact_id"] = meta.artifact_id
    payload.setdefault("scene", meta.scene)
    payload.setdefault("kind", meta.kind)
    return normalize_cw_guide_payload(payload)


def apply_cw_guide(session: SessionModel, guide_data: dict) -> SessionModel:
    guide_payload = normalize_cw_guide_payload(guide_data)
    cw_state = ensure_cw_state(session)
    defaults = CwSceneState().model_dump()
    cw_state["guide"] = {
        "artifact": guide_payload.get("artifact_id"),
        "share_code": guide_payload["share_code"],
        "remaining_purchases": {
            **guide_payload.get("on_field", {}),
            **guide_payload.get("off_field", {}),
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
    cw_state["shop"] = defaults["shop"]
    cw_state["stage"] = defaults["stage"]
    return session
