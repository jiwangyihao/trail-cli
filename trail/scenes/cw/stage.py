from __future__ import annotations

from time import monotonic, sleep

from trail.core.errors import TrailError
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


def _invalidate_cw_stage(session: SessionModel, *, code: str, message: str) -> None:
    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {
        "stale": True,
        "error": {"code": code, "message": message},
    }
    session.last_stage = None


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


def _detect_cw_stage_from_ocr(runtime) -> str | None:
    ocr = getattr(runtime, "ocr", None)
    if not callable(ocr):
        return None

    pieces = ocr() or []
    text = "".join(_read_ocr_piece(piece).strip() for piece in pieces)
    if any(keyword in text for keyword in SETTLE_OCR_KEYWORDS):
        return "settle"
    return None


def build_cw_stage_detector(runtime):
    grouped_templates: dict[str, list[str]] = {}
    for alias, value in STAGE_RESOURCE_ALIASES:
        template = str(resolve_scene_asset("cw", alias))
        grouped_templates.setdefault(template, []).append(value)

    templates = tuple((tuple(values), template) for template, values in grouped_templates.items())

    def detector() -> str | None:
        for values, template in templates:
            if runtime.locate(template) is not None:
                if len(values) == 1:
                    return values[0]
                raise TrailError("STAGE_AMBIGUOUS", f"当前资源无法区分阶段: {', '.join(values)}")
        ocr_stage = _detect_cw_stage_from_ocr(runtime)
        if ocr_stage is not None:
            return ocr_stage
        return None

    return detector


def detect_cw_stage(session: SessionModel, *, detector) -> SessionModel:
    try:
        stage = detector()
    except TrailError as exc:
        if exc.code == "STAGE_AMBIGUOUS":
            _invalidate_cw_stage(session, code=exc.code, message=str(exc))
        raise

    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {"value": stage, "stale": False}
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
