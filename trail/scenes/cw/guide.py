from __future__ import annotations

from collections.abc import Mapping
import ctypes
import html
import json
import re
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trail.artifacts.models import ArtifactMeta
from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
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

CW_WIDTH = 1920
CW_HEIGHT = 1080
GUIDE_INPUT_POINT = (int(CW_WIDTH * 0.5), int(CW_HEIGHT * 0.5))
GUIDE_ESC_PRESSES = 3
GUIDE_ESC_INTERVAL = 1.0
GUIDE_UI_WAIT_TIMEOUT = 10
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


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


def _copy_text_to_clipboard(text: str) -> None:
    user32 = getattr(ctypes, "windll", None)
    if user32 is None or not hasattr(user32, "user32") or not hasattr(user32, "kernel32"):
        raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "clipboard backend unavailable")

    user32_lib = user32.user32
    kernel32_lib = user32.kernel32
    handle = None
    if user32_lib.OpenClipboard(None) == 0:
        raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "failed to open clipboard")

    try:
        if user32_lib.EmptyClipboard() == 0:
            raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "failed to clear clipboard")

        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32_lib.GlobalAlloc(GMEM_MOVEABLE, size)
        if handle == 0:
            raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "failed to allocate clipboard buffer")

        locked = kernel32_lib.GlobalLock(handle)
        if locked == 0:
            raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "failed to lock clipboard buffer")

        try:
            ctypes.memmove(locked, ctypes.addressof(buffer), size)
        finally:
            kernel32_lib.GlobalUnlock(handle)

        if user32_lib.SetClipboardData(CF_UNICODETEXT, handle) == 0:
            raise TrailError("GUIDE_CLIPBOARD_UNAVAILABLE", "failed to set clipboard text")
        handle = None
    finally:
        if handle:
            kernel32_lib.GlobalFree(handle)
        user32_lib.CloseClipboard()


def _paste_clipboard_text() -> None:
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        raise TrailError("INPUT_BACKEND_UNAVAILABLE", "pyautogui backend unavailable") from exc

    pyautogui.hotkey("ctrl", "v")


def apply_cw_guide_via_ui(runtime, *, share_code: str) -> None:
    _click_required_template(runtime, alias="guide.strategy")
    _click_required_template(runtime, alias="guide.enter_code")
    runtime.click_point(*GUIDE_INPUT_POINT)
    _copy_text_to_clipboard(share_code)
    _paste_clipboard_text()
    sleep(0.2)
    _click_required_template(runtime, alias="guide.confirm")
    _click_required_template(runtime, alias="guide.apply")
    runtime.press_key("esc", presses=GUIDE_ESC_PRESSES, interval=GUIDE_ESC_INTERVAL)


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

    operations: list[object] = []
    if isinstance(structured_content, str) and structured_content:
        try:
            structured_content = json.loads(structured_content)
        except json.JSONDecodeError:
            structured_content = None

    if isinstance(structured_content, dict):
        maybe_ops = structured_content.get("ops")
        if isinstance(maybe_ops, list):
            operations = maybe_ops
    elif isinstance(structured_content, list):
        operations = structured_content

    if operations:
        parts: list[str] = []
        for operation in operations:
            if not isinstance(operation, dict):
                continue
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
    cw_state["sell_plan"] = defaults["sell_plan"]
    cw_state["shop"] = defaults["shop"]
    cw_state["stage"] = defaults["stage"]
    return session
