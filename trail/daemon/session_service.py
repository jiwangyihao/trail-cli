from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock, local
from typing import Any
from uuid import uuid4

from trail.core.errors import TrailError
from trail.core.jsonable import to_jsonable
from trail.session.store import SessionStore


RISKY_FINAL_STATES = {"applied_but_not_persisted", "persisted_but_response_unknown"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_workspace_root(workspace_root: Path | str) -> Path:
    return Path(os.path.normpath(str(Path(workspace_root).resolve(strict=False))))


def _workspace_registry_key(workspace_root: Path | str) -> str:
    return os.path.normcase(str(_normalize_workspace_root(workspace_root)))


class SessionService:
    def __init__(self, *, workspace_root: Path):
        self.workspace_root = _normalize_workspace_root(workspace_root)
        self._store = SessionStore(self.workspace_root / ".trail" / "sessions")
        self._journal_root = self.workspace_root / ".trail" / "requests"
        self._jobs_root = self.workspace_root / ".trail" / "jobs"
        self._calls_root = self.workspace_root / ".trail" / "calls"
        self._lease_probes: dict[str | None, dict[str, Any]] = {}
        self._lease_assertions: dict[str | None, dict[str, Any]] = {}
        self._journal_root.mkdir(parents=True, exist_ok=True)
        self._jobs_root.mkdir(parents=True, exist_ok=True)
        self._calls_root.mkdir(parents=True, exist_ok=True)
        self._mutex = RLock()
        self._journal_mutex = RLock()
        self._journal_lock_state = local()

    def _journal_path(self, request_id: str) -> Path:
        return self._journal_root / f"{request_id}.json"

    def _job_path(self, job_id: str) -> Path:
        return self._jobs_root / f"{job_id}.json"

    def _call_path(self, call_id: str) -> Path:
        return self._calls_root / f"{call_id}.json"

    def _load_json_record(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _journal_lock_depth(self) -> int:
        return int(getattr(self._journal_lock_state, "depth", 0))

    @contextmanager
    def _journal_locked(self):
        self._journal_mutex.acquire()
        self._journal_lock_state.depth = self._journal_lock_depth() + 1
        try:
            yield
        finally:
            depth = self._journal_lock_depth() - 1
            self._journal_lock_state.depth = depth if depth > 0 else 0
            self._journal_mutex.release()

    def _save_json_atomic(self, path: Path, record: dict) -> dict:
        snapshot = deepcopy(to_jsonable(record))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return snapshot

    def _load_record(self, request_id: str) -> dict | None:
        return self._load_json_record(self._journal_path(request_id))

    def _save_record(self, record: dict) -> dict:
        snapshot = deepcopy(to_jsonable(record))
        if self._journal_lock_depth() > 0:
            return self._save_json_atomic(self._journal_path(snapshot["request_id"]), snapshot)
        with self._journal_locked():
            return self._save_json_atomic(self._journal_path(snapshot["request_id"]), snapshot)

    def _record_tainted(self, record: dict) -> bool:
        if "tainted" in record:
            return bool(record["tainted"])
        return record.get("final_state") in RISKY_FINAL_STATES

    def _apply_terminal_record(self, *, record: dict, command_name: str, final_state: str, envelope: dict, tainted: bool) -> dict:
        envelope_snapshot = to_jsonable(envelope)
        record["method"] = command_name
        record["final_state"] = final_state
        record["last_visible_stage"] = "responded"
        record["updated_at"] = _utc_now()
        record["tainted"] = bool(tainted)
        record["last_envelope"] = deepcopy(envelope_snapshot)
        record["last_result"] = {
            "command": command_name,
            "ok": envelope_snapshot["ok"],
            "data": deepcopy(envelope_snapshot["data"]),
            "error": deepcopy(envelope_snapshot["error"]),
        }
        return record

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
            with self._journal_locked():
                self._save_record(record)

    def create_session(self, *, window_binding: dict):
        with self.session_mutation_lock(None):
            return self._store.create(window_binding=window_binding)

    def load_session(self, session_id: str):
        return self._store.load(session_id)

    def save_session(self, session) -> None:
        with self._mutex:
            self._store.save(session)

    def install_test_lease_probe(
        self,
        session_id: str | None,
        *,
        entered=None,
        release=None,
        fail_if_entered: bool = False,
    ) -> None:
        self._lease_probes[session_id] = {"entered": entered, "release": release, "fail_if_entered": fail_if_entered}

    def install_test_lease_assertion(self, session_id: str | None, *, required_until_call_state: str) -> None:
        self._lease_assertions[session_id] = {"required_until_call_state": required_until_call_state, "active": False}

    @contextmanager
    def session_mutation_lock(self, session_id: str | None):
        probe = self._lease_probes.get(session_id)
        assertion = self._lease_assertions.get(session_id)
        with self._mutex:
            if probe is not None:
                if probe.get("fail_if_entered"):
                    raise AssertionError(f"session mutation lease unexpectedly entered: {session_id}")
                entered = probe.get("entered")
                release = probe.get("release")
                if entered is not None:
                    entered.set()
                if release is not None:
                    release.wait(2)
            if assertion is not None:
                assertion["active"] = True
            try:
                yield
            finally:
                if assertion is not None:
                    assertion["active"] = False

    def _load_job_record(self, job_id: str) -> dict | None:
        return self._load_json_record(self._job_path(job_id))

    def _load_call_record(self, call_id: str) -> dict | None:
        return self._load_json_record(self._call_path(call_id))

    def _save_job_record(self, record: dict) -> dict:
        if self._journal_lock_depth() > 0:
            return self._save_json_atomic(self._job_path(str(record["job_id"])), record)
        with self._journal_locked():
            return self._save_json_atomic(self._job_path(str(record["job_id"])), record)

    def _save_call_record(self, record: dict) -> dict:
        session_id = record.get("session_id")
        assertion = self._lease_assertions.get(session_id)
        if assertion is not None and record.get("state") == assertion.get("required_until_call_state") and not assertion.get("active"):
            raise AssertionError(f"call journal wrote {record.get('state')} outside session mutation lease: {session_id}")
        if self._journal_lock_depth() > 0:
            return self._save_json_atomic(self._call_path(str(record["call_id"])), record)
        with self._journal_locked():
            return self._save_json_atomic(self._call_path(str(record["call_id"])), record)

    def create_job_record(
        self,
        *,
        job_id: str,
        job_key: str,
        method: str,
        payload_digest: str,
        session_id: str | None,
    ) -> dict:
        with self._mutex:
            now = _utc_now()
            record = {
                "job_id": job_id,
                "job_key": job_key,
                "method": method,
                "payload_digest": payload_digest,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "state": "accepted",
                "final": False,
                "final_state": None,
                "last_visible_stage": "accepted",
                "side_effect_stage": "none",
                "tainted": False,
                "started_at": now,
                "updated_at": now,
            }
            return self._save_job_record(record)

    def create_call_record(
        self,
        *,
        call_id: str,
        job_id: str | None,
        method: str,
        session_id: str | None,
        state: str,
        executed: bool,
    ) -> dict:
        with self._mutex:
            now = _utc_now()
            job = self._load_job_record(job_id) if job_id else None
            record = {
                "call_id": call_id,
                "job_id": job_id,
                "method": method,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "state": job.get("state", state) if job else state,
                "executed": bool(executed),
                "final": bool(job.get("final", False)) if job else False,
                "final_state": job.get("final_state") if job else None,
                "last_visible_stage": job.get("last_visible_stage", state) if job else state,
                "side_effect_stage": job.get("side_effect_stage", "none") if job else "none",
                "tainted": bool(job.get("tainted", False)) if job else False,
                "started_at": now,
                "updated_at": now,
            }
            return self._save_call_record(record)

    def create_rejected_call_record(
        self,
        *,
        call_id: str,
        method: str,
        rejection_code: str,
        active_job_id: str,
        active_command: str,
        active_session: str | None,
    ) -> dict:
        with self._mutex:
            now = _utc_now()
            record = {
                "call_id": call_id,
                "job_id": None,
                "method": method,
                "workspace_root": str(self.workspace_root),
                "session_id": None,
                "state": "rejected",
                "executed": False,
                "final": True,
                "final_state": "failed_before_side_effect",
                "last_visible_stage": "rejected",
                "side_effect_stage": "none",
                "tainted": False,
                "rejection_code": rejection_code,
                "active_request": active_job_id,
                "active_command": active_command,
                "active_session": active_session,
                "started_at": now,
                "updated_at": now,
            }
            return self._save_call_record(record)

    def finish_call_record(
        self,
        call_id: str,
        *,
        job_id: str | None,
        method: str,
        session_id: str | None,
        state: str,
        executed: bool,
    ) -> dict:
        with self._mutex:
            record = self._load_call_record(call_id) or {
                "call_id": call_id,
                "job_id": job_id,
                "method": method,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "started_at": _utc_now(),
            }
            record.update(
                {
                    "job_id": job_id,
                    "method": method,
                    "session_id": session_id,
                    "state": state,
                    "executed": bool(executed),
                    "final": state in {"completed", "failed", "cancelled", "cancel_unknown"},
                    "last_visible_stage": state,
                    "updated_at": _utc_now(),
                }
            )
            return self._save_call_record(record)

    def get_job_record(self, job_id: str) -> dict | None:
        with self._mutex:
            record = self._load_job_record(job_id)
            return deepcopy(record) if record is not None else None

    def get_call_record(self, call_id: str) -> dict | None:
        with self._mutex:
            record = self._load_call_record(call_id)
            return deepcopy(record) if record is not None else None

    def find_active_job_by_key(self, job_key: str) -> dict | None:
        with self._mutex:
            for path in self._jobs_root.glob("*.json"):
                record = self._load_json_record(path)
                if record and record.get("job_key") == job_key and not record.get("final", False):
                    return deepcopy(record)
            return None

    def find_latest_job_for_method(self, method: str) -> dict | None:
        with self._mutex:
            matches = [
                record
                for path in self._jobs_root.glob("*.json")
                if (record := self._load_json_record(path)) and record.get("method") == method
            ]
            matches.sort(key=lambda item: item.get("updated_at") or item.get("started_at") or "", reverse=True)
            return deepcopy(matches[0]) if matches else None

    def update_job_record(self, job_id: str, **updates: Any) -> dict:
        with self._mutex:
            record = self._load_job_record(job_id)
            if record is None:
                raise TrailError("REQUEST_NOT_FOUND", f"request not found: {job_id}")
            record.update(to_jsonable(updates))
            record["updated_at"] = _utc_now()
            return self._save_job_record(record)


    def finish_job_record(
        self,
        job_id: str,
        *,
        state: str,
        final_state: str,
        last_visible_stage: str,
        side_effect_stage: str,
        envelope: dict,
        tainted: bool,
    ) -> dict:
        envelope_snapshot = to_jsonable(envelope)
        return self.update_job_record(
            job_id,
            state=state,
            final=True,
            final_state=final_state,
            last_visible_stage=last_visible_stage,
            side_effect_stage=side_effect_stage,
            tainted=bool(tainted),
            last_envelope=envelope_snapshot,
        )

    def find_reusable_session(self, *, window_binding: dict):
        with self._mutex:
            candidates = []
            for session in self._store.list():
                if session.window_binding != window_binding:
                    continue
                if self._is_session_tainted_unlocked(session.session_id):
                    continue
                candidates.append(session)
        candidates.sort(key=lambda item: (item.updated_at or item.created_at, item.created_at), reverse=True)
        return candidates[0] if candidates else None

    def dump_state(self, *, session_id: str) -> dict:
        snapshot = self.load_session(session_id).to_dict()
        snapshot.setdefault("scene_state", {}).setdefault("daemon", {})["tainted"] = self.is_session_tainted(session_id)
        return snapshot

    def _session_record_is_tainted(self, session_id: str) -> bool:
        for root in (self._journal_root, self._jobs_root):
            for path in root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if record.get("session_id") != session_id:
                    continue
                if self._record_tainted(record):
                    return True
        return False

    def is_session_tainted(self, session_id: str) -> bool:
        with self._mutex:
            return self._is_session_tainted_unlocked(session_id)

    def _is_session_tainted_unlocked(self, session_id: str) -> bool:
        try:
            session = self.load_session(session_id)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            session = None
        if session is not None and bool(session.scene_state.get("daemon", {}).get("tainted", False)):
            return True
        return self._session_record_is_tainted(session_id)

    def ensure_cw_mutation_allowed(self, session_id: str) -> None:
        if not self.is_session_tainted(session_id):
            return
        raise TrailError(
            "SESSION_RECONCILE_REQUIRED",
            "session is tainted; reconcile before mutating cw commands",
        )

    def mark_session_tainted(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        with self._mutex:
            session = self.load_session(session_id)
            session.scene_state.setdefault("daemon", {})["tainted"] = True
            self.save_session(session)

    def begin_mutation(
        self,
        *,
        session_id: str | None,
        request_id: str,
        command_name: str,
        enforce_cw_tainted: bool = False,
    ) -> dict:
        with self._mutex:
            record = self._load_record(request_id)
            if record is not None:
                if (
                    command_name == "start.run"
                    and record.get("method") == "start.run"
                    and session_id is None
                    and record.get("session_id") is not None
                ):
                    if record.get("final_state"):
                        return {"status": "duplicate_terminal", "record": deepcopy(record)}
                    return {"status": "duplicate_in_progress", "record": deepcopy(record)}
                if record.get("session_id") != session_id or record.get("method") != command_name:
                    return {"status": "request_id_conflict", "record": deepcopy(record)}
                if record.get("final_state"):
                    return {"status": "duplicate_terminal", "record": deepcopy(record)}
                return {"status": "duplicate_in_progress", "record": deepcopy(record)}

            if enforce_cw_tainted and isinstance(session_id, str) and session_id and self._is_session_tainted_unlocked(session_id):
                return {"status": "session_tainted", "session_id": session_id}

            now = _utc_now()
            fresh = {
                "request_id": request_id,
                "method": command_name,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "final_state": None,
                "last_visible_stage": "accepted",
                "tainted": False,
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
            record["session_id"] = session_id
            risky_final_state = final_state in RISKY_FINAL_STATES
            record = self._apply_terminal_record(
                record=record,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
                tainted=risky_final_state,
            )
            if risky_final_state:
                self._save_record(record)

            if session_id is not None:
                try:
                    session = self.load_session(session_id)
                    if risky_final_state:
                        session.scene_state.setdefault("daemon", {})["tainted"] = True
                    session.last_result = deepcopy(record["last_result"])
                    screenshot = record["last_envelope"].get("screenshot")
                    if screenshot:
                        session.last_screenshot = screenshot
                    self.save_session(session)
                except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                    if not risky_final_state:
                        raise

            if not risky_final_state:
                self._save_record(record)

    def finalize_journal_record(
        self,
        *,
        request_id: str,
        command_name: str,
        final_state: str,
        envelope: dict,
        session_id: str | None = None,
    ) -> None:
        with self._mutex:
            record = self._require_record(request_id=request_id)
            record["session_id"] = session_id
            record = self._apply_terminal_record(
                record=record,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
                tainted=final_state in RISKY_FINAL_STATES,
            )
            self._save_record(record)

    def _status_from_job_record(self, job: dict) -> dict:
        return {
            "request_id": job["job_id"],
            "job_id": job.get("job_id"),
            "method": job["method"],
            "workspace_root": job.get("workspace_root", str(self.workspace_root)),
            "session_id": job.get("session_id"),
            "state": job.get("state", "accepted"),
            "final": bool(job.get("final", job.get("final_state") is not None)),
            "final_state": job.get("final_state"),
            "last_visible_stage": job.get("last_visible_stage", "accepted"),
            "side_effect_stage": job.get("side_effect_stage", "none"),
            "tainted": bool(job.get("tainted", False)),
            "started_at": job.get("started_at"),
            "updated_at": job.get("updated_at"),
            "next_request_id": job["job_id"],
        }

    def _status_from_call_record(self, call: dict, job: dict | None = None) -> dict:
        source = job or call
        status = {
            "request_id": call["call_id"],
            "job_id": call.get("job_id"),
            "method": source["method"],
            "workspace_root": source.get("workspace_root", call.get("workspace_root", str(self.workspace_root))),
            "session_id": source.get("session_id", call.get("session_id")),
            "state": source.get("state", call.get("state", "accepted")),
            "executed": bool(call.get("executed", False)),
            "final": bool(source.get("final", source.get("final_state") is not None)),
            "final_state": source.get("final_state"),
            "last_visible_stage": source.get("last_visible_stage", source.get("state", "accepted")),
            "side_effect_stage": source.get("side_effect_stage", "none"),
            "tainted": bool(source.get("tainted", False)),
            "started_at": source.get("started_at", call.get("started_at")),
            "updated_at": source.get("updated_at", call.get("updated_at")),
            "next_request_id": call.get("job_id") or call["call_id"],
        }
        for key in ("active_request", "active_command", "active_session", "rejection_code"):
            if key in call:
                status[key] = call[key]
        return status

    def request_status(self, request_id: str) -> dict:
        with self._journal_locked():
            call = self._load_call_record(request_id)
            if call is not None:
                job_id = call.get("job_id")
                job = self._load_job_record(job_id) if job_id and call.get("state") != "rejected" else None
                return self._status_from_call_record(call, job=job)

            job = self._load_job_record(request_id)
            if job is not None:
                return self._status_from_job_record(job)

            record = self._require_record(request_id=request_id)
            tainted = self._record_tainted(record)
            session_id = record.get("session_id")
            if session_id is not None:
                try:
                    session = self.load_session(session_id)
                except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                    pass
                else:
                    tainted = tainted or bool(session.scene_state.get("daemon", {}).get("tainted", False))
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
        with self._mutex:
            session = self.load_session(session_id)
            session.scene_state.setdefault("daemon", {})["tainted"] = False
            self.save_session(session)
            for root in (self._journal_root, self._jobs_root):
                for path in root.glob("*.json"):
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, json.JSONDecodeError):
                        continue
                    if record.get("session_id") != session_id:
                        continue
                    if not self._record_tainted(record):
                        continue
                    record["tainted"] = False
                    record["updated_at"] = _utc_now()
                    if root == self._jobs_root:
                        self._save_job_record(record)
                    else:
                        self._save_record(record)
            return {"session_id": session_id, "tainted": False}


class SessionServiceRegistry:
    def __init__(self):
        self._services: dict[str, SessionService] = {}
        self._mutex = RLock()

    def for_workspace(self, workspace_root: Path | str) -> SessionService:
        key = _workspace_registry_key(workspace_root)
        with self._mutex:
            service = self._services.get(key)
            if service is None:
                service = SessionService(workspace_root=Path(key))
                self._services[key] = service
            return service
