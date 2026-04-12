from __future__ import annotations

from copy import deepcopy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from trail.session.models import SessionModel


SESSION_ID_PATTERN = re.compile(r"[a-f0-9]{32}")


class SessionStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

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
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("session_id") != path.stem:
            raise ValueError("session_id mismatch")
        payload["workspace"] = str(self.workspace)
        return SessionModel.from_dict(payload)

    def save(self, session: SessionModel) -> SessionModel:
        safe_session_id = self._validate_session_id(session.session_id)
        session.workspace = self.workspace
        path = self.workspace / f"{safe_session_id}.json"
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return session
