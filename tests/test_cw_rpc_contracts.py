from __future__ import annotations

import inspect

import pytest

from trail.cli import app
from trail.commands.cw import cw_enter, cw_guide_app, cw_guide_apply, hand_app, slots_app
from tests.support.fake_daemon import build_success_response as _build_success_response


SESSION_ID = "a" * 32


def build_success_response(*, request_id: str, data: dict, screenshot: str | None = None) -> dict:
    response = _build_success_response(request_id=request_id, data=data, screenshot=screenshot)
    if screenshot is not None:
        response["image_guidance"] = {"read_image_first": True}
    return response
def _assert_single_call(client, *, method: str, payload: dict, tmp_path) -> None:
    assert client.calls == [
        {
            "method": method,
            "payload": {"session_id": SESSION_ID, **payload},
            "workspace_root": str(tmp_path),
            "session_id": SESSION_ID,
            "verbose": False,
        }
    ]


def _expected_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.append(f"shot path={screenshot}")
        lines.append("info read_image_first=1")
    if body:
        lines.extend(body)
    return lines


def _registered_command_names(typer_app) -> set[str]:
    return {command.name for command in typer_app.registered_commands}


def _registered_group_help(typer_app) -> str:
    return typer_app.info.help or ""


def _function_option_decls(callback) -> set[str]:
    signature = inspect.signature(callback)
    return {
        option_decl
        for parameter in signature.parameters.values()
        for option_decl in getattr(parameter.default, "param_decls", ())
    }


def test_cw_stage_detect_renders_stage_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.stage.detect": build_success_response(
                request_id="req-cw-stage-detect",
                data={"value": "preparation", "stale": False},
                screenshot=".trail/shots/req-cw-stage-detect.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.stage.detect stage=preparation stale=0",
        screenshot=".trail/shots/req-cw-stage-detect.png",
    )
    _assert_single_call(client, method="cw.stage.detect", payload={}, tmp_path=tmp_path)


def test_cw_enter_renders_home_summary_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.enter": build_success_response(
                request_id="req-cw-enter",
                data={"page": "home"},
                screenshot=".trail/shots/req-cw-enter.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "enter", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.enter page=home",
        screenshot=".trail/shots/req-cw-enter.png",
        body=[
            "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
        ],
    )
    _assert_single_call(client, method="cw.enter", payload={}, tmp_path=tmp_path)


def test_cw_enter_renders_already_home_info(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.enter": build_success_response(
                request_id="req-cw-enter-already-home",
                data={"page": "home", "already_home": True},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "enter", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.enter page=home",
        "info already_home=1",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]
    _assert_single_call(client, method="cw.enter", payload={}, tmp_path=tmp_path)


def test_cw_enter_rejects_legacy_start_options():
    option_decls = _function_option_decls(cw_enter)

    assert "--session" in option_decls
    assert "--mode" not in option_decls


def test_cw_start_renders_portal_cards_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.start": build_success_response(
                request_id="req-cw-start",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "new": 1,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "version": "3.2",
                                    "labels": [],
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                    "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                                }
                            ],
                        },
                        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
                    ],
                    "mode": "continue",
                    "difficulty": "current",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-start.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "start",
            "--session",
            SESSION_ID,
            "--mode",
            "continue",
            "--difficulty",
            "current",
            "--battle-mode",
            "standard",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.start cards=2",
        screenshot=".trail/shots/req-cw-start.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
            'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
            'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
            'opt idx=2 说明="Beta Desc"',
        ],
    )
    _assert_single_call(
        client,
        method="cw.start",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        tmp_path=tmp_path,
    )


def test_cw_start_forwards_exact_rank_payload_without_rendering_exact_rank_or_numeric_mapping(
    cli_runner,
    fake_daemon_client,
    tmp_path,
):
    client = fake_daemon_client(
        {
            "cw.start": build_success_response(
                request_id="req-cw-start-exact-rank",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "guides": [],
                        }
                    ],
                    "mode": "continue",
                    "difficulty": "A7-3",
                    "requested_difficulty": "A7-3",
                    "target_enemy_difficulty": 51,
                    "current_enemy_difficulty": 54,
                    "page": "invest",
                    "reason": "exact_rank_requested",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-start-exact-rank.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "start",
            "--session",
            SESSION_ID,
            "--mode",
            "continue",
            "--difficulty",
            "A7-3",
            "--battle-mode",
            "standard",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.start cards=1",
        screenshot=".trail/shots/req-cw-start-exact-rank.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=0',
            'opt idx=1 说明="Alpha Desc"',
        ],
    )
    assert "A7-3" not in result.stdout
    assert "51" not in result.stdout
    assert "requested_difficulty=" not in result.stdout
    assert "target_enemy_difficulty=" not in result.stdout
    _assert_single_call(
        client,
        method="cw.start",
        payload={"mode": "continue", "difficulty": "A7-3", "battle_mode": "standard"},
        tmp_path=tmp_path,
    )


def test_cw_portal_select_renders_selected_card_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.portal.select": build_success_response(
                request_id="req-cw-portal-select",
                data={
                    "card_idx": 2,
                    "portal_title": "Beta Portal",
                    "portal_description": "Beta Desc",
                    "score": 0.88,
                    "equipment": {
                        "count": 0,
                        "uncertain": 0,
                        "empty": 60,
                        "backend": "vector",
                        "layout": "default",
                        "items": [],
                        "stale": False,
                    },
                    "shop": {
                        "items": [{"slot": 1, "name": "银狼", "price": 20}],
                        "coins": 40,
                        "reserve_full": False,
                    },
                },
                screenshot=".trail/shots/req-cw-portal-select.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", SESSION_ID, "--card-idx", "2"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        'ok cw.portal.select idx=2 投资环境="Beta Portal"',
        screenshot=".trail/shots/req-cw-portal-select.png",
        body=[
            "# 装备信息",
            "info count=0 uncertain=0 empty=60 backend=vector layout=default",
            "# 商店信息",
            "item idx=1 slot=1 name=银狼 cost=20",
            "info coins=40 reserve_full=0",
            "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
        ],
    )
    lines = result.stdout.splitlines()
    assert "# 装备信息" in lines
    assert "info count=0 uncertain=0 empty=60 backend=vector layout=default" in lines
    assert lines.index("# 装备信息") < lines.index("# 商店信息")
    assert result.stdout.rstrip().endswith(
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
    )
    _assert_single_call(client, method="cw.portal.select", payload={"card_idx": 2}, tmp_path=tmp_path)


