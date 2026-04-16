from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from tests.support.fake_daemon import build_success_response


def _guide_request(*, workspace_root: Path, method: str, payload: dict | None = None):
    return DaemonRequest(
        request_id=f"req-{method}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(workspace_root),
        session_id=None,
        verbose=False,
        method=method,
        payload=payload or {},
    )


def test_guide_fetch_uses_daemon_client(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide fetch path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.fetch.cw": build_success_response(
                request_id="req-guide-fetch",
                data={"lineup_id": "abc", "share_code": "##demo##"},
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"lineup_id": "abc", "share_code": "##demo##"}
    assert client.calls == [
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_config_uses_daemon_client(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide config path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.config.cw": build_success_response(
                request_id="req-guide-config",
                data={"season_id": 12},
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "config", "cw"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"season_id": 12}
    assert client.calls == [
        {
            "method": "guide.config.cw",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_list_uses_daemon_client(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list",
                data={"list": [{"lineup_id": "abc"}], "next_page_token": "next-token"},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "guide",
            "list",
            "cw",
            "--page",
            "2",
            "--limit",
            "10",
            "--trait-id",
            "1005",
            "--order",
            "Recent",
            "--next-page-token",
            "token-2",
            "--match-change-job",
            "true",
            "--match-hard",
            "false",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"list": [{"lineup_id": "abc"}], "next_page_token": "next-token"}
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 2,
                "limit": 10,
                "trait_id": 1005,
                "order": "Recent",
                "next_page_token": "token-2",
                "match_change_job": "true",
                "match_hard": "false",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_fetch_returns_stable_error_when_scene_not_supported(cli_runner):
    result = cli_runner.invoke(app, ["guide", "fetch", "boss", "abc"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "SCENE_NOT_SUPPORTED",
        "message": "暂不支持场景 boss",
    }


def test_command_service_handles_guide_fetch_cw(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    calls: list[tuple[str, object]] = []

    def fake_fetch_payload(url: str):
        calls.append(("fetch_payload", url))
        return {"lineup_id": url, "share_code": "##demo##"}

    def fake_fetch_guide(url: str, *, fetcher):
        calls.append(("fetch_guide", url))
        return fetcher(url)

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_payload", fake_fetch_payload)
    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide", fake_fetch_guide)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.fetch.cw",
            payload={"url": "abc"},
        )
    )

    assert payload == {
        "request_id": "req-guide.fetch.cw",
        "ok": True,
        "data": {"lineup_id": "abc", "share_code": "##demo##"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert calls == [("fetch_guide", "abc"), ("fetch_payload", "abc")]


def test_command_service_handles_guide_list_cw_accepts_boolean_filters(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    observed: list[dict[str, object]] = []

    def fake_fetch_guide_list(**kwargs):
        observed.append(kwargs)
        return {"list": [{"lineup_id": "abc"}], "next_page_token": "next-token"}

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_list", fake_fetch_guide_list)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 2,
                "limit": 10,
                "trait_id": 1005,
                "order": "Recent",
                "next_page_token": "token-2",
                "match_change_job": True,
                "match_hard": False,
            },
        )
    )

    assert payload == {
        "request_id": "req-guide.list.cw",
        "ok": True,
        "data": {"list": [{"lineup_id": "abc"}], "next_page_token": "next-token"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert observed == [
        {
            "page": 2,
            "limit": 10,
            "trait_id": 1005,
            "order": "Recent",
            "next_page_token": "token-2",
            "match_change_job": True,
            "match_hard": False,
        }
    ]


def test_command_service_rejects_unsupported_guide_scene(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    service = CommandService(runtime_service=SimpleNamespace())

    with pytest.raises(TrailError) as exc_info:
        service.handle(
            _guide_request(
                workspace_root=tmp_path,
                method="guide.fetch.boss",
                payload={"url": "abc"},
            )
        )

    assert exc_info.value.code == "SCENE_NOT_SUPPORTED"
    assert str(exc_info.value) == "暂不支持场景 boss"
