from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.output.rendering import render_output
from tests.support.fake_daemon import build_success_response


def _guide_request(
    *,
    workspace_root: Path,
    method: str,
    payload: dict | None = None,
    session_id: str | None = None,
):
    return DaemonRequest(
        request_id=f"req-{method}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(workspace_root),
        session_id=session_id,
        verbose=False,
        method=method,
        payload=payload or {},
    )


def _expected_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.append(f"shot path={screenshot}")
        lines.append("info read_image_first=1")
    if body:
        lines.extend(body)
    return lines


def test_guide_fetch_renders_summary_and_yaml(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
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
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "labels": ["7级搜牌", "银河学者"],
                    "version": "3.2",
                    "min_coins": 40,
                    "min_level": 7,
                    "mid_level": 8,
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": True,
                    "operation_guide": "前期：过渡\n中期：D牌\n后期：补强",
                    "portals": ["商店", "事件"],
                    "first_fight_augments": ["快攻", "回蓝"],
                    "second_fight_augments": ["暴击", "连携"],
                    "order_basic": ["升级", "买卡", "打精英"],
                    "order_compose": ["希儿", "停云"],
                    "role_stages": [
                        {
                            "stage": "Opening",
                            "front_roles": [{"name": "黑塔", "star": 1, "rarity": 1, "is_carry": False}],
                            "back_roles": [{"name": "艾丝妲", "star": 1, "rarity": 1, "is_carry": False}],
                                "traits": ["1智识"],
                        },
                        {
                            "stage": "Final",
                            "front_roles": [
                                {
                                    "name": "希儿",
                                    "star": 3,
                                    "rarity": 3,
                                    "is_carry": True,
                                    "first_equipments": ["高周波电锯", "战场进化手册"],
                                    "second_equipments": ["胜利之旗"],
                                }
                            ],
                            "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                                "traits": ["1巡猎", "2量子"],
                        },
                    ],
                },
            )
        }
    )

    text_result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "fetch", "cw", "abc"])

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert text_result.stdout.splitlines() == _expected_lines(
        "ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8",
        body=[
            "guide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问",
                "guide 羁绊列表=1智识|1巡猎|2量子",
            "guide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携",
            "guide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云",
                "guide 阶段=前期阵容 前台=黑塔/star:1/rarity:1 后台=艾丝妲/star:1/rarity:1 羁绊=1智识",
                "guide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 后台=佩拉/star:2/rarity:2 羁绊=1巡猎|2量子",
            "guide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯|战场进化手册 次选装备=胜利之旗",
            'guide 运营思路="前期：过渡\\n中期：D牌\\n后期：补强"',
        ],
    )
    assert yaml_result.stdout.splitlines()[0:9] == _expected_lines(
        "ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8",
        body=[
            "guide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问",
            "guide 羁绊列表=1智识|1巡猎|2量子",
            "guide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携",
            "guide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云",
            "guide 阶段=前期阵容 前台=黑塔/star:1/rarity:1 后台=艾丝妲/star:1/rarity:1 羁绊=1智识",
            "guide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 后台=佩拉/star:2/rarity:2 羁绊=1巡猎|2量子",
            "guide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯|战场进化手册 次选装备=胜利之旗",
            'guide 运营思路="前期：过渡\\n中期：D牌\\n后期：补强"',
        ],
    )
    assert "lineup_id: abc" in yaml_result.stdout
    assert "operation_guide: '前期：过渡" in yaml_result.stdout
    assert "role_stages:" in yaml_result.stdout
    assert client.calls == [
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_guide_fetch_select_routes_session_at_top_level_and_keeps_yaml_shape(
    cli_runner,
    fake_daemon_client,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide fetch path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.fetch.cw": build_success_response(
                request_id="req-guide-fetch-select",
                data={
                    "lineup_id": "abc",
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "labels": ["7级搜牌"],
                    "version": "3.2",
                    "min_coins": 40,
                    "min_level": 7,
                    "mid_level": 8,
                    "support_hard": False,
                    "has_change_equip": False,
                    "has_expert": False,
                    "operation_guide": "前期：过渡",
                    "portals": [],
                    "first_fight_augments": [],
                    "second_fight_augments": [],
                    "order_basic": [],
                    "order_compose": [],
                    "role_stages": [],
                },
            )
        }
    )

    text_result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--select", "--session", "sess-1"])
    yaml_result = cli_runner.invoke(
        app,
        ["--format", "yaml", "guide", "fetch", "cw", "abc", "--select", "--session", "sess-1"],
    )

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert text_result.stdout.splitlines()[0] == (
        "ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8"
    )
    assert "lineup_id: abc" in yaml_result.stdout
    assert "selected:" not in yaml_result.stdout
    assert "session_id:" not in yaml_result.stdout
    assert "artifact_id" not in text_result.stdout
    assert "artifact_id:" not in yaml_result.stdout
    assert client.calls == [
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc", "select": True},
            "workspace_root": str(tmp_path),
            "session_id": "sess-1",
            "verbose": False,
        },
        {
            "method": "guide.fetch.cw",
            "payload": {"url": "abc", "select": True},
            "workspace_root": str(tmp_path),
            "session_id": "sess-1",
            "verbose": False,
        },
    ]


