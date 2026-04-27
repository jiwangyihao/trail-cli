from __future__ import annotations

from trail.scenes.cw.catalog import (
    build_cw_catalog,
    clean_cw_trait_entry,
    merge_cw_trait_entries,
    resolve_cw_role_name,
    summarize_cw_field_traits,
)


def _config() -> dict[str, object]:
    return {
        "traits": [
            {"id": "1004", "name": "公司", "layers": [{"layer": 2}, {"layer": 3}]},
            {
                "id": "1007",
                "name": "仙舟",
                "layers": [{"layer": 3}, {"layer": 5}, {"layer": 7}, {"layer": 10}],
            },
            {
                "id": "2002",
                "name": "击破",
                "layers": [
                    {"layer": 2},
                    {"layer": 4},
                    {"layer": 6},
                    {"layer": 8},
                    {"layer": 10},
                ],
            },
            {"id": "3001", "name": "缺层羁绊", "layers": []},
        ],
        "roles": [
            {"id": "1502", "name": "爻光", "trait_ids": ["1007"]},
            {"id": "1304", "name": "砂金", "trait_ids": ["1004"]},
            {"id": "1222", "name": "忘归人", "trait_ids": ["1007", "2002"]},
            {"id": "1999", "name": "测试角色", "trait_ids": ["3001"]},
        ],
    }


def test_resolve_role_name_returns_canonical_role_with_low_confidence_warning() -> None:
    catalog = build_cw_catalog(_config())

    match = resolve_cw_role_name("交光", catalog, position={"kind": "slot", "area": "front", "index": 1})

    assert match is not None
    assert match.name == "爻光"
    assert match.role_id == "1502"
    assert match.raw_name == "交光"
    assert match.match_kind == "low_confidence"
    assert match.match_score == 0.5
    assert match.traits == ["仙舟"]
    assert match.warning is not None
    assert match.warning["code"] == "CW_ROLE_MATCH_LOW_CONFIDENCE"


def test_exact_role_match_has_no_raw_name_or_warning() -> None:
    catalog = build_cw_catalog(_config())

    match = resolve_cw_role_name("砂金", catalog, position={"kind": "slot", "area": "front", "index": 3})

    assert match is not None
    assert match.name == "砂金"
    assert match.raw_name is None
    assert match.match_kind == "exact"
    assert match.warning is None


def test_clean_trait_entry_keeps_stable_fields_and_strips_current_state() -> None:
    cleaned = clean_cw_trait_entry(
        {
            "trait_id": "2002",
            "trait_name": "击破",
            "trait_icon": "break.png",
            "trait_type": 1,
            "current_role_count": 4,
            "layers": [
                {"layer": 2, "quality": 0, "is_activated": True, "trait_desc": "二层"},
                {"layer": "4", "quality": 1, "is_activated": False, "trait_desc": "四层"},
            ],
            "remarks": [{"remark": "说明", "position": 0}],
            "role_ids": [1222, "1315"],
            "simple_desc": "击破说明",
        }
    )

    assert cleaned == {
        "id": "2002",
        "name": "击破",
        "icon": "break.png",
        "type": 1,
        "simple_desc": "击破说明",
        "remarks": [{"remark": "说明", "position": 0}],
        "role_ids": ["1222", "1315"],
        "layers": [
            {"layer": 2, "quality": 0, "trait_desc": "二层"},
            {"layer": 4, "quality": 1, "trait_desc": "四层"},
        ],
    }


def test_merge_trait_entries_unions_layers_remarks_and_role_ids_in_stable_order() -> None:
    merged = merge_cw_trait_entries(
        {
            "id": "1004",
            "name": "公司",
            "layers": [{"layer": 2, "trait_desc": "二层"}],
            "remarks": [{"remark": "A"}],
            "role_ids": ["1304"],
        },
        {
            "id": "1004",
            "name": "公司",
            "layers": [
                {"layer": 2, "quality": 1, "trait_desc": "二层完整"},
                {"layer": 3, "trait_desc": "三层"},
            ],
            "remarks": [{"remark": "A"}, {"remark": "B"}],
            "role_ids": ["1304", "1008"],
        },
    )

    assert merged["layers"] == [
        {"layer": 2, "quality": 1, "trait_desc": "二层完整"},
        {"layer": 3, "trait_desc": "三层"},
    ]
    assert merged["remarks"] == [{"remark": "A"}, {"remark": "B"}]
    assert merged["role_ids"] == ["1008", "1304"]


def test_summarize_field_traits_uses_enriched_layers_without_inference() -> None:
    catalog = build_cw_catalog(_config())
    front = [
        {"name": "砂金", "traits": ["公司"]},
        {"name": "忘归人", "traits": ["仙舟", "击破"]},
        {"name": "测试角色", "traits": ["缺层羁绊"]},
    ]

    summary = summarize_cw_field_traits(front=front, back=[], catalog=catalog)

    by_trait = {item["trait"]: item for item in summary}
    assert by_trait["公司"]["tiers"] == [2, 3]
    assert by_trait["公司"]["active_tier"] == 0
    assert by_trait["仙舟"]["tiers"] == [3, 5, 7, 10]
    assert by_trait["仙舟"]["active_tier"] == 0
    assert by_trait["击破"]["tiers"] == [2, 4, 6, 8, 10]
    assert by_trait["击破"]["active_tier"] == 0
    assert "缺层羁绊" not in by_trait


def test_catalog_helpers_ignore_invalid_collection_shapes_and_non_string_ocr() -> None:
    empty = build_cw_catalog({"traits": "bad", "roles": "bad"})
    assert empty.traits == []
    assert empty.roles == []

    catalog = build_cw_catalog(
        {
            "traits": [{"id": "1007", "name": "仙舟"}],
            "roles": [{"id": "1502", "name": "爻光", "trait_ids": 123}],
        }
    )
    assert catalog.roles[0]["traits"] == []
    assert catalog.role_traits_by_name == {"爻光": []}

    assert resolve_cw_role_name(123, build_cw_catalog(_config()), position={"kind": "slot"}) is None

    cleaned = clean_cw_trait_entry({"trait_name": "X", "layers": "bad", "role_ids": 123, "remarks": "bad"})
    assert cleaned == {"name": "X"}


def test_summarize_field_traits_ratio_is_bounded_by_highest_tier() -> None:
    catalog = build_cw_catalog(_config())
    half_summary = summarize_cw_field_traits(
        front=[{"traits": ["仙舟"]} for _ in range(5)],
        back=[],
        catalog=catalog,
    )
    full_summary = summarize_cw_field_traits(
        front=[{"traits": ["击破"]} for _ in range(10)],
        back=[],
        catalog=catalog,
    )

    by_half_trait = {item["trait"]: item for item in half_summary}
    by_full_trait = {item["trait"]: item for item in full_summary}
    assert by_half_trait["仙舟"]["active_tier"] == 5
    assert by_half_trait["仙舟"]["ratio"] == 0.5
    assert by_full_trait["击破"]["active_tier"] == 10
    assert by_full_trait["击破"]["ratio"] == 1.0
    assert 0 <= by_half_trait["仙舟"]["ratio"] <= 1
    assert 0 <= by_full_trait["击破"]["ratio"] <= 1
