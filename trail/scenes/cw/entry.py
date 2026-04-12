from __future__ import annotations

from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel


def enter_cw(
    session: SessionModel,
    *,
    mode: str,
    difficulty: str | None = None,
    battle_mode: str | None = None,
) -> SessionModel:
    cw_state = ensure_cw_state(session)
    cw_state["entry"] = {
        "mode": mode,
        "difficulty": difficulty or "current",
        "battle_mode": battle_mode or "standard",
    }
    cw_state["stage"] = {"stale": True}
    return session
