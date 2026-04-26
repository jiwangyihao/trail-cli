from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import traceback
from typing import TYPE_CHECKING
from time import sleep
from typing import Any

from trail.core.errors import TrailError
from trail.scenes.cw.catalog import CwCatalog, build_cw_catalog, resolve_cw_role_name, summarize_cw_field_traits
from trail.scenes.cw.guide import complete_cw_guide_or_none
from trail.runtime.batch_ocr import BatchOcrTarget, run_batch_ocr
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import (
    CW_STATUS_EXP_REGION,
    CW_STATUS_LEVEL_REGION,
    CW_STATUS_TEAM_SIZE_REGION,
    mark_cw_stage_status_stale,
    parse_cw_stage_status,
    parse_cw_stage_exp as _shared_parse_cw_stage_exp,
    parse_cw_stage_level as _shared_parse_cw_stage_level,
    parse_cw_stage_team_size as _shared_parse_cw_stage_team_size,
)
from trail.session.models import SessionModel

CW_WIDTH = 1920
CW_HEIGHT = 1080


def _point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
    return int(CW_WIDTH * x_ratio), int(CW_HEIGHT * y_ratio)


def _pixel_point(x: int, y: int) -> tuple[int, int]:
    return x, y


def _region(from_x: float, from_y: float, to_x: float, to_y: float) -> dict[str, int]:
    return {
        "from_x": int(CW_WIDTH * from_x),
        "from_y": int(CW_HEIGHT * from_y),
        "to_x": int(CW_WIDTH * to_x),
        "to_y": int(CW_HEIGHT * to_y),
    }


def _pixel_region(from_x: int, from_y: int, to_x: int, to_y: int) -> dict[str, int]:
    return {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}


SHOP_OPEN_POINT = _pixel_point(1628, 992)
SHOP_CLOSE_POINT = _point(0.5, 0.55)
SHOP_SCAN_RESET_POINT = SHOP_CLOSE_POINT
SHOP_SLOT_POINTS = {
    1: _point(0.25, 0.18),
    2: _point(0.40, 0.18),
    3: _point(0.55, 0.18),
    4: _point(0.68, 0.18),
    5: _point(0.80, 0.18),
}
SHOP_EXP_BUY_POINT = _pixel_point(320, 992)
SHOP_SCAN_REGION = _region(0.19, 0.26, 0.88, 0.31)
SHOP_COINS_REGION = _pixel_region(1615, 902, 1698, 956)
SHOP_SCAN_RESET_SETTLE_SECONDS = 1.0
SHOP_SCAN_OPEN_SETTLE_SECONDS = 1.5
SHOP_BUY_CONFIRM_RETRY_SECONDS = 0.5
SHOP_BUY_CONFIRM_MAX_ATTEMPTS = 4
SHOP_LEGACY_STAGE_FIELDS = {"level", "exp", "team_size", "role_count"}

ShopScanner = Callable[[], dict[str, Any]]
ShopSnapshotReader = Callable[[], dict[str, Any]]
ShopSnapshotSource = ShopScanner | ShopSnapshotReader
ShopBuyer = Callable[..., object]
ShopExpBuyer = Callable[[], object]
ShopAction = Callable[[], object]


@dataclass(frozen=True)
class CwShopApplied:
    session: SessionModel
    response_snapshot: dict[str, Any]

    @property
    def scene_state(self) -> dict[str, dict[str, Any]]:
        return self.session.scene_state

SHOP_SUMMARY_CONSTRAINT_KEYS = ("min_coins", "min_level", "mid_level")
SHOP_DEBUG_CAPTURE_ENV = "TRAIL_DEBUG_CW_SHOP_CAPTURE"
SHOP_MATCH_DIAGNOSTIC_KEYS = {"raw_name", "match_score", "score", "match_kind"}
_MISSING = object()
if TYPE_CHECKING:
    from trail.runtime.operator import RuntimeOperator


def _read_ocr_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], str | int | float):
            return str(item[1])
        for value in item:
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, str | int | float):
        return str(item)
    return ""


def _debug_capture_path(runtime: "RuntimeOperator", *, name: str) -> Path | None:
    if os.getenv(SHOP_DEBUG_CAPTURE_ENV) not in {"1", "true", "TRUE", "yes", "YES"}:
        return None
    window = getattr(runtime, "window", None)
    workspace = getattr(window, "workspace", None)
    if workspace is None:
        return None
    return Path(workspace) / f"debug-shop-{name}.png"