def test_guide_fetch_select_rejects_missing_session(cli_runner):
    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--select"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.fetch.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide.fetch.cw --select requires --session"',
    ]


def test_guide_fetch_rejects_session_without_select(cli_runner):
    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--session", "sess-1"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.fetch.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide.fetch.cw --session requires --select"',
    ]


def test_guide_fetch_rejects_empty_session_string(cli_runner):
    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc", "--session", ""])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.fetch.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide.fetch.cw --session must be a non-empty string"',
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
                    "strategy_list": [
                        {"strategy_id": "rush", "title": "快攻", "description": "desc1"},
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
        "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
        "info 搜牌档位=1 羁绊=2 角色=3 角色标签=1 投资环境=2",
    ]
    assert yaml_result.stdout.splitlines()[0:2] == [
        "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
        "info 搜牌档位=1 羁绊=2 角色=3 角色标签=1 投资环境=2",
    ]
    assert "meta:" in yaml_result.stdout
    assert "portal_list:" in yaml_result.stdout
    assert "strategy_list:" in yaml_result.stdout
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
                            "version": "3.2",
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
        "guide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
        "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3|佩拉/star:4/rarity:2",
    ]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 2,
                "limit": 10,
                "trait": None,
                "trait_id": 1005,
                "role": None,
                "role_id": None,
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


def test_guide_list_trait_and_role_payload_mapping(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-trait-role",
                data={"list": [], "next_page_token": None},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "guide",
            "list",
            "cw",
            "--trait",
            "巡猎",
            "--role",
            "黑塔",
            "--role",
            "大黑塔",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok guide.list.cw count=0 more=0"]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 20,
                "trait": "巡猎",
                "trait_id": None,
                "role": ["黑塔", "大黑塔"],
                "role_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
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
                            "version": "3.2",
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
        "guide 攻略ID=portal-guide 攻略标题=购物阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
        "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3",
    ]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 5,
                "trait": None,
                "trait_id": None,
                "role": None,
                "role_id": None,
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


def test_guide_list_with_portal_and_role_payload_mapping(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portal-role",
                data={"list": [], "next_page_token": None},
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--role", "黑塔", "--limit", "5"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok guide.list.cw count=0 more=0"]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 5,
                "trait": None,
                "trait_id": None,
                "role": ["黑塔"],
                "role_id": None,
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
                                    "version": "3.2",
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
                                    "version": "3.2",
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
        "guide 投资环境=购物区 count=1 more=0",
        "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
        "guide 投资环境=购物区 idx=1 最终阵容=希儿/carry:1/star:5/rarity:3",
        "guide 投资环境=事件区 count=1 more=0",
        "guide 投资环境=事件区 攻略ID=event-guide 攻略标题=事件区优选阵容 版本=3.2 idx=1 主C=停云 攻略标签=#星徽攻略 点赞=22 收藏=9",
        "guide 投资环境=事件区 idx=1 最终阵容=停云/carry:1/star:4/rarity:2",
    ]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 3,
                "trait": None,
                "trait_id": None,
                "role": None,
                "role_id": None,
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


