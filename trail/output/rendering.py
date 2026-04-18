from __future__ import annotations

from enum import StrEnum
from typing import Any

import yaml

from trail.output.debug import render_debug_lines


class OutputFormat(StrEnum):
    TEXT = "text"
    YAML = "yaml"


_OUTPUT_OPTIONS = {"format": OutputFormat.TEXT, "verbose": False}
YAML_ALLOWLIST = {"daemon.status", "state.dump", "guide.config.cw"}


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


def _append_warnings(lines: list[str], payload: dict[str, Any]) -> None:
    warnings = payload.get("warnings") or []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        lines.append(
            f"warn code={_encode_value(warning.get('code'))} msg={_encode_value(warning.get('message'))}"
        )


def _append_references(lines: list[str], payload: dict[str, Any]) -> None:
    references = payload.get("references") or []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        lines.append(
            f"ref path={_encode_value(reference.get('path'))} sim={_encode_value(reference.get('similarity'))}"
        )


def _append_common_success_lines(lines: list[str], payload: dict[str, Any]) -> list[str]:
    _append_shot(lines, payload)
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
    return _append_common_success_lines([f"ok {command} {summary}" if summary else f"ok {command}"], payload)


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


def _append_cw_shop_items(lines: list[str], data: dict[str, Any]) -> None:
    for index, item in enumerate(_iter_sorted_cw_shop_items(data), start=1):
        facts: list[tuple[str, Any]] = [("idx", index)]
        if item.get("slot") is not None:
            facts.append(("slot", item.get("slot")))
        if item.get("name") is not None:
            facts.append(("name", item.get("name")))
        cost = item.get("price") if item.get("price") is not None else item.get("cost")
        if cost is not None:
            facts.append(("cost", cost))
        lines.append("item " + _format_fact_sequence(*facts))


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


def _render_cw_entry(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("mode", data.get("mode")),
        ("difficulty", data.get("difficulty")),
        ("battle", data.get("battle_mode") or data.get("battle")),
    )


def _render_cw_guide_summary(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("id", data.get("lineup_id") or data.get("id")),
        ("artifact", data.get("artifact_id") or data.get("artifact")),
    )


def _render_cw_slots(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("front", _filled_count(data.get("front"))),
        ("back", _filled_count(data.get("back"))),
        ("hand", _filled_count(data.get("hand"))),
        ("stale", bool(data.get("stale")) if "stale" in data else None),
    )


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
                facts.append(("rarity", item.get("rarity")))
                facts.append(("carry", True if item.get("is_carry") is True else None))
                facts.append(("cost", cost))
                lines.append("slot " + _format_fact_sequence(*facts))
                continue

            facts.append(("name", value))
            lines.append("slot " + _format_fact_sequence(*facts))


def _render_cw_slots_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = _render_cw_slots(command, payload)
    insert_at = 2 if payload.get("screenshot") else 1
    slot_lines: list[str] = []
    _append_cw_slot_lines(slot_lines, data)
    lines[insert_at:insert_at] = slot_lines
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


