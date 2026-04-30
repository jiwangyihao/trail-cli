from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from tests.test_cw_guide import fake_cw_config_response, fake_lineup_index_item


def load_guide_module():
    return importlib.import_module("trail.scenes.cw.guide")


def patch_bundle_raw_config(monkeypatch, guide_module):
    monkeypatch.setattr(
        guide_module,
        "load_default_cw_resource_bundle",
        lambda workspace_root=None: SimpleNamespace(raw_config=fake_cw_config_response()["data"]),
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_config_data",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("config network must not be called")),
        raising=False,
    )


def test_fetch_cw_guide_list_rejects_portal_and_portal_id_together(monkeypatch):
    guide_module = load_guide_module()
    patch_bundle_raw_config(monkeypatch, guide_module)

    with pytest.raises(guide_module.TrailError) as exc_info:
        guide_module.fetch_cw_guide_list(
            page=1,
            limit=20,
            trait_id=None,
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
            portal="购物区",
            portal_id="shop",
        )

    assert exc_info.value.code == "GUIDE_INPUT_INVALID"
    assert str(exc_info.value) == "guide options '--portal' and '--portal-id' are mutually exclusive"


@pytest.mark.parametrize(
    ("page", "next_page_token"),
    [
        (2, None),
        (1, "token-2"),
    ],
)
def test_fetch_cw_guide_list_rejects_pagination_in_portal_mode(monkeypatch, page: int, next_page_token: str | None):
    guide_module = load_guide_module()

    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: pytest.fail("portal pagination should be rejected before list fetch"),
        raising=False,
    )

    with pytest.raises(guide_module.TrailError) as exc_info:
        guide_module.fetch_cw_guide_list(
            page=page,
            limit=20,
            trait_id=None,
            order=None,
            next_page_token=next_page_token,
            match_change_job=None,
            match_hard=None,
            portal="购物区",
        )

    assert exc_info.value.code == "GUIDE_PORTAL_PAGINATION_UNSUPPORTED"
    assert str(exc_info.value) == "guide portal filter only supports first page without next_page_token"


def test_fetch_cw_guide_list_rejects_unknown_portal_with_top3_candidates(monkeypatch):
    guide_module = load_guide_module()
    patch_bundle_raw_config(monkeypatch, guide_module)

    with pytest.raises(guide_module.GuidePortalLookupError) as exc_info:
        guide_module.fetch_cw_guide_list(
            page=1,
            limit=20,
            trait_id=None,
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
            portal="购物曲",
        )

    assert exc_info.value.code == "GUIDE_PORTAL_INVALID"
    assert str(exc_info.value) == "guide portal invalid: 购物曲"
    assert len(exc_info.value.candidates) == 2
    assert [candidate["title"] for candidate in exc_info.value.candidates] == ["购物区", "事件区"]
    assert exc_info.value.candidates[0]["score"] >= exc_info.value.candidates[1]["score"]


def test_fetch_cw_guide_list_portal_filter_uses_list_item_detail_without_fanout(monkeypatch):
    guide_module = load_guide_module()
    captured_list_request: dict[str, object] = {}

    patch_bundle_raw_config(monkeypatch, guide_module)

    def fake_fetch_list_data(**kwargs):
        captured_list_request.update(kwargs)
        return {
            "list": [
                fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"]),
                fake_lineup_index_item(lineup_id="lineup-event", title="事件阵容", summary_portals=["事件区"]),
                fake_lineup_index_item(lineup_id="lineup-shop-2", title="购物二号", summary_portals=["购物区"]),
            ],
            "next_page_token": "raw-next-token",
        }

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_fetch_list_data, raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=1,
        trait_id=1005,
        order="Recent",
        next_page_token=None,
        match_change_job=True,
        match_hard=False,
        portal="购物区",
    )

    assert captured_list_request == {
        "page": 1,
        "limit": 10,
        "trait_id": 1005,
        "role_ids": [],
        "order": "Recent",
        "next_page_token": None,
        "match_change_job": True,
        "match_hard": False,
        "timeout": 10,
    }
    assert [item["id"] for item in payload["list"]] == ["lineup-shop"]
    assert payload["next_page_token"] == "raw-next-token"


