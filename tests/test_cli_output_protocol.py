from __future__ import annotations

from pathlib import Path
import pytest

from trail.output.envelope import command_failure
from trail.output.rendering import render_output

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
                    "session_id": "sess-1",
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
        "ok daemon.request_status request=req-42 command=input.click session=sess-1 final_state=completed last_visible_stage=responded tainted=0"
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


@pytest.mark.parametrize(
    ("render_command", "inner"),
    [
        (
            "start.run",
            build_success_response(
                request_id="job-start",
                data={"status": "attached", "session": "sess-1", "reused": 1, "title": "崩坏：星穹铁道", "hwnd": 123},
                screenshot=".trail/shots/job-start.png",
            ),
        ),
        (
            "cw.battle.run",
            build_success_response(
                request_id="job-battle",
                data={"status": "completed", "result": "win", "stage": "shop", "stale": False, "in_battle": False},
                screenshot=".trail/shots/job-battle.png",
            ),
        ),
        (
            "cw.portal.select",
            build_success_response(
                request_id="job-portal",
                data={"card_idx": 1, "portal_title": "传送门", "stage": "preparation", "stale": False},
                screenshot=".trail/shots/job-portal.png",
            ),
        ),
        (
            "input.click",
            command_failure(code="REQUEST_CANCELLED", message="request cancelled", screenshot=None, debug={"request_id": "job-click"}),
        ),
    ],
)
def test_daemon_request_result_renders_inner_business_envelope_exactly(cli_runner, fake_daemon_client, render_command, inner):
    if inner.get("screenshot"):
        inner["image_guidance"] = {"read_image_first": 1}
    inner["warnings"] = [{"code": "SOFT", "message": "kept"}]
    inner["references"] = [{"label": "ref", "path": "skills/trail-hsr/SKILL.md"}]
    inner["timing"] = {"elapsed_ms": 123}
    inner_debug = dict(inner.get("debug") or {})
    inner_debug.setdefault("request_id", inner.get("request_id") or "job-click")
    inner_debug["trace"] = [{"step": "demo", "ok": 1}]
    inner["debug"] = inner_debug
    fake_daemon_client(
        {"daemon.request_result": build_success_response(request_id="call-result", data={"render_command": render_command, "envelope": inner})}
    )

    result = cli_runner.invoke(app, ["daemon", "request-result", "--request-id", inner_debug["request_id"]])

    assert result.exit_code == 0
    assert result.stdout.strip() == render_output(render_command, inner).strip()


def test_daemon_request_result_passes_inner_envelope_object_to_print_output(monkeypatch, cli_runner, fake_daemon_client):
    inner = build_success_response(request_id="job-struct", data={"captured": True}, screenshot=".trail/shots/job-struct.png")
    inner["image_guidance"] = {"read_image_first": 1}
    inner["debug"] = {"trace": [{"step": "demo", "ok": 1}], "request_id": "job-struct"}
    inner["timing"] = {"elapsed_ms": 123}
    captured = {}

    def spy_print_output(command, payload):
        captured["command"] = command
        captured["payload"] = payload

    monkeypatch.setattr("trail.commands.daemon.print_output", spy_print_output)
    fake_daemon_client(
        {"daemon.request_result": build_success_response(request_id="call-result", data={"render_command": "screen.shot", "envelope": inner})}
    )

    result = cli_runner.invoke(app, ["daemon", "request-result", "--request-id", "job-struct"])

    assert result.exit_code == 0
    assert captured == {"command": "screen.shot", "payload": inner}


def test_daemon_request_result_malformed_control_success_renders_protocol_failure(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {"daemon.request_result": build_success_response(request_id="call-result", data={"render_command": "screen.shot"})}
    )

    result = cli_runner.invoke(app, ["daemon", "request-result", "--request-id", "job-missing-envelope"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail daemon.request_result code=REQUEST_RESULT_PROTOCOL_INVALID",
        'why msg="daemon.request_result response missing render_command or envelope"',
    ]

def test_daemon_request_cancel_force_cli_payload(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "daemon.request_cancel": command_failure(
                code="FORCE_CANCEL_NOT_SUPPORTED",
                message="force cancel requires process-isolated workers",
                screenshot=None,
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "request-cancel", "--request-id", "job-1", "--force", "--confirm-taint"])

    assert result.exit_code == 0
    assert client.calls[0]["payload"] == {"request_id": "job-1", "force": True, "confirm_taint": True}
    assert result.stdout.splitlines() == [
        "fail daemon.request_cancel code=FORCE_CANCEL_NOT_SUPPORTED",
        'why msg="force cancel requires process-isolated workers"',
    ]
