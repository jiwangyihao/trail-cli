from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


def _to_workspace_relative(path_value: Path | str | None, *, workspace_root: Path | None) -> str | None:
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        return path.as_posix()
    if workspace_root is None:
        return str(path)
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _from_workspace_relative(path_value: Path | str, *, workspace_root: Path | None) -> Path:
    path = Path(path_value)
    if workspace_root is None or path.is_absolute():
        return path
    return workspace_root / path


@dataclass(slots=True)
class SessionModel:
    session_id: str
    workspace: Path
    window_binding: JsonDict
    created_at: str
    updated_at: str | None = None
    scene_state: dict[str, JsonDict] = field(default_factory=dict)
    last_result: JsonDict | None = None
    last_screenshot: str | None = None
    last_stage: JsonDict | None = None

    def to_dict(self, *, workspace_root: Path | None = None) -> JsonDict:
        return {
            "session_id": self.session_id,
            "workspace": _to_workspace_relative(self.workspace, workspace_root=workspace_root),
            "window_binding": self.window_binding,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "scene_state": self.scene_state,
            "last_result": self.last_result,
            "last_screenshot": _to_workspace_relative(self.last_screenshot, workspace_root=workspace_root),
            "last_stage": self.last_stage,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict, *, workspace_root: Path | None = None) -> SessionModel:
        return cls(
            session_id=str(payload["session_id"]),
            workspace=_from_workspace_relative(payload["workspace"], workspace_root=workspace_root),
            window_binding=dict(payload["window_binding"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]) if payload.get("updated_at") is not None else None,
            scene_state=dict(payload.get("scene_state") or {}),
            last_result=payload.get("last_result"),
            last_screenshot=_to_workspace_relative(payload.get("last_screenshot"), workspace_root=workspace_root),
            last_stage=payload.get("last_stage"),
        )