def test_cw_portal_select_renders_equipment_warning_shop_and_handoff_last(
    cli_runner, fake_daemon_client, tmp_path
):
    response = build_success_response(
        request_id="req-cw-portal-select",
        data={
            "card_idx": 2,
            "portal_title": "Beta Portal",
            "shop": {
                "items": [{"slot": 1, "name": "银狼", "price": 20}],
                "coins": 40,
                "reserve_full": False,
            },
        },
        screenshot=".trail/shots/req-cw-portal-select.png",
    )
    response["warnings"] = [
        {
            "code": "CW_EQUIPMENT_AUTO_COLLECT_FAILED",
            "message": "equipment grid requires canonical 1920x1080 screenshot",
        }
    ]
    client = fake_daemon_client({"cw.portal.select": response})

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", SESSION_ID, "--card-idx", "2"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    warning_line = 'warn code=CW_EQUIPMENT_AUTO_COLLECT_FAILED msg="equipment grid requires canonical 1920x1080 screenshot"'
    handoff_line = "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
    shop_title_index = lines.index("# 商店信息")
    shop_item_index = lines.index("item idx=1 slot=1 name=银狼 cost=20")
    warning_index = lines.index(warning_line)

    assert shop_title_index < warning_index
    assert shop_item_index < warning_index
    assert lines[-2:] == [warning_line, handoff_line]
    _assert_single_call(client, method="cw.portal.select", payload={"card_idx": 2}, tmp_path=tmp_path)


def test_cw_portal_select_renders_recoverable_unknown_result_lines(cli_runner, fake_daemon_client, tmp_path):
    fake_daemon_client(
        {
            "cw.portal.select": {
                "request_id": "req-cw-portal-select-unknown",
                "ok": False,
                "data": {"tainted": True},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"last_known_stage": "side_effect_applied"},
                "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
            }
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", SESSION_ID, "--card-idx", "2"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.portal.select code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-portal-select-unknown",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-portal-select-unknown",
    ]


def test_cw_portal_detect_renders_portal_cards_family(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.portal.detect": build_success_response(
                request_id="req-cw-portal-detect",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                }
                            ],
                        }
                    ],
                    "mode": None,
                    "difficulty": None,
                    "battle_mode": None,
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-portal-detect.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "detect", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.portal.detect cards=1",
        screenshot=".trail/shots/req-cw-portal-detect.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=0',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
        ],
    )
    _assert_single_call(client, method="cw.portal.detect", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "payload", "response_data", "screenshot"),
    [
        (
            ["cw", "strategy", "detect", "--session", SESSION_ID],
            "cw.strategy.detect",
            {},
            {
                "cards": [
                    {
                        "card_idx": 1,
                        "strategy_title": "快攻",
                        "strategy_description": "desc",
                        "refresh_count": 1,
                        "guide_match": "优选",
                        "guide_loaded": 1,
                    }
                ],
                "stale": False,
            },
            ".trail/shots/req-cw-strategy-detect.png",
        ),
        (
            ["cw", "strategy", "select", "--session", SESSION_ID, "--card-idx", "2"],
            "cw.strategy.select",
            {"card_idx": 2},
            {
                "card_idx": 2,
                "strategy_title": "回蓝",
                "strategy_description": "desc",
                "refresh_count": 1,
                "guide_match": "次选",
                "guide_loaded": 1,
            },
            ".trail/shots/req-cw-strategy-select.png",
        ),
        (
            ["cw", "strategy", "refresh", "--session", SESSION_ID, "--card-idx", "3"],
            "cw.strategy.refresh",
            {"card_idx": 3},
            {
                "cards": [
                    {
                        "card_idx": 3,
                        "strategy_title": "暴击",
                        "strategy_description": "desc",
                        "refresh_count": 2,
                        "guide_match": "否",
                        "guide_loaded": 1,
                    }
                ],
                "stale": False,
            },
            ".trail/shots/req-cw-strategy-refresh.png",
        ),
    ],
)
def test_cw_strategy_rpc_contracts(cli_runner, fake_daemon_client, tmp_path, args, method: str, payload: dict, response_data: dict, screenshot: str):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data=response_data,
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    _assert_single_call(client, method=method, payload=payload, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "screenshot"),
    [
        (["cw", "portal", "refresh", "--session", SESSION_ID], "cw.portal.refresh", ".trail/shots/req-cw-portal-refresh.png"),
        (["cw", "portal", "restart", "--session", SESSION_ID], "cw.portal.restart", ".trail/shots/req-cw-portal-restart.png"),
    ],
)
def test_cw_portal_refresh_and_restart_render_portal_cards(cli_runner, fake_daemon_client, tmp_path, args, method: str, screenshot: str):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "new": 1,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "version": "3.2",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                    "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                                }
                            ],
                        },
                        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
                    ],
                    "mode": "continue",
                    "difficulty": "current",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        f"ok {method} cards=2",
        screenshot=screenshot,
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
            'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
            'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
            'opt idx=2 说明="Beta Desc"',
        ],
    )
    _assert_single_call(client, method=method, payload={}, tmp_path=tmp_path)


