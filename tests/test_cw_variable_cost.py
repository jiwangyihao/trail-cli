from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest

from trail.scenes.cw.catalog import build_cw_catalog, resolve_cw_role_name
from trail.scenes.cw.variable_cost import (
    apply_cw_variable_cost_choice,
    bind_cw_variable_cost_shop_item,
    cw_role_business_key,
    is_cw_variable_cost_role,
)
from trail.scenes.cw.events import handle_cw_event


ObjectDict = dict[str, object]


def _role_state(cw_state: ObjectDict) -> ObjectDict:
    roles = cast(ObjectDict, cw_state["variable_cost_roles"])
    return cast(ObjectDict, roles["银狼LV.999"])


def _shop_state(cw_state: ObjectDict) -> ObjectDict:
    return cast(ObjectDict, cw_state["shop"])


def _shop_items(cw_state: ObjectDict) -> list[ObjectDict]:
    shop = _shop_state(cw_state)
    return cast(list[ObjectDict], shop["items"])


def test_plain_silver_wolf_and_lv999_are_unrelated_business_roles() -> None:
    assert is_cw_variable_cost_role("银狼LV.999") is True
    assert is_cw_variable_cost_role("银狼") is False
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼", cost=None, star=1)
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼", cost=4, star=1)


def test_catalog_resolver_never_collapses_plain_silver_wolf_and_lv999() -> None:
    catalog = build_cw_catalog(
        {
            "roles": [
                {"id": "1006", "name": "银狼", "trait_ids": []},
                {"id": "15061", "name": "银狼LV.999", "trait_ids": []},
            ],
            "traits": [],
        }
    )

    plain = resolve_cw_role_name("银狼", catalog)
    exact_lv999 = resolve_cw_role_name("银狼LV.999", catalog)
    compact_lv999 = resolve_cw_role_name("银狼LV999", catalog)

    assert plain is not None
    assert plain.name == "银狼"
    assert exact_lv999 is not None
    assert exact_lv999.name == "银狼LV.999"
    assert compact_lv999 is not None
    assert compact_lv999.name == "银狼LV.999"


def test_lv999_cost_phases_keep_same_role_name_but_distinct_phase_keys() -> None:
    assert cw_role_business_key("银狼LV.999", cost=3, star=1).name == "银狼LV.999"
    assert cw_role_business_key("银狼LV.999", cost=4, star=1).name == "银狼LV.999"
    assert cw_role_business_key("银狼LV.999", cost=3, star=1) != cw_role_business_key("银狼LV.999", cost=4, star=1)
    assert cw_role_business_key("银狼LV.999", cost=4, star=1) != cw_role_business_key("银狼LV.999", cost=4, star=2)


def test_confirmed_cost_up_updates_state_and_shop_items_without_stale() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}, {"name": "黑塔", "cost": 1}]},
    }

    apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert _role_state(cw_state) == {
        "cost": 4,
        "star": 1,
        "choice_available": False,
        "choice_confirmed": False,
        "last_confirmed_choice": "cost_up",
        "last_confirmed_choice_cost": 3,
        "confirmed_choices_by_cost": {3: "cost_up"},
    }
    assert _shop_state(cw_state)["stale"] is False
    assert _shop_items(cw_state)[0]["cost"] == 4
    assert _shop_items(cw_state)[1]["cost"] == 1


def test_confirmed_equipment_choice_keeps_cost_and_shop_items() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]},
    }

    apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="equipment")

    role_state = _role_state(cw_state)
    assert role_state["cost"] == 4
    assert role_state["star"] == 2
    assert role_state["choice_available"] is False
    assert role_state["last_confirmed_choice_cost"] == 4
    assert role_state["confirmed_choices_by_cost"] == {4: "equipment"}
    assert _shop_items(cw_state)[0]["cost"] == 4


def test_replaying_confirmed_same_cost_choice_is_rejected_without_shop_mutation() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {"4": "equipment"}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]},
    }

    with pytest.raises(ValueError, match="choice already confirmed"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert _role_state(cw_state)["cost"] == 4
    assert _role_state(cw_state)["confirmed_choices_by_cost"] == {"4": "equipment"}
    assert _shop_items(cw_state)[0]["cost"] == 4


def test_confirmed_choice_requires_fielded_two_star_available_state() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 3, "star": 1, "choice_available": False, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]},
    }

    with pytest.raises(ValueError, match="choice is not available"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert _role_state(cw_state)["cost"] == 3
    assert _shop_items(cw_state)[0]["cost"] == 3


def test_cost_up_requires_known_current_lv999_cost_without_shop_mutation() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"star": 2, "choice_available": True}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999"}]},
    }

    with pytest.raises(ValueError, match="current cost is unknown"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert "cost" not in _role_state(cw_state)
    assert "cost" not in _shop_items(cw_state)[0]


def test_cost_up_from_max_lv999_cost_is_rejected_without_shop_mutation() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 5, "star": 2, "choice_available": True}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 5}]},
    }
    before = deepcopy(cw_state)

    with pytest.raises(ValueError, match="already at max cost"):
        apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert cw_state == before


