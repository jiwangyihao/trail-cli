from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from PIL import Image


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
    columns: int = 3,
    rows: int = 6,
) -> Iterable[EquipmentGridCell]:
    idx = 1
    for row in range(1, rows + 1):
        for col in range(1, columns + 1):
            x, y, box = _box_for(profile, col=col, row=row)
            yield EquipmentGridCell(idx=idx, row=row, col=col, x=x, y=y, box=box)
            idx += 1


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
