from pathlib import Path

import pytest

from trail.cli import app
from trail.output.rendering import print_output, render_output, set_output_options
from tests.support.fake_daemon import build_success_response


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_VERBOSE_ONLY_KEYS = (
    "ocr_mode_requested",
    "ocr_mode_effective",
    "ocr_scale_applied",
    "ocr_retry_high",
    "ocr_retry_reason",
)


def _stage_payload() -> dict:
    return {
        "ok": True,
        "data": {"value": "shop", "stale": False},
        "screenshot": ".trail/shots/req-stage.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def _daemon_status_payload() -> dict:
    return {
        "ok": True,
        "data": {
            "install": {"protocol_version": 1},
            "runtime": {
                "state": "ready",
                "pid": 1234,
                "endpoint": "127.0.0.1:8765",
                "last_start_error": None,
            },
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def _ocr_failure_payload(*, code: str, message: str, screenshot: str | None = None, debug: dict | None = None) -> dict:
    return {
        "ok": False,
        "data": {},
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": debug,
        "error": {"code": code, "message": message},
    }


@pytest.fixture(autouse=True)
def reset_output_options():
    set_output_options(output_format="text", verbose=False)
    yield
    set_output_options(output_format="text", verbose=False)


def test_render_output_renders_canonical_stage_text():
    payload = _stage_payload()

    assert render_output("cw.stage.detect", payload).splitlines() == [
        "ok cw.stage.detect stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
    ]


def test_readme_mentions_text_output_protocol() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "- 默认输出是紧凑文本协议，统一首行为 `<ok|fail> <command> <核心事实...>`，例如 `ok cw.shop.status count=2`" in readme
    assert (
        "- 默认模式是常规消费层；`--format yaml` 是结构化兜底，`--verbose` 是开发/排障层，不应作为终端 Agent 的常规依赖"
        in readme
    )
    assert (
        "- 默认模式绝不输出 YAML；只有显式指定 `--format yaml` 且命令进入 allowlist 时，才会在首行摘要后追加结构化块"
        in readme
    )
    assert (
        "- `shot path=...` 表示当前命令结果对应的截图路径；只要当前命令有截图，就会输出 `shot path=...`，且位于实体行之前"
        in readme
    )
    assert "- 默认失败路径只要当前结果携带 `request_id`，就会保留 `request id=<id>`，用于恢复与排障" in readme
    assert (
        "- 只有结果未知或当前失败显式可恢复时，才会出现 `recover action=daemon.request_status request=<id>`；仅有 `request id=<id>` 不等于当前失败一定可恢复"
        in readme
    )
    assert (
        "- `tainted=1` 表示当前 failure 或状态带有运行态污染风险；继续执行前，先确认请求终态，再决定是否执行 `trail daemon reconcile-session --session <id>`"
        in readme
    )
    assert (
        "- `--verbose` 只追加 `debug kind=...` 调试行，不改变默认文本协议里的事实集合与顺序"
        in readme
    )
    assert (
        "```text\nok guide.list.cw count=2 more=1 next=token-2\nguide id=abc idx=1 carry=希儿 hard=1 change_equip=0 expert=1\nguide id=def idx=2 hard=0 change_equip=1 expert=0\n```"
        in readme
    )
    assert (
        "```text\nok ocr.read hits=2\nshot path=.trail/shots/req-ocr.png\ntext value=点击进入 box=122,88,74,20 center=159,98\ntext value=开始挑战 box=410,502,120,36 center=470,520\n```"
        in readme
    )
    assert (
        "```text\nok cw.shop.status count=2\nshot path=.trail/shots/req-shop.png\nitem idx=1 slot=1 name=希儿 cost=2\nitem idx=2 slot=2 name=停云 cost=1\ninfo coins=40 level=7 reserve_full=0 max_team_size=8\n```"
        in readme
    )
    assert "商店快照里的 `coins` / `level` / `reserve_full` / `max_team_size` 当前只在 `trail cw shop scan` 与 `trail cw shop status` 暴露" in readme
    assert (
        "```text\nfail input.click code=INPUT_BACKEND_MISSING tainted=1\nrequest id=req-42\nwhy msg=\"input backend missing\"\nrecover action=daemon.request_status request=req-42\n```"
        in readme
    )
    assert "trail daemon request-status --request-id <id>" in readme
    assert "默认输出结构化 envelope" not in readme
    assert "ok/data/screenshot/debug" not in readme


def test_readme_documents_ocr_provider_and_lang_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "trail ocr read --provider auto|cpu|dml" in readme
    assert "trail ocr read --lang ch" in readme
    assert "首版仅支持 `ch`" in readme
    assert "TRAIL_OCR_PROVIDER`、`TRAIL_OCR_LANG`、`TRAIL_OCR_USE_CLS`、`TRAIL_OCR_TEXT_SCORE" in readme
    assert "provider=auto` 会优先尝试 DirectML；如果当前环境不可用或本次 DML 推理失败，会自动回退 CPU" in readme
    assert "provider=dml` 会把 DirectML 视为硬约束；环境不可用或推理期 DML 失败都会返回 `OCR_PROVIDER_UNAVAILABLE`" in readme
    assert "lang` 首版仅支持 `ch`；其他值返回 `OCR_LANG_UNSUPPORTED`" in readme


def test_readme_documents_ocr_mode_and_retry_high_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "trail ocr read --ocr-mode fast|high" in readme
    assert "trail ocr read --retry-high auto|never|always" in readme
    assert "TRAIL_OCR_MODE`、`TRAIL_OCR_RETRY_HIGH` 用于设置低优先级默认值" in readme
    assert "默认 `ocr_mode=fast`" in readme
    assert "默认 `retry_high=auto`" in readme
    assert "`fast = 1280x720`" in readme
    assert "`high = native`" in readme
    assert "`retry_high=auto` 只在 `hits==0`、平均分过低、或出现 `OCR_LOW_CONFIDENCE` 时触发" in readme
    assert "`retry_high=always` 在 `ocr_mode=fast` 下会先跑 `fast`，再无条件补跑一次 `high`" in readme
    assert "`ocr_mode=high` 下 `retry_high` 为 no-op" in readme
    assert "模式与重试事实只在 `--verbose` 下出现" in readme


def test_readme_documents_trail_start_as_default_entry() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick Start", 1)[1].split("## ", 1)[0]

    assert "trail start" in quick_start
    assert "trail ocr read" in quick_start
    assert "trail input" in quick_start
    assert "trail daemon install" not in quick_start
    assert "trail daemon status" not in quick_start
    assert "trail daemon start" not in quick_start
    assert "trail daemon request-status" not in quick_start
    assert "trail daemon reconcile-session" not in quick_start
    assert "trail window launch" not in quick_start
    assert "trail window attach" not in quick_start
    assert "trail session create" not in quick_start
    assert "trail screen shot" not in quick_start
    assert "trail image" not in quick_start
    assert "trail state dump" not in quick_start


def test_render_output_renders_canonical_stage_wait_text():
    payload = _stage_payload()

    assert render_output("cw.stage.wait", payload).splitlines() == [
        "ok cw.stage.wait stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
    ]


def test_render_output_renders_cw_shop_status_sorted_items_and_costs():
    payload = {
        "ok": True,
        "data": {
            "items": [
                {"slot": 3, "name": "布洛妮娅", "price": 4},
                {"slot": 1, "name": "希儿", "price": 2},
                {"name": "无槽位条目", "price": 9},
                {"slot": 2, "name": "停云", "price": 1},
            ],
            "coins": 40,
            "level": 7,
            "reserve_full": False,
            "max_team_size": 8,
        },
        "screenshot": ".trail/shots/req-shop.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=4",
        "shot path=.trail/shots/req-shop.png",
        "item idx=1 slot=1 name=希儿 cost=2",
        "item idx=2 slot=2 name=停云 cost=1",
        "item idx=3 slot=3 name=布洛妮娅 cost=4",
        "item idx=4 name=无槽位条目 cost=9",
        "info coins=40 level=7 reserve_full=0 max_team_size=8",
    ]


@pytest.mark.parametrize(
    ("command", "payload", "expected_tokens"),
    [
        (
            "cw.stage.detect",
            {
                "ok": True,
                "data": {"value": "shop", "stale": False},
                "screenshot": ".trail/shots/req-stage-detect-focused.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.stage.detect",
                "stage=shop",
                "stale=0",
                "shot path=.trail/shots/req-stage-detect-focused.png",
            ],
        ),
        (
            "cw.shop.status",
            {
                "ok": True,
                "data": {
                    "items": [
                        {"slot": 2, "name": "停云", "price": 1},
                        {"slot": 1, "name": "希儿", "price": 2},
                    ]
                },
                "screenshot": ".trail/shots/req-shop-focused.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.shop.status count=2",
                "item idx=1 slot=1 name=希儿 cost=2",
                "item idx=2 slot=2 name=停云 cost=1",
                "shot path=.trail/shots/req-shop-focused.png",
            ],
        ),
        (
            "guide.list.cw",
            {
                "ok": True,
                "data": {
                    "list": [
                        {
                            "lineup_id": "guide-1",
                            "carry_roles": ["希儿"],
                            "final_role_cards": [
                                {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                                {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                            ],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                        }
                    ],
                    "next_page_token": "next-guide-token",
                },
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok guide.list.cw count=1 more=1 next=next-guide-token",
                "guide id=guide-1",
                "carry=希儿",
                "hard=1",
                "change_equip=0",
                "expert=1",
                "guide idx=1 final_roles=希儿/carry:1/star:5/rarity:3|佩拉/star:4/rarity:2",
            ],
        ),
        (
            "ocr.read",
            {
                "ok": True,
                "data": {
                    "result": [
                        {
                            "text": "点击进入",
                            "score": 0.98,
                            "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                        },
                        {
                            "text": "开始挑战",
                            "score": 0.93,
                            "box": {"left": 410, "top": 502, "width": 120, "height": 36},
                        },
                    ]
                },
                "screenshot": ".trail/shots/req-ocr-focused.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok ocr.read hits=2",
                "shot path=.trail/shots/req-ocr-focused.png",
                "text value=点击进入 box=122,88,74,20 center=159,98",
                "text value=开始挑战 box=410,502,120,36 center=470,520",
            ],
        ),
    ],
)
def test_render_output_preserves_must_keep_facts(command: str, payload: dict, expected_tokens: list[str]):
    rendered = render_output(command, payload)

    for token in expected_tokens:
        assert token in rendered


def test_render_output_ocr_read_OCR_PROVIDER_UNAVAILABLE_is_hard_failure():
    payload = _ocr_failure_payload(
        code="OCR_PROVIDER_UNAVAILABLE",
        message="requested dml provider unavailable",
        screenshot=".trail/shots/req-ocr-dml.png",
        debug={"request_id": "req-ocr-dml"},
    )

    assert render_output("ocr.read", payload).splitlines() == [
        "fail ocr.read code=OCR_PROVIDER_UNAVAILABLE",
        "request id=req-ocr-dml",
        "shot path=.trail/shots/req-ocr-dml.png",
        'why msg="requested dml provider unavailable"',
    ]


def test_render_output_ocr_read_OCR_LANG_UNSUPPORTED_omits_recover():
    payload = _ocr_failure_payload(
        code="OCR_LANG_UNSUPPORTED",
        message="unsupported ocr lang: en",
        debug={"request_id": "req-ocr-lang"},
    )

    assert render_output("ocr.read", payload).splitlines() == [
        "fail ocr.read code=OCR_LANG_UNSUPPORTED",
        "request id=req-ocr-lang",
        'why msg="unsupported ocr lang: en"',
    ]


def test_render_output_ocr_read_success_does_not_expand_provider_or_lang_fields():
    payload = {
        "ok": True,
        "data": {
            "result": [
                {
                    "text": "点击进入",
                    "score": 0.98,
                    "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                }
            ]
        },
        "screenshot": ".trail/shots/req-ocr-provider-hidden.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "trace": [
                {
                    "step": "ocr_provider",
                    "requested_provider": "auto",
                    "effective_provider": "cpu",
                    "lang": "ch",
                }
            ]
        },
        "error": None,
    }

    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-provider-hidden.png",
        "text value=点击进入 box=122,88,74,20 center=159,98",
    ]


def test_render_output_ocr_read_success_keeps_ocr_mode_retry_context_verbose_only():
    payload = {
        "ok": True,
        "data": {
            "result": [
                {
                    "text": "点击进入",
                    "score": 0.98,
                    "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                }
            ]
        },
        "screenshot": ".trail/shots/req-ocr-verbose-only-success.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-ocr-success",
            "ocr_mode_requested": "fast",
            "ocr_mode_effective": "high",
            "ocr_scale_applied": "native",
            "ocr_retry_high": 1,
            "ocr_retry_reason": "low_confidence",
        },
        "error": None,
    }

    rendered = render_output("ocr.read", payload)

    assert rendered.splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-verbose-only-success.png",
        "text value=点击进入 box=122,88,74,20 center=159,98",
    ]
    assert all(key not in rendered for key in OCR_VERBOSE_ONLY_KEYS)


def test_render_output_ocr_read_failure_keeps_ocr_mode_retry_context_verbose_only():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-ocr-verbose-only-fail.png",
        "timing": {},
        "warnings": [{"code": "OCR_LOW_CONFIDENCE", "message": "text may be incomplete"}],
        "references": [{"path": "refs/ocr.png", "similarity": 0.75}],
        "debug": {
            "request_id": "req-ocr-fail",
            "ocr_mode_requested": "fast",
            "ocr_mode_effective": "fast",
            "ocr_scale_applied": "1280x720",
            "ocr_retry_high": 0,
            "ocr_retry_reason": "none",
        },
        "error": {"code": "OCR_BACKEND_UNAVAILABLE", "message": "ocr backend unavailable"},
    }

    rendered = render_output("ocr.read", payload)

    assert rendered.splitlines() == [
        "fail ocr.read code=OCR_BACKEND_UNAVAILABLE",
        "request id=req-ocr-fail",
        "shot path=.trail/shots/req-ocr-verbose-only-fail.png",
        'why msg="ocr backend unavailable"',
        'warn code=OCR_LOW_CONFIDENCE msg="text may be incomplete"',
        "ref path=refs/ocr.png sim=0.75",
    ]
    assert all(key not in rendered for key in OCR_VERBOSE_ONLY_KEYS)


def test_render_output_renders_guide_list_with_paging_and_frozen_fields():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "购物阵容",
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
                },
                {
                    "lineup_id": "def",
                    "title": "事件阵容",
                    "carry_roles": [],
                    "support_hard": False,
                    "has_change_equip": True,
                    "has_expert": False,
                    "like": 22,
                    "favour": 9,
                },
            ],
            "next_page_token": "token-2",
        },
        "screenshot": ".trail/shots/req-guide-list.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=2 more=1 next=token-2",
        "shot path=.trail/shots/req-guide-list.png",
        "guide id=abc title=购物阵容 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45",
        "guide idx=1 final_roles=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3|佩拉/star:4/rarity:2",
        "guide id=def title=事件阵容 idx=2 hard=0 change_equip=1 expert=0 like=22 favour=9",
    ]


