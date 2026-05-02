from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from time import monotonic, sleep
from typing import Any

from trail.core.errors import TrailError
from trail.runtime.batch_locate import BatchLocateTarget, run_batch_locate
from trail.runtime.ocr_config import OcrRequestConfig
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel


STAGE_RESOURCE_ALIASES: tuple[tuple[str, str], ...] = (
    ("stage.preparation", "preparation"),
    ("stage.shop", "shop"),
    ("stage.replenish", "replenish"),
    ("stage.encounter", "encounter"),
    ("stage.invest", "invest"),
    ("stage.boss_preview", "boss_preview"),
    ("stage.fortune", "fortune"),
    ("stage.event", "event"),
    ("stage.settle", "settle"),
    ("stage.game_over", "game_over"),
)

SETTLE_OCR_KEYWORDS: tuple[str, ...] = ("继续挑战", "挑战成功", "挑战失败")
STAGE_WAIT_INTERVAL_SECONDS = 0.5
CW_STATUS_LEVEL_REGION = {"from_x": 220, "from_y": 880, "to_x": 360, "to_y": 950}
CW_STATUS_EXP_REGION = {"from_x": 256, "from_y": 943, "to_x": 325, "to_y": 973}
CW_STATUS_TEAM_SIZE_REGION = {"from_x": 835, "from_y": 188, "to_x": 1095, "to_y": 281}


def _stage_state(session: SessionModel) -> dict:
    cw_state = ensure_cw_state(session)
    stage = cw_state.get("stage")
    if not isinstance(stage, dict):
        stage = {"stale": True}
        cw_state["stage"] = stage
    return stage


def _replace_stage_fields(session: SessionModel, **fields) -> None:
    current = dict(_stage_state(session))
    old_status = current.get("status")
    current.update(fields)
    if "status" in fields:
        status = fields["status"]
    else:
        status = old_status
    if isinstance(status, dict):
        current["status"] = deepcopy(status)
    elif isinstance(current.get("status"), dict):
        current["status"] = deepcopy(current["status"])
    ensure_cw_state(session)["stage"] = current


def mark_cw_stage_status_stale(session: SessionModel) -> None:
    stage = _stage_state(session)
    status = dict(stage.get("status") if isinstance(stage.get("status"), dict) else {})
    status["stale"] = True
    stage["status"] = status


def mark_cw_stage_stale(session: SessionModel) -> None:
    current = _stage_state(session)
    status = current.get("status")
    next_stage: dict[str, Any] = {"stale": True}
    if isinstance(status, dict):
        next_stage["status"] = deepcopy(status)
    ensure_cw_state(session)["stage"] = next_stage
    session.last_stage = None


def _invalidate_cw_stage(session: SessionModel, *, code: str, message: str) -> None:
    mark_cw_stage_stale(session)
    _replace_stage_fields(session, stale=True, error={"code": code, "message": message})


def _read_ocr_piece(item: object) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], str | int | float):
            return str(item[1])
        for value in item:
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(item, str | int | float):
        return str(item)
    return ""


def _read_ocr_box(item: Any) -> tuple[float, float, float, float] | None:
    if isinstance(item, Mapping):
        raw_box = item.get("box") or item.get("polygon") or item.get("points") or item
    elif isinstance(item, (list, tuple)) and item:
        raw_box = item[0]
    else:
        raw_box = getattr(item, "box", None)
    return _normalize_ocr_box(raw_box)


def _normalize_ocr_box(raw_box: Any) -> tuple[float, float, float, float] | None:
    if isinstance(raw_box, Mapping):
        try:
            left = float(raw_box.get("left", raw_box.get("x")))
            top = float(raw_box.get("top", raw_box.get("y")))
        except (TypeError, ValueError):
            return None
        width = raw_box.get("width")
        height = raw_box.get("height")
        try:
            if width is not None and height is not None:
                return left, top, left + float(width), top + float(height)
            return left, top, float(raw_box["right"]), float(raw_box["bottom"])
        except (KeyError, TypeError, ValueError):
            return None

    if isinstance(raw_box, (list, tuple)):
        points: list[tuple[float, float]] = []
        for raw_point in raw_box:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                return None
            try:
                points.append((float(raw_point[0]), float(raw_point[1])))
            except (TypeError, ValueError):
                return None
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return min(xs), min(ys), max(xs), max(ys)

    try:
        left = float(getattr(raw_box, "left", getattr(raw_box, "x", None)))
        top = float(getattr(raw_box, "top", getattr(raw_box, "y", None)))
    except (TypeError, ValueError):
        return None
    width = getattr(raw_box, "width", None)
    height = getattr(raw_box, "height", None)
    right = getattr(raw_box, "right", None)
    bottom = getattr(raw_box, "bottom", None)
    try:
        if width is not None and height is not None:
            return left, top, left + float(width), top + float(height)
        if right is not None and bottom is not None:
            return left, top, float(right), float(bottom)
    except (TypeError, ValueError):
        return None
    return None


