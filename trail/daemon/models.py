from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class InstallRecord:
    bootstrap_type: str
    bootstrap_id: str
    daemon_entrypoint: str
    manifest_version: int
    protocol_version: int
    token_file: str
    log_dir: str
    workspace_strategy: str


@dataclass(slots=True)
class RuntimeRecord:
    instance_id: str | None
    pid: int | None
    state: str
    endpoint: str | None
    token_generation: int | None
    updated_at: str | None
    last_transition_at: str | None
    last_start_error: str | None


@dataclass(slots=True)
class TrailDaemonManifest:
    install: InstallRecord
    runtime: RuntimeRecord


@dataclass(slots=True)
class DaemonRequest:
    request_id: str
    protocol_version: int
    workspace_root: str
    session_id: str | None
    verbose: bool
    method: str
    payload: JsonDict