def _maybe_dump_shop_capture(runtime: "RuntimeOperator", *, name: str, capture: dict[str, int]) -> Path | None:
    path = _debug_capture_path(runtime, name=name)
    if path is None:
        return None
    try:
        payload = runtime.screenshot(**capture)
        path.write_bytes(payload)
        return path
    except Exception:
        fallback = path.with_suffix(".error.txt")
        fallback.write_text(traceback.format_exc(), encoding="utf-8")
        return fallback


def _parse_first_int(items: list[Any], *, default: int | None) -> int | None:
    for item in items:
        match = re.search(r"\d+", _read_ocr_text(item))
        if match is not None:
            return int(match.group())
    return default


def _parse_shop_items(raw_items: list[Any] | None) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    slot_items: dict[int, dict[str, Any]] = {}
    reserve_full = False
    slot_lane_width = _shop_scan_slot_lane_width(raw_items or [])
    for item in raw_items or []:
        text = _read_ocr_text(item).strip()
        if not text:
            continue
        if "备" in text:
            reserve_full = True
            continue
        slot = _shop_scan_slot_for_item(item, lane_width=slot_lane_width)
        if slot is not None:
            box = _read_ocr_box(item)
            bottom = box[3] if box is not None else 0.0
            entry = slot_items.setdefault(slot, {"slot": slot, "price": None, "_price_bottom": -1.0, "_names": []})
            if text.isdecimal():
                if bottom >= entry["_price_bottom"]:
                    entry["price"] = int(text)
                    entry["_price_bottom"] = bottom
                continue
            entry["_names"].append((bottom, text))
            continue
        if text.isdecimal():
            if items and items[-1]["price"] is None:
                items[-1]["price"] = int(text)
            continue
        items.append({"name": text, "price": None})
    if slot_items:
        slotted_items: list[dict[str, Any]] = []
        for slot in sorted(SHOP_SLOT_POINTS):
            entry = slot_items.get(slot)
            if entry is None:
                slotted_items.append({"slot": slot, "name": None, "price": None})
                continue
            price = entry.get("price")
            if price is None:
                slotted_items.append({"slot": slot, "name": None, "price": None})
                continue
            name_candidates = [candidate for candidate in entry.get("_names", []) if candidate[0] <= entry["_price_bottom"] + 20.0]
            if not name_candidates:
                name_candidates = entry.get("_names", [])
            name = max(name_candidates, default=(0.0, None), key=lambda candidate: candidate[0])[1]
            slotted_items.append({"slot": slot, "name": name, "price": price})
        return slotted_items, reserve_full
    return items, reserve_full


def _shop_scan_slot_lane_width(items: list[Any]) -> float | None:
    boxes = [box for item in items if (box := _read_ocr_box(item)) is not None and box[2] > 10 and box[3] > 10]
    if not boxes:
        return None
    scan_width = max(box[2] for box in boxes)
    if scan_width < 100:
        return None
    return scan_width / float(len(SHOP_SLOT_POINTS))


def _shop_scan_slot_for_item(item: Any, *, lane_width: float | None) -> int | None:
    if lane_width is None:
        return None
    box = _read_ocr_box(item)
    if box is None:
        return None
    left, top, right, bottom = box
    if right <= 10 or bottom <= 10:
        return None
    center_x = (left + right) / 2.0
    slot = int(center_x / lane_width) + 1
    return max(1, min(len(SHOP_SLOT_POINTS), slot))


