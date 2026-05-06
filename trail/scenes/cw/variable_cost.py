from __future__ import annotations

from dataclasses import dataclass
import re
from typing import cast


ObjectDict = dict[str, object]


VARIABLE_COST_ROLE_NAME = "银狼LV.999"
VARIABLE_COST_ROLE_COSTS = (3, 4, 5)


@dataclass(frozen=True)
class CwRoleBusinessKey:
    name: str
    cost: int | None
    star: int | None


def _normalize_variable_cost_role_name(name: object) -> str:
    return re.sub(r"[\s.]+", "", str(name or "").strip().lower())


def is_cw_variable_cost_role(name: object) -> bool:
    return _normalize_variable_cost_role_name(name) == _normalize_variable_cost_role_name(VARIABLE_COST_ROLE_NAME)


def cw_role_business_key(name: object, *, cost: object | None = None, star: object | None = None) -> CwRoleBusinessKey:
    role_name = str(name or "").strip()
    if not is_cw_variable_cost_role(role_name):
        return CwRoleBusinessKey(role_name, None, _parse_star(star))
    return CwRoleBusinessKey(role_name, _parse_variable_cost(cost), _parse_star(star))


def _parse_variable_cost(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in VARIABLE_COST_ROLE_COSTS:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed in VARIABLE_COST_ROLE_COSTS else None
    return None


def _parse_star(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _variable_state(cw_state: ObjectDict, role_name: str) -> ObjectDict | None:
    roles_value = cw_state.get("variable_cost_roles")
    if not isinstance(roles_value, dict):
        return None
    roles = cast(ObjectDict, roles_value)
    state = roles.get(role_name)
    return cast(ObjectDict, state) if isinstance(state, dict) else None


def is_variable_cost_roles_stale(cw_state: ObjectDict) -> bool:
    return cw_state.get("variable_cost_roles_stale") is True


def bind_cw_variable_cost_shop_item(item: ObjectDict, cw_state: ObjectDict) -> ObjectDict:
    bound = dict(item)
    if not is_cw_variable_cost_role(bound.get("name")):
        return bound
    bound["name"] = VARIABLE_COST_ROLE_NAME
    state = _variable_state(cw_state, VARIABLE_COST_ROLE_NAME)
    cost = _parse_variable_cost(bound.get("cost"))
    if cost is None and not is_variable_cost_roles_stale(cw_state):
        cost = _parse_variable_cost(state.get("cost") if state else None)
    if cost is None:
        _ = bound.pop("cost", None)
        bound["uncertain"] = True
        bound["stale"] = True
        return bound
    bound["cost"] = cost
    _ = bound.pop("price", None)
    _ = bound.pop("uncertain", None)
    _ = bound.pop("stale", None)
    return bound


def _choice_for_cost(confirmed_by_cost: dict[object, object], cost: int) -> str | None:
    for key in (cost, str(cost)):
        value = confirmed_by_cost.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _next_variable_cost(current_cost: int) -> int | None:
    index = VARIABLE_COST_ROLE_COSTS.index(current_cost)
    if index + 1 >= len(VARIABLE_COST_ROLE_COSTS):
        return None
    return VARIABLE_COST_ROLE_COSTS[index + 1]


def apply_cw_variable_cost_choice(cw_state: ObjectDict, *, role_name: str, choice: str) -> None:
    if not is_cw_variable_cost_role(role_name):
        return
    if choice not in {"cost_up", "equipment"}:
        raise ValueError("unsupported 银狼LV.999 choice")
    state = _variable_state(cw_state, VARIABLE_COST_ROLE_NAME)
    if state is None:
        raise ValueError("银狼LV.999 current cost is unknown")
    current_cost = _parse_variable_cost(state.get("cost"))
    if current_cost is None:
        raise ValueError("银狼LV.999 current cost is unknown")
    if _parse_star(state.get("star")) != 2 or state.get("choice_available") is not True:
        raise ValueError("银狼LV.999 choice is not available")
    confirmed_value = state.get("confirmed_choices_by_cost")
    confirmed_by_cost = cast(dict[object, object], confirmed_value) if isinstance(confirmed_value, dict) else {}
    if _choice_for_cost(confirmed_by_cost, current_cost) is not None:
        raise ValueError("银狼LV.999 choice already confirmed for current cost")
    if choice == "cost_up":
        next_cost = _next_variable_cost(current_cost)
        if next_cost is None:
            raise ValueError("银狼LV.999 already at max cost")
        if not isinstance(confirmed_value, dict):
            state["confirmed_choices_by_cost"] = confirmed_by_cost
        confirmed_by_cost[current_cost] = "cost_up"
        state.clear()
        state.update(
            {
                "cost": next_cost,
                "star": 1,
                "choice_available": False,
                "choice_confirmed": False,
                "last_confirmed_choice": "cost_up",
                "last_confirmed_choice_cost": current_cost,
                "confirmed_choices_by_cost": confirmed_by_cost,
            }
        )
        _sync_shop_items_to_cost(cw_state, next_cost)
    elif choice == "equipment":
        if not isinstance(confirmed_value, dict):
            state["confirmed_choices_by_cost"] = confirmed_by_cost
        confirmed_by_cost[current_cost] = "equipment"
        state.update(
            {
                "cost": current_cost,
                "choice_available": False,
                "choice_confirmed": True,
                "last_confirmed_choice": "equipment",
                "last_confirmed_choice_cost": current_cost,
                "confirmed_choices_by_cost": confirmed_by_cost,
            }
        )


def _sync_shop_items_to_cost(cw_state: ObjectDict, cost: int) -> None:
    shop_value = cw_state.get("shop")
    if not isinstance(shop_value, dict):
        return
    shop = cast(ObjectDict, shop_value)
    items = shop.get("items")
    if not isinstance(items, list):
        return
    for item in cast(list[object], items):
        if not isinstance(item, dict):
            continue
        shop_item = cast(ObjectDict, item)
        if is_cw_variable_cost_role(shop_item.get("name")):
            shop_item["name"] = VARIABLE_COST_ROLE_NAME
            shop_item["cost"] = cost
            _ = shop_item.pop("price", None)
            _ = shop_item.pop("uncertain", None)
            _ = shop_item.pop("stale", None)
