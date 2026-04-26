from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trail.output.debug import render_debug_lines


class OutputFormat(StrEnum):
    TEXT = "text"
    YAML = "yaml"


_OUTPUT_OPTIONS = {"format": OutputFormat.TEXT, "verbose": False}
YAML_ALLOWLIST = {"daemon.status", "state.dump", "guide.fetch.cw", "guide.config.cw"}
WORKFLOW_HANDOFFS_PATH = Path(__file__).resolve().parents[2] / "skills" / "registry" / "workflow-handoffs.yaml"


def _normalize_output_format(output_format: str | OutputFormat) -> OutputFormat:
    if isinstance(output_format, OutputFormat):
        return output_format
    return OutputFormat(output_format)


def set_output_options(*, output_format: str | OutputFormat, verbose: bool) -> None:
    _OUTPUT_OPTIONS["format"] = _normalize_output_format(output_format)
    _OUTPUT_OPTIONS["verbose"] = bool(verbose)


def _quote(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def _encode_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    if not text:
        return _quote(text)
    if any(ch.isspace() or ch in {'"', '\\', '=', ','} for ch in text):
        return _quote(text)
    return text


def _append_shot(lines: list[str], payload: dict[str, Any]) -> None:
    screenshot = payload.get("screenshot")
    if screenshot:
        lines.append(f"shot path={_encode_value(screenshot)}")
        if payload.get("ok") is True:
            lines.append("info read_image_first=1")


def _append_success_capture_block(lines: list[str], payload: dict[str, Any]) -> None:
    _append_shot(lines, payload)


def _append_warnings(lines: list[str], payload: dict[str, Any]) -> None:
    warnings = payload.get("warnings") or []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        if warning.get("portal") is not None:
            lines.append(
                f"warn portal={_encode_value(warning.get('portal'))} score={_encode_value(_format_score_value(warning.get('score')))}"
            )
            continue
        if warning.get("trait") is not None or warning.get("trait_id") is not None:
            _append_fact_line(
                lines,
                "warn",
                ("trait", warning.get("trait")),
                ("trait_id", warning.get("trait_id")),
                ("score", _format_score_value(warning.get("score"))),
            )
            continue
        _append_fact_line(
            lines,
            "warn",
            ("code", warning.get("code")),
            ("query", warning.get("query")),
            ("resolved", warning.get("resolved")),
            ("msg", warning.get("message")),
        )


def _append_references(lines: list[str], payload: dict[str, Any]) -> None:
    references = payload.get("references") or []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        lines.append(
            f"ref path={_encode_value(reference.get('path'))} sim={_encode_value(reference.get('similarity'))}"
        )


def _append_common_success_lines(lines: list[str], payload: dict[str, Any], _command: str | None = None) -> list[str]:
    _append_success_capture_block(lines, payload)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _format_box(box: Any) -> str | None:
    if not isinstance(box, dict):
        return None
    left = box.get("left")
    top = box.get("top")
    width = box.get("width")
    height = box.get("height")
    if left is None or top is None or width is None or height is None:
        return None
    return f"{left},{top},{width},{height}"


def _format_fact_sequence(*facts: tuple[str, Any]) -> str:
    tokens: list[str] = []
    for key, value in facts:
        if value is None:
            continue
        tokens.append(f"{key}={_encode_value(value)}")
    return " ".join(tokens)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _render_success_summary(command: str, payload: dict[str, Any], *facts: tuple[str, Any]) -> list[str]:
    summary = _format_fact_sequence(*facts)
    return _append_common_success_lines([f"ok {command} {summary}" if summary else f"ok {command}"], payload, command)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _non_empty(value: Any) -> Any:
    if value == "":
        return None
    return value


def _first_carry_role(item: dict[str, Any]) -> str | None:
    carry_roles = _as_list(item.get("carry_roles"))
    for carry in carry_roles:
        if isinstance(carry, dict):
            name = carry.get("name")
            if isinstance(name, str) and name:
                return name
            continue
        if isinstance(carry, str) and carry:
            return carry
    return None


def _guide_id(item: dict[str, Any]) -> Any:
    return item.get("lineup_id") or item.get("id")


def _filled_count(values: Any) -> int:
    return sum(1 for value in _as_list(values) if value not in (None, ""))


def _compact_mapping(value: Any) -> str | None:
    mapping = _as_dict(value)
    if not mapping:
        return None
    return "|".join(f"{key}:{item}" for key, item in mapping.items())


def _compact_sequence_item(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if not value:
            return None
        return "/".join(f"{key}:{item}" for key, item in value.items())
    if isinstance(value, list):
        compact = _compact_sequence(value)
        return compact
    return str(value)


def _compact_sequence(value: Any) -> str | None:
    items = [_compact_sequence_item(item) for item in _as_list(value)]
    compact_items = [item for item in items if item]
    if not compact_items:
        return None
    return "|".join(compact_items)


def _compact_text_or_sequence(value: Any) -> str | None:
    text = _first_string(value)
    if text is not None:
        return text
    return _compact_sequence(value)


def _format_score_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    if isinstance(value, str):
        try:
            return f"{float(value):.2f}"
        except ValueError:
            return value or None
    return str(value)


def _compact_role_card(value: Any) -> str | None:
    item = _as_dict(value)
    name = _first_string(item.get("name"))
    if name is None:
        return None

    parts = [name]
    if item.get("is_carry") is True:
        parts.append("carry:1")

    for key in ("star", "rarity", "cost"):
        if item.get(key) is not None:
            parts.append(f"{key}:{item.get(key)}")
    return "/".join(parts)


def _compact_role_cards(value: Any) -> str | None:
    items = [_compact_role_card(item) for item in _as_list(value)]
    compact_items = [item for item in items if item]
    if not compact_items:
        return None
    return "|".join(compact_items)


def _append_fact_line(lines: list[str], prefix: str, *facts: tuple[str, Any]) -> None:
    rendered = _format_fact_sequence(*facts)
    if rendered:
        lines.append(f"{prefix} {rendered}")


@lru_cache(maxsize=1)
def _load_workflow_handoffs() -> dict[str, Any]:
    try:
        registry = yaml.safe_load(WORKFLOW_HANDOFFS_PATH.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}
    commands = registry.get("commands")
    return commands if isinstance(commands, dict) else {}


def _select_workflow_handoff(command: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(command, str) or not command:
        return {}
    command_handoff = _load_workflow_handoffs().get(command)
    if not isinstance(command_handoff, dict):
        return {}

    default = command_handoff.get("default")
    statuses = command_handoff.get("statuses")
    default_handoff = default if isinstance(default, dict) else {}
    status_handoffs = statuses if isinstance(statuses, dict) else {}

    status = _first_string(_as_dict(payload.get("data")).get("status"))
    if status is not None:
        status_handoff = status_handoffs.get(status)
        if isinstance(status_handoff, dict):
            return status_handoff

    return default_handoff


def _append_workflow_handoff(lines: list[str], command: Any, payload: dict[str, Any]) -> None:
    handoff = _select_workflow_handoff(command, payload)
    handoff_skill = _first_string(handoff.get("handoff_skill"))
    handoff_strength = _first_string(handoff.get("handoff_strength"))
    handoff_reason = _first_string(handoff.get("handoff_reason"))
    if handoff_skill is None or handoff_strength is None or handoff_reason is None:
        return
    _append_fact_line(
        lines,
        "info",
        ("handoff_skill", handoff_skill),
        ("handoff_strength", handoff_strength),
        ("handoff_reason", handoff_reason),
    )


def _finalize_success_lines(lines: list[str], command: str, payload: dict[str, Any]) -> list[str]:
    finalized_lines = list(lines)
    _append_workflow_handoff(finalized_lines, command, payload)
    return finalized_lines


def _append_guide_role_candidate_blocks(lines: list[str], data: dict[str, Any]) -> None:
    for block in _as_list(data.get("role_candidates")):
        if not isinstance(block, dict):
            continue
        candidates = [candidate for candidate in _as_list(block.get("candidates")) if isinstance(candidate, dict)]
        _append_fact_line(
            lines,
            "info",
            ("role_query", block.get("query")),
            ("role_resolution", block.get("role_resolution")),
            ("resolved", block.get("resolved")),
            ("candidates", len(candidates)),
        )
        for candidate in candidates:
            _append_fact_line(
                lines,
                "opt",
                ("query", candidate.get("query") or block.get("query")),
                ("role", candidate.get("role")),
                ("id", candidate.get("id")),
                ("selected", candidate.get("selected") if "selected" in candidate else None),
                ("score", _format_score_value(candidate.get("score"))),
                ("front_back", candidate.get("front_back")),
                ("traits", _compact_text_or_sequence(candidate.get("traits"))),
                ("role_tags", _compact_text_or_sequence(candidate.get("role_tags"))),
            )


def _append_guide_list_item(lines: list[str], item: dict[str, Any], *, index: int, portal: Any = None) -> None:
    final_roles = _compact_role_cards(item.get("final_role_cards"))
    facts: list[tuple[str, Any]] = []
    if portal is not None:
        facts.append(("portal", portal))
    facts.extend(
        [
            ("id", _guide_id(item)),
            ("title", item.get("title")),
            ("version", item.get("version")),
            ("idx", index),
            ("carry", _first_carry_role(item)),
            ("hard", bool(item.get("support_hard"))),
            ("change_equip", bool(item.get("has_change_equip"))),
            ("expert", bool(item.get("has_expert"))),
            ("like", item.get("like")),
            ("favour", item.get("favour")),
        ]
    )
    lines.append("guide " + _format_fact_sequence(*facts))
    if final_roles is not None:
        final_role_facts: list[tuple[str, Any]] = []
        if portal is not None:
            final_role_facts.append(("portal", portal))
        final_role_facts.extend([("idx", index), ("final_roles", final_roles)])


def _guide_tags(item: dict[str, Any]) -> str | None:
    return _guide_fetch_tags(item)


def _append_guide_summary_line(
    lines: list[str],
    *,
    item: dict[str, Any],
    idx: int | None = None,
    gid: int | None = None,
    portal: str | None = None,
) -> None:
    facts: list[tuple[str, Any]] = [("投资环境", _non_empty(portal))]
    if gid is not None:
        facts.extend((("idx", idx), ("gid", gid)))
    facts.extend(
        [
            ("攻略ID", _non_empty(_guide_id(item))),
            ("攻略标题", _non_empty(item.get("title"))),
            ("版本", _non_empty(item.get("version"))),
        ]
    )
    if gid is None:
        facts.append(("idx", idx))
    facts.extend(
        [
            ("主C", _first_carry_role(item)),
            ("攻略标签", _guide_tags(item)),
            ("点赞", item.get("like")),
            ("收藏", item.get("favour")),
        ]
    )
    _append_fact_line(lines, "guide", *facts)


def _append_guide_final_roles_line(
    lines: list[str],
    *,
    item: dict[str, Any],
    idx: int | None = None,
    gid: int | None = None,
    portal: str | None = None,
) -> None:
    final_roles = _compact_role_cards(item.get("final_role_cards"))
    if final_roles is None:
        return

    facts: list[tuple[str, Any]] = [("投资环境", _non_empty(portal))]
    if gid is not None:
        facts.extend((("idx", idx), ("gid", gid)))
    else:
        facts.append(("idx", idx))
    facts.append(("最终阵容", final_roles))
    _append_fact_line(lines, "guide", *facts)


def _append_cw_shop_items(lines: list[str], data: dict[str, Any]) -> None:
    for index, item in enumerate(_iter_sorted_cw_shop_items(data), start=1):
        facts: list[tuple[str, Any]] = [("idx", index)]
        slot = item.get("slot")
        if slot is not None:
            facts.append(("slot", slot))
        name = item.get("name")
        if name is not None:
            facts.append(("name", name))
        cost = item.get("price") if item.get("price") is not None else item.get("cost")
        if cost is not None:
            facts.append(("cost", cost))
        elif name is None and slot is not None:
            facts.append(("empty", True))
        lines.append("item " + _format_fact_sequence(*facts))


def _append_cw_shop_snapshot_info(lines: list[str], data: dict[str, Any]) -> None:
    _append_fact_line(
        lines,
        "info",
        ("coins", data.get("coins") if "coins" in data else None),
        ("level", data.get("level") if "level" in data else None),
        ("exp", data.get("exp") if "exp" in data else None),
        ("reserve_full", bool(data.get("reserve_full")) if "reserve_full" in data else None),
        ("team_size", data.get("team_size") if "team_size" in data else None),
    )


def _should_render_recover(payload: dict[str, Any]) -> bool:
    debug = payload.get("debug") or {}
    return isinstance(debug.get("last_known_stage"), str) and bool(debug.get("last_known_stage"))


def _render_cw_stage(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    stale = bool(data.get("stale")) if "stale" in data or data.get("value") is not None else None
    return _render_success_summary(
        command,
        payload,
        ("stage", data.get("value")),
        ("stale", stale),
    )


def _render_cw_battle_run(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("status", _non_empty(data.get("status"))),
        ("result", _non_empty(data.get("result"))),
        ("stage", _non_empty(data.get("stage"))),
        ("stale", bool(data.get("stale")) if "stale" in data else None),
        ("in_battle", bool(data.get("in_battle")) if "in_battle" in data else None),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_shot(lines, payload)
    _append_fact_line(
        lines,
        "info",
        ("round", _non_empty(data.get("round"))),
        ("hp", data.get("hp") if "hp" in data else None),
        ("coins", data.get("coins") if "coins" in data else None),
        ("exp", data.get("exp") if "exp" in data else None),
    )
    _append_fact_line(lines, "info", ("settle_text", _non_empty(data.get("settle_text"))))
    _append_fact_line(
        lines,
        "info",
        ("timeout_seconds", data.get("timeout_seconds") if "timeout_seconds" in data else None),
    )
    if data.get("status") == "in_progress":
        _append_fact_line(
            lines,
            "info",
            ("next_action", "cw.battle.run"),
            ("why", "battle_flow_not_finished"),
        )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_battle_clear_in_progress(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(command, payload, ("cleared", bool(data.get("cleared"))))


def _render_cw_entry(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = [f"ok {command} page=home"]
    _append_success_capture_block(lines, payload)
    if data.get("already_home") is True:
        lines.append("info already_home=1")
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_portal_cards(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    cards = _as_list(data.get("cards"))
    lines = [f"ok {command} cards={_encode_value(len(cards))}"]
    _append_success_capture_block(lines, payload)
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_idx = card.get("card_idx")
        _append_fact_line(
            lines,
            "opt",
            ("idx", card_idx),
            ("投资环境", _non_empty(card.get("portal_title"))),
            ("score", card.get("score")),
            ("待收集", 1 if card.get("new") else 0),
        )
        description = _non_empty(card.get("portal_description"))
        if description is not None:
            _append_fact_line(
                lines,
                "opt",
                ("idx", card_idx),
                ("说明", description),
            )
        for guide_index, guide in enumerate(_as_list(card.get("guides")), start=1):
            if not isinstance(guide, dict):
                continue
            _append_guide_summary_line(lines, item=guide, idx=card_idx, gid=guide_index)
            _append_guide_final_roles_line(lines, item=guide, idx=card_idx, gid=guide_index)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_portal_select(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("idx", data.get("card_idx")),
        ("投资环境", _non_empty(data.get("portal_title"))),
    )
    lines = [
        f"ok {command} {summary}" if summary else f"ok {command}"
    ]
    _append_success_capture_block(lines, payload)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_strategy_cards(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    cards = [card for card in _as_list(data.get("cards")) if isinstance(card, dict)]
    lines = [f"ok {command} cards={_encode_value(len(cards))}"]
    _append_success_capture_block(lines, payload)

    loaded_guide = 0
    for card in cards:
        if card.get("guide_loaded") is True or _coerce_int(card.get("guide_loaded")) == 1:
            loaded_guide = 1
        _append_fact_line(
            lines,
            "opt",
            ("idx", card.get("card_idx")),
            ("投资策略", _non_empty(card.get("strategy_title"))),
            ("攻略推荐", _non_empty(card.get("guide_match"))),
            ("刷新次数", card.get("refresh_count")),
        )
        description = _non_empty(card.get("strategy_description"))
        if description is not None:
            _append_fact_line(
                lines,
                "opt",
                ("idx", card.get("card_idx")),
                ("说明", description),
            )

    _append_fact_line(lines, "info", ("已加载攻略", loaded_guide))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_strategy_select(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("idx", data.get("card_idx")),
        ("投资策略", _non_empty(data.get("strategy_title"))),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_success_capture_block(lines, payload)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_guide_summary(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("攻略ID", _non_empty(_guide_id(data))),
        ("攻略标题", _non_empty(data.get("title"))),
        ("攻略码", _non_empty(data.get("share_code"))),
        ("版本", _non_empty(data.get("version"))),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_success_capture_block(lines, payload)
    _append_fact_line(lines, "guide", ("攻略标签", _guide_tags(data)))
    _append_fact_line(
        lines,
        "info",
        ("攻略快照ID", _non_empty(data.get("artifact")) or _non_empty(data.get("artifact_id"))),
    )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_slots_summary_line(command: str, data: dict[str, Any]) -> str:
    summary = _format_fact_sequence(
        ("front", _filled_count(data.get("front"))),
        ("back", _filled_count(data.get("back"))),
        ("hand", _filled_count(data.get("hand"))),
        ("stale", bool(data.get("stale")) if "stale" in data else None),
    )
    return f"ok {command} {summary}" if summary else f"ok {command}"


def _render_cw_slots(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _append_common_success_lines([_render_cw_slots_summary_line(command, data)], payload, command)


def _append_cw_slot_lines(lines: list[str], data: dict[str, Any]) -> None:
    for zone in ("front", "back", "hand"):
        for index, value in enumerate(_as_list(data.get(zone))):
            facts: list[tuple[str, Any]] = [("pos", f"{zone}:{index}")]

            if value in (None, ""):
                facts.append(("empty", True))
                lines.append("slot " + _format_fact_sequence(*facts))
                continue

            item = _as_dict(value)
            if item:
                name = item.get("name")
                cost = item.get("cost") if item.get("cost") is not None else item.get("price")
                facts.append(("name", name if name is not None else value))
                facts.append(("star", item.get("star")))
                facts.append(("traits", _compact_text_or_sequence(item.get("traits"))))
                facts.append(("rarity", item.get("rarity")))
                facts.append(("carry", True if item.get("is_carry") is True else None))
                facts.append(("cost", cost))
                lines.append("slot " + _format_fact_sequence(*facts))
                continue

            facts.append(("name", value))
            lines.append("slot " + _format_fact_sequence(*facts))


def _append_cw_slot_trait_summary(lines: list[str], data: dict[str, Any]) -> None:
    for item in _as_list(data.get("trait_summary")):
        if not isinstance(item, dict):
            continue
        active_tier = item.get("active_tier")
        total_tiers = item.get("total_tiers")
        activated = None
        if active_tier is not None and total_tiers is not None:
            activated = f"{active_tier}/{total_tiers}"
        tiers = item.get("tiers")
        tiers_text = None
        if isinstance(tiers, list):
            compact = [str(value) for value in tiers if value is not None]
            if compact:
                tiers_text = ",".join(compact)
        _append_fact_line(
            lines,
            "info",
            ("羁绊", item.get("trait")),
            ("档位", tiers_text),
            ("当前角色", item.get("owned_roles")),
            ("已激活档位", activated),
            ("占比", _format_score_value(item.get("ratio"))),
        )


def _render_cw_slots_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = [_render_cw_slots_summary_line(command, data)]
    _append_success_capture_block(lines, payload)
    _append_cw_slot_lines(lines, data)
    _append_cw_slot_trait_summary(lines, data)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _iter_sorted_cw_shop_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = _as_list(data.get("items"))
    decorated: list[tuple[int, int, int, dict[str, Any]]] = []
    for original_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        slot_value = _coerce_int(item.get("slot"))
        decorated.append(
            (
                1 if slot_value is None else 0,
                0 if slot_value is None else slot_value,
                original_index,
                item,
            )
        )
    decorated.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [item for _, _, _, item in decorated]


def _count_cw_shop_items(items: list[Any]) -> int:
    count = 0
    for item in items:
        if isinstance(item, dict):
            if item.get("name") is not None:
                count += 1
            continue
        if item is not None:
            count += 1
    return count


def _render_cw_shop_status(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("items"))
    lines = [f"ok {command} count={_encode_value(_count_cw_shop_items(items))}"]
    _append_success_capture_block(lines, payload)
    _append_cw_shop_items(lines, data)
    _append_cw_shop_snapshot_info(lines, data)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_shop_action(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("items")) if isinstance(data.get("items"), list) else None
    summary = _format_fact_sequence(
        ("opened", bool(data.get("opened")) if "opened" in data else None),
        ("stale", bool(data.get("stale")) if "stale" in data else None),
        ("count", _count_cw_shop_items(items) if items is not None else None),
        ("slot", data.get("slot")),
        ("expect", data.get("expect")),
        ("got", data.get("got")),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_success_capture_block(lines, payload)
    if items is not None:
        _append_cw_shop_items(lines, data)
    if command == "cw.shop.scan":
        _append_cw_shop_snapshot_info(lines, data)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_metrics(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("status", data.get("last_crystal_collection")),
    )


def _render_cw_sell_plan(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(command, payload, ("count", len(_as_list(data.get("candidates")))))


def _render_cw_options(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    options = _as_list(data.get("options"))
    lines = [f"ok {command} count={_encode_value(len(options))}"]
    _append_success_capture_block(lines, payload)
    for index, option in enumerate(options, start=1):
        if isinstance(option, dict):
            facts: list[tuple[str, Any]] = [("idx", index)]
            if option.get("id") is not None:
                facts.append(("id", option.get("id")))
            if option.get("name") is not None:
                facts.append(("name", option.get("name")))
            if len(facts) == 1:
                facts.append(("value", option.get("value")))
            lines.append("opt " + _format_fact_sequence(*facts))
            continue
        lines.append("opt " + _format_fact_sequence(("idx", index), ("value", option)))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_event_result(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("event_type", data.get("event_type")),
        ("handled_action", data.get("handled_action")),
    )


def _extract_ocr_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return []

    direct_points: list[tuple[float, float]] = []
    for item in value:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            direct_points.append((float(item[0]), float(item[1])))
    if direct_points:
        return direct_points

    nested_points: list[tuple[float, float]] = []
    for item in value:
        nested_points.extend(_extract_ocr_points(item))
    return nested_points


def _normalize_ocr_box(value: Any) -> dict[str, int] | None:
    if isinstance(value, dict):
        box = _format_box(value)
        if box is None:
            return None
        left, top, width, height = [int(part) for part in box.split(",")]
        return {"left": left, "top": top, "width": width, "height": height}

    points = _extract_ocr_points(value)
    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = int(min(xs))
    top = int(min(ys))
    right = int(max(xs))
    bottom = int(max(ys))
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _format_box_center(box: Any) -> str | None:
    if not isinstance(box, dict):
        return None
    left = box.get("left")
    top = box.get("top")
    width = box.get("width")
    height = box.get("height")
    if left is None or top is None or width is None or height is None:
        return None
    center_x = round(left + width / 2)
    center_y = round(top + height / 2)
    return f"{center_x},{center_y}"


def _normalize_ocr_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        text = item.get("text")
        if text in (None, ""):
            return None
        normalized = {"text": text}
        if item.get("score") is not None:
            normalized["score"] = item.get("score")
        box = _normalize_ocr_box(item.get("box"))
        if box is not None:
            normalized["box"] = box
        return normalized

    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None

    text = item[1]
    if text in (None, ""):
        return None
    normalized = {"text": text}
    if len(item) >= 3 and isinstance(item[2], (int, float)):
        normalized["score"] = item[2]
    box = _normalize_ocr_box(item[0])
    if box is not None:
        normalized["box"] = box
    return normalized


def _render_guide_fetch(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    summary = _format_fact_sequence(
        ("攻略标题", data.get("title")),
        ("攻略码", data.get("share_code")),
        ("版本", data.get("version")),
        ("最低金币", data.get("min_coins")),
        ("最低等级", data.get("min_level")),
        ("中期等级", data.get("mid_level")),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_success_capture_block(lines, payload)
    _append_fact_line(
        lines,
        "guide",
        ("攻略标签", _guide_fetch_tags(data)),
    )
    _append_fact_line(
        lines,
        "guide",
        ("羁绊列表", _guide_fetch_traits(data)),
    )
    _append_fact_line(
        lines,
        "guide",
        ("投资环境", _compact_sequence(data.get("portals"))),
        ("优选投资策略", _compact_sequence(data.get("first_fight_augments"))),
        ("次选投资策略", _compact_sequence(data.get("second_fight_augments"))),
    )
    _append_fact_line(
        lines,
        "guide",
        ("简易装备优先度", _compact_sequence(data.get("order_basic"))),
        ("进阶装备优先度", _compact_sequence(data.get("order_compose"))),
    )
    for stage in _as_list(data.get("role_stages")):
        if not isinstance(stage, dict):
            continue
        stage_name = _guide_fetch_stage_name(stage.get("stage"))
        _append_fact_line(
            lines,
            "guide",
            ("阶段", stage_name),
            ("前台", _compact_role_cards(stage.get("front_roles"))),
            ("后台", _compact_role_cards(stage.get("back_roles"))),
            ("羁绊", _compact_sequence(stage.get("traits"))),
        )
        for role in _as_list(stage.get("front_roles")) + _as_list(stage.get("back_roles")):
            if not isinstance(role, dict):
                continue
            first_equipments = _compact_sequence(role.get("first_equipments"))
            second_equipments = _compact_sequence(role.get("second_equipments"))
            if first_equipments is None and second_equipments is None:
                continue
            _append_fact_line(
                lines,
                "guide",
                ("阶段", stage_name),
                ("角色", role.get("name")),
                ("优选装备", first_equipments),
                ("次选装备", second_equipments),
            )
    _append_fact_line(
        lines,
        "guide",
        ("运营思路", data.get("operation_guide")),
    )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _guide_fetch_stage_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    mapping = {
        "Opening": "前期阵容",
        "Early": "前期阵容",
        "Middle": "中期阵容",
        "Mid": "中期阵容",
        "Final": "最终阵容",
    }
    return mapping.get(value, value)


def _guide_fetch_tags(data: dict[str, Any]) -> str | None:
    tags: list[str] = []

    for item in _as_list(data.get("labels")):
        if isinstance(item, str) and item:
            tags.append(f"#{item}")

    if bool(data.get("support_hard")):
        tags.append("#适用超频博弈")
    if bool(data.get("has_change_equip")):
        tags.append("#星徽攻略")
    if bool(data.get("has_expert")):
        tags.append("#专家顾问")

    if not tags:
        return None
    return "|".join(tags)


def _guide_fetch_traits(data: dict[str, Any]) -> str | None:
    order: list[str] = []
    counts: dict[str, int | None] = {}

    for stage in _as_list(data.get("role_stages")):
        if not isinstance(stage, dict):
            continue
        for trait in _as_list(stage.get("traits")):
            if not isinstance(trait, str) or not trait:
                continue
            count, name = _split_trait_count(trait)
            key = name or trait
            if key not in counts:
                counts[key] = count
                order.append(key)
                continue
            existing = counts[key]
            if count is not None and (existing is None or count > existing):
                counts[key] = count

    if not order:
        return None
    rendered: list[str] = []
    for key in order:
        count = counts[key]
        rendered.append(f"{count}{key}" if count is not None else key)
    return "|".join(rendered)


def _split_trait_count(value: str) -> tuple[int | None, str]:
    index = 0
    while index < len(value) and value[index].isdigit():
        index += 1
    if index == 0:
        return None, value
    return int(value[:index]), value[index:]


def _render_guide_list(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    portal_groups = _as_list(data.get("portals"))
    if portal_groups:
        total_count = data.get("count")
        if not isinstance(total_count, int):
            total_count = sum(len(_as_list(group.get("list")) if isinstance(group, dict) else []) for group in portal_groups)
        summary = _format_fact_sequence(
            ("groups", len(portal_groups)),
            ("count", total_count),
            ("more", bool(data.get("more"))),
        )
        lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
        _append_success_capture_block(lines, payload)
        _append_guide_role_candidate_blocks(lines, data)
        for group in portal_groups:
            if not isinstance(group, dict):
                continue
            items = _as_list(group.get("list"))
            _append_fact_line(
                lines,
                "guide",
                ("投资环境", _non_empty(group.get("portal_title"))),
                ("count", len(items)),
                ("more", bool(group.get("more"))),
                (
                    "next",
                    group.get("next_page_token")
                    if bool(group.get("more")) and group.get("next_page_token")
                    else None,
                ),
            )
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                portal = group.get("portal_title")
                _append_guide_summary_line(lines, item=item, idx=index, portal=portal)
                _append_guide_final_roles_line(lines, item=item, idx=index, portal=portal)
        _append_warnings(lines, payload)
        _append_references(lines, payload)
        return lines

    items = _as_list(data.get("list"))
    next_page_token = data.get("next_page_token")
    summary = _format_fact_sequence(
        ("count", len(items)),
        ("more", bool(next_page_token)),
        ("next", next_page_token if next_page_token else None),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_success_capture_block(lines, payload)
    _append_guide_role_candidate_blocks(lines, data)
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        _append_guide_summary_line(lines, item=item, idx=index)
        _append_guide_final_roles_line(lines, item=item, idx=index)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_window_attach(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [f"ok {command} title={_encode_value(data.get('title'))} hwnd={_encode_value(data.get('hwnd'))}"],
        payload,
        command,
    )


def _render_window_launch(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [
            f"ok {command} started={_encode_value(bool(data.get('started')))} "
            f"already_running={_encode_value(bool(data.get('already_running')))} path={_encode_value(data.get('path'))}"
        ],
        payload,
        command,
    )


def _render_session_create(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    binding = data.get("window_binding") or {}
    return _append_common_success_lines(
        [
            f"ok {command} session={_encode_value(data.get('session_id'))} "
            f"title={_encode_value(binding.get('title'))} hwnd={_encode_value(binding.get('hwnd'))}"
        ],
        payload,
        command,
    )


def _render_start_run(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    first_line = (
        f"ok {command} "
        f"status={_encode_value(data.get('status'))} "
        f"session={_encode_value(data.get('session'))} "
        f"reused={_encode_value(data.get('reused'))} "
        f"title={_encode_value(data.get('title'))} "
        f"hwnd={_encode_value(data.get('hwnd'))}"
    )
    return _append_common_success_lines([first_line], payload, command)


def _render_screen_shot(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [f"ok {command} captured={_encode_value(bool(data.get('captured')))}"],
        payload,
        command,
    )


def _render_ocr_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    result = data.get("result") or []
    lines = [f"ok {command} hits={_encode_value(len(result))}"]
    _append_success_capture_block(lines, payload)
    for item in result:
        normalized = _normalize_ocr_item(item)
        if normalized is None:
            continue
        line = f"text value={_encode_value(normalized.get('text'))}"
        box = _format_box(normalized.get("box"))
        if box is not None:
            line += f" box={box}"
        center = _format_box_center(normalized.get("box"))
        if center is not None:
            line += f" center={center}"
        lines.append(line)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_image_locate(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    box = _format_box(data.get("box"))
    line = f"ok {command}"
    if box is not None:
        line += f" box={box}"
    return _append_common_success_lines([line], payload, command)


def _render_image_wait(command: str, payload: dict[str, Any]) -> list[str]:
    return _render_image_locate(command, payload)


def _render_daemon_install(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} manifest_path={_encode_value(data.get('manifest_path'))}"]


def _render_daemon_start(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [
        f"ok {command} started={_encode_value(bool(data.get('started')))} "
        f"already_running={_encode_value(bool(data.get('already_running')))}"
    ]


def _render_daemon_status(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    install = data.get("install") or {}
    runtime = data.get("runtime") or {}
    facts = _format_fact_sequence(
        ("state", runtime.get("state")),
        ("pid", runtime.get("pid")),
        ("endpoint", runtime.get("endpoint")),
        ("protocol", install.get("protocol_version")),
    )
    lines = [f"ok {command} {facts}" if facts else f"ok {command}"]
    if runtime.get("last_start_error"):
        lines.append(f"why msg={_encode_value(runtime.get('last_start_error'))}")
    return _append_common_success_lines(lines, payload, command)


def _render_daemon_stop(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} stopped={_encode_value(bool(data.get('stopped')))}"]


def _render_daemon_restart(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [
        f"ok {command} stopped={_encode_value(bool(data.get('stopped')))} "
        f"started={_encode_value(bool(data.get('started')))} "
        f"already_running={_encode_value(bool(data.get('already_running')))}"
    ]


def _render_daemon_logs(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} log_dir={_encode_value(data.get('log_dir'))}"]


def _render_daemon_request_status(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    facts = _format_fact_sequence(
        ("request", data.get("request_id")),
        ("session", data.get("session_id")),
        ("final_state", data.get("final_state")),
        ("last_visible_stage", data.get("last_visible_stage")),
        ("tainted", bool(data.get("tainted"))),
    )
    return [
        f"ok {command} {facts}" if facts else f"ok {command}"
    ]


def _render_daemon_reconcile_session(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [
        f"ok {command} session={_encode_value(data.get('session_id'))} tainted={_encode_value(bool(data.get('tainted')))}"
    ]


def _render_state_dump(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    scene_state = _as_dict(data.get("scene_state"))
    scene = next((name for name in scene_state if name != "daemon"), "unknown")

    daemon_state = _as_dict(scene_state.get("daemon"))
    selected_scene_state = _as_dict(scene_state.get(scene))
    stage_state = _as_dict(selected_scene_state.get("stage"))
    last_stage = _as_dict(data.get("last_stage"))

    facts: list[tuple[str, Any]] = [
        ("session", data.get("session_id")),
        ("scene", scene),
        ("last_stage", last_stage.get("value")),
        ("stage_stale", True if stage_state.get("stale") is True else None),
        (
            "stage_error",
            stage_state.get("error", {}).get("code")
            if isinstance(stage_state.get("error"), dict)
            else None,
        ),
        ("tainted", bool(daemon_state.get("tainted"))),
    ]
    summary = _format_fact_sequence(*facts)
    return _append_common_success_lines([f"ok {command} {summary}" if summary else f"ok {command}"], payload, command)


def _render_guide_config(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    meta = _as_dict(data.get("meta")) or data
    summary = _format_fact_sequence(
        ("赛季", meta.get("season_id")),
        ("子赛季", meta.get("sub_season_id")),
        ("大版本", meta.get("big_version")),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_shot(lines, payload)
    _append_fact_line(
        lines,
        "info",
        ("搜牌档位", len(_as_list(data.get("lineup_levels")))),
        ("羁绊", len(_as_list(data.get("traits")))),
        ("角色", len(_as_list(data.get("roles")))),
        ("角色标签", len(_as_list(data.get("role_tags")))),
        ("投资环境", len(_as_list(data.get("portal_list")))),
    )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_generic_success(command: str, payload: dict[str, Any]) -> list[str]:
    return _append_common_success_lines([f"ok {command}"], payload, command)


TEXT_RENDERERS = {
    "cw.enter": _render_cw_entry,
    "cw.start": _render_cw_portal_cards,
    "cw.portal.select": _render_cw_portal_select,
    "cw.portal.detect": _render_cw_portal_cards,
    "cw.portal.refresh": _render_cw_portal_cards,
    "cw.portal.restart": _render_cw_portal_cards,
    "cw.strategy.detect": _render_cw_strategy_cards,
    "cw.strategy.refresh": _render_cw_strategy_cards,
    "cw.strategy.select": _render_cw_strategy_select,
    "cw.guide.apply": _render_cw_guide_summary,
    "cw.guide.current": _render_cw_guide_summary,
    "cw.stage.detect": _render_cw_stage,
    "cw.stage.wait": _render_cw_stage,
    "cw.slots.read": _render_cw_slots_read,
    "cw.slots.swap": _render_cw_slots,
    "cw.slots.place": _render_cw_slots,
    "cw.crystals.collect": _render_cw_metrics,
    "cw.hand.sell": _render_cw_slots,
    "cw.hand.sell_plan": _render_cw_sell_plan,
    "cw.shop.open": _render_cw_shop_action,
    "cw.shop.scan": _render_cw_shop_action,
    "cw.shop.buy_slot": _render_cw_shop_action,
    "cw.shop.refresh": _render_cw_shop_action,
    "cw.shop.close": _render_cw_shop_action,
    "cw.shop.status": _render_cw_shop_status,
    "cw.replenish.read": _render_cw_options,
    "cw.replenish.choose": _render_cw_stage,
    "cw.invest.read": _render_cw_options,
    "cw.invest.choose": _render_cw_stage,
    "cw.encounter.read": _render_cw_options,
    "cw.encounter.choose": _render_cw_stage,
    "cw.fortune.read": _render_cw_options,
    "cw.fortune.choose": _render_cw_stage,
    "cw.boss_preview.confirm": _render_cw_stage,
    "cw.battle.start": _render_cw_stage,
    "cw.battle.continue": _render_cw_stage,
    "cw.battle.run": _render_cw_battle_run,
    "cw.battle.clear_in_progress": _render_cw_battle_clear_in_progress,
    "cw.settle.next": _render_cw_stage,
    "cw.event.handle": _render_cw_event_result,
    "window.attach": _render_window_attach,
    "window.launch": _render_window_launch,
    "session.create": _render_session_create,
    "start.run": _render_start_run,
    "screen.shot": _render_screen_shot,
    "ocr.read": _render_ocr_read,
    "image.locate": _render_image_locate,
    "image.wait": _render_image_wait,
    "guide.fetch.cw": _render_guide_fetch,
    "guide.list.cw": _render_guide_list,
    "daemon.install": _render_daemon_install,
    "daemon.start": _render_daemon_start,
    "daemon.status": _render_daemon_status,
    "daemon.stop": _render_daemon_stop,
    "daemon.restart": _render_daemon_restart,
    "daemon.logs": _render_daemon_logs,
    "daemon.request_status": _render_daemon_request_status,
    "daemon.reconcile_session": _render_daemon_reconcile_session,
    "state.dump": _render_state_dump,
    "guide.config.cw": _render_guide_config,
}


def _unsupported_yaml_payload(payload: dict[str, Any], command: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": {},
        "screenshot": payload.get("screenshot"),
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": payload.get("debug"),
        "error": {
            "code": "OUTPUT_FORMAT_NOT_SUPPORTED",
            "message": f"yaml not supported for {command}",
        },
    }


def _failure_tainted_value(payload: dict[str, Any]) -> bool | None:
    debug = _as_dict(payload.get("debug"))
    if "tainted" in debug:
        return bool(debug.get("tainted"))

    data = _as_dict(payload.get("data"))
    if "tainted" in data:
        return bool(data.get("tainted"))

    if payload.get("ok") is False and _should_render_recover(payload):
        return True

    return None


def _render_failure_lines(command: str, payload: dict[str, Any]) -> list[str]:
    error = payload.get("error") or {}
    debug = _as_dict(payload.get("debug"))

    first_line = f"fail {command} code={_encode_value(error.get('code'))}"
    tainted = _failure_tainted_value(payload)
    if tainted is not None:
        first_line += f" tainted={_encode_value(tainted)}"

    lines = [first_line]
    request_id = debug.get("request_id") or payload.get("request_id")
    if request_id:
        lines.append(f"request id={_encode_value(request_id)}")

    _append_shot(lines, payload)

    message = error.get("message")
    if message is not None:
        lines.append(f"why msg={_encode_value(message)}")

    _append_warnings(lines, payload)
    _append_references(lines, payload)

    if request_id and _should_render_recover(payload):
        lines.append(f"recover action=daemon.request_status request={_encode_value(request_id)}")

    return lines


def _render_text_lines(command: str, payload: dict[str, Any]) -> list[str]:
    if payload.get("ok"):
        renderer = TEXT_RENDERERS.get(command, _render_generic_success)
        return _finalize_success_lines(renderer(command, payload), command, payload)
    return _render_failure_lines(command, payload)


def render_output(
    command: str,
    payload: dict[str, Any],
    *,
    output_format: str | OutputFormat = OutputFormat.TEXT,
    verbose: bool = False,
) -> str:
    resolved_output_format = _normalize_output_format(output_format)
    rendered_payload = payload
    if resolved_output_format is OutputFormat.YAML and command not in YAML_ALLOWLIST and payload.get("ok") is True:
        rendered_payload = _unsupported_yaml_payload(payload, command)

    lines = _render_text_lines(command, rendered_payload)
    if resolved_output_format is OutputFormat.YAML and command in YAML_ALLOWLIST:
        body = yaml.safe_dump(rendered_payload.get("data") or {}, allow_unicode=True, sort_keys=False).rstrip()
        if body:
            lines.append(body)
    if verbose:
        lines.extend(render_debug_lines(rendered_payload.get("debug")))
    return "\n".join(lines)


def print_output(command: str, payload: dict[str, Any]) -> None:
    print(
        render_output(
            command,
            payload,
            output_format=_OUTPUT_OPTIONS["format"],
            verbose=_OUTPUT_OPTIONS["verbose"],
        )
    )