def _parse_cw_stage_level_value(text: str) -> int | None:
    if not re.fullmatch(r"[1-9]\d*", text):
        return None
    return int(text)


def parse_cw_stage_level(items: list[Any], *, default: int | None = None) -> int | None:
    normalized_texts: list[str] = []
    for item in items:
        text = _read_ocr_piece(item).strip()
        if text:
            normalized_texts.append(re.sub(r"\s+", "", text))

    for normalized in normalized_texts:
        match = re.fullmatch(r"lv\.?([0-9]+)", normalized, re.IGNORECASE)
        if match is not None:
            value = _parse_cw_stage_level_value(match.group(1))
            if value is not None:
                return value

    for index, normalized in enumerate(normalized_texts[:-1]):
        next_text = normalized_texts[index + 1]
        if re.fullmatch(r"lv[.]?", normalized, re.IGNORECASE):
            value = _parse_cw_stage_level_value(next_text)
            if value is not None:
                return value
            continue
        if re.fullmatch(r"lv[.]?", next_text, re.IGNORECASE):
            value = _parse_cw_stage_level_value(normalized)
            if value is not None:
                return value

    if any("/" in text for text in normalized_texts):
        return default

    digit_values = [_parse_cw_stage_level_value(text) for text in normalized_texts]
    digit_values = [value for value in digit_values if value is not None]
    if len(digit_values) == 1:
        return digit_values[0]

    return default


