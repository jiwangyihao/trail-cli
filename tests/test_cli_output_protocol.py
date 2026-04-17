from __future__ import annotations

from pathlib import Path

from trail.cli import app
from tests.support.fake_daemon import build_success_response


def test_daemon_request_status_renders_recovery_state(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "daemon.request_status": build_success_response(
                request_id="req-daemon-request-status",
                data={
                    "request_id": "req-42",
                    "method": "input.click",
                    "workspace_root": str(tmp_path),
                    "session_id": None,
                    "final_state": "completed",
                    "last_visible_stage": "responded",
                    "tainted": False,
                    "started_at": "2026-04-16T00:00:00+00:00",
                    "updated_at": "2026-04-16T00:00:01+00:00",
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "request-status", "--request-id", "req-42"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok daemon.request_status request=req-42 final_state=completed last_visible_stage=responded tainted=0"
    ]
    assert client.calls == [
        {
            "method": "daemon.request_status",
            "payload": {"request_id": "req-42"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_daemon_request_status_omits_missing_optional_fields(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "daemon.request_status": build_success_response(
                request_id="req-daemon-request-status-pending",
                data={
                    "request_id": "req-1",
                    "final_state": "failed",
                    "last_visible_stage": None,
                    "tainted": False,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "request-status", "--request-id", "req-1"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok daemon.request_status request=req-1 final_state=failed tainted=0"]
    assert "last_visible_stage=null" not in result.stdout


def test_daemon_reconcile_session_cli_output_protocol(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "daemon.reconcile_session": build_success_response(
                request_id="req-daemon-reconcile-session",
                data={"session_id": "session-1", "tainted": False},
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "reconcile-session", "--session", "session-1"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok daemon.reconcile_session session=session-1 tainted=0"]
    assert client.calls == [
        {
            "method": "daemon.reconcile_session",
            "payload": {"session_id": "session-1"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]
