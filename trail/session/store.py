from __future__ import annotations

from copy import deepcopy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from trail.session.models import SessionModel


SESSION_ID_PATTERN = re.compile(r"[a-f0-9]{32}")


def _workspace_root_for_path(path_value: Path | str) -> Path:
    path = Path(path_value)
    if path.parent.name == ".trail":
        return path.parent.parent
    return path


class SessionStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _workspace_root(self) -> Path:
        return _workspace_root_for_path(self.workspace)

    def _rebase_legacy_last_screenshot(self, payload: dict) -> dict:
        legacy_workspace = payload.get("workspace")
        last_screenshot = payload.get("last_screenshot")
        if not isinstance(legacy_workspace, str) or not isinstance(last_screenshot, str):
            return payload

        legacy_workspace_path = Path(legacy_workspace)
        screenshot_path = Path(last_screenshot)
        if not legacy_workspace_path.is_absolute() or not screenshot_path.is_absolute():
            return payload

        try:
            payload["last_screenshot"] = screenshot_path.resolve().relative_to(
                _workspace_root_for_path(legacy_workspace_path).resolve()
            ).as_posix()
        except ValueError:
            return payload
        return payload

    def _validate_session_id(self, session_id: str) -> str:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("invalid session_id")
        return session_id

    def _path_for(self, session_id: str) -> Path:
        return self.workspace / f"{self._validate_session_id(session_id)}.json"

    def create(self, *, window_binding: dict) -> SessionModel:
        session = SessionModel(
            session_id=uuid4().hex,
            workspace=self.workspace,
            window_binding=deepcopy(window_binding),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self.save(session)

    def load(self, session_id: str) -> SessionModel:
        path = self._path_for(session_id)
        payload = self._rebase_legacy_last_screenshot(json.loads(path.read_text(encoding="utf-8")))
        if payload.get("session_id") != path.stem:
            raise ValueError("session_id mismatch")
        session = SessionModel.from_dict(payload, workspace_root=self._workspace_root())
        session.workspace = self.workspace
        return session

    def save(self, session: SessionModel) -> SessionModel:
        safe_session_id = self._validate_session_id(session.session_id)
        session.workspace = self.workspace
        path = self.workspace / f"{safe_session_id}.json"
        path.write_text(
            json.dumps(session.to_dict(workspace_root=self._workspace_root()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return session