def test_fetch_cw_guide_list_portal_filter_matches_list_portal_without_id(monkeypatch):
    guide_module = load_guide_module()

    patch_bundle_raw_config(monkeypatch, guide_module)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {
            "list": [fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"])],
            "next_page_token": None,
        },
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=20,
        trait_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
        portal_id="shop",
    )

    assert [item["id"] for item in payload["list"]] == ["lineup-shop"]


def test_fetch_cw_guide_list_portal_filter_paginates_until_limit(monkeypatch):
    guide_module = load_guide_module()
    requests: list[dict[str, object]] = []

    patch_bundle_raw_config(monkeypatch, guide_module)

    responses = iter(
        [
            {
                "list": [
                    fake_lineup_index_item(lineup_id="lineup-shop-1", title="购物阵容一", summary_portals=["购物区"]),
                    fake_lineup_index_item(lineup_id="lineup-event", title="事件阵容", summary_portals=["事件区"]),
                ],
                "next_page_token": "token-2",
            },
            {
                "list": [
                    fake_lineup_index_item(lineup_id="lineup-shop-2", title="购物阵容二", summary_portals=["购物区"]),
                ],
                "next_page_token": "token-3",
            },
        ]
    )

    def fake_fetch_list_data(**kwargs):
        requests.append(kwargs)
        return next(responses)

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_fetch_list_data, raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=2,
        trait_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
        portal="购物区",
    )

    assert requests == [
        {
            "page": 1,
            "limit": 10,
            "trait_id": None,
            "role_ids": [],
            "order": None,
            "next_page_token": None,
            "match_change_job": None,
            "match_hard": None,
            "timeout": 10,
        },
        {
            "page": 1,
            "limit": 10,
            "trait_id": None,
            "role_ids": [],
            "order": None,
            "next_page_token": "token-2",
            "match_change_job": None,
            "match_hard": None,
            "timeout": 10,
        },
    ]
    assert [item["id"] for item in payload["list"]] == ["lineup-shop-1", "lineup-shop-2"]
    assert payload["next_page_token"] == "token-3"


def test_fetch_cw_guide_list_multi_portal_groups_results(monkeypatch):
    guide_module = load_guide_module()
    patch_bundle_raw_config(monkeypatch, guide_module)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {
            "list": [
                fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"]),
                fake_lineup_index_item(lineup_id="lineup-event", title="事件阵容", summary_portals=["事件区"]),
            ],
            "next_page_token": None,
        },
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=2,
        trait_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
        portal=["购物区", "事件区"],
    )

    assert payload == {
        "portals": [
            {
                "portal_title": "购物区",
                "list": [
                    {
                        "id": "lineup-shop",
                        "title": "购物阵容",
                        "nickname": "测试作者",
                        "description": "高分稳定上分阵容",
                        "labels": ["9级搜牌", "银河学者"],
                        "final_traits": ["巡猎", "量子"],
                        "final_role_cards": [
                            {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                            {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                            {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                        ],
                        "has_change_equip": True,
                        "has_expert": True,
                        "version": "3.1",
                        "created_at": 1734691200,
                        "last_edit": 1734777600,
                        "carry_roles": ["希儿", "布洛妮娅"],
                        "like": 123,
                        "favour": 45,
                        "interact": {"like": 123, "favour": 45, "view": 6789, "use": 321},
                        "recent_interact": {"like": 12, "favour": 4, "view": 345, "use": 22},
                        "support_hard": True,
                    }
                ],
                "more": False,
                "next_page_token": None,
            },
            {
                "portal_title": "事件区",
                "list": [
                    {
                        "id": "lineup-event",
                        "title": "事件阵容",
                        "nickname": "测试作者",
                        "description": "高分稳定上分阵容",
                        "labels": ["9级搜牌", "银河学者"],
                        "final_traits": ["巡猎", "量子"],
                        "final_role_cards": [
                            {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                            {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                            {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                        ],
                        "has_change_equip": True,
                        "has_expert": True,
                        "version": "3.1",
                        "created_at": 1734691200,
                        "last_edit": 1734777600,
                        "carry_roles": ["希儿", "布洛妮娅"],
                        "like": 123,
                        "favour": 45,
                        "interact": {"like": 123, "favour": 45, "view": 6789, "use": 321},
                        "recent_interact": {"like": 12, "favour": 4, "view": 345, "use": 22},
                        "support_hard": True,
                    }
                ],
                "more": False,
                "next_page_token": None,
            },
        ],
        "count": 2,
        "more": False,
    }
