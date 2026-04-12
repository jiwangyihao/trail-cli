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


def build_cw_stage_detector(runtime):
    templates = tuple((value, str(resolve_scene_asset("cw", alias))) for alias, value in STAGE_RESOURCE_ALIASES)

    def detector() -> str | None:
        for value, template in templates:
            if runtime.locate(template) is not None:
                return value
        return None

    return detector


def detect_cw_stage(session: SessionModel, *, detector) -> SessionModel:
    stage = detector()
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