def test_guide_list_grouped_paged_multi_portal_rendering_keeps_next_only_on_group_line(
    cli_runner, fake_daemon_client, tmp_path: Path
):
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-grouped-paged",
                data={
                    "portals": [
                        {
                            "portal_title": "购物区",
                            "list": [
                                {
                                    "lineup_id": "shop-guide",
                                    "title": "购物区优选阵容",
                                    "version": "3.2",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "like": 123,
                                    "favour": 45,
                                }
                            ],
                            "more": True,
                            "next_page_token": "group-token",
                        },
                        {
                            "portal_title": "事件区",
                            "list": [],
                            "more": True,
                            "next_page_token": "group-token",
                        }
                    ],
                    "count": 1,
                    "more": True,
                },
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["guide", "list", "cw", "--portal", "购物区", "--portal", "事件区", "--limit", "3"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw groups=2 count=1 more=1",
        "guide 投资环境=购物区 count=1 more=1 next=group-token",
        "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45",
        "guide 投资环境=事件区 count=0 more=1 next=group-token",
    ]
    assert "next=group-token" not in result.stdout.splitlines()[0]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 3,
                "trait": None,
                "trait_id": None,
                "role": None,
                "role_id": None,
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
                "trait": None,
                "trait_id": None,
                "role": None,
                "role_id": None,
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


def test_guide_list_with_portal_and_role_id_payload_mapping(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portal-role-id",
                data={"list": [], "next_page_token": None},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["guide", "list", "cw", "--portal", "购物区", "--role-id", "1001", "--role-id", "1002", "--limit", "5"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok guide.list.cw count=0 more=0"]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 5,
                "trait": None,
                "trait_id": None,
                "role": None,
                "role_id": ["1001", "1002"],
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


def test_guide_list_role_fuzzy_success_renders_candidates_and_warning(
    cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-role-fuzzy",
                data={
                    "list": [
                        {
                            "lineup_id": "lineup-herta",
                            "title": "黑塔阵容",
                            "version": "3.2",
                            "carry_roles": ["黑塔"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": False,
                            "like": 21,
                            "favour": 8,
                        }
                    ],
                    "next_page_token": None,
                    "role_candidates": [
                        {
                            "query": "黑搭",
                            "role_resolution": "fuzzy",
                            "resolved": "黑塔",
                            "candidates": [
                                {
                                    "query": "黑搭",
                                    "role": "黑塔",
                                    "id": "1001",
                                    "selected": True,
                                    "score": 0.96,
                                    "front_back": "front",
                                    "traits": ["智识"],
                                    "role_tags": ["输出", "智识"],
                                },
                                {
                                    "query": "黑搭",
                                    "role": "大黑塔",
                                    "id": "1002",
                                    "selected": False,
                                    "score": 0.82,
                                    "front_back": "front",
                                    "traits": ["智识"],
                                    "role_tags": ["输出"],
                                },
                            ],
                        }
                    ],
                },
            )
        }
    )
    client._responses["guide.list.cw"]["warnings"] = [
        {
            "code": "GUIDE_ROLE_FUZZY_MATCH",
            "query": "黑搭",
            "resolved": "黑塔",
            "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        }
    ]

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--role", "黑搭"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "info role_query=黑搭 role_resolution=fuzzy resolved=黑塔 candidates=2",
        "opt query=黑搭 role=黑塔 id=1001 selected=1 score=0.96 front_back=front traits=智识 role_tags=输出|智识",
        "opt query=黑搭 role=大黑塔 id=1002 selected=0 score=0.82 front_back=front traits=智识 role_tags=输出",
        "guide 攻略ID=lineup-herta 攻略标题=黑塔阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈 点赞=21 收藏=8",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑搭 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
    ]


def test_guide_list_role_exact_ambiguous_success_renders_candidates_and_warning(
    cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-role-exact-ambiguous",
                data={
                    "list": [
                        {
                            "lineup_id": "lineup-wolf",
                            "title": "银狼阵容",
                            "version": "3.2",
                            "carry_roles": ["银狼"],
                            "support_hard": False,
                            "has_change_equip": True,
                            "has_expert": True,
                            "like": 13,
                            "favour": 5,
                        }
                    ],
                    "next_page_token": None,
                    "role_candidates": [
                        {
                            "query": "银狼",
                            "role_resolution": "exact_ambiguous",
                            "resolved": "银狼",
                            "candidates": [
                                {
                                    "query": "银狼",
                                    "role": "银狼",
                                    "id": "1003",
                                    "selected": True,
                                    "score": 1.0,
                                    "front_back": "back",
                                    "traits": ["虚无"],
                                    "role_tags": ["减防"],
                                },
                                {
                                    "query": "银狼",
                                    "role": "银狼Lv.999",
                                    "id": "1004",
                                    "selected": False,
                                    "score": 0.86,
                                    "front_back": "back",
                                    "traits": ["虚无"],
                                    "role_tags": ["减防"],
                                },
                            ],
                        }
                    ],
                },
            )
        }
    )
    client._responses["guide.list.cw"]["warnings"] = [
        {
            "code": "GUIDE_ROLE_SIMILAR_CANDIDATES",
            "query": "银狼",
            "resolved": "银狼",
            "message": "角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
        }
    ]

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--role", "银狼"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "info role_query=银狼 role_resolution=exact_ambiguous resolved=银狼 candidates=2",
        "opt query=银狼 role=银狼 id=1003 selected=1 score=1.00 front_back=back traits=虚无 role_tags=减防",
        "opt query=银狼 role=银狼Lv.999 id=1004 selected=0 score=0.86 front_back=back traits=虚无 role_tags=减防",
        "guide 攻略ID=lineup-wolf 攻略标题=银狼阵容 版本=3.2 idx=1 主C=银狼 攻略标签=#星徽攻略|#专家顾问 点赞=13 收藏=5",
        "warn code=GUIDE_ROLE_SIMILAR_CANDIDATES query=银狼 resolved=银狼 msg=角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
    ]


