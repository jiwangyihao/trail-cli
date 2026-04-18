from __future__ import annotations

from difflib import SequenceMatcher

import pytest

from trail.scenes.cw.portal import summarize_portal_cards


def _dict_piece(text: str, left: int, top: int, width: int = 80, height: int = 20) -> dict[str, object]:
    return {
        "text": text,
        "box": {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
    }


def _tuple_piece(text: str, left: int, top: int, width: int = 80, height: int = 20) -> list[object]:
    polygon = [
        [left, top],
        [left + width, top],
        [left + width, top + height],
        [left, top + height],
    ]
    return [polygon, text, 0.99]


def test_summarize_portal_cards_groups_three_lanes_and_merges_rows():
    portal_list = [
        {"portal_id": "lane1-alt", "title": "Alpha Beta", "description": "Gamma"},
        {"portal_id": "lane1-win", "title": "Beta Alpha", "description": "Gamma"},
        {"portal_id": "lane2", "title": "Delta Epsilon", "description": "Zeta"},
        {"portal_id": "lane3", "title": "Eta Theta", "description": "Iota"},
    ]
    pieces = [
        _dict_piece("Alpha", left=120, top=10),
        _dict_piece("Beta", left=20, top=42),
        _dict_piece("Gamma", left=20, top=90),
        _tuple_piece("Delta", left=760, top=20),
        _tuple_piece("Epsilon", left=860, top=20),
        _tuple_piece("Zeta", left=760, top=70),
        _dict_piece("Eta", left=1500, top=30),
        _dict_piece("Theta", left=1590, top=30),
        _dict_piece("Iota", left=1500, top=80),
    ]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards == [
        {
            "card_idx": 1,
            "portal_title": "Beta Alpha",
            "portal_description": "Gamma",
            "score": pytest.approx(1.0),
        },
        {
            "card_idx": 2,
            "portal_title": "Delta Epsilon",
            "portal_description": "Zeta",
            "score": pytest.approx(1.0),
        },
        {
            "card_idx": 3,
            "portal_title": "Eta Theta",
            "portal_description": "Iota",
            "score": pytest.approx(1.0),
        },
    ]


def test_summarize_portal_cards_normalizes_case_whitespace_and_punctuation_noise():
    portal_list = [
        {"portal_id": "aaa", "title": "Alpha Bonus", "description": "Great Rewards"},
        {"portal_id": "zzz", "title": "Other Portal", "description": "Other Description"},
    ]
    pieces = [
        _tuple_piece(" ALPHA，", left=40, top=20),
        _tuple_piece("bonus！！  ", left=150, top=20),
        _tuple_piece("Great   Rewards...", left=40, top=70, width=180),
    ]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards[0]["portal_title"] == "Alpha Bonus"
    assert cards[0]["portal_description"] == "Great Rewards"


def test_summarize_portal_cards_uses_higher_of_title_and_title_plus_description_scores():
    portal_list = [
        {"portal_id": "combo", "title": "Market", "description": "Discounted Upgrades"},
        {"portal_id": "plain", "title": "Market Deals", "description": "Supplies"},
    ]
    pieces = [_dict_piece("Market Discounted Upgrades", left=50, top=20, width=260)]

    cards = summarize_portal_cards(pieces, portal_list)
    expected_combined_score = SequenceMatcher(
        a="market discounted upgrades",
        b="market discounted upgrades",
    ).ratio()

    assert cards[0]["portal_title"] == "Market"
    assert cards[0]["portal_description"] == "Discounted Upgrades"
    assert cards[0]["score"] == pytest.approx(expected_combined_score)


def test_summarize_portal_cards_breaks_ties_by_title_score_then_portal_id_and_keeps_stable_empty_lane_behavior():
    portal_list = [
        {"portal_id": "zzz", "title": "Alpha", "description": "Bonus"},
        {"portal_id": "aaa", "title": "Alpha Bonus", "description": ""},
        {"portal_id": "bbb", "title": "Beta", "description": "Extra"},
    ]
    pieces = [_dict_piece("Alpha Bonus", left=60, top=20, width=160)]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards[0] == {
        "card_idx": 1,
        "portal_title": "Alpha Bonus",
        "portal_description": "",
        "score": pytest.approx(1.0),
    }
    assert cards[1] == {
        "card_idx": 2,
        "portal_title": "Alpha Bonus",
        "portal_description": "",
        "score": pytest.approx(0.0),
    }
    assert cards[2] == {
        "card_idx": 3,
        "portal_title": "Alpha Bonus",
        "portal_description": "",
        "score": pytest.approx(0.0),
    }
    assert all(set(card) == {"card_idx", "portal_title", "portal_description", "score"} for card in cards)
