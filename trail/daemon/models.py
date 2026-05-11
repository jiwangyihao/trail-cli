from __future__ import annotations

from dataclasses import dataclass, field
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


class RequestCancelled(Exception):
    """Raised when an async daemon request observes a soft-cancel token."""


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RequestCancelled()


@dataclass(slots=True)
class RequestControl:
    mode: str = "async_wait"
    wait_timeout: float = 100.0
    cancellation_token: CancellationToken = field(default_factory=CancellationToken, repr=False, compare=False)
    side_effect_stage: str = "none"

    @classmethod
    def from_mapping(cls, value: Any) -> "RequestControl":
        from trail.daemon.command_timeouts import normalize_daemon_wait_timeout

        mapping = value if isinstance(value, dict) else {}
        return cls(
            mode=str(mapping.get("mode") or "async_wait"),
            wait_timeout=normalize_daemon_wait_timeout(mapping.get("wait_timeout"), no_wait=False),
            side_effect_stage=str(mapping.get("side_effect_stage") or "none"),
        )

    def to_dict(self) -> JsonDict:
        return {
            "mode": self.mode,
            "wait_timeout": float(self.wait_timeout),
            "side_effect_stage": self.side_effect_stage,
        }


@dataclass(slots=True)
class DaemonRequest:
    request_id: str
    protocol_version: int
    workspace_root: str
    session_id: str | None
    verbose: bool
    method: str
    payload: JsonDict
    call_id: str | None = None
    job_id: str | None = None
    control: RequestControl = field(default_factory=RequestControl)
    session_service: Any | None = None

    def __post_init__(self) -> None:
        if self.call_id is None:
            self.call_id = self.request_id
        if not isinstance(self.control, RequestControl):
            self.control = RequestControl.from_mapping(self.control)