def _render_cw_shop_status(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("items"))
    lines = [f"ok {command} count={_encode_value(len(items))}"]
    _append_shot(lines, payload)
    _append_cw_shop_items(lines, data)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_cw_shop_action(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("items")) if isinstance(data.get("items"), list) else None
    summary = _format_fact_sequence(
        ("opened", bool(data.get("opened")) if "opened" in data else None),
        ("stale", bool(data.get("stale")) if "stale" in data else None),
        ("count", len(items) if items is not None else None),
        ("slot", data.get("slot")),
        ("expect", data.get("expect")),
        ("got", data.get("got")),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_shot(lines, payload)
    if items is not None:
        _append_cw_shop_items(lines, data)
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
    _append_shot(lines, payload)
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
        ("id", data.get("lineup_id") or data.get("id")),
        ("share_code", data.get("share_code")),
        ("version", data.get("version")),
        ("min_level", data.get("min_level")),
        ("mid_level", data.get("mid_level")),
        ("hard", bool(data.get("support_hard"))),
        ("change_equip", bool(data.get("has_change_equip"))),
        ("expert", bool(data.get("has_expert"))),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_shot(lines, payload)
    _append_fact_line(
        lines,
        "guide",
        ("on_field", _compact_mapping(data.get("on_field"))),
        ("off_field", _compact_mapping(data.get("off_field"))),
    )
    _append_fact_line(
        lines,
        "guide",
        ("portals", _compact_sequence(data.get("portals"))),
        ("first_augments", _compact_sequence(data.get("first_fight_augments"))),
        ("second_augments", _compact_sequence(data.get("second_fight_augments"))),
    )
    _append_fact_line(
        lines,
        "guide",
        ("order_basic", _compact_sequence(data.get("order_basic"))),
        ("order_compose", _compact_sequence(data.get("order_compose"))),
        ("role_stages", _compact_sequence(data.get("role_stages"))),
    )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_guide_list(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    items = _as_list(data.get("list"))
    next_page_token = data.get("next_page_token")
    summary = _format_fact_sequence(
        ("count", len(items)),
        ("more", bool(next_page_token)),
        ("next", next_page_token if next_page_token else None),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    _append_shot(lines, payload)
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        final_roles = _compact_role_cards(item.get("final_role_cards"))
        lines.append(
            "guide "
            + _format_fact_sequence(
                ("id", _guide_id(item)),
                ("idx", index),
                ("carry", _first_carry_role(item)),
                ("hard", bool(item.get("support_hard"))),
                ("change_equip", bool(item.get("has_change_equip"))),
                ("expert", bool(item.get("has_expert"))),
            )
        )
        if final_roles is not None:
            _append_fact_line(lines, "guide", ("idx", index), ("final_roles", final_roles))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


def _render_window_attach(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [f"ok {command} title={_encode_value(data.get('title'))} hwnd={_encode_value(data.get('hwnd'))}"],
        payload,
    )


def _render_window_launch(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [
            f"ok {command} started={_encode_value(bool(data.get('started')))} "
            f"already_running={_encode_value(bool(data.get('already_running')))} path={_encode_value(data.get('path'))}"
        ],
        payload,
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
    )


def _render_screen_shot(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return _append_common_success_lines(
        [f"ok {command} captured={_encode_value(bool(data.get('captured')))}"],
        payload,
    )


def _render_ocr_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    result = data.get("result") or []
    lines = [f"ok {command} hits={_encode_value(len(result))}"]
    _append_shot(lines, payload)
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
    return _append_common_success_lines([line], payload)


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
    return _append_common_success_lines(lines, payload)


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
    return _append_common_success_lines([f"ok {command} {summary}" if summary else f"ok {command}"], payload)


def _render_guide_config(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    meta = _as_dict(data.get("meta")) or data
    summary = _format_fact_sequence(
        ("season", meta.get("season_id")),
        ("sub_season", meta.get("sub_season_id")),
        ("big_version", meta.get("big_version")),
    )
    lines = [f"ok {command} {summary}" if summary else f"ok {command}"]
    lines.append(
        "info "
        + _format_fact_sequence(
            ("lineup_levels", len(data.get("lineup_levels") or [])),
            ("traits", len(data.get("traits") or [])),
            ("roles", len(data.get("roles") or [])),
            ("role_tags", len(data.get("role_tags") or [])),
        )
    )
    return _append_common_success_lines(lines, payload)


def _render_generic_success(command: str, payload: dict[str, Any]) -> list[str]:
    return _append_common_success_lines([f"ok {command}"], payload)


TEXT_RENDERERS = {
    "cw.enter": _render_cw_entry,
    "cw.guide.apply": _render_cw_guide_summary,
    "cw.guide.current": _render_cw_guide_summary,
    "cw.stage.detect": _render_cw_stage,
    "cw.stage.wait": _render_cw_stage,
    "cw.slots.read": _render_cw_slots_read,
    "cw.slots.swap": _render_cw_slots,
    "cw.slots.place_one": _render_cw_slots,
    "cw.crystals.collect": _render_cw_metrics,
    "cw.hand.sell_one": _render_cw_slots,
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
    "cw.settle.next": _render_cw_stage,
    "cw.event.handle": _render_cw_event_result,
    "window.attach": _render_window_attach,
    "window.launch": _render_window_launch,
    "session.create": _render_session_create,
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


def _is_tainted_failure(payload: dict[str, Any]) -> bool:
    debug = _as_dict(payload.get("debug"))
    if debug.get("tainted") is True:
        return True

    data = _as_dict(payload.get("data"))
    if data.get("tainted") is True:
        return True

    return payload.get("ok") is False and _should_render_recover(payload)


def _render_failure_lines(command: str, payload: dict[str, Any]) -> list[str]:
    error = payload.get("error") or {}
    debug = _as_dict(payload.get("debug"))

    first_line = f"fail {command} code={_encode_value(error.get('code'))}"
    if _is_tainted_failure(payload):
        first_line += " tainted=1"

    lines = [first_line]
    request_id = debug.get("request_id")
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
        return renderer(command, payload)
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
