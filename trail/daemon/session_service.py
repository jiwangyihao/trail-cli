from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock

from trail.core.errors import TrailError
from trail.session.store import SessionStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


RISKY_FINAL_STATES = {"applied_but_not_persisted", "persisted_but_response_unknown"}


def _normalize_workspace_root(workspace_root: Path | str) -> Path:
    return Path(os.path.normpath(str(Path(workspace_root).resolve(strict=False))))


def _workspace_registry_key(workspace_root: Path | str) -> str:
    return os.path.normcase(str(_normalize_workspace_root(workspace_root)))


class SessionService:
    def __init__(self, *, workspace_root: Path):
        self.workspace_root = _normalize_workspace_root(workspace_root)
        self._store = SessionStore(self.workspace_root / ".trail" / "sessions")
        self._journal_root = self.workspace_root / ".trail" / "requests"
        self._journal_root.mkdir(parents=True, exist_ok=True)
        self._mutex = Lock()

    def _journal_path(self, request_id: str) -> Path:
        return self._journal_root / f"{request_id}.json"

    def _load_record(self, request_id: str) -> dict | None:
        path = self._journal_path(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_record(self, record: dict) -> dict:
        snapshot = deepcopy(record)
        self._journal_path(snapshot["request_id"]).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshot

    def _require_record(self, *, request_id: str) -> dict:
        record = self._load_record(request_id)
        if record is None:
            raise TrailError("REQUEST_NOT_FOUND", f"request not found: {request_id}")
        return record

    def _update_stage(self, *, request_id: str, stage: str) -> None:
        with self._mutex:
            record = self._require_record(request_id=request_id)
            record["last_visible_stage"] = stage
            record["updated_at"] = _utc_now()
            self._save_record(record)

    def create_session(self, *, window_binding: dict):
        return self._store.create(window_binding=window_binding)

    def load_session(self, session_id: str):
        return self._store.load(session_id)

    def save_session(self, session) -> None:
        self._store.save(session)

    def dump_state(self, *, session_id: str) -> dict:
        return self.load_session(session_id).to_dict()

    def begin_mutation(self, *, session_id: str | None, request_id: str, command_name: str) -> dict:
        with self._mutex:
            record = self._load_record(request_id)
            if record is not None:
                if record.get("session_id") != session_id or record.get("method") != command_name:
                    return {"status": "request_id_conflict", "record": deepcopy(record)}
                if record.get("final_state"):
                    return {"status": "duplicate_terminal", "record": deepcopy(record)}
                return {"status": "duplicate_in_progress", "record": deepcopy(record)}

            now = _utc_now()
            fresh = {
                "request_id": request_id,
                "method": command_name,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "final_state": None,
                "last_visible_stage": "accepted",
                "started_at": now,
                "updated_at": now,
            }
            return {"status": "accepted", "record": self._save_record(fresh)}

    def mark_executing(self, *, request_id: str, session_id: str | None, command_name: str) -> None:
        del session_id, command_name
        self._update_stage(request_id=request_id, stage="executing")

    def mark_side_effect_applied(self, *, request_id: str, session_id: str | None, command_name: str) -> None:
        del session_id, command_name
        self._update_stage(request_id=request_id, stage="side_effect_applied")

    def mark_state_persisted(self, *, request_id: str, session_id: str | None, command_name: str) -> None:
        del session_id, command_name
        self._update_stage(request_id=request_id, stage="state_persisted")

    def finish_mutation(
        self,
        *,
        session_id: str | None,
        request_id: str,
        command_name: str,
        final_state: str,
        envelope: dict,
    ) -> None:
        with self._mutex:
            record = self._require_record(request_id=request_id)
            record["method"] = command_name
            record["final_state"] = final_state
            record["last_visible_stage"] = "responded"
            record["updated_at"] = _utc_now()
            record["last_envelope"] = deepcopy(envelope)
            record["last_result"] = {
                "command": command_name,
                "ok": envelope["ok"],
                "data": deepcopy(envelope["data"]),
                "error": deepcopy(envelope["error"]),
            }

            risky_final_state = final_state in RISKY_FINAL_STATES
            if risky_final_state:
                self._save_record(record)

            if session_id is not None:
                try:
                    session = self.load_session(session_id)
                    if risky_final_state:
                        session.scene_state.setdefault("daemon", {})["tainted"] = True
                    session.last_result = deepcopy(record["last_result"])
                    screenshot = envelope.get("screenshot")
                    if screenshot:
                        session.last_screenshot = screenshot
                    self.save_session(session)
                except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                    if not risky_final_state:
                        raise

            if not risky_final_state:
                self._save_record(record)

    def request_status(self, request_id: str) -> dict:
        record = self._require_record(request_id=request_id)
        tainted = False
        session_id = record.get("session_id")
        if session_id is not None:
            try:
                session = self.load_session(session_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                tainted = record.get("final_state") in RISKY_FINAL_STATES
            else:
                tainted = bool(session.scene_state.get("daemon", {}).get("tainted", False))
        return {
            "request_id": record["request_id"],
            "method": record["method"],
            "workspace_root": record["workspace_root"],
            "session_id": record.get("session_id"),
            "final_state": record.get("final_state"),
            "last_visible_stage": record["last_visible_stage"],
            "tainted": tainted,
            "started_at": record["started_at"],
            "updated_at": record["updated_at"],
        }

    def reconcile_session(self, session_id: str) -> dict:
        session = self.load_session(session_id)
        session.scene_state.setdefault("daemon", {})["tainted"] = False
        self.save_session(session)
        return {"session_id": session_id, "tainted": False}


class SessionServiceRegistry:
    def __init__(self):
        self._services: dict[str, SessionService] = {}
        self._mutex = Lock()

    def for_workspace(self, workspace_root: str) -> SessionService:
        key = _workspace_registry_key(workspace_root)
        with self._mutex:
            if key not in self._services:
                self._services[key] = SessionService(workspace_root=_normalize_workspace_root(workspace_root))
            return self._services[key]
