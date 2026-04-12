from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class SessionModel:
    session_id: str
    workspace: Path
    window_binding: JsonDict
    created_at: str
    scene_state: dict[str, JsonDict] = field(default_factory=dict)
    last_result: JsonDict | None = None
    last_screenshot: str | None = None
    last_stage: JsonDict | None = None

    def to_dict(self) -> JsonDict:
        return {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "window_binding": self.window_binding,
            "created_at": self.created_at,
            "scene_state": self.scene_state,
            "last_result": self.last_result,
            "last_screenshot": self.last_screenshot,
            "last_stage": self.last_stage,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> SessionModel:
        return cls(
            session_id=str(payload["session_id"]),
            workspace=Path(payload["workspace"]),
            window_binding=dict(payload["window_binding"]),
            created_at=str(payload["created_at"]),
            scene_state=dict(payload.get("scene_state") or {}),
            last_result=payload.get("last_result"),
            last_screenshot=payload.get("last_screenshot"),
            last_stage=payload.get("last_stage"),
        )