def test_cw_shop_buy_slot_renders_purchase_summary_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.buy_slot": build_success_response(
                request_id="req-cw-shop-buy-slot",
                data={
                    "items": [
                        {"slot": 2, "name": "停云", "price": 10},
                        {"slot": 1, "name": "银狼", "price": 20},
                    ],
                    "opened": True,
                    "stale": False,
                    "role_verification": {
                        "name": "银狼",
                        "before_count": 8,
                        "after_count": 9,
                        "delta": 1,
                        "required": 1,
                        "verified": True,
                    },
                    "slots": {
                        "front": [{"name": "银狼", "star": 3}],
                        "back": [],
                        "hand": [],
                        "stale": False,
                    },
                    "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
                },
                screenshot=".trail/shots/req-cw-shop-buy-slot.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "shop", "buy-slot", "--session", SESSION_ID, "--slot", "2", "--expect", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.buy_slot opened=1 stale=0 count=2",
        screenshot=".trail/shots/req-cw-shop-buy-slot.png",
        body=[
            "# 综合信息",
            "info action=buy_slot role=银狼 verified=1 before_count=8 after_count=9 delta=1 required=1",
            "# 商店信息",
            "item idx=1 slot=1 name=银狼 cost=20",
            "item idx=2 slot=2 name=停云 cost=10",
            "# 角色信息",
            "slot pos=front:1 name=银狼 star=3",
        ],
    )
    _assert_single_call(client, method="cw.shop.buy_slot", payload={"slot": 2, "expect": "希儿"}, tmp_path=tmp_path)


def test_cw_shop_buy_exp_maps_to_canonical_command(cli_runner, fake_daemon_client, tmp_path) -> None:
    client = fake_daemon_client(
        {
            "cw.shop.buy_exp": build_success_response(
                request_id="req-cw-shop-buy-exp",
                data={
                    "opened": True,
                    "stale": False,
                    "items": [{"slot": 1, "name": "灵砂", "price": 3}],
                    "coins": 36,
                    "level": 4,
                    "exp": "0/8",
                    "reserve_full": False,
                    "team_size": "4/4",
                },
                screenshot=".trail/shots/req-cw-shop-buy-exp.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "buy-exp", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.buy_exp opened=1 stale=0 count=1",
        screenshot=".trail/shots/req-cw-shop-buy-exp.png",
        body=[
            "# 商店信息",
            "item idx=1 slot=1 name=灵砂 cost=3",
            "info coins=36 reserve_full=0",
            "# 综合信息",
            "info level=4 exp=0/8 team_size=4/4",
        ],
    )
    _assert_single_call(client, method="cw.shop.buy_exp", payload={}, tmp_path=tmp_path)


def test_cw_equipment_prepare_maps_to_canonical_command(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.prepare": build_success_response(
                request_id="req-equipment-prepare",
                data={"big_version": "3.2", "count": 2, "cached": 1, "downloaded": 1, "refreshed": True},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "equipment", "prepare", "--session", SESSION_ID, "--refresh"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.equipment.prepare big_version=3.2 count=2 cached=1 downloaded=1 refreshed=1"
    ]
    _assert_single_call(client, method="cw.equipment.prepare", payload={"refresh": True}, tmp_path=tmp_path)


def test_cw_equipment_read_maps_to_canonical_command_and_supports_yaml(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.read": build_success_response(
                request_id="req-equipment-read",
                data={"count": 0, "uncertain": 0, "empty": 60, "items": [], "backend": "vector", "layout": "default"},
                screenshot=".trail/shots/req-equipment-read.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "equipment", "read", "--session", SESSION_ID])
    yaml_client = fake_daemon_client(
        {
            "cw.equipment.read": build_success_response(
                request_id="req-equipment-read-yaml",
                data={"count": 0, "uncertain": 0, "empty": 60, "items": [], "backend": "vector", "layout": "default"},
                screenshot=".trail/shots/req-equipment-read.png",
            )
        }
    )
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "cw", "equipment", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.equipment.read count=0 uncertain=0 empty=60",
        "shot path=.trail/shots/req-equipment-read.png",
        "info read_image_first=1",
        "info backend=vector layout=default",
    ]
    assert yaml_result.exit_code == 0
    yaml_lines = yaml_result.stdout.splitlines()
    assert yaml_lines[:3] == [
        "ok cw.equipment.read count=0 uncertain=0 empty=60",
        "shot path=.trail/shots/req-equipment-read.png",
        "info read_image_first=1",
    ]
    assert "items: []" in yaml_result.stdout
    assert "backend: vector" in yaml_result.stdout
    assert client.calls[0]["method"] == "cw.equipment.read"
    _assert_single_call(client, method="cw.equipment.read", payload={}, tmp_path=tmp_path)
    _assert_single_call(yaml_client, method="cw.equipment.read", payload={}, tmp_path=tmp_path)


def test_cw_equipment_compose_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose",
                data={
                    "pos": "front:1",
                    "name": "希儿",
                    "equipment": "高周波电锯",
                    "count": 2,
                    "materials": [
                        {"idx": 2, "pos": "equipment:2", "name": "基础装甲"},
                        {"idx": 5, "pos": "equipment:5", "name": "光能电池"},
                    ],
                    "result_item": {"idx": 2, "pos": "equipment:2", "name": "高周波电锯"},
                    "compose_action": {"drag_from": "equipment:5", "drag_to": "equipment:2"},
                    "equip_action": {"drag_from": "equipment:2", "drag_to": "front:1"},
                    "verified": True,
                    "consumed": 2,
                    "post_compose_equipment_count": 2,
                    "post_equip_equipment_count": 1,
                    "verified_shift": False,
                },
                screenshot=".trail/shots/req-cw-equipment-compose.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2",
        "shot path=.trail/shots/req-cw-equipment-compose.png",
        "info read_image_first=1",
        "# 综合信息",
        "info action=compose drag_from=equipment:5 drag_to=equipment:2 verified=1 consumed=2 post_compose_equipment_count=2 verified_shift=0",
        "info action=equip drag_from=equipment:2 drag_to=front:1 verified=1 post_equip_equipment_count=1 equipment_stale=1",
        "# 装备信息",
        "item kind=material phase=pre_compose idx=2 pos=equipment:2 name=基础装甲",
        "item kind=material phase=pre_compose idx=5 pos=equipment:5 name=光能电池",
        "item kind=result phase=post_compose idx=2 pos=equipment:2 name=高周波电锯",
        "# 角色信息",
        "slot pos=front:1 name=希儿 装备=高周波电锯 count=2",
    ]
    _assert_single_call(
        client,
        method="cw.equipment.compose",
        payload={"name": "高周波电锯", "slot": "front:0", "role": "希儿"},
        tmp_path=tmp_path,
    )


def test_cw_equipment_compose_direct_existing_target_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose-direct",
                data={
                    "action": "equip_existing",
                    "pos": "front:1",
                    "name": "希儿",
                    "equipment": "高周波电锯",
                    "count": 2,
                    "role": "希儿",
                    "slot": "front:1",
                    "equipment_name": "高周波电锯",
                    "existing_item": {
                        "idx": 4,
                        "pos": "equipment:4",
                        "name": "高周波电锯",
                        "score": 0.99,
                        "uncertain": False,
                    },
                    "equip_action": {"drag_from": "equipment:4", "drag_to": "front:1"},
                    "verified": True,
                    "consumed": 0,
                    "post_equip_equipment_count": 3,
                    "equipment_stale": True,
                },
                screenshot=".trail/shots/req-cw-equipment-compose-direct.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines == [
        "ok cw.equipment.compose pos=front:1 name=希儿 装备=高周波电锯 count=2",
        "shot path=.trail/shots/req-cw-equipment-compose-direct.png",
        "info read_image_first=1",
        "# 综合信息",
        "info action=equip_existing verified=1 consumed=0 post_equip_equipment_count=3 equipment_stale=1",
        "info action=equip drag_from=equipment:4 drag_to=front:1 verified=1 post_equip_equipment_count=3 equipment_stale=1",
        "# 装备信息",
        "item kind=existing phase=pre_equip idx=4 pos=equipment:4 name=高周波电锯 score=0.99 uncertain=0",
        "# 角色信息",
        "slot pos=front:1 name=希儿 装备=高周波电锯 count=2",
    ]
    rendered = "\n".join(lines)
    assert "action=compose" not in rendered
    assert "post_compose_equipment_count" not in rendered
    assert "verified_shift" not in rendered
    assert "kind=material" not in rendered
    assert "phase=post_compose" not in rendered
    _assert_single_call(
        client,
        method="cw.equipment.compose",
        payload={"name": "高周波电锯", "slot": "front:0", "role": "希儿"},
        tmp_path=tmp_path,
    )