def test_guide_list_multi_role_queries_keep_duplicate_candidate_blocks(
    cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-role-duplicate-blocks",
                data={
                    "list": [
                        {
                            "lineup_id": "lineup-herta",
                            "title": "黑塔阵容",
                            "version": "3.2",
                            "carry_roles": ["黑塔"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": False,
                            "like": 21,
                            "favour": 8,
                        }
                    ],
                    "next_page_token": None,
                    "role_candidates": [
                        {
                            "query": "黑搭",
                            "role_resolution": "fuzzy",
                            "resolved": "黑塔",
                            "candidates": [
                                {
                                    "query": "黑搭",
                                    "role": "黑塔",
                                    "id": "1001",
                                    "selected": True,
                                    "score": 0.96,
                                    "front_back": "front",
                                    "traits": ["智识"],
                                    "role_tags": ["输出", "智识"],
                                }
                            ],
                        },
                        {
                            "query": "黑塔塔",
                            "role_resolution": "fuzzy",
                            "resolved": "黑塔",
                            "candidates": [
                                {
                                    "query": "黑塔塔",
                                    "role": "黑塔",
                                    "id": "1001",
                                    "selected": True,
                                    "score": 0.88,
                                    "front_back": "front",
                                    "traits": ["智识"],
                                    "role_tags": ["输出", "智识"],
                                }
                            ],
                        },
                    ],
                },
            )
        }
    )
    client._responses["guide.list.cw"]["warnings"] = [
        {
            "code": "GUIDE_ROLE_FUZZY_MATCH",
            "query": "黑搭",
            "resolved": "黑塔",
            "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        },
        {
            "code": "GUIDE_ROLE_FUZZY_MATCH",
            "query": "黑塔塔",
            "resolved": "黑塔",
            "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        },
    ]

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--role", "黑搭", "--role", "黑塔塔"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "info role_query=黑搭 role_resolution=fuzzy resolved=黑塔 candidates=1",
        "opt query=黑搭 role=黑塔 id=1001 selected=1 score=0.96 front_back=front traits=智识 role_tags=输出|智识",
        "info role_query=黑塔塔 role_resolution=fuzzy resolved=黑塔 candidates=1",
        "opt query=黑塔塔 role=黑塔 id=1001 selected=1 score=0.88 front_back=front traits=智识 role_tags=输出|智识",
        "guide 攻略ID=lineup-herta 攻略标题=黑塔阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈 点赞=21 收藏=8",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑搭 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑塔塔 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
    ]


def test_guide_list_trait_lookup_failure_renders_trait_candidates(
    cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": {
                "request_id": "req-guide-list-trait-invalid",
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [
                    {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
                    {"trait": "智识", "trait_id": "2003", "score": 0.67},
                ],
                "references": [],
                "debug": {"request_id": "req-guide-list-trait-invalid"},
                "error": {"code": "GUIDE_TRAIT_INVALID", "message": "guide trait invalid: 巡烈"},
            }
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--trait", "巡烈"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_TRAIT_INVALID",
        "request id=req-guide-list-trait-invalid",
        'why msg="guide trait invalid: 巡烈"',
        "warn trait=巡猎 trait_id=2001 score=0.91",
        "warn trait=智识 trait_id=2003 score=0.67",
    ]


def test_guide_list_with_portal_and_trait_payload_mapping(cli_runner, fake_daemon_client, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.guide.fetch_cw_guide_list",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local guide list path used")),
        raising=False,
    )
    client = fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list-portal-trait",
                data={"list": [], "next_page_token": None},
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--trait", "巡猎", "--limit", "5"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok guide.list.cw count=0 more=0"]
    assert client.calls == [
        {
            "method": "guide.list.cw",
            "payload": {
                "page": 1,
                "limit": 5,
                "trait": "巡猎",
                "trait_id": None,
                "role": None,
                "role_id": None,
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


def test_guide_list_rejects_portal_and_portal_id_together(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物区", "--portal-id", "shop"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide options \'--portal\' and \'--portal-id\' are mutually exclusive"',
    ]


def test_guide_list_rejects_trait_and_trait_id_together(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--trait", "巡猎", "--trait-id", "2001"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide options \'--trait\' and \'--trait-id\' are mutually exclusive"',
    ]


def test_guide_list_rejects_role_and_role_id_together(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--role", "黑塔", "--role-id", "1001"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide options \'--role\' and \'--role-id\' are mutually exclusive"',
    ]


def test_guide_list_failure_keeps_fail_request_why_warn_order(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "guide.list.cw": {
                "request_id": "req-portal-invalid",
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [{"portal": "购物区", "score": 0.98}],
                "references": [],
                "debug": {"request_id": "req-portal-invalid"},
                "error": {"code": "GUIDE_PORTAL_INVALID", "message": "guide portal invalid: 购物曲"},
            }
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--portal", "购物曲"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_PORTAL_INVALID",
        "request id=req-portal-invalid",
        'why msg="guide portal invalid: 购物曲"',
        "warn portal=购物区 score=0.98",
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


def test_command_service_handles_guide_fetch_cw_select_and_persists_session(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    calls: list[tuple[str, object]] = []

    def fake_fetch_payload(url: str):
        calls.append(("fetch_payload", url))
        return {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": url,
            "share_code": "##demo##",
            "title": "Alpha攻略",
            "version": "3.2",
            "operation_guide": "前期 按测试运营",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "on_field": {"front_a": 1},
            "off_field": {"back_b": 2},
            "role_stages": [{"stage": "Opening", "front_roles": [{"name": "front_a"}], "back_roles": [], "traits": []}],
            "first_fight_augments": [{"name": "快攻"}],
            "second_fight_augments": [{"name": "回蓝"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
        }

    def fake_fetch_guide(url: str, *, fetcher):
        calls.append(("fetch_guide", url))
        return fetcher(url)

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_payload", fake_fetch_payload)
    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide", fake_fetch_guide)

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
            session_id=session.session_id,
        )
    )

    loaded = session_service.load_session(session.session_id)
    artifacts = list((tmp_path / ".trail" / "artifacts").glob("*.json"))

    assert payload == {
        "request_id": "req-guide.fetch.cw",
        "ok": True,
        "data": {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": "abc",
            "share_code": "##demo##",
            "title": "Alpha攻略",
            "version": "3.2",
            "operation_guide": "前期 按测试运营",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "on_field": {"front_a": 1},
            "off_field": {"back_b": 2},
            "role_stages": [{"stage": "Opening", "front_roles": [{"name": "front_a"}], "back_roles": [], "traits": []}],
            "first_fight_augments": [{"name": "快攻"}],
            "second_fight_augments": [{"name": "回蓝"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert calls == [("fetch_guide", "abc"), ("fetch_payload", "abc")]
    assert artifacts == []
    assert loaded.scene_state["cw"]["guide"] == {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "abc",
        "share_code": "##demo##",
        "title": "Alpha攻略",
        "version": "3.2",
        "operation_guide": "前期 按测试运营",
        "priority": {},
        "positioning": {},
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 8,
        "role_stages": [{"stage": "Opening", "front_roles": [{"name": "front_a"}], "back_roles": [], "traits": []}],
        "first_fight_augments": [{"name": "快攻"}],
        "second_fight_augments": [{"name": "回蓝"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
    }
    assert "artifact" not in loaded.scene_state["cw"]["guide"]
    assert "artifact_id" not in loaded.scene_state["cw"]["guide"]
    assert loaded.scene_state["cw"]["constraints"] == {
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 8,
        "priority": {},
        "positioning": {},
    }
    assert session_service.request_status("req-guide.fetch.cw")["final_state"] == "completed"


def test_command_service_guide_fetch_select_does_not_create_artifact_when_save_session_fails(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    def fake_fetch_payload(url: str):
        return {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": url,
            "share_code": "##demo##",
            "title": "Alpha攻略",
            "version": "3.2",
            "operation_guide": "前期 按测试运营",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "on_field": {"front_a": 1},
            "off_field": {"back_b": 2},
            "role_stages": [{"stage": "Opening", "front_roles": [{"name": "front_a"}], "back_roles": [], "traits": []}],
            "first_fight_augments": [{"name": "快攻"}],
            "second_fight_augments": [{"name": "回蓝"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
        }

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_payload", fake_fetch_payload)
    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide", lambda url, *, fetcher: fetcher(url))

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    monkeypatch.setattr(
        "trail.daemon.command_service.ArtifactStore.create",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select path must not create artifact")),
    )
    monkeypatch.setattr(session_service, "save_session", lambda model: (_ for _ in ()).throw(OSError("save failed")))
    service = CommandService(runtime_service=SimpleNamespace(), session_service=registry)

    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.fetch.cw",
            payload={"url": "abc", "select": True},
            session_id=session.session_id,
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {"code": "OSError", "message": "save failed"}
    assert session_service.request_status("req-guide.fetch.cw")["final_state"] == "failed_before_side_effect"
    assert list((tmp_path / ".trail" / "artifacts").glob("*.json")) == []


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
            "trait": None,
            "trait_id": 1005,
            "role": None,
            "role_id": None,
            "order": "Recent",
            "next_page_token": "token-2",
            "match_change_job": True,
            "match_hard": False,
            "workspace_root": str(tmp_path),
        }
    ]


def test_command_service_handles_guide_list_cw_with_trait_and_roles(tmp_path: Path, monkeypatch):
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
                "page": 2,
                "limit": 10,
                "trait": "巡猎",
                "trait_id": None,
                "role": ["黑塔", "大黑塔"],
                "role_id": None,
                "order": "Recent",
                "next_page_token": "token-2",
                "match_change_job": "true",
                "match_hard": "false",
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
            "page": 2,
            "limit": 10,
            "trait": "巡猎",
            "trait_id": None,
            "role": ["黑塔", "大黑塔"],
            "role_id": None,
            "order": "Recent",
            "next_page_token": "token-2",
            "match_change_job": True,
            "match_hard": False,
            "workspace_root": str(tmp_path),
        }
    ]


def test_command_service_promotes_role_warnings_to_envelope(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide_list",
        lambda **kwargs: {
            "list": [],
            "next_page_token": None,
            "role_candidates": [
                {
                    "query": "黑搭",
                    "role_resolution": "fuzzy",
                    "resolved": "黑塔",
                    "candidates": [],
                }
            ],
            "role_warnings": [
                {
                    "code": "GUIDE_ROLE_FUZZY_MATCH",
                    "query": "黑搭",
                    "resolved": "黑塔",
                    "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
                }
            ],
        },
    )

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 1,
                "limit": 20,
                "trait": None,
                "trait_id": None,
                "role": ["黑搭"],
                "role_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
            },
        )
    )

    assert payload == {
        "request_id": "req-guide.list.cw",
        "ok": True,
        "data": {
            "list": [],
            "next_page_token": None,
            "role_candidates": [
                {
                    "query": "黑搭",
                    "role_resolution": "fuzzy",
                    "resolved": "黑塔",
                    "candidates": [],
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [
            {
                "code": "GUIDE_ROLE_FUZZY_MATCH",
                "query": "黑搭",
                "resolved": "黑塔",
                "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
            }
        ],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert "role_warnings" not in payload["data"]


def test_command_service_maps_GuideTraitLookupError_to_failure_envelope(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.scenes.cw.guide import GuideTraitLookupError

    def fake_fetch_guide_list(**kwargs):
        raise GuideTraitLookupError(
            "巡烈",
            candidates=[
                {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
                {"trait": "智识", "trait_id": "2003", "score": 0.67},
            ],
        )

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_list", fake_fetch_guide_list)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 1,
                "limit": 20,
                "trait": "巡烈",
                "trait_id": None,
                "role": None,
                "role_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
            },
        )
    )

    assert payload == {
        "request_id": "req-guide.list.cw",
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [
            {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
            {"trait": "智识", "trait_id": "2003", "score": 0.67},
        ],
        "references": [],
        "debug": None,
        "error": {"code": "GUIDE_TRAIT_INVALID", "message": "guide trait invalid: 巡烈"},
    }


def test_command_service_trait_lookup_failure_renders_request_line_via_render_output(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService
    from trail.scenes.cw.guide import GuideTraitLookupError

    def fake_fetch_guide_list(**kwargs):
        raise GuideTraitLookupError(
            "巡烈",
            candidates=[
                {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
            ],
        )

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_list", fake_fetch_guide_list)

    service = CommandService(runtime_service=SimpleNamespace())
    payload = service.handle(
        _guide_request(
            workspace_root=tmp_path,
            method="guide.list.cw",
            payload={
                "page": 1,
                "limit": 20,
                "trait": "巡烈",
                "trait_id": None,
                "role": None,
                "role_id": None,
                "order": None,
                "next_page_token": None,
                "match_change_job": None,
                "match_hard": None,
            },
        )
    )

    assert render_output("guide.list.cw", payload).splitlines() == [
        "fail guide.list.cw code=GUIDE_TRAIT_INVALID",
        "request id=req-guide.list.cw",
        'why msg="guide trait invalid: 巡烈"',
        "warn trait=巡猎 trait_id=2001 score=0.91",
    ]


def test_command_service_handles_guide_config_cw_with_portal_list(tmp_path: Path, monkeypatch):
    from trail.daemon.command_service import CommandService

    monkeypatch.setattr(
        "trail.scenes.cw.guide.fetch_cw_guide_config",
        lambda **kwargs: {
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
            "trait": None,
            "trait_id": None,
            "role": None,
            "role_id": None,
            "order": None,
            "next_page_token": None,
            "match_change_job": None,
            "match_hard": None,
            "portal": "购物区",
            "workspace_root": str(tmp_path),
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
            "trait": None,
            "trait_id": None,
            "role": None,
            "role_id": None,
            "order": None,
            "next_page_token": None,
            "match_change_job": None,
            "match_hard": None,
            "portal": ["购物区", "事件区"],
            "workspace_root": str(tmp_path),
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