def test_render_output_guide_list_omits_next_when_not_paginated():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "购物阵容",
                    "carry_roles": ["希儿"],
                    "support_hard": False,
                    "has_change_equip": False,
                    "has_expert": False,
                    "like": 7,
                    "favour": 3,
                }
            ],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.list.cw", payload).splitlines()

    assert lines == [
        "ok guide.list.cw count=1 more=0",
        "guide id=abc title=购物阵容 idx=1 carry=希儿 hard=0 change_equip=0 expert=0 like=7 favour=3",
    ]
    assert all("final_roles=" not in line for line in lines)


def test_render_output_renders_grouped_portal_guide_lists():
    payload = {
        "ok": True,
        "data": {
            "portals": [
                {
                    "portal_title": "购物区",
                    "list": [
                        {
                            "lineup_id": "shop-guide",
                            "title": "购物区优选阵容",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                            "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
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
                            "support_hard": False,
                            "has_change_equip": True,
                            "has_expert": False,
                            "like": 22,
                            "favour": 9,
                            "final_role_cards": [{"name": "停云", "star": 4, "rarity": 2, "is_carry": True}],
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                },
            ],
            "count": 2,
            "more": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw groups=2 count=2 more=0",
        "guide portal=购物区 count=1 more=0",
        "guide portal=购物区 id=shop-guide title=购物区优选阵容 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45",
        "guide portal=购物区 idx=1 final_roles=希儿/carry:1/star:5/rarity:3",
        "guide portal=事件区 count=1 more=0",
        "guide portal=事件区 id=event-guide title=事件区优选阵容 idx=1 carry=停云 hard=0 change_equip=1 expert=0 like=22 favour=9",
        "guide portal=事件区 idx=1 final_roles=停云/carry:1/star:4/rarity:2",
    ]


def test_render_output_renders_guide_fetch_summary_text():
    payload = {
        "ok": True,
        "data": {
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
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.fetch.cw", payload).splitlines() == [
        "ok guide.fetch.cw id=abc share_code=##demo## version=3.2 min_level=7 mid_level=8 hard=1 change_equip=0 expert=1",
        "guide on_field=希儿:9|停云:3 off_field=佩拉:1",
        "guide portals=商店|事件 first_augments=快攻|回蓝 second_augments=暴击|连携",
        "guide order_basic=升级|买卡|打精英 order_compose=希儿|停云 role_stages=name:希儿/stage:1|name:停云/stage:2",
    ]


def test_render_output_renders_cw_enter_home_text():
    payload = {
        "ok": True,
        "data": {"page": "home"},
        "screenshot": ".trail/shots/req-enter.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter.png",
    ]


def test_render_output_renders_cw_enter_already_home_info():
    payload = {
        "ok": True,
        "data": {"page": "home", "already_home": True},
        "screenshot": ".trail/shots/req-enter-home.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-home.png",
        "info already_home=1",
    ]


def test_render_output_renders_cw_start_portal_cards_family():
    payload = {
        "ok": True,
        "data": {
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
        "screenshot": ".trail/shots/req-start.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.start", payload).splitlines() == [
        "ok cw.start cards=2",
        "shot path=.trail/shots/req-start.png",
        'opt idx=1 title="Alpha Portal" score=0.99 new=1',
        'opt idx=1 desc="Alpha Desc"',
        'guide idx=1 gid=1 id=alpha-guide title=Alpha攻略 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45',
        'guide idx=1 gid=1 final_roles=希儿/carry:1/star:5/rarity:3',
        'opt idx=2 title="Beta Portal" score=0.88',
        'opt idx=2 desc="Beta Desc"',
    ]


@pytest.mark.parametrize("command", ["cw.portal.refresh", "cw.portal.restart"])
def test_render_output_renders_cw_portal_refresh_family(command: str):
    payload = {
        "ok": True,
        "data": {
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
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                },
            ],
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
            "stale": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output(command, payload).splitlines() == [
        f"ok {command} cards=1",
        'opt idx=1 title="Alpha Portal" score=0.99 new=1',
        'opt idx=1 desc="Alpha Desc"',
        'guide idx=1 gid=1 id=alpha-guide title=Alpha攻略 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45',
    ]


def test_render_output_renders_cw_portal_select_summary_text():
    payload = {
        "ok": True,
        "data": {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
        "screenshot": ".trail/shots/req-portal-select.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.select", payload).splitlines() == [
        'ok cw.portal.select idx=2 title="Beta Portal"',
        "shot path=.trail/shots/req-portal-select.png",
    ]


def test_render_output_renders_cw_slots_summary_text():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 4}, None],
            "back": [{"name": "佩拉", "rarity": 2}],
            "hand": [{"name": "停云", "cost": 2, "is_carry": True}, None],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-slots.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=1 hand=1 stale=1",
        "shot path=.trail/shots/req-slots.png",
        "slot pos=front:0 name=希儿 star=4",
        "slot pos=front:1 empty=1",
        "slot pos=back:0 name=佩拉 rarity=2",
        "slot pos=hand:0 name=停云 carry=1 cost=2",
        "slot pos=hand:1 empty=1",
    ]


def test_render_output_renders_cw_options_text():
    payload = {
        "ok": True,
        "data": {"options": [{"id": 1, "name": "量子力学"}, 2]},
        "screenshot": ".trail/shots/req-options.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.invest.read", payload).splitlines() == [
        "ok cw.invest.read count=2",
        "shot path=.trail/shots/req-options.png",
        "opt idx=1 id=1 name=量子力学",
        "opt idx=2 value=2",
    ]


def test_render_output_renders_cw_shop_buy_slot_summary_text():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 2, "name": "停云", "price": 10}, {"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "guide_summary": {"remaining_purchases": {"银狼": 0}},
        },
        "screenshot": ".trail/shots/req-buy-slot.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.buy_slot", payload).splitlines() == [
        "ok cw.shop.buy_slot opened=1 stale=0 count=2",
        "shot path=.trail/shots/req-buy-slot.png",
        "item idx=1 slot=1 name=银狼 cost=20",
        "item idx=2 slot=2 name=停云 cost=10",
    ]


def test_render_output_renders_cw_shop_scan_snapshot_info_text():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "coins": 40,
            "level": 7,
            "reserve_full": False,
            "max_team_size": 8,
        },
        "screenshot": ".trail/shots/req-shop-scan.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=1",
        "shot path=.trail/shots/req-shop-scan.png",
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 level=7 reserve_full=0 max_team_size=8",
    ]