def test_cw_equipment_compose_rejects_zero_slot_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(
        app,
        ["cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:0", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert "fail cw.equipment.compose code=CW_OPTION_INVALID" in result.stdout
    assert client.calls == []


def test_cw_equipment_compose_rejects_yaml_output(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "cw.equipment.compose": build_success_response(
                request_id="req-cw-equipment-compose-yaml",
                data={"pos": "front:1", "name": "希儿", "equipment": "高周波电锯", "count": 1},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["--format", "yaml", "cw", "equipment", "compose", "--session", SESSION_ID, "--name", "高周波电锯", "--slot", "front:1", "--role", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.equipment.compose code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.equipment.compose"',
    ]
    assert client.calls == []


def test_cw_equipment_prepare_rejects_yaml_output_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "cw.equipment.prepare": build_success_response(
                request_id="req-cw-equipment-prepare-yaml",
                data={"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": True},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["--format", "yaml", "cw", "equipment", "prepare", "--session", SESSION_ID, "--refresh"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.equipment.prepare code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.equipment.prepare"',
    ]
    assert client.calls == []


def test_cw_slots_read_rejects_yaml_output_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "cw.slots.read": build_success_response(
                request_id="req-cw-slots-read-yaml",
                data={"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False},
                screenshot=".trail/shots/req-cw-slots-read-yaml.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "cw", "slots", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.slots.read"',
    ]
    assert client.calls == []


def test_cw_shop_scan_renders_unknown_result_failure_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.scan": {
                "request_id": "req-cw-shop-scan-unknown",
                "ok": False,
                "data": {},
                "screenshot": ".trail/shots/req-cw-shop-scan-unknown.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {
                    "request_id": "req-cw-shop-scan-unknown",
                    "last_known_stage": "side_effect_applied",
                    "detail": "flush failed",
                },
                "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
            }
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "scan", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-unknown",
        "shot path=.trail/shots/req-cw-shop-scan-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-unknown",
    ]
    _assert_single_call(client, method="cw.shop.scan", payload={}, tmp_path=tmp_path)


def test_cw_guide_current_renders_guide_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.guide.current": build_success_response(
                request_id="req-cw-guide-current",
                data={
                    "lineup_id": "abc",
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "version": "3.2",
                    "labels": ["7级搜牌"],
                    "support_hard": True,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "current", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
        "guide 攻略标签=#7级搜牌|#适用超频博弈",
    ]
    assert "artifact" not in result.stdout
    assert "攻略快照ID" not in result.stdout
    _assert_single_call(client, method="cw.guide.current", payload={}, tmp_path=tmp_path)


def test_cw_guide_help_describes_selected_guide_flow():
    normalized = " ".join(_registered_group_help(cw_guide_app).split())

    assert "当前已选攻略" in normalized
    assert "guide.fetch.cw --select" in normalized
    assert "cw.portal.select" in normalized
    assert "cw.guide.apply 只作为手动兜底" in normalized
    assert normalized.index("guide.fetch.cw --select") < normalized.index("cw.portal.select")
    assert normalized.index("cw.portal.select") < normalized.index("cw.guide.apply 只作为手动兜底")
    assert "当前已应用攻略" not in normalized
    assert "已经 apply 过后" not in normalized
    assert "--lineup-id" not in normalized
    assert "--guide" not in normalized


def test_cw_guide_apply_renders_guide_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.guide.apply": build_success_response(
                request_id="req-cw-guide-apply",
                data={
                    "lineup_id": "abc",
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "version": "3.2",
                    "labels": ["7级搜牌"],
                    "support_hard": True,
                },
                screenshot=".trail/shots/req-cw-guide-apply.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.guide.apply 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
        screenshot=".trail/shots/req-cw-guide-apply.png",
        body=["guide 攻略标签=#7级搜牌|#适用超频博弈"],
    )
    assert "artifact" not in result.stdout
    assert "攻略快照ID" not in result.stdout
    _assert_single_call(client, method="cw.guide.apply", payload={}, tmp_path=tmp_path)


def test_cw_guide_apply_rejects_legacy_cli_flags():
    option_decls = _function_option_decls(cw_guide_apply)

    assert "--session" in option_decls
    assert "--lineup-id" not in option_decls
    assert "--guide" not in option_decls


def test_cw_slots_place_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.slots.place": build_success_response(
                request_id="req-cw-slots-place",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
                screenshot=".trail/shots/req-cw-slots-place.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "slots",
            "place",
            "--session",
            SESSION_ID,
            "--action",
            "hand:1,front:1",
            "--action",
            "hand:2,back:3",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.slots.place front=1 back=1 hand=0 stale=1",
        screenshot=".trail/shots/req-cw-slots-place.png",
    )
    _assert_single_call(
        client,
        method="cw.slots.place",
        payload={
            "actions": [
                {"source": "hand:0", "target": "front:0"},
                {"source": "hand:1", "target": "back:2"},
            ]
        },
        tmp_path=tmp_path,
    )


def test_cw_hand_sell_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.hand.sell": build_success_response(
                request_id="req-cw-hand-sell",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [None, "银狼", None], "stale": True},
                screenshot=".trail/shots/req-cw-hand-sell.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "1", "--slot", "3"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.hand.sell front=1 back=1 hand=1 stale=1",
        screenshot=".trail/shots/req-cw-hand-sell.png",
    )
    _assert_single_call(client, method="cw.hand.sell", payload={"slots": [0, 2]}, tmp_path=tmp_path)


