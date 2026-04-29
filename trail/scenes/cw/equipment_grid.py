from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from PIL import Image, ImageStat

from trail.core.errors import TrailError


@dataclass(frozen=True)
class EquipmentGridProfile:
    flow: str = "right_to_left_then_top_to_bottom"
    tracked_top_slot_count: int = 2
    tracked_top_slots_excluded: bool = True
    crop_width: int = 70
    crop_height: int = 70
    col1_left: float = 1820.0
    col_step: float = 79.75
    row_origin_top: float = 240.0
    row_step: float = 77.5


@dataclass(frozen=True)
class EquipmentGridCell:
    idx: int
    row: int
    col: int
    x: float
    y: int
    box: dict[str, int]


@dataclass(frozen=True)
class EquipmentCrop:
    cell: EquipmentGridCell
    image: Image.Image
    variant: str


DEFAULT_EQUIPMENT_GRID_PROFILE = EquipmentGridProfile()
DARK_CORNER_SIZE = 12
DARK_CORNER_LUMA_THRESHOLD = 55.0
MIN_DARK_CORNERS = 2
FRAME_BAND_WIDTH = 6
FRAME_INSET = 8
LIGHT_FRAME_LUMA_THRESHOLD = 120.0
LIGHT_FRAME_MIN_RATIO = 0.01
MIN_LIGHT_FRAME_EDGES = 2


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _box_for(
    profile: EquipmentGridProfile, *, col: int, row: int
) -> tuple[float, int, dict[str, int]]:
    x = profile.col1_left - (col - 1) * profile.col_step
    y = round_half_up(profile.row_origin_top + (row - 1) * profile.row_step)
    box = {"left": int(x), "top": y, "width": profile.crop_width, "height": profile.crop_height}
    return x, y, box


def iter_equipment_grid_cells(
    profile: EquipmentGridProfile = DEFAULT_EQUIPMENT_GRID_PROFILE,
    *,
    columns: int = 10,
    rows: int = 6,
) -> Iterable[EquipmentGridCell]:
    idx = 1
    for col in range(1, columns + 1):
        for row in range(1, rows + 1):
            x, y, box = _box_for(profile, col=col, row=row)
            yield EquipmentGridCell(idx=idx, row=row, col=col, x=x, y=y, box=box)
            idx += 1


def equipment_cell_center(cell: EquipmentGridCell) -> tuple[int, int]:
    return (
        cell.box["left"] + cell.box["width"] // 2,
        cell.box["top"] + cell.box["height"] // 2,
    )


def equipment_slot_center(
    value: str,
    *,
    profile: EquipmentGridProfile = DEFAULT_EQUIPMENT_GRID_PROFILE,
    columns: int = 10,
    rows: int = 6,
) -> tuple[int, int]:
    prefix, separator, raw_idx = str(value).partition(":")
    if prefix != "equipment" or separator != ":" or not raw_idx.isdecimal():
        raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")

    idx = int(raw_idx)
    if idx < 1 or idx > columns * rows:
        raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")

    for cell in iter_equipment_grid_cells(profile, columns=columns, rows=rows):
        if cell.idx == idx:
            return equipment_cell_center(cell)

    raise TrailError("CW_EQUIPMENT_SLOT_INVALID", f"invalid equipment slot: {value}")


def _luma_mean(image: Image.Image) -> float:
    red, green, blue = ImageStat.Stat(image.convert("RGB")).mean
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _light_ratio(image: Image.Image, threshold: float) -> float:
    rgb = image.convert("RGB")
    get_flattened_data = getattr(rgb, "get_flattened_data", None)
    pixels = get_flattened_data() if callable(get_flattened_data) else rgb.getdata()
    total = 0
    light = 0
    for red, green, blue in pixels:
        total += 1
        luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if luma >= threshold:
            light += 1
    return 0.0 if total == 0 else light / total


def _corner_boxes(width: int, height: int, size: int) -> list[tuple[int, int, int, int]]:
    corner_width = min(size, width)
    corner_height = min(size, height)
    return [
        (0, 0, corner_width, corner_height),
        (width - corner_width, 0, width, corner_height),
        (0, height - corner_height, corner_width, height),
        (width - corner_width, height - corner_height, width, height),
    ]


def _edge_band_boxes(width: int, height: int, band_width: int) -> list[tuple[int, int, int, int]]:
    band_x = min(band_width, width)
    band_y = min(band_width, height)
    boxes = [
        (0, 0, width, band_y),
        (0, height - band_y, width, height),
        (0, 0, band_x, height),
        (width - band_x, 0, width, height),
    ]
    if width > FRAME_INSET * 2 + band_x and height > FRAME_INSET * 2 + band_y:
        boxes.extend(
            [
                (FRAME_INSET, FRAME_INSET, width - FRAME_INSET, FRAME_INSET + band_y),
                (FRAME_INSET, height - FRAME_INSET - band_y, width - FRAME_INSET, height - FRAME_INSET),
                (FRAME_INSET, FRAME_INSET, FRAME_INSET + band_x, height - FRAME_INSET),
                (width - FRAME_INSET - band_x, FRAME_INSET, width - FRAME_INSET, height - FRAME_INSET),
            ]
        )
    return boxes


def crop_has_equipment_slot_markers(image: Image.Image) -> bool:
    rgb = image.convert("RGB")
    width, height = rgb.size
    dark_corners = sum(
        1
        for box in _corner_boxes(width, height, DARK_CORNER_SIZE)
        if _luma_mean(rgb.crop(box)) <= DARK_CORNER_LUMA_THRESHOLD
    )
    if dark_corners < MIN_DARK_CORNERS:
        return False

    light_edges = sum(
        1
        for box in _edge_band_boxes(width, height, FRAME_BAND_WIDTH)
        if _light_ratio(rgb.crop(box), LIGHT_FRAME_LUMA_THRESHOLD) >= LIGHT_FRAME_MIN_RATIO
    )
    return light_edges >= MIN_LIGHT_FRAME_EDGES


def _crop_exact(source: Image.Image, *, left: int, top: int, width: int, height: int) -> Image.Image:
    crop = source.crop((left, top, left + width, top + height))
    return crop.resize((width, height), Image.Resampling.LANCZOS)


def crop_equipment_cells(source: Image.Image, cells: Iterable[EquipmentGridCell]) -> list[EquipmentCrop]:
    crops: list[EquipmentCrop] = []
    for cell in cells:
        left = cell.x
        top = cell.y
        width = cell.box["width"]
        height = cell.box["height"]
        if float(left).is_integer():
            crops.append(
                EquipmentCrop(
                    cell=cell,
                    image=_crop_exact(source, left=int(left), top=top, width=width, height=height),
                    variant="exact",
                )
            )
            continue
        floor_left = int(left)
        ceil_left = floor_left + 1
        crops.append(
            EquipmentCrop(
                cell=cell,
                image=_crop_exact(source, left=floor_left, top=top, width=width, height=height),
                variant="floor",
            )
        )
        crops.append(
            EquipmentCrop(
                cell=cell,
                image=_crop_exact(source, left=ceil_left, top=top, width=width, height=height),
                variant="ceil",
            )
        )
    return crops