def test_render_output_does_not_render_stale_shop_snapshot_info_for_open_command():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": True,
            "coins": 40,
            "level": 7,
            "reserve_full": False,
            "max_team_size": 8,
        },
        "screenshot": ".trail/shots/req-shop-open.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.open", payload).splitlines() == [
        "ok cw.shop.open opened=1 stale=1 count=1",
        "shot path=.trail/shots/req-shop-open.png",
        "item idx=1 slot=1 name=银狼 cost=20",
    ]


def test_render_output_renders_ocr_read_from_rapidocr_tuple_items():
    payload = {
        "ok": True,
        "data": {
            "result": [
                [[122, 88], [196, 88], [196, 108], [122, 108]],
            ]
        },
        "screenshot": ".trail/shots/req-ocr-raw.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    payload["data"]["result"] = [
        [
            [[122, 88], [196, 88], [196, 108], [122, 108]],
            "点击进入",
            0.98,
        ],
        [
            [[410, 502], [530, 502], [530, 538], [410, 538]],
            "开始挑战",
            0.93,
        ],
    ]

    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=2",
        "shot path=.trail/shots/req-ocr-raw.png",
        "text value=点击进入 box=122,88,74,20 center=159,98",
        "text value=开始挑战 box=410,502,120,36 center=470,520",
    ]