def test_cw_slots_place_one_is_removed():
    assert "place-one" not in _registered_command_names(slots_app)


def test_cw_slots_place_requires_at_least_one_action(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.place code=CW_OPTION_INVALID",
        'why msg="cw slots place requires at least one --action"',
    ]
    assert client.calls == []


def test_cw_slots_place_rejects_malformed_action_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID, "--action", "hand:0-front:0"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.place code=CW_OPTION_INVALID",
        'why msg="cw slots place action invalid"',
    ]
    assert "hand:0" not in result.stdout
    assert "hand:1" not in result.stdout
    assert "front:0" not in result.stdout
    assert "front:1" not in result.stdout
    assert client.calls == []


def test_cw_hand_sell_one_is_removed():
    assert "sell-one" not in _registered_command_names(hand_app)


def test_cw_hand_sell_requires_at_least_one_slot(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "hand", "sell", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.hand.sell code=CW_OPTION_INVALID",
        'why msg="cw hand sell requires at least one --slot"',
    ]
    assert client.calls == []


def test_cw_invest_choose_renders_stage_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.invest.choose": build_success_response(
                request_id="req-cw-invest-choose",
                data={"value": "battle", "stale": False},
                screenshot=".trail/shots/req-cw-invest-choose.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "invest", "choose", "--session", SESSION_ID, "--option", "2"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.invest.choose stage=battle stale=0",
        screenshot=".trail/shots/req-cw-invest-choose.png",
    )
    _assert_single_call(client, method="cw.invest.choose", payload={"option": 2}, tmp_path=tmp_path)


def test_cw_battle_continue_renders_stage_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.continue": build_success_response(
                request_id="req-cw-battle-continue",
                data={"value": "settle", "stale": False},
                screenshot=".trail/shots/req-cw-battle-continue.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "continue", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.battle.continue stage=settle stale=0",
        screenshot=".trail/shots/req-cw-battle-continue.png",
    )
    _assert_single_call(client, method="cw.battle.continue", payload={}, tmp_path=tmp_path)


def test_cw_battle_run_forwards_new_default_timeout(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run",
                data={},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 90}, tmp_path=tmp_path)


def test_cw_battle_run_renders_completed_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-completed",
                data={
                    "status": "completed",
                    "result": "win",
                    "stage": "shop",
                    "stale": False,
                    "in_battle": False,
                    "round": "1-1",
                    "hp": 82,
                    "coins": 4,
                    "exp": 2,
                    "settle_text": "挑战成功",
                },
                screenshot=".trail/shots/req-cw-battle-run-completed.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=completed result=win stage=shop stale=0 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-completed.png",
        "info read_image_first=1",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_battle_run_renders_timeout_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-timeout",
                data={"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": 570},
                screenshot=".trail/shots/req-cw-battle-run-timeout.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req-cw-battle-run-timeout.png",
        "info read_image_first=1",
        "info timeout_seconds=570",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_battle_clear_in_progress_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.clear_in_progress": build_success_response(
                request_id="req-cw-battle-clear-in-progress",
                data={},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "clear-in-progress", "--session", SESSION_ID])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.clear_in_progress", payload={}, tmp_path=tmp_path)


