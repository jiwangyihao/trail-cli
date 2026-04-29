from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.session.models import SessionModel
from trail.scenes.cw.equipment_grid import (
    DEFAULT_EQUIPMENT_GRID_PROFILE,
    crop_equipment_cells,
    crop_has_equipment_slot_markers,
    equipment_cell_center,
    iter_equipment_grid_cells,
)
from trail.scenes.cw.equipment_recognition import EquipmentRecognitionResult, VectorEquipmentIconRecognizer
from trail.scenes.cw.equipment_resources import (
    build_cw_equipment_catalog,
    load_cached_equipment_icons,
    prepare_equipment_icon_cache,
)
from trail.scenes.cw.guide import fetch_cw_raw_guide_config
from trail.scenes.cw.models import ensure_cw_state


def prepare_cw_equipment(*, workspace_root: str | Path | None = None, refresh: bool = False) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(raw_config)
    return prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=refresh)


def _runtime_image(runtime) -> Image.Image:
    capture_image = getattr(runtime, "capture_image", None)
    if callable(capture_image):
        try:
            image = capture_image(normalize=True)
        except TypeError:
            image = capture_image()
    else:
        screenshot = getattr(runtime, "screenshot", None)
        if not callable(screenshot):
            raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot")
        image = screenshot()

    if isinstance(image, bytes | bytearray):
        try:
            with Image.open(BytesIO(image)) as opened:
                image = opened.convert("RGBA")
        except Exception as exc:
            raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot") from exc

    if not isinstance(image, Image.Image):
        raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot")
    if image.size != (1920, 1080):
        raise TrailError("CW_EQUIPMENT_LAYOUT_MISMATCH", "equipment grid requires canonical 1920x1080 screenshot")
    return image.convert("RGBA")


def _item_from_result(crop, result: EquipmentRecognitionResult) -> dict[str, Any] | None:
    if result.empty:
        return None
    top = result.candidates[0] if result.candidates else None
    alt = result.candidates[1] if len(result.candidates) > 1 else None
    center_x, center_y = equipment_cell_center(crop.cell)
    item = {
        "idx": crop.cell.idx,
        "pos": f"equipment:{crop.cell.idx}",
        "row": crop.cell.row,
        "col": crop.cell.col,
        "center": {"x": center_x, "y": center_y},
        "name": top.name if top is not None else None,
        "equipment_id": top.equipment_id if top is not None else None,
        "cache_key": top.cache_key if top is not None else None,
        "score": result.score,
        "gap": result.gap,
        "uncertain": bool(result.uncertain),
        "alt": alt.name if alt is not None else None,
        "alt_score": alt.score if alt is not None else None,
        "candidates": [asdict(candidate) for candidate in result.candidates],
    }
    return item


def read_cw_equipment(runtime, *, workspace_root: str | Path | None = None) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(raw_config)
    prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
    recognizer = VectorEquipmentIconRecognizer(load_cached_equipment_icons(catalog, workspace_root=workspace_root))
    image = _runtime_image(runtime)
    cells = list(iter_equipment_grid_cells(DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))
    best_by_idx: dict[int, dict[str, Any]] = {}

    for crop in crop_equipment_cells(image, cells):
        if not crop_has_equipment_slot_markers(crop.image):
            continue
        item = _item_from_result(crop, recognizer.recognize(crop.image))
        if item is None:
            continue
        previous = best_by_idx.get(crop.cell.idx)
        item_score = item.get("score")
        previous_score = None if previous is None else previous.get("score")
        if previous is None or float(item_score if item_score is not None else -1.0) > float(previous_score if previous_score is not None else -1.0):
            best_by_idx[crop.cell.idx] = item

    items = [best_by_idx[idx] for idx in sorted(best_by_idx)]
    return {
        "count": len(items),
        "uncertain": sum(1 for item in items if item.get("uncertain")),
        "empty": len(cells) - len(items),
        "items": items,
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }


def apply_cw_equipment_read(
    session: SessionModel,
    runtime,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    snapshot = read_cw_equipment(runtime, workspace_root=workspace_root)
    ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
    return snapshot
