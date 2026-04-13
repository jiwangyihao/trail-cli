from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    @property
    def tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    def sub_region(self, from_x: int | float, from_y: int | float, to_x: int | float, to_y: int | float) -> Region:
        def _convert(value: int | float, size: int) -> int:
            if isinstance(value, float) and 0.0 <= value <= 1.0:
                return int(size * value)
            return int(value)

        left_offset = _convert(from_x, self.width)
        top_offset = _convert(from_y, self.height)
        right_offset = _convert(to_x, self.width)
        bottom_offset = _convert(to_y, self.height)

        new_left = self.left + left_offset
        new_top = self.top + top_offset
        new_width = right_offset - left_offset
        new_height = bottom_offset - top_offset
        return Region(new_left, new_top, new_width, new_height)

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True)
class Box:
    left: int
    top: int
    width: int
    height: int
    source: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "source": self.source,
        }


@dataclass(slots=True)
class WindowBinding:
    title: str
    hwnd: int | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {"title": self.title, "hwnd": self.hwnd}