def test_cw_battle_clear_in_progress_renders_summary(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.battle.clear_in_progress": build_success_response(
                request_id="req-cw-battle-clear-in-progress",
                data={"cleared": True},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "clear-in-progress", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.battle.clear_in_progress cleared=1"]


def test_cw_battle_clear_in_progress_rejects_yaml_output(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.battle.clear_in_progress": build_success_response(
                request_id="req-cw-battle-clear-in-progress-yaml",
                data={"cleared": True},
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["--format", "yaml", "cw", "battle", "clear-in-progress", "--session", SESSION_ID],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.battle.clear_in_progress code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.battle.clear_in_progress"',
    ]


def test_cw_stage_wait_renders_stage_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.stage.wait": build_success_response(
                request_id="req-cw-stage-wait",
                data={"value": "settle", "stale": False},
                screenshot=".trail/shots/req-cw-stage-wait.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "stage", "wait", "--session", SESSION_ID, "--timeout", "120"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.stage.wait stage=settle stale=0",
        screenshot=".trail/shots/req-cw-stage-wait.png",
    )
    _assert_single_call(client, method="cw.stage.wait", payload={"timeout": 120}, tmp_path=tmp_path)


def test_cw_shop_status_renders_items_in_slot_order(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status",
                data={
                    "items": [
                        {"slot": 2, "name": "停云", "price": 1},
                        {"slot": 1, "name": "希儿", "price": 2},
                    ],
                    "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
                },
                screenshot=None,
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.status count=2",
        body=[
            "item idx=1 slot=1 name=希儿 cost=2",
            "item idx=2 slot=2 name=停云 cost=1",
        ],
    )
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_shop_scan_renders_fresh_stage_status_projection(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.scan": build_success_response(
                request_id="req-cw-shop-scan-stage-fresh",
                data={
                    "items": [{"slot": 1, "name": "银狼", "price": 20}],
                    "opened": True,
                    "stale": False,
                    "stage_status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
                    "stage_status_stale": False,
                },
                screenshot=".trail/shots/req-cw-shop-scan-stage-fresh.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "scan", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.scan opened=1 stale=0 count=1",
        screenshot=".trail/shots/req-cw-shop-scan-stage-fresh.png",
        body=[
            "# 商店信息",
            "item idx=1 slot=1 name=银狼 cost=20",
            "# 综合信息",
            "info stage_level=7 stage_exp=4/52 stage_team_size=3/3 stage_status_stale=0",
        ],
    )
    _assert_single_call(client, method="cw.shop.scan", payload={}, tmp_path=tmp_path)


