from __future__ import annotations

import importlib

import pytest

from tests.test_cw_guide import fake_cw_config_response, fake_lineup_index_item


def load_guide_module():
    return importlib.import_module("trail.scenes.cw.guide")


def test_fetch_cw_guide_list_rejects_portal_and_portal_id_together():
    guide_module = load_guide_module()

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
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)

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


def test_fetch_cw_guide_list_portal_filter_uses_first_page_60_and_detail_fanout(monkeypatch):
    guide_module = load_guide_module()
    captured_list_request: dict[str, object] = {}
    detail_calls: list[str] = []

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)

    def fake_fetch_list_data(**kwargs):
        captured_list_request.update(kwargs)
        return {
            "list": [
                fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["错误摘要"]),
                fake_lineup_index_item(lineup_id="lineup-event", title="事件阵容", summary_portals=["购物区"]),
                fake_lineup_index_item(lineup_id="lineup-shop-2", title="购物二号", summary_portals=[]),
            ],
            "next_page_token": "raw-next-token",
        }

    def fake_fetch_lineup_detail(lineup_id: str, *, timeout: int = 10):
        detail_calls.append(lineup_id)
        portal_name = "购物区" if lineup_id != "lineup-event" else "事件区"
        return lineup_id, f"https://example.invalid/{lineup_id}", {
            "id": lineup_id,
            "tourn_detail": {
                "portals": [
                    {
                        "id": "shop" if portal_name == "购物区" else "event",
                        "name": portal_name,
                        "description": f"{portal_name}描述",
                    }
                ]
            },
        }

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_fetch_list_data, raising=False)
    monkeypatch.setattr(guide_module, "_fetch_lineup_detail", fake_fetch_lineup_detail, raising=False)

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
        "limit": 60,
        "trait_id": 1005,
        "order": "Recent",
        "next_page_token": None,
        "match_change_job": True,
        "match_hard": False,
        "timeout": 10,
    }
    assert detail_calls == ["lineup-shop", "lineup-event", "lineup-shop-2"]
    assert [item["id"] for item in payload["list"]] == ["lineup-shop"]
    assert payload["next_page_token"] is None


def test_fetch_cw_guide_list_portal_filter_matches_detail_portal_without_id(monkeypatch):
    guide_module = load_guide_module()

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {"list": [fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容")], "next_page_token": None},
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda lineup_id, *, timeout=10: (
            lineup_id,
            f"https://example.invalid/{lineup_id}",
            {
                "id": lineup_id,
                "tourn_detail": {
                    "portals": [
                        {
                            "name": "购物区",
                            "description": "购物区描述",
                        }
                    ]
                },
            },
        ),
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