def test_render_output_renders_cw_shop_refresh_action_with_snapshot_facts():
    payload = {
        "ok": True,
        "data": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": False, "stale": True},
        "screenshot": ".trail/shots/req-refresh.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.refresh", payload).splitlines() == [
        "ok cw.shop.refresh opened=0 stale=1 count=1",
        "shot path=.trail/shots/req-refresh.png",
        "item idx=1 slot=1 name=银狼 cost=20",
    ]


def test_render_output_renders_cw_event_handle_summary_text():
    payload = {
        "ok": True,
        "data": {"event_type": "special", "handled_action": "confirm"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.event.handle", payload).splitlines() == [
        "ok cw.event.handle event_type=special handled_action=confirm"
    ]


def test_render_output_renders_cw_hand_sell_plan_text():
    payload = {
        "ok": True,
        "data": {"candidates": [0, 2]},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell_plan", payload).splitlines() == [
        "ok cw.hand.sell_plan count=2"
    ]


def test_render_output_accepts_yaml_string_format():
    assert render_output("daemon.status", _daemon_status_payload(), output_format="yaml").splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
    ]


def test_render_output_appends_debug_lines_after_yaml_block_when_verbose():
    payload = _daemon_status_payload()
    payload["debug"] = {"request_id": "req-daemon-status", "detail": "bootstrap missing"}

    assert render_output("daemon.status", payload, output_format="yaml", verbose=True).splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
        "debug kind=request msg=req-daemon-status",
        'debug kind=detail msg="bootstrap missing"',
    ]


