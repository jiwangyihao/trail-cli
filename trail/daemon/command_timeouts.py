from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

SOCKET_RESPONSE_TIMEOUT_SECONDS = 120.0
DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS = 570
CW_BATTLE_RUN_TIMEOUT_BUFFER_SECONDS = 30.0
START_RUN_EXECUTION_TIMEOUT_SECONDS = 180


def normalize_cw_battle_run_timeout(raw_timeout: Any) -> int:
    if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool) and raw_timeout > 0:
        return raw_timeout
    return DEFAULT_CW_BATTLE_RUN_TIMEOUT_SECONDS


def _cw_battle_run_execution_timeout(payload: dict[str, Any] | None) -> int:
    raw_timeout = payload.get("timeout") if isinstance(payload, dict) else None
    return normalize_cw_battle_run_timeout(raw_timeout)


def _start_run_execution_timeout(payload: dict[str, Any] | None) -> int:
    del payload
    return START_RUN_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CommandTimeoutPolicy:
    execution_timeout_resolver: Callable[[dict[str, Any] | None], int]
    response_timeout_buffer_seconds: float = 0.0


COMMAND_TIMEOUT_POLICIES: dict[str, CommandTimeoutPolicy] = {
    "cw.battle.run": CommandTimeoutPolicy(
        execution_timeout_resolver=_cw_battle_run_execution_timeout,
        response_timeout_buffer_seconds=CW_BATTLE_RUN_TIMEOUT_BUFFER_SECONDS,
    ),
    "start.run": CommandTimeoutPolicy(
        execution_timeout_resolver=_start_run_execution_timeout,
    ),
}


def resolve_command_execution_timeout(method: str, payload: dict[str, Any] | None) -> int | None:
    policy = COMMAND_TIMEOUT_POLICIES.get(method)
    if policy is None:
        return None
    return int(policy.execution_timeout_resolver(payload))


def resolve_command_response_timeout(method: str, payload: dict[str, Any] | None) -> float:
    execution_timeout = resolve_command_execution_timeout(method, payload)
    if execution_timeout is None:
        return SOCKET_RESPONSE_TIMEOUT_SECONDS
    policy = COMMAND_TIMEOUT_POLICIES[method]
    return float(execution_timeout) + float(policy.response_timeout_buffer_seconds)