def test_cw_shop_status_renders_stale_stage_status_without_stale_values(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status-stage-stale",
                data={
                    "items": [{"slot": 1, "name": "银狼", "price": 20}],
                    "stage_status": {"stale": True, "level": 7, "exp": "4/52", "team_size": "3/3"},
                    "stage_status_stale": True,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.status count=1",
        body=["# 商店信息", "item idx=1 slot=1 name=银狼 cost=20", "# 综合信息", "info stage_status_stale=1"],
    )
    assert "stage_level=7" not in result.stdout
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_shop_status_renders_missing_stage_status_as_stale(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status-stage-missing",
                data={"stale": True, "stage_status_stale": True},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.shop.status count=0", "info stage_status_stale=1"]
    assert "stage_level" not in result.stdout
    assert "stage_exp" not in result.stdout
    assert "stage_team_size" not in result.stdout
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_shop_status_renders_empty_status_without_sections(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status-empty",
                data={"items": []},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.shop.status count=0"]
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_event_handle_renders_event_result(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.event.handle": build_success_response(
                request_id="req-cw-event-handle",
                data={"event_type": "special", "handled_action": "confirm"},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.event.handle event_type=special handled_action=confirm"]
    _assert_single_call(client, method="cw.event.handle", payload={}, tmp_path=tmp_path)


def test_cw_invest_read_renders_options(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.invest.read": build_success_response(
                request_id="req-cw-invest-read",
                data={"options": [{"id": 1, "name": "量子力学"}]},
                screenshot=".trail/shots/req-cw-invest-read.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "invest", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.invest.read count=1",
        screenshot=".trail/shots/req-cw-invest-read.png",
        body=["opt idx=1 id=1 name=量子力学"],
    )
    _assert_single_call(client, method="cw.invest.read", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "payload", "response_data", "screenshot", "expected_lines"),
    [
        (
            ["cw", "slots", "read", "--session", SESSION_ID, "--slot", "front:1", "--slot", "back:2"],
            "cw.slots.read",
            {"slot": ["front:0", "back:1"]},
            {"front": ["希儿", None], "back": [None], "hand": ["停云", None], "stale": False},
            ".trail/shots/req-cw-slots-read.png",
            _expected_lines(
                "ok cw.slots.read front=1 back=0 hand=1 stale=0",
                screenshot=".trail/shots/req-cw-slots-read.png",
                body=[
                    "# 角色信息",
                    "slot pos=front:1 name=希儿",
                    "slot pos=front:2 empty=1",
                    "slot pos=back:1 empty=1",
                    "slot pos=hand:1 name=停云",
                    "slot pos=hand:2 empty=1",
                ],
            ),
        ),
        (
            ["cw", "slots", "swap", "--session", SESSION_ID, "--source", "hand:1", "--target", "front:1"],
            "cw.slots.swap",
            {"source": "hand:0", "target": "front:0"},
            {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
            ".trail/shots/req-cw-slots-swap.png",
            _expected_lines(
                "ok cw.slots.swap front=1 back=1 hand=0 stale=1",
                screenshot=".trail/shots/req-cw-slots-swap.png",
            ),
        ),
        (
            ["cw", "replenish", "read", "--session", SESSION_ID],
            "cw.replenish.read",
            {},
            {"options": [1, 2, 3]},
            ".trail/shots/req-cw-replenish-read.png",
            _expected_lines(
                "ok cw.replenish.read count=3",
                screenshot=".trail/shots/req-cw-replenish-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2", "opt idx=3 value=3"],
            ),
        ),
        (
            ["cw", "replenish", "choose", "--session", SESSION_ID, "--option", "2"],
            "cw.replenish.choose",
            {"option": 2},
            {"value": "shop", "stale": False},
            ".trail/shots/req-cw-replenish-choose.png",
            _expected_lines(
                "ok cw.replenish.choose stage=shop stale=0",
                screenshot=".trail/shots/req-cw-replenish-choose.png",
            ),
        ),
        (
            ["cw", "crystals", "collect", "--session", SESSION_ID],
            "cw.crystals.collect",
            {},
            {"last_crystal_collection": "done"},
            ".trail/shots/req-cw-crystals-collect.png",
            _expected_lines(
                "ok cw.crystals.collect status=done",
                screenshot=".trail/shots/req-cw-crystals-collect.png",
            ),
        ),
        (
            ["cw", "hand", "sell-plan", "--session", SESSION_ID],
            "cw.hand.sell_plan",
            {},
            {
                "reference_only": True,
                "candidates": [],
                "todos": ["stage"],
                "items": [
                    {
                        "slot": 0,
                        "name": "阮·梅",
                        "star": 1,
                        "target_star": None,
                        "current_star": None,
                        "category": "非攻略",
                        "recommendation": "不推荐",
                        "priority": 10,
                        "protected": False,
                        "reason": "缺少当前阶段，仅提供参考",
                    }
                ],
            },
            None,
            [
                "ok cw.hand.sell_plan count=1 reference_only=1 candidates=0 todos=1",
                "slot pos=hand:1 name=阮·梅 star=1 分类=非攻略 推荐度=不推荐 priority=10 protected=0 reason=缺少当前阶段，仅提供参考",
                "info todo=stage",
            ],
        ),
        (
            ["cw", "shop", "open", "--session", SESSION_ID],
            "cw.shop.open",
            {},
            {
                "items": [{"slot": 2, "name": "停云", "price": 10}, {"slot": 1, "name": "银狼", "price": 20}],
                "opened": True,
                "stale": True,
            },
            ".trail/shots/req-cw-shop-open.png",
            _expected_lines(
                "ok cw.shop.open opened=1 stale=1 count=2",
                screenshot=".trail/shots/req-cw-shop-open.png",
                body=["item idx=1 slot=1 name=银狼 cost=20", "item idx=2 slot=2 name=停云 cost=10"],
            ),
        ),
        (
            ["cw", "shop", "scan", "--session", SESSION_ID],
            "cw.shop.scan",
            {},
            {
                "items": [{"slot": 1, "name": "银狼", "price": 20}],
                "opened": True,
                "stale": False,
                "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
            },
            ".trail/shots/req-cw-shop-scan.png",
            _expected_lines(
                "ok cw.shop.scan opened=1 stale=0 count=1",
                screenshot=".trail/shots/req-cw-shop-scan.png",
                body=["item idx=1 slot=1 name=银狼 cost=20"],
            ),
        ),
        (
            ["cw", "shop", "refresh", "--session", SESSION_ID],
            "cw.shop.refresh",
            {},
            {"items": [{"slot": 1, "name": "阮·梅", "price": 30}], "opened": False, "stale": True},
            ".trail/shots/req-cw-shop-refresh.png",
            _expected_lines(
                "ok cw.shop.refresh opened=0 stale=1 count=1",
                screenshot=".trail/shots/req-cw-shop-refresh.png",
                body=["item idx=1 slot=1 name=阮·梅 cost=30"],
            ),
        ),
        (
            ["cw", "shop", "close", "--session", SESSION_ID],
            "cw.shop.close",
            {},
            {"items": [], "opened": False, "stale": True},
            ".trail/shots/req-cw-shop-close.png",
            _expected_lines(
                "ok cw.shop.close opened=0 stale=1 count=0",
                screenshot=".trail/shots/req-cw-shop-close.png",
            ),
        ),
        (
            ["cw", "encounter", "read", "--session", SESSION_ID],
            "cw.encounter.read",
            {},
            {"options": [1, 2]},
            ".trail/shots/req-cw-encounter-read.png",
            _expected_lines(
                "ok cw.encounter.read count=2",
                screenshot=".trail/shots/req-cw-encounter-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2"],
            ),
        ),
        (
            ["cw", "encounter", "choose", "--session", SESSION_ID, "--option", "1"],
            "cw.encounter.choose",
            {"option": 1},
            {"value": "event", "stale": False},
            None,
            ["ok cw.encounter.choose stage=event stale=0"],
        ),
        (
            ["cw", "fortune", "read", "--session", SESSION_ID],
            "cw.fortune.read",
            {},
            {"options": [1, 2]},
            ".trail/shots/req-cw-fortune-read.png",
            _expected_lines(
                "ok cw.fortune.read count=2",
                screenshot=".trail/shots/req-cw-fortune-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2"],
            ),
        ),
        (
            ["cw", "fortune", "choose", "--session", SESSION_ID, "--option", "1"],
            "cw.fortune.choose",
            {"option": 1},
            {"value": "battle", "stale": False},
            None,
            ["ok cw.fortune.choose stage=battle stale=0"],
        ),
        (
            ["cw", "boss-preview", "confirm", "--session", SESSION_ID],
            "cw.boss_preview.confirm",
            {},
            {"value": "battle", "stale": False},
            None,
            ["ok cw.boss_preview.confirm stage=battle stale=0"],
        ),
        (
            ["cw", "battle", "start", "--session", SESSION_ID],
            "cw.battle.start",
            {},
            {"value": "battle", "stale": False},
            ".trail/shots/req-cw-battle-start.png",
            _expected_lines(
                "ok cw.battle.start stage=battle stale=0",
                screenshot=".trail/shots/req-cw-battle-start.png",
            ),
        ),
        (
            ["cw", "settle", "next", "--session", SESSION_ID],
            "cw.settle.next",
            {},
            {"value": "shop", "stale": False},
            ".trail/shots/req-cw-settle-next.png",
            _expected_lines(
                "ok cw.settle.next stage=shop stale=0",
                screenshot=".trail/shots/req-cw-settle-next.png",
            ),
        ),
    ],
    ids=[
        "cw_slots_read",
        "cw_slots_swap",
        "cw_replenish_read",
        "cw_replenish_choose",
        "cw_crystals_collect",
        "cw_hand_sell_plan",
        "cw_shop_open",
        "cw_shop_scan",
        "cw_shop_refresh",
        "cw_shop_close",
        "cw_encounter_read",
        "cw_encounter_choose",
        "cw_fortune_read",
        "cw_fortune_choose",
        "cw_boss_preview_confirm",
        "cw_battle_start",
        "cw_settle_next",
    ],
)
def test_cw_rpc_wrapper_matrix(
    cli_runner,
    fake_daemon_client,
    tmp_path,
    args,
    method: str,
    payload: dict,
    response_data: dict,
    screenshot: str | None,
    expected_lines: list[str],
):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data=response_data,
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == expected_lines
    _assert_single_call(client, method=method, payload=payload, tmp_path=tmp_path)


def test_cw_slots_read_contract_includes_stage_projection(cli_runner, fake_daemon_client, tmp_path):
    response_data = {
        "front": [{"name": "希儿"}],
        "back": [],
        "hand": [],
        "stale": False,
        "stage": "preparation",
        "stage_stale": False,
        "stage_status": {
            "stale": False,
            "level": 3,
            "exp": "0/8",
            "team_size": "1/2",
            "role_count": {"front": 1, "back": 0, "hand": 0, "field": 1, "total": 1},
        },
        "stage_status_stale": False,
    }
    client = fake_daemon_client(
        {
            "cw.slots.read": build_success_response(
                request_id="req-cw-slots-read-stage-projection",
                data=response_data,
                screenshot=".trail/shots/req-cw-slots-read-stage-projection.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "slots", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.slots.read front=1 back=0 hand=0 stale=0",
        screenshot=".trail/shots/req-cw-slots-read-stage-projection.png",
        body=[
            "# 综合信息",
            "info stage=preparation stale=0",
            "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
            "# 角色信息",
            "slot pos=front:1 name=希儿",
        ],
    )
    _assert_single_call(client, method="cw.slots.read", payload={"slot": None}, tmp_path=tmp_path)


def test_cw_equipment_read_yaml_keeps_structured_fields_after_resource_bundle(
    cli_runner,
    fake_daemon_client,
    tmp_path,
):
    client = fake_daemon_client(
        {
            "cw.equipment.read": build_success_response(
                request_id="req-equipment-read-yaml-fields",
                data={
                    "count": 1,
                    "uncertain": 1,
                    "empty": 59,
                    "items": [
                        {
                            "pos": "equipment:1",
                            "idx": 1,
                            "row": 1,
                            "col": 1,
                            "center": {"x": 1855, "y": 275},
                            "name": "幸运星",
                            "score": 0.7,
                            "gap": 0.01,
                            "uncertain": True,
                            "alt": "光能电池",
                            "alt_score": 0.69,
                            "candidates": [{"name": "幸运星", "score": 0.7}],
                        }
                    ],
                    "backend": "vector",
                    "layout": "default",
                    "columns": 10,
                    "rows": 6,
                    "stale": False,
                },
                screenshot=".trail/shots/req-equipment-read-yaml-fields.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "cw", "equipment", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert "idx: 1" in result.stdout
    assert "row: 1" in result.stdout
    assert "col: 1" in result.stdout
    assert "candidates:" in result.stdout
    assert "gap: 0.01" in result.stdout
    assert "alt: 光能电池" in result.stdout
    assert "alt_score: 0.69" in result.stdout
    assert "idx=" not in result.stdout
    assert "row=" not in result.stdout
    assert "col=" not in result.stdout
    _assert_single_call(client, method="cw.equipment.read", payload={}, tmp_path=tmp_path)


def test_guide_config_cw_cli_and_yaml_keep_bundle_shape(cli_runner, fake_daemon_client, tmp_path):
    data = {
        "meta": {"big_version": "3.2", "season_id": "s1", "sub_season_id": "sub1"},
        "lineup_levels": [],
        "traits": [{"id": "t1", "name": "贝洛伯格", "layers": [2, 4, 6]}],
        "roles": [],
        "role_tags": [],
        "portal_list": [],
        "strategy_list": [],
    }
    default_client = fake_daemon_client(
        {"guide.config.cw": build_success_response(request_id="req-guide-config", data=data)}
    )

    default_result = cli_runner.invoke(app, ["guide", "config", "cw"])

    assert default_result.exit_code == 0
    assert "ok guide.config.cw" in default_result.stdout
    assert "大版本=3.2" in default_result.stdout
    assert default_client.calls[0]["method"] == "guide.config.cw"

    yaml_client = fake_daemon_client(
        {"guide.config.cw": build_success_response(request_id="req-guide-config-yaml", data=data)}
    )

    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "config", "cw"])

    assert yaml_result.exit_code == 0
    assert "meta:" in yaml_result.stdout
    assert "season_id: s1" in yaml_result.stdout
    assert "sub_season_id: sub1" in yaml_result.stdout
    assert "lineup_levels: []" in yaml_result.stdout
    assert "traits:" in yaml_result.stdout
    assert "贝洛伯格" in yaml_result.stdout
    assert "roles: []" in yaml_result.stdout
    assert "role_tags: []" in yaml_result.stdout
    assert "portal_list: []" in yaml_result.stdout
    assert "strategy_list: []" in yaml_result.stdout
    assert yaml_client.calls[0]["method"] == "guide.config.cw"