def test_render_output_appends_yaml_and_debug_lines_for_allowlist_failure_when_verbose():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-daemon-fail", "detail": "bootstrap missing"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("daemon.status", payload, output_format="yaml", verbose=True).splitlines() == [
        "fail daemon.status code=DAEMON_UNAVAILABLE",
        "request id=req-daemon-fail",
        'why msg="daemon unavailable"',
        "{}",
        "debug kind=request msg=req-daemon-fail",
        'debug kind=detail msg="bootstrap missing"',
    ]


def test_render_output_keeps_debug_lines_for_non_allowlist_yaml_rejection_when_verbose():
    payload = {
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-ocr", "detail": "backend missing"},
        "error": None,
    }

    assert render_output("ocr.read", payload, output_format="yaml", verbose=True).splitlines() == [
        "fail ocr.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "request id=req-ocr",
        'why msg="yaml not supported for ocr.read"',
        "debug kind=request msg=req-ocr",
        'debug kind=detail msg="backend missing"',
    ]


def test_print_output_uses_global_output_options(capsys):
    set_output_options(output_format="yaml", verbose=False)

    print_output("daemon.status", _daemon_status_payload())

    assert capsys.readouterr().out.splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
    ]


def test_ocr_read_rejects_yaml_output(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read",
                data={"result": [{"text": "点击进入"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail ocr.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for ocr.read"',
    ]


def test_render_output_preserves_original_failure_for_non_allowlist_yaml():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-1.png",
        "timing": {},
        "warnings": [{"code": "OCR_LOW_CONFIDENCE", "message": "text may be incomplete"}],
        "references": [{"path": "refs/ocr.png", "similarity": 0.75}],
        "debug": {"request_id": "req-1"},
        "error": {"code": "OCR_BACKEND_UNAVAILABLE", "message": "ocr backend unavailable"},
    }

    assert render_output("ocr.read", payload, output_format="yaml").splitlines() == [
        "fail ocr.read code=OCR_BACKEND_UNAVAILABLE",
        "request id=req-1",
        "shot path=.trail/shots/req-1.png",
        'why msg="ocr backend unavailable"',
        'warn code=OCR_LOW_CONFIDENCE msg="text may be incomplete"',
        "ref path=refs/ocr.png sim=0.75",
    ]


def test_render_output_verbose_includes_ocr_mode_effective_ocr_scale_applied_and_ocr_retry_reason_from_capture_context(tmp_path):
    from trail.output.capture import with_auto_capture

    class RuntimeStub:
        def capture_after_action(self, optional: bool = False):
            del optional
            return tmp_path / "ocr-context.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            return []

        def consume_debug_context(self):
            return {
                "ocr_mode_requested": "fast",
                "ocr_mode_effective": "high",
                "ocr_scale_applied": "native",
                "ocr_retry_high": 1,
                "ocr_retry_reason": "low_confidence",
            }

    payload = with_auto_capture(RuntimeStub(), lambda: {"result": [{"text": "点击进入"}]}, verbose=True)
    encoded_screenshot = payload["screenshot"].replace("\\", "\\\\")

    assert render_output("ocr.read", payload, verbose=True).splitlines() == [
        "ok ocr.read hits=1",
        f'shot path="{encoded_screenshot}"',
        "text value=点击进入",
        "debug kind=context key=ocr_mode_requested value=fast",
        "debug kind=context key=ocr_mode_effective value=high",
        "debug kind=context key=ocr_scale_applied value=native",
        "debug kind=context key=ocr_retry_high value=1",
        "debug kind=context key=ocr_retry_reason value=low_confidence",
    ]


def test_render_output_verbose_includes_ocr_retry_high_zero_and_retry_reason_none_from_capture_context(tmp_path):
    from trail.output.capture import with_auto_capture

    class RuntimeStub:
        def capture_after_action(self, optional: bool = False):
            del optional
            return tmp_path / "ocr-context-none.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            return []

        def consume_debug_context(self):
            return {
                "ocr_mode_requested": "fast",
                "ocr_mode_effective": "fast",
                "ocr_scale_applied": "1280x720",
                "ocr_retry_high": 0,
                "ocr_retry_reason": "none",
            }

    payload = with_auto_capture(RuntimeStub(), lambda: {"result": [{"text": "点击进入"}]}, verbose=True)
    encoded_screenshot = payload["screenshot"].replace("\\", "\\\\")

    assert render_output("ocr.read", payload, verbose=True).splitlines() == [
        "ok ocr.read hits=1",
        f'shot path="{encoded_screenshot}"',
        "text value=点击进入",
        "debug kind=context key=ocr_mode_requested value=fast",
        "debug kind=context key=ocr_mode_effective value=fast",
        "debug kind=context key=ocr_scale_applied value=1280x720",
        "debug kind=context key=ocr_retry_high value=0",
        "debug kind=context key=ocr_retry_reason value=none",
    ]


def test_render_output_state_dump_tolerates_non_mapping_data():
    payload = {
        "ok": True,
        "data": "oops",
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("state.dump", payload).splitlines() == [
        "ok state.dump scene=unknown tainted=0"
    ]


def test_render_output_guide_config_tolerates_non_mapping_data():
    payload = {
        "ok": True,
        "data": "oops",
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.config.cw", payload).splitlines() == [
        "ok guide.config.cw",
        "info lineup_levels=0 traits=0 roles=0 role_tags=0 portal_list=0",
    ]


def test_render_output_guide_list_failure_renders_portal_candidates_as_warn_lines():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [
            {"portal": "购物区", "score": 0.98},
            {"portal": "事件区", "score": 0.81},
            {"portal": "补给区", "score": 0.74},
        ],
        "references": [],
        "debug": {"request_id": "req-portal-invalid"},
        "error": {"code": "GUIDE_PORTAL_INVALID", "message": "guide portal invalid: 购物曲"},
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "fail guide.list.cw code=GUIDE_PORTAL_INVALID",
        "request id=req-portal-invalid",
        'why msg="guide portal invalid: 购物曲"',
        "warn portal=购物区 score=0.98",
        "warn portal=事件区 score=0.81",
        "warn portal=补给区 score=0.74",
    ]


def test_render_output_omits_recover_for_local_control_plane_failure():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-42", "detail": "timeout"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("daemon.start", payload).splitlines() == [
        "fail daemon.start code=DAEMON_UNAVAILABLE",
        "request id=req-42",
        'why msg="daemon unavailable"',
    ]


def test_render_output_window_launch_required_path_has_request_id_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-required"},
        "error": {"code": "GAME_PATH_REQUIRED", "message": "请提供游戏路径"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_PATH_REQUIRED",
        "request id=req-window-launch-required",
        "why msg=请提供游戏路径",
    ]


def test_render_output_window_launch_explicit_missing_path_keeps_game_path_not_found():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-missing"},
        "error": {"code": "GAME_PATH_NOT_FOUND", "message": "未找到游戏启动路径"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_PATH_NOT_FOUND",
        "request id=req-window-launch-missing",
        "why msg=未找到游戏启动路径",
    ]


def test_render_output_window_launch_explicit_launch_failure_keeps_stable_code():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-failed"},
        "error": {"code": "GAME_LAUNCH_FAILED", "message": "显式提供的游戏路径启动失败: launch explode"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_LAUNCH_FAILED",
        "request id=req-window-launch-failed",
        'why msg="显式提供的游戏路径启动失败: launch explode"',
    ]


def test_render_output_window_launch_success_keeps_persist_failure_warning():
    path = r"C:\Games\StarRail.exe"
    payload = {
        "ok": True,
        "data": {
            "started": True,
            "already_running": False,
            "path": path,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [
            {
                "code": "GAME_PATH_PERSIST_FAILED",
                "message": "游戏已成功启动，但历史路径持久化失败: disk full",
            }
        ],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("window.launch", payload).splitlines() == [
        'ok window.launch started=1 already_running=0 path="C:\\\\Games\\\\StarRail.exe"',
        'warn code=GAME_PATH_PERSIST_FAILED msg="游戏已成功启动，但历史路径持久化失败: disk full"',
    ]


def test_render_output_start_run_success_keeps_fixed_first_line_order():
    payload = {
        "ok": True,
        "data": {
            "session": "sess-start-1",
            "reused": 1,
            "title": "崩坏：星穹铁道",
            "hwnd": 123,
        },
        "screenshot": ".trail/shots/req-start-run.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("start.run", payload).splitlines() == [
        "ok start.run session=sess-start-1 reused=1 title=崩坏：星穹铁道 hwnd=123",
        "shot path=.trail/shots/req-start-run.png",
    ]


def test_render_output_start_run_success_keeps_reused_zero_fact():
    payload = {
        "ok": True,
        "data": {
            "session": "sess-start-2",
            "reused": 0,
            "title": "崩坏：星穹铁道",
            "hwnd": 456,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("start.run", payload).splitlines() == [
        "ok start.run session=sess-start-2 reused=0 title=崩坏：星穹铁道 hwnd=456"
    ]


def test_render_output_start_run_game_path_required_keeps_request_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-start-game-path"},
        "error": {"code": "GAME_PATH_REQUIRED", "message": "请提供游戏路径"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=GAME_PATH_REQUIRED",
        "request id=req-start-game-path",
        "why msg=请提供游戏路径",
    ]


def test_render_output_start_run_unknown_result_keeps_request_tainted_and_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-start-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-start-unknown",
            "last_known_stage": "state_persisted",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-start-unknown",
        "shot path=.trail/shots/req-start-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-start-unknown",
    ]


def test_render_output_start_run_local_pre_daemon_failure_stays_on_start_run_token():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "DAEMON_INSTALL_FAILED", "message": "daemon install failed"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=DAEMON_INSTALL_FAILED",
        'why msg="daemon install failed"',
    ]


def test_render_output_daemon_request_status_keeps_session_fact_for_reconcile_chain():
    payload = {
        "ok": True,
        "data": {
            "request_id": "req-42",
            "session_id": "sess-1",
            "final_state": "completed",
            "last_visible_stage": "responded",
            "tainted": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=req-42 session=sess-1 final_state=completed last_visible_stage=responded tainted=0"
    ]


def test_readme_documents_window_launch_path_resolution_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`trail window launch --channel official|bilibili|global`" in readme
    assert "显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退" in readme
    assert "无显式路径时固定顺序：历史成功路径 -> 默认路径 -> 直接问用户" in readme
    assert "默认路径只覆盖 `official`" in readme
    assert r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe" in readme
    assert "`bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`" in readme
    assert "Agent 不应默认乱搜路径" in readme
    assert "`GAME_PATH_REQUIRED` 表示现在该直接问用户提供路径" in readme
    assert "`GAME_PATH_NOT_FOUND`" in readme
    assert "`GAME_LAUNCH_FAILED`" in readme
    assert "`GAME_PATH_PERSIST_FAILED`" in readme
    assert "trail window launch --game-path <StarRail.exe>" not in readme
    assert "自动搜索常见目录" not in readme
    assert "注册表" not in readme
    assert "全盘搜索" not in readme


def test_skill_docs_split_simple_and_advanced_commands() -> None:
    basic = (PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    advanced = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md").read_text(encoding="utf-8")
    cw = (PROJECT_ROOT / "skills" / "trail-cw" / "SKILL.md").read_text(encoding="utf-8")

    assert "trail start" in basic
    assert "trail ocr read" in basic
    assert "trail input" in basic
    assert "加载 advanced skill" in basic
    assert "trail-hsr-advanced" in basic
    assert "trail daemon install" not in basic
    assert "trail daemon status" not in basic
    assert "trail daemon start" not in basic
    assert "trail daemon request-status" not in basic
    assert "trail daemon reconcile-session" not in basic
    assert "trail window launch" not in basic
    assert "trail window attach" not in basic
    assert "trail session create" not in basic
    assert "trail screen shot" not in basic
    assert "trail image" not in basic
    assert "trail state dump" not in basic
    assert "trail daemon install" in advanced
    assert "trail daemon status" in advanced
    assert "trail daemon start" in advanced
    assert "trail daemon request-status" in advanced
    assert "trail daemon reconcile-session" in advanced
    assert "trail window launch" in advanced
    assert "trail window attach" in advanced
    assert "trail session create" in advanced
    assert "trail screen shot" in advanced
    assert "trail image locate" in advanced
    assert "trail image wait" in advanced
    assert "trail state dump" in advanced
    assert "trail start" in cw


def test_advanced_skill_documents_window_launch_path_resolution_contract() -> None:
    advanced = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md").read_text(encoding="utf-8")

    assert "`trail window launch --channel official|bilibili|global`" in advanced
    assert "显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退" in advanced
    assert "历史成功路径 -> 默认路径 -> 直接问用户" in advanced
    assert "默认路径只覆盖 `official`" in advanced
    assert r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe" in advanced
    assert "`bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`" in advanced
    assert "Agent 不应默认乱搜路径" in advanced
    assert "`GAME_PATH_REQUIRED`" in advanced
    assert "`GAME_PATH_NOT_FOUND`" in advanced
    assert "`GAME_LAUNCH_FAILED`" in advanced
    assert "`GAME_PATH_PERSIST_FAILED`" in advanced
    assert "session=<id>" in advanced
    assert "trail window launch --game-path <StarRail.exe>" not in advanced
    assert "自动搜索常见目录" not in advanced
    assert "注册表" not in advanced
    assert "全盘搜索" not in advanced


def test_cw_skill_family_uses_existing_session_instead_of_old_bootstrap_chain() -> None:
    for relative_path in (
        Path("skills/trail-cw/SKILL.md"),
        Path("skills/trail-cw-events/SKILL.md"),
        Path("skills/trail-cw-guide/SKILL.md"),
        Path("skills/trail-cw-replenish/SKILL.md"),
        Path("skills/trail-cw-shop/SKILL.md"),
        Path("skills/trail-cw-slots/SKILL.md"),
    ):
        content = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

        assert "trail daemon install" not in content
        assert "trail daemon status" not in content
        assert "trail daemon start" not in content
        assert "trail daemon request-status" not in content
        assert "trail daemon reconcile-session" not in content
        assert "trail window launch" not in content
        assert "trail window attach" not in content
        assert "trail session create" not in content
        assert "trail screen shot" not in content
        assert "trail image" not in content
        assert "trail state dump" not in content
        assert "使用已有 session" in content or "trail start" in content
        assert "--session <id>" in content or "--session <session_id>" in content


def test_render_output_renders_daemon_restart_summary():
    payload = {
        "ok": True,
        "data": {"stopped": True, "started": True, "already_running": False},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("daemon.restart", payload).splitlines() == [
        "ok daemon.restart stopped=1 started=1 already_running=0"
    ]


def test_render_output_keeps_recover_for_unknown_result_failure():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-42.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-42",
            "detail": "OSError: flush failed",
            "last_known_stage": "state_persisted",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("input.click", payload).splitlines() == [
        "fail input.click code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-42",
        "shot path=.trail/shots/req-42.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-42",
    ]


def test_render_output_preserves_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-unknown",
            "last_known_stage": "side_effect_applied",
            "detail": "flush failed",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("cw.shop.buy_slot", payload).splitlines() == [
        "fail cw.shop.buy_slot code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-unknown",
        "shot path=.trail/shots/req-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-unknown",
    ]


def test_cli_accepts_yaml_format_option(cli_runner):
    result = cli_runner.invoke(app, ["--format", "yaml", "version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("trail ")


def test_cli_rejects_invalid_format_option(cli_runner):
    result = cli_runner.invoke(app, ["--format", "json", "version"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.output