def _parse_cw_stage_ratio(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_cw_stage_exp_candidate(text: str) -> str | None:
    ratio = _parse_cw_stage_ratio(text)
    if ratio is None:
        return None
    current_exp, max_exp = ratio
    if current_exp < 0 or max_exp <= 0 or max_exp < current_exp:
        return None
    return f"{current_exp}/{max_exp}"


def parse_cw_stage_exp(items: list[Any], *, default: str | None = None) -> str | None:
    normalized_texts: list[str] = []
    for item in items:
        text = _read_ocr_piece(item).strip()
        if text:
            normalized_texts.append(re.sub(r"\s+", "", text))

    for normalized in normalized_texts:
        value = _parse_cw_stage_exp_candidate(normalized)
        if value is not None:
            return value

    return default


def _parse_cw_stage_team_size_candidate(text: str) -> str | None:
    ratio = _parse_cw_stage_ratio(text)
    if ratio is None:
        return None
    current_size, max_size = ratio
    if current_size < 2 or max_size < current_size:
        return None
    return f"{current_size}/{max_size}"


def _team_size_tokens_are_contiguous(boxes: list[tuple[float, float, float, float]]) -> bool:
    if len(boxes) < 2:
        return False

    heights = [bottom - top for _, top, _, bottom in boxes]
    average_height = sum(heights) / len(heights)
    max_center_y_delta = max(4.0, average_height * 0.35)
    centers_y = [((top + bottom) / 2.0) for _, top, _, bottom in boxes]
    if max(centers_y) - min(centers_y) > max_center_y_delta:
        return False

    max_gap = max(6.0, average_height * 0.5)
    previous_left, _, previous_right, _ = boxes[0]
    previous_center_x = (previous_left + previous_right) / 2.0
    for left, _, right, _ in boxes[1:]:
        center_x = (left + right) / 2.0
        if center_x <= previous_center_x:
            return False
        if left - previous_right > max_gap:
            return False
        previous_right = right
        previous_center_x = center_x

    return True


def parse_cw_stage_team_size(items: list[Any], *, default: str | None = None) -> str | None:
    tokens: list[tuple[str, tuple[float, float, float, float] | None]] = []
    for item in items:
        text = _read_ocr_piece(item).strip()
        if text:
            tokens.append((re.sub(r"\s+", "", text), _read_ocr_box(item)))

    for text, _ in tokens:
        value = _parse_cw_stage_team_size_candidate(text)
        if value is not None:
            return value

    for window_size in (2, 3):
        for index in range(len(tokens) - window_size + 1):
            window = tokens[index : index + window_size]
            boxes = [box for _, box in window]
            if not all(box is not None for box in boxes):
                continue
            candidate = "".join(text for text, _ in window)
            value = _parse_cw_stage_team_size_candidate(candidate)
            if value is None:
                continue
            if _team_size_tokens_are_contiguous(boxes):
                return value

    return default


def _stage_status_items(by_key: Mapping[Any, Any], field: str) -> list[Any]:
    result = by_key.get(("stage_status", field))
    if result is None:
        result = by_key.get(field)
    if result is None:
        return []
    pieces = getattr(result, "pieces", None)
    if isinstance(pieces, list):
        return pieces
    if isinstance(pieces, tuple):
        return list(pieces)
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return [text]
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    return []


def parse_cw_stage_status(by_key: Mapping[Any, Any], *, role_count: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {
        "stale": False,
        "level": parse_cw_stage_level(_stage_status_items(by_key, "level"), default=None),
        "exp": parse_cw_stage_exp(_stage_status_items(by_key, "exp"), default=None),
        "team_size": parse_cw_stage_team_size(_stage_status_items(by_key, "team_size"), default=None),
    }
    if role_count is not None:
        status["role_count"] = dict(role_count)
    return status


def _detect_cw_stage_from_ocr(runtime) -> str | None:
    ocr = getattr(runtime, "ocr", None)
    if not callable(ocr):
        return None

    pieces = ocr() or []
    text = "".join(_read_ocr_piece(piece).strip() for piece in pieces)
    if any(keyword in text for keyword in SETTLE_OCR_KEYWORDS):
        return "settle"
    return None


def _detect_cw_stage_from_ocr_image(runtime, image) -> str | None:
    ocr_image = getattr(runtime, "ocr_image", None)
    if not callable(ocr_image):
        return None

    pieces = ocr_image(image, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto")) or []
    text = "".join(_read_ocr_piece(piece).strip() for piece in pieces)
    if any(keyword in text for keyword in SETTLE_OCR_KEYWORDS):
        return "settle"
    return None


def _normalized_ocr_text(items: list[Any]) -> str:
    text = "".join(_read_ocr_piece(item).strip() for item in items)
    return re.sub(r"\s+", "", text)


def _is_layer_transition_from_ocr(items: list[Any]) -> bool:
    text = _normalized_ocr_text(items)
    return "点击空白处继续" in text and "位面" in text and "本场对局首领" not in text


def _is_true_boss_preview_from_ocr(items: list[Any]) -> bool:
    return "本场对局首领" in _normalized_ocr_text(items)


def _read_blank_continue_ocr(runtime, image) -> list[Any]:
    ocr_image = getattr(runtime, "ocr_image", None)
    if not callable(ocr_image):
        return []
    result = ocr_image(image, ocr=OcrRequestConfig(lang="ch")) or []
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    return []


def build_cw_stage_detector(runtime):
    grouped_templates: dict[str, list[str]] = {}
    for alias, value in STAGE_RESOURCE_ALIASES:
        if value == "boss_preview":
            continue
        template = str(resolve_scene_asset("cw", alias))
        grouped_templates.setdefault(template, []).append(value)

    ordered_targets = tuple(
        BatchLocateTarget(key=tuple(values), template=template) for template, values in grouped_templates.items()
    )
    blank_continue_target = BatchLocateTarget(
        key="blank_continue_candidate",
        template=str(resolve_scene_asset("cw", "stage.boss_preview")),
    )

    def detector() -> str | None:
        screenshot = getattr(runtime, "screenshot", None)
        if not callable(screenshot):
            return None
        shared_image = runtime.screenshot()
        locate_result = run_batch_locate(
            runtime,
            (*ordered_targets, blank_continue_target),
            image=shared_image,
            trace_prefix="cw_stage_batch_locate",
        )
        blank_continue_candidate = locate_result.by_key[blank_continue_target.key]
        if blank_continue_candidate.found:
            ocr_items = _read_blank_continue_ocr(runtime, shared_image)
            if _is_true_boss_preview_from_ocr(ocr_items):
                return "boss_preview"
            if _is_layer_transition_from_ocr(ocr_items):
                return "layer_transition"

        for target in ordered_targets:
            result = locate_result.by_key[target.key]
            if not result.found:
                continue
            values = tuple(target.key)
            if len(values) == 1:
                return values[0]
            raise TrailError("STAGE_AMBIGUOUS", f"当前资源无法区分阶段: {', '.join(values)}")
        return _detect_cw_stage_from_ocr_image(runtime, shared_image)

    return detector


def detect_cw_stage(session: SessionModel, *, detector) -> SessionModel:
    try:
        stage = detector()
    except TrailError as exc:
        if exc.code == "STAGE_AMBIGUOUS":
            _invalidate_cw_stage(session, code=exc.code, message=str(exc))
        raise

    _replace_stage_fields(session, value=stage, stale=False)
    session.last_stage = {"scene": "cw", "value": stage}
    return session


def wait_cw_stage(session: SessionModel, *, detector, timeout: int, interval: float = STAGE_WAIT_INTERVAL_SECONDS) -> SessionModel:
    deadline = monotonic() + timeout
    while True:
        session = detect_cw_stage(session, detector=detector)
        if session.scene_state["cw"]["stage"]["value"]:
            return session
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
    raise TrailError("STAGE_TIMEOUT", "等待货币战争阶段超时")