def _read_ocr_box(item: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    raw_box = item[0]
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
        return None

    points: list[tuple[float, float]] = []
    for raw_point in raw_box:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            return None
        raw_x, raw_y = raw_point[0], raw_point[1]
        if not isinstance(raw_x, int | float) or not isinstance(raw_y, int | float):
            return None
        points.append((float(raw_x), float(raw_y)))

    if len(points) < 4:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _batch_ocr_items(by_key: dict[Any, Any], key: Any) -> list[Any]:
    result = by_key.get(key)
    if result is None:
        return []
    pieces = getattr(result, "pieces", None)
    if isinstance(pieces, list):
        return pieces
    if isinstance(pieces, tuple):
        return list(pieces)
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return [text]
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    return []


_parse_shop_level = _shared_parse_cw_stage_level
_parse_shop_exp = _shared_parse_cw_stage_exp
_parse_shop_team_size = _shared_parse_cw_stage_team_size


def _guide_state(cw_state: dict) -> dict | None:
    guide = cw_state.get("guide")
    if not isinstance(guide, dict):
        return None
    if complete_cw_guide_or_none(cw_state) is None:
        return None
    return guide


def _remaining_purchases(cw_state: dict) -> dict[str, Any]:
    guide = _guide_state(cw_state)
    if guide is None:
        return {}
    remaining = guide.get("remaining_purchases")
    return remaining if isinstance(remaining, dict) else {}


def _stable_constraints_summary(cw_state: dict) -> dict[str, Any]:
    constraints = cw_state.get("constraints")
    if not isinstance(constraints, dict):
        return {}
    return {key: deepcopy(constraints.get(key)) for key in SHOP_SUMMARY_CONSTRAINT_KEYS if key in constraints}


def _guide_summary(cw_state: dict) -> dict[str, Any] | None:
    if _guide_state(cw_state) is None:
        return None
    return {
        "remaining_purchases": deepcopy(_remaining_purchases(cw_state)),
        "constraints": _stable_constraints_summary(cw_state),
    }


def _sync_shop_guide_summary(cw_state: dict, shop_state: dict[str, Any], *, include_when_absent: bool) -> dict[str, Any]:
    guide_summary = _guide_summary(cw_state)
    if guide_summary is None:
        shop_state.pop("guide_summary", None)
        return shop_state
    if include_when_absent or "guide_summary" in shop_state:
        shop_state["guide_summary"] = guide_summary
    return shop_state


def _shop_state(cw_state: dict) -> dict[str, Any]:
    shop_state = cw_state.get("shop")
    return shop_state if isinstance(shop_state, dict) else {}


def _shop_catalog(guide_config: dict[str, Any] | None) -> CwCatalog | None:
    if not isinstance(guide_config, dict):
        return None
    catalog = build_cw_catalog(guide_config)
    return catalog if catalog.roles else None


def _strip_shop_match_diagnostics(item: Any) -> Any:
    if isinstance(item, dict):
        return {key: deepcopy(value) for key, value in item.items() if key not in SHOP_MATCH_DIAGNOSTIC_KEYS}
    return deepcopy(item)


def _stable_shop_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        stable: dict[str, Any] = {"name": item.get("name"), "price": item.get("price")}
        if item.get("slot") is not None:
            stable["slot"] = item.get("slot")
        if item.get("cost") is not None:
            stable["cost"] = item.get("cost")
        if item.get("role_id") is not None:
            stable["role_id"] = item.get("role_id")
        if item.get("traits") is not None:
            stable["traits"] = deepcopy(item.get("traits"))
        return _strip_shop_match_diagnostics(stable)
    if isinstance(item, str):
        return {"name": item, "price": None}
    return None


def _shop_item_from_catalog_match(item: dict[str, Any], match) -> dict[str, Any]:
    stable: dict[str, Any] = {"name": match.name}
    if item.get("slot") is not None:
        stable["slot"] = item.get("slot")
    if item.get("price") is not None or "price" in item:
        stable["price"] = item.get("price")
    if item.get("cost") is not None:
        stable["cost"] = item.get("cost")
    if match.role_id is not None:
        stable["role_id"] = match.role_id
    if match.traits:
        stable["traits"] = list(match.traits)
    return stable


def _shop_item_response_from_catalog_match(item: dict[str, Any], match) -> dict[str, Any]:
    response = _shop_item_from_catalog_match(item, match)
    if match.match_kind != "exact":
        if match.raw_name is not None:
            response["raw_name"] = match.raw_name
        response["match_score"] = match.match_score
        response["match_kind"] = match.match_kind
    return response


def _canonicalize_shop_item(
    item: Any,
    *,
    catalog: CwCatalog | None,
    idx: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    stable = _stable_shop_item(item)
    if stable is None:
        return None, None, []
    name = stable.get("name")
    if catalog is None or not isinstance(name, str) or not name.strip():
        return stable, deepcopy(stable), []
    position: dict[str, Any] = {"kind": "shop"}
    if stable.get("slot") is not None:
        position["slot"] = stable.get("slot")
    elif idx is not None:
        position["idx"] = idx
    match = resolve_cw_role_name(name, catalog, position=position)
    if match is None:
        return stable, deepcopy(stable), []
    warnings = [match.warning] if isinstance(match.warning, dict) else []
    return _shop_item_from_catalog_match(stable, match), _shop_item_response_from_catalog_match(stable, match), warnings


def _canonicalize_shop_items(items: Any, *, guide_config: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    response: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized, response, warnings
    catalog = _shop_catalog(guide_config)
    for idx, item in enumerate(items, start=1):
        stable_item, response_item, item_warnings = _canonicalize_shop_item(item, catalog=catalog, idx=idx)
        if stable_item is None or response_item is None:
            continue
        normalized.append(stable_item)
        response.append(response_item)
        warnings.extend(item_warnings)
    return normalized, response, warnings


def sanitize_cw_shop_state(cw_state: dict) -> dict[str, Any]:
    shop_state = cw_state.get("shop")
    if not isinstance(shop_state, dict):
        shop_state = {"stale": True}
        cw_state["shop"] = shop_state
        return shop_state
    for key in SHOP_LEGACY_STAGE_FIELDS:
        shop_state.pop(key, None)
    shop_state.pop("warnings", None)
    shop_state.pop("trait_summary", None)
    if "items" in shop_state:
        shop_state["items"] = _normalized_shop_items(shop_state.get("items"))
    return shop_state


def _preserved_shop_flags(cw_state: dict) -> dict[str, Any]:
    shop_state = _shop_state(cw_state)
    if "opened" not in shop_state:
        return {}
    return {"opened": shop_state["opened"]}


def _build_shop_snapshot(
    cw_state: dict,
    *,
    items: list[Any],
    coins: int | None,
    reserve_full: bool,
    stage_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = {
        **_preserved_shop_flags(cw_state),
        "items": deepcopy(items),
        "coins": coins,
        "reserve_full": reserve_full,
        "stale": False,
    }
    if stage_fields is not None:
        snapshot["level"] = stage_fields.get("level")
        snapshot["exp"] = stage_fields.get("exp")
        if "team_size" in stage_fields:
            snapshot["team_size"] = stage_fields.get("team_size")
        else:
            snapshot["team_size"] = _shop_state(cw_state).get("team_size")
    return _sync_shop_guide_summary(cw_state, snapshot, include_when_absent=True)


def _apply_scanned_shop_flags(snapshot: dict[str, Any], scanned: dict[str, Any]) -> dict[str, Any]:
    if "opened" in scanned:
        snapshot["opened"] = bool(scanned["opened"])
    if "stale" in scanned:
        snapshot["stale"] = bool(scanned["stale"])
    return snapshot


def _scan_shop_snapshot_pair(
    cw_state: dict,
    *,
    scanner: ShopSnapshotSource,
    include_stage_fields: bool = False,
    guide_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scanned = scanner()
    if isinstance(scanned, dict):
        stage_fields = scanned if include_stage_fields else None
        stable_items, response_items, warnings = _canonicalize_shop_items(scanned.get("items"), guide_config=guide_config)
        snapshot = _build_shop_snapshot(
            cw_state,
            items=stable_items,
            coins=scanned.get("coins"),
            reserve_full=bool(scanned.get("reserve_full", False)),
            stage_fields=stage_fields,
        )
        response = _build_shop_snapshot(
            cw_state,
            items=response_items,
            coins=scanned.get("coins"),
            reserve_full=bool(scanned.get("reserve_full", False)),
            stage_fields=stage_fields,
        )
        _apply_scanned_shop_flags(snapshot, scanned)
        _apply_scanned_shop_flags(response, scanned)
        if warnings:
            response["warnings"] = warnings
        return snapshot, response

    items, coins, level, reserve_full, team_size = scanned
    stage_fields = {"level": level, "team_size": team_size} if include_stage_fields else None
    stable_items, response_items, warnings = _canonicalize_shop_items(items, guide_config=guide_config)
    snapshot = _build_shop_snapshot(
        cw_state,
        items=stable_items,
        coins=coins,
        reserve_full=reserve_full,
        stage_fields=stage_fields,
    )
    response = _build_shop_snapshot(
        cw_state,
        items=response_items,
        coins=coins,
        reserve_full=reserve_full,
        stage_fields=stage_fields,
    )
    if warnings:
        response["warnings"] = warnings
    return snapshot, response


def _scan_shop_snapshot(
    cw_state: dict,
    *,
    scanner: ShopSnapshotSource,
    include_stage_fields: bool = False,
    guide_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot, _ = _scan_shop_snapshot_pair(
        cw_state,
        scanner=scanner,
        include_stage_fields=include_stage_fields,
        guide_config=guide_config,
    )
    return snapshot


def _normalized_shop_items(items: Any) -> list[dict[str, Any]]:
    normalized, _, _ = _canonicalize_shop_items(items)
    return normalized


def _shop_item_for_slot(items: list[dict[str, Any]], *, slot: int) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_slot = item.get("slot")
        if item_slot == slot or str(item_slot) == str(slot):
            return item
    index = slot - 1
    if 0 <= index < len(items):
        return items[index]
    return None


def _require_fresh_shop_item(cw_state: dict, *, slot: int, expect: str, guide_config: dict[str, Any] | None = None) -> dict[str, Any]:
    shop_state = _shop_state(cw_state)
    if shop_state.get("stale", True):
        raise TrailError("SHOP_STALE", "商店快照已失效，请先执行 trail cw shop scan")

    items, _, _ = _canonicalize_shop_items(shop_state.get("items"), guide_config=guide_config)
    current = _shop_item_for_slot(items, slot=slot)
    if current is None:
        raise TrailError("SHOP_SLOT_MISMATCH", f"shop slot {slot} expected {expect}, got: empty")

    if current.get("name") != expect:
        actual = current.get("name") or "empty"
        raise TrailError("SHOP_SLOT_MISMATCH", f"shop slot {slot} expected {expect}, got: {actual}")
    return current


def _purchase_confirmed(*, before_items: list[dict[str, Any]], after_items: list[dict[str, Any]], slot: int, expect: str) -> bool:
    before_item = _shop_item_for_slot(before_items, slot=slot)
    after_item = _shop_item_for_slot(after_items, slot=slot)
    after_name = after_item.get("name") if isinstance(after_item, dict) else None
    return before_item != after_item and after_name != expect


def _scan_until_purchase_confirmed(
    cw_state: dict,
    *,
    before_items: list[dict[str, Any]],
    slot: int,
    expect: str,
    scanner: ShopScanner,
    guide_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated_shop: dict[str, Any] | None = None
    response_shop: dict[str, Any] | None = None
    for attempt in range(SHOP_BUY_CONFIRM_MAX_ATTEMPTS):
        if attempt > 0:
            sleep(SHOP_BUY_CONFIRM_RETRY_SECONDS)
        updated_shop, response_shop = _scan_shop_snapshot_pair(cw_state, scanner=scanner, guide_config=guide_config)
        after_items = _normalized_shop_items(updated_shop.get("items"))
        if _purchase_confirmed(before_items=before_items, after_items=after_items, slot=slot, expect=expect):
            return updated_shop, response_shop
    raise TrailError("SHOP_BUY_NOT_CONFIRMED", f"shop purchase not confirmed for slot {slot}: {expect}")


def _decrement_remaining_purchase(cw_state: dict, *, expect: str) -> None:
    guide = _guide_state(cw_state)
    if guide is None:
        return
    remaining = guide.get("remaining_purchases")
    if not isinstance(remaining, dict):
        remaining = {}
        guide["remaining_purchases"] = remaining
    remaining[expect] = max(0, remaining.get(expect, 0) - 1)


def build_cw_shop_opener(runtime) -> ShopAction:
    return lambda: runtime.click_point(*SHOP_OPEN_POINT)


def _read_shop_page_snapshot(runtime, *, read_team_size: bool) -> dict[str, Any]:
    _maybe_dump_shop_capture(runtime, name="scan", capture=SHOP_SCAN_REGION)
    _maybe_dump_shop_capture(runtime, name="coins", capture=SHOP_COINS_REGION)
    targets = [
        BatchOcrTarget("items", runtime.capture_image(**SHOP_SCAN_REGION, normalize=False)),
        BatchOcrTarget("coins", runtime.capture_image(**SHOP_COINS_REGION, normalize=False)),
    ]
    if read_team_size:
        _maybe_dump_shop_capture(runtime, name="level", capture=CW_STATUS_LEVEL_REGION)
        _maybe_dump_shop_capture(runtime, name="exp", capture=CW_STATUS_EXP_REGION)
        _maybe_dump_shop_capture(runtime, name="team-size", capture=CW_STATUS_TEAM_SIZE_REGION)
        targets.extend(
            [
                BatchOcrTarget(("stage_status", "level"), runtime.capture_image(**CW_STATUS_LEVEL_REGION, normalize=False)),
                BatchOcrTarget(("stage_status", "exp"), runtime.capture_image(**CW_STATUS_EXP_REGION, normalize=False)),
                BatchOcrTarget(("stage_status", "team_size"), runtime.capture_image(**CW_STATUS_TEAM_SIZE_REGION, normalize=False)),
            ]
        )
    batch_result = run_batch_ocr(runtime, targets, trace_prefix="cw_shop_batch_ocr")
    items, reserve_full = _parse_shop_items(_batch_ocr_items(batch_result.by_key, "items"))
    coins = _parse_first_int(_batch_ocr_items(batch_result.by_key, "coins"), default=0)
    snapshot = {
        "items": items,
        "coins": coins,
        "reserve_full": reserve_full,
    }
    if read_team_size:
        stage_status = parse_cw_stage_status(batch_result.by_key)
        snapshot["level"] = stage_status.get("level")
        snapshot["exp"] = stage_status.get("exp")
        snapshot["team_size"] = stage_status.get("team_size")
    return snapshot


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> dict[str, Any]:
        return _read_shop_page_snapshot(runtime, read_team_size=False)

    return scanner


def build_cw_shop_scan_snapshot_reader(runtime, *, read_stage_status: bool = False) -> ShopSnapshotReader:
    def reader() -> dict[str, Any]:
        runtime.click_point(*SHOP_SCAN_RESET_POINT)
        sleep(SHOP_SCAN_RESET_SETTLE_SECONDS)
        runtime.click_point(*SHOP_OPEN_POINT)
        sleep(SHOP_SCAN_OPEN_SETTLE_SECONDS)
        snapshot = _read_shop_page_snapshot(runtime, read_team_size=read_stage_status)
        return {"opened": True, "stale": False, **snapshot}

    return reader


def build_cw_shop_buyer(runtime) -> ShopBuyer:
    def buyer(*, slot: int, expect: str) -> None:
        del expect
        target = SHOP_SLOT_POINTS.get(slot)
        if target is None:
            raise TrailError("SHOP_SLOT_INVALID", f"invalid shop slot: {slot}")
        runtime.click_point(*target)

    return buyer


def build_cw_shop_exp_buyer(runtime: "RuntimeOperator") -> ShopExpBuyer:
    def buyer() -> None:
        runtime.click_point(*SHOP_EXP_BUY_POINT)
        sleep(SHOP_BUY_CONFIRM_RETRY_SECONDS)

    return buyer


def build_cw_shop_refresher(runtime) -> ShopAction:
    return lambda: runtime.press_key("d")


def build_cw_shop_closer(runtime) -> ShopAction:
    return lambda: runtime.click_point(*SHOP_CLOSE_POINT)


def open_cw_shop(session: SessionModel, *, opener: ShopAction | None = None) -> SessionModel:
    if opener is not None:
        opener()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = _sync_shop_guide_summary(
        cw_state,
        {**cw_state.get("shop", {}), "opened": True, "stale": True},
        include_when_absent=False,
    )
    return session


def scan_cw_shop(
    session: SessionModel,
    *,
    scanner: ShopSnapshotSource,
    guide_config: dict[str, Any] | None = None,
) -> CwShopApplied:
    cw_state = ensure_cw_state(session)
    snapshot, response = _scan_shop_snapshot_pair(cw_state, scanner=scanner, guide_config=guide_config)
    cw_state["shop"] = snapshot
    return CwShopApplied(session=session, response_snapshot=response)


def buy_cw_shop_slot(
    session: SessionModel,
    *,
    slot: int,
    expect: str,
    buyer: ShopBuyer,
    scanner: ShopScanner,
    guide_config: dict[str, Any] | None = None,
) -> CwShopApplied:
    cw_state = ensure_cw_state(session)
    before_items, _, _ = _canonicalize_shop_items(_shop_state(cw_state).get("items"), guide_config=guide_config)
    _require_fresh_shop_item(cw_state, slot=slot, expect=expect, guide_config=guide_config)
    buyer(slot=slot, expect=expect)
    updated_shop, response_shop = _scan_until_purchase_confirmed(
        cw_state,
        before_items=before_items,
        slot=slot,
        expect=expect,
        scanner=scanner,
        guide_config=guide_config,
    )
    _decrement_remaining_purchase(cw_state, expect=expect)
    cw_state["shop"] = _sync_shop_guide_summary(cw_state, updated_shop, include_when_absent=True)
    response_shop = _sync_shop_guide_summary(cw_state, response_shop, include_when_absent=True)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    cw_state["sell_plan"] = {}
    mark_cw_stage_status_stale(session)
    return CwShopApplied(session=session, response_snapshot=response_shop)


def buy_cw_shop_exp(
    session: SessionModel,
    *,
    buyer: ShopExpBuyer,
    scanner: ShopSnapshotSource,
    guide_config: dict[str, Any] | None = None,
) -> CwShopApplied:
    buyer()
    cw_state = ensure_cw_state(session)
    snapshot, response = _scan_shop_snapshot_pair(
        cw_state,
        scanner=scanner,
        include_stage_fields=True,
        guide_config=guide_config,
    )
    cw_state["shop"] = snapshot
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    cw_state["sell_plan"] = {}
    mark_cw_stage_status_stale(session)
    return CwShopApplied(session=session, response_snapshot=response)


def refresh_cw_shop(session: SessionModel, *, refresher: ShopAction | None = None) -> SessionModel:
    if refresher is not None:
        refresher()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = _sync_shop_guide_summary(
        cw_state,
        {**cw_state.get("shop", {}), "stale": True},
        include_when_absent=False,
    )
    return session


def close_cw_shop(session: SessionModel, *, closer: ShopAction | None = None) -> SessionModel:
    if closer is not None:
        closer()
    cw_state = ensure_cw_state(session)
    cw_state["shop"] = _sync_shop_guide_summary(
        cw_state,
        {**cw_state.get("shop", {}), "opened": False, "stale": True},
        include_when_absent=False,
    )
    return session


def _fresh_cw_field_slots(cw_state: dict) -> tuple[list[Any], list[Any]] | None:
    slots = cw_state.get("slots")
    if not isinstance(slots, dict) or slots.get("stale", True) is not False:
        return None
    front = slots.get("front") if isinstance(slots.get("front"), list) else []
    back = slots.get("back") if isinstance(slots.get("back"), list) else []
    return front, back


def _field_trait_summary(cw_state: dict, *, guide_config: dict[str, Any] | None) -> list[dict[str, Any]]:
    field_slots = _fresh_cw_field_slots(cw_state)
    if field_slots is None or not isinstance(guide_config, dict):
        return []
    front, back = field_slots
    return summarize_cw_field_traits(front=front, back=back, catalog=build_cw_catalog(guide_config))


def _without_legacy_shop_stage_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(snapshot)
    for key in SHOP_LEGACY_STAGE_FIELDS:
        payload.pop(key, None)
    return payload


def project_cw_shop_snapshot(
    session: SessionModel,
    guide_config: dict[str, Any] | None = None,
    *,
    include_field_trait_summary: bool = False,
    shop_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cw_state = ensure_cw_state(session)
    payload = (
        _without_legacy_shop_stage_fields(shop_snapshot)
        if isinstance(shop_snapshot, dict)
        else deepcopy(sanitize_cw_shop_state(cw_state))
    )
    stage = cw_state.get("stage")
    status = stage.get("status") if isinstance(stage, dict) else None
    if isinstance(status, dict):
        payload["stage_status"] = deepcopy(status)
        payload["stage_status_stale"] = bool(status.get("stale", True))
    else:
        payload["stage_status_stale"] = True
    if include_field_trait_summary:
        trait_summary = _field_trait_summary(cw_state, guide_config=guide_config)
        if trait_summary:
            payload["trait_summary"] = deepcopy(trait_summary)
    return payload


def shop_cw_status(
    session: SessionModel,
    guide_config: dict[str, Any] | None = None,
    *,
    include_field_trait_summary: bool = False,
) -> dict:
    cw_state = ensure_cw_state(session)
    status = project_cw_shop_snapshot(
        session,
        guide_config=guide_config,
        include_field_trait_summary=include_field_trait_summary,
    )
    guide_summary = _guide_summary(cw_state)
    if guide_summary is not None:
        status["guide_summary"] = guide_summary
    else:
        status.pop("guide_summary", None)
    return status