def test_shop_item_without_confirmed_lv999_cost_is_not_fresh() -> None:
    cw_state: ObjectDict = {"variable_cost_roles": {}, "shop": {"opened": True, "stale": False}}

    item = bind_cw_variable_cost_shop_item({"name": "银狼LV.999"}, cw_state)

    assert item["name"] == "银狼LV.999"
    assert item["uncertain"] is True
    assert item["stale"] is True
    assert "cost" not in item


def test_shop_item_with_known_lv999_cost_canonicalizes_alias_and_clears_unknown_flags() -> None:
    cw_state: ObjectDict = {"variable_cost_roles": {"银狼LV.999": {"cost": 4}}}

    item = bind_cw_variable_cost_shop_item({"name": "银狼LV999", "uncertain": True, "stale": True}, cw_state)

    assert item == {"name": "银狼LV.999", "cost": 4}


def test_bind_variable_cost_shop_item_does_not_backfill_when_roles_stale() -> None:
    cw_state: ObjectDict = {"variable_cost_roles_stale": True, "variable_cost_roles": {"银狼LV.999": {"cost": 4}}}

    item = bind_cw_variable_cost_shop_item({"name": "银狼LV999"}, cw_state)

    assert item["name"] == "银狼LV.999"
    assert item["uncertain"] is True
    assert item["stale"] is True
    assert "cost" not in item


def test_bind_variable_cost_shop_item_ignores_nested_stale_flags_for_backfill() -> None:
    cw_state: ObjectDict = {"variable_cost_roles": {"stale": True, "银狼LV.999": {"cost": 4, "stale": True}}}

    item = bind_cw_variable_cost_shop_item({"name": "银狼LV999"}, cw_state)

    assert item == {"name": "银狼LV.999", "cost": 4}


def test_cost_up_syncs_shop_items_to_next_cost_and_clears_unknown_flags() -> None:
    cw_state: ObjectDict = {
        "variable_cost_roles": {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}},
        "shop": {"opened": True, "stale": False, "items": [{"name": "银狼LV999", "uncertain": True, "stale": True}]},
    }

    apply_cw_variable_cost_choice(cw_state, role_name="银狼LV.999", choice="cost_up")

    assert _shop_items(cw_state)[0] == {"name": "银狼LV.999", "cost": 4}


def test_cw_event_handle_confirmed_lv999_choice_updates_shop_snapshot(tmp_path) -> None:
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}

    result = handle_cw_event(
        session,
        handler=lambda: ("special", "confirm"),
        variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
    )

    assert result["event_type"] == "special"
    assert result["variable_cost_choice"] == {"role_name": "银狼LV.999", "choice": "cost_up"}
    assert _role_state(cw_state)["cost"] == 4
    assert _role_state(cw_state)["star"] == 1
    assert _shop_state(cw_state)["stale"] is False
    assert _shop_items(cw_state)[0]["cost"] == 4


def test_cw_event_handle_rejects_replayed_lv999_choice_without_shop_mutation(tmp_path) -> None:
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {
        "银狼LV.999": {"cost": 4, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {"4": "equipment"}}
    }
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 4}]}

    with pytest.raises(ValueError, match="choice already confirmed"):
        handle_cw_event(
            session,
            handler=lambda: ("special", "confirm"),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert _role_state(cw_state)["cost"] == 4
    assert _shop_state(cw_state)["stale"] is False
    assert _shop_items(cw_state)[0]["cost"] == 4


def test_cw_event_handle_without_explicit_lv999_choice_does_not_infer_cost_up(tmp_path) -> None:
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = session.scene_state["cw"]
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}

    handle_cw_event(session, handler=lambda: ("special", "confirm"))

    assert _role_state(cw_state)["cost"] == 3
    assert _role_state(cw_state)["star"] == 2
    assert _shop_items(cw_state)[0]["cost"] == 3
