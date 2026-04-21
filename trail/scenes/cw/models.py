from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from trail.session.models import SessionModel


@dataclass(slots=True)
class CwSceneState:
    guide: dict | None = None
    constraints: dict = field(
        default_factory=lambda: {
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 7,
            "priority": {},
            "positioning": {},
        }
    )
    slots: dict = field(default_factory=lambda: {"stale": True})
    sell_plan: dict = field(default_factory=dict)
    portal: dict = field(
        default_factory=lambda: {
            "cards": [],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": True,
        }
    )
    strategy: dict = field(default_factory=lambda: {"cards": [], "stale": True})
    shop: dict = field(default_factory=lambda: {"stale": True, "team_size": None, "exp": None})
    stage: dict = field(default_factory=lambda: {"stale": True})
    metrics: dict = field(default_factory=dict)

    def model_dump(self) -> dict:
        return {
            "guide": deepcopy(self.guide),
            "constraints": deepcopy(self.constraints),
            "slots": deepcopy(self.slots),
            "sell_plan": deepcopy(self.sell_plan),
            "portal": deepcopy(self.portal),
            "strategy": deepcopy(self.strategy),
            "shop": deepcopy(self.shop),
            "stage": deepcopy(self.stage),
            "metrics": deepcopy(self.metrics),
        }


def ensure_cw_state(session: SessionModel) -> dict:
    defaults = CwSceneState().model_dump()
    cw_state = session.scene_state.setdefault("cw", {})
    for key, value in defaults.items():
        cw_state.setdefault(key, value)
    return cw_state
