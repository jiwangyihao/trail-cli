from __future__ import annotations

from time import monotonic

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


def _invalidate_cw_stage(session: SessionModel, *, code: str, message: str) -> None:
    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {
        "stale": True,
        "error": {"code": code, "message": message},
    }
    session.last_stage = None


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


def wait_cw_stage(session: SessionModel, *, detector, timeout: int) -> SessionModel:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        session = detect_cw_stage(session, detector=detector)
        if session.scene_state["cw"]["stage"]["value"]:
            return session
    raise TrailError("STAGE_TIMEOUT", "等待货币战争阶段超时")
