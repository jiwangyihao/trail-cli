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


def _expected_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.append(f"shot path={screenshot}")
    if body:
        lines.extend(body)
    return lines


def test_guide_fetch_renders_summary_and_uses_daemon_client(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide fetch path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.fetch.cw": build_success_response(
                request_id="req-guide-fetch",
                data={
                    "lineup_id": "abc",
                    "share_code": "##demo##",
                    "version": "3.2",
                    "min_level": 7,
                    "mid_level": 8,
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": True,
                    "on_field": {"希儿": 9, "停云": 3},
                    "off_field": {"佩拉": 1},
                    "portals": ["商店", "事件"],
                    "first_fight_augments": ["快攻", "回蓝"],
                    "second_fight_augments": ["暴击", "连携"],
                    "order_basic": ["升级", "买卡", "打精英"],
                    "order_compose": ["希儿", "停云"],
                    "role_stages": [{"name": "希儿", "stage": 1}, {"name": "停云", "stage": 2}],
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok guide.fetch.cw id=abc share_code=##demo## version=3.2 min_level=7 mid_level=8 hard=1 change_equip=0 expert=1",
        body=[
            "guide on_field=希儿:9|停云:3 off_field=佩拉:1",
            "guide portals=商店|事件 first_augments=快攻|回蓝 second_augments=暴击|连携",
            "guide order_basic=升级|买卡|打精英 order_compose=希儿|停云 role_stages=name:希儿/stage:1|name:停云/stage:2",
        ],
    )
    assert client.calls == [
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_config_renders_summary_and_yaml(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide config path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.config.cw": build_success_response(
                request_id="req-guide-config",
                data={
                    "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
                    "lineup_levels": [{"id": 1}],
                    "traits": [{"id": 1001}, {"id": 1002}],
                    "roles": [{"id": 1}, {"id": 2}, {"id": 3}],
                    "role_tags": [{"id": 11}],
                    "portal_list": [
                        {"portal_id": "shop", "title": "购物区", "description": "花金币买角色和升级"},
                        {"portal_id": "event", "title": "事件区", "description": "处理事件、补给与遭遇"},
                    ],
                },
            )
        }
    )

    text_result = cli_runner.invoke(app, ["guide", "config", "cw"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "config", "cw"])

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert text_result.stdout.splitlines() == [
        "ok guide.config.cw season=12 sub_season=3 big_version=3.2",
        "info lineup_levels=1 traits=2 roles=3 role_tags=1 portal_list=2",
    ]
    assert yaml_result.stdout.splitlines()[0:2] == [
        "ok guide.config.cw season=12 sub_season=3 big_version=3.2",
        "info lineup_levels=1 traits=2 roles=3 role_tags=1 portal_list=2",
    ]
    assert "meta:" in yaml_result.stdout
    assert "portal_list:" in yaml_result.stdout
    assert client.calls == [
        {
            "method": "guide.config.cw",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
        {
            "method": "guide.config.cw",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_list_renders_paging_and_facts(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list",
                data={
                    "list": [
                        {
                            "lineup_id": "abc",
                            "title": "7群攻2银河学者",
                            "carry_roles": ["希儿", "停云"],
                            "final_role_cards": [
                                {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                                {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                                {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                            ],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                    "next_page_token": "next-token",
                },
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
    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=1 next=next-token",
        "guide id=abc title=7群攻2银河学者 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45",
        "guide idx=1 final_roles=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3|佩拉/star:4/rarity:2",
    ]
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


def test_guide_list_portal_payload_mapping_and_filtered_rendering(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portal",
                data={
                    "list": [
                        {
                            "lineup_id": "portal-guide",
                            "title": "购物阵容",
                            "carry_roles": ["希儿"],
                            "final_role_cards": [
                                {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                            ],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                    "next_page_token": None,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--limit", "5"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "guide id=portal-guide title=购物阵容 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45",
        "guide idx=1 final_roles=希儿/carry:1/star:5/rarity:3",
    ]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 5,
                "trait_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal": "购物区",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_list_multi_portal_payload_mapping_and_grouped_rendering(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portals",
                data={
                    "portals": [
                        {
                            "portal_title": "购物区",
                            "list": [
                                {
                                    "lineup_id": "shop-guide",
                                    "title": "购物区优选阵容",
                                    "carry_roles": ["希儿"],
                                    "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                }
                            ],
                            "more": False,
                            "next_page_token": None,
                        },
                        {
                            "portal_title": "事件区",
                            "list": [
                                {
                                    "lineup_id": "event-guide",
                                    "title": "事件区优选阵容",
                                    "carry_roles": ["停云"],
                                    "final_role_cards": [{"name": "停云", "star": 4, "rarity": 2, "is_carry": True}],
                                    "support_hard": False,
                                    "has_change_equip": True,
                                    "has_expert": False,
                                    "like": 22,
                                    "favour": 9,
                                }
                            ],
                            "more": False,
                            "next_page_token": None,
                        },
                    ],
                    "count": 2,
                    "more": False,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--portal", "事件区", "--limit", "3"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw groups=2 count=2 more=0",
        "guide portal=购物区 count=1 more=0",
        "guide portal=购物区 id=shop-guide title=购物区优选阵容 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45",
        "guide portal=购物区 idx=1 final_roles=希儿/carry:1/star:5/rarity:3",
        "guide portal=事件区 count=1 more=0",
        "guide portal=事件区 id=event-guide title=事件区优选阵容 idx=1 carry=停云 hard=0 change_equip=1 expert=0 like=22 favour=9",
        "guide portal=事件区 idx=1 final_roles=停云/carry:1/star:4/rarity:2",
    ]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 3,
                "trait_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal": ["购物区", "事件区"],
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_list_portal_id_payload_mapping(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portal-id",
                data={"list": [], "next_page_token": None},
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal-id", "shop", "--limit", "3"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok guide.list.cw count=0 more=0"]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 3,
                "trait_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal_id": "shop",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_list_rejects_portal_and_portal_id_together(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--portal-id", "shop"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide options \'--portal\' and \'--portal-id\' are mutually exclusive"',
    ]


@pytest.mark.parametrize(
    ("args", "command"),
    [
        (["guide", "fetch", "boss", "abc"], "guide.fetch.boss"),
        (["guide", "config", "boss"], "guide.config.boss"),
        (["guide", "list", "boss"], "guide.list.boss"),
    ],
)
def test_guide_commands_render_text_error_when_scene_not_supported(cli_runner, args, command: str):
    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        f"fail {command} code=SCENE_NOT_SUPPORTED",
        body=['why msg="暂不支持场景 boss"'],
    )


def test_command_service_handles_guide_fetch_cw_and_persists_artifact(tmp_path: Path, monkeypatch):
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
    artifacts = list((tmp_path / ".trail" / "artifacts").glob("*.json"))
    assert len(artifacts) == 1
    artifact_payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact_payload == {
        "artifact_id": artifacts[0].stem,
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "abc",
        "share_code": "##demo##",
        "recovery_origin": "guide.fetch.cw",
        "created_at": artifact_payload["created_at"],
    }


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


def test_command_service_handles_guide_config_cw_with_portal_list(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide_config",
        lambda: {
            "meta": {"season_id": 12},
            "lineup_levels": [],
            "traits": [],
            "roles": [],
            "role_tags": [],
            "portal_list": [{"portal_id": "shop", "title": "购物区", "description": "花金币买角色和升级"}],
        },
    )

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.config.cw",
            payload={},
        )
    )

    assert payload == {
        "request_id": "req-guide.config.cw",
        "ok": True,
        "data": {
            "meta": {"season_id": 12},
            "lineup_levels": [],
            "traits": [],
            "roles": [],
            "role_tags": [],
            "portal_list": [{"portal_id": "shop", "title": "购物区", "description": "花金币买角色和升级"}],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def test_command_service_handles_guide_list_cw_with_portal_filters(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    observed: list[dict[str, object]] = []

    def fake_fetch_guide_list(**kwargs):
        observed.append(kwargs)
        return {"list": [], "next_page_token": None}

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_list", fake_fetch_guide_list)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 1,
                "limit": 20,
                "trait_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal": "购物区",
            },
        )
    )

    assert payload == {
        "request_id": "req-guide.list.cw",
        "ok": True,
        "data": {"list": [], "next_page_token": None},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert observed == [
        {
            "page": 1,
            "limit": 20,
            "trait_id": None,
            "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal": "购物区",
            }
        ]


def test_command_service_handles_guide_list_cw_with_multi_portal_groups(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    observed: list[dict[str, object]] = []

    def fake_fetch_guide_list(**kwargs):
        observed.append(kwargs)
        return {
            "portals": [
                {"portal_title": "购物区", "list": [{"lineup_id": "shop-guide", "like": 123, "favour": 45}], "more": False, "next_page_token": None},
                {"portal_title": "事件区", "list": [{"lineup_id": "event-guide", "like": 22, "favour": 9}], "more": True, "next_page_token": "token-next"},
            ],
            "count": 2,
            "more": True,
        }

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_list", fake_fetch_guide_list)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 1,
                "limit": 3,
                "trait_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
                "portal": ["购物区", "事件区"],
            },
        )
    )

    assert payload == {
        "request_id": "req-guide.list.cw",
        "ok": True,
        "data": {
            "portals": [
                {"portal_title": "购物区", "list": [{"lineup_id": "shop-guide", "like": 123, "favour": 45}], "more": False, "next_page_token": None},
                {"portal_title": "事件区", "list": [{"lineup_id": "event-guide", "like": 22, "favour": 9}], "more": True, "next_page_token": "token-next"},
            ],
            "count": 2,
            "more": True,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert observed == [
        {
            "page": 1,
            "limit": 3,
            "trait_id": None,
            "order": None,
            "next_page_token": None,
            "match_change_job": None,
            "match_hard": None,
            "portal": ["购物区", "事件区"],
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
