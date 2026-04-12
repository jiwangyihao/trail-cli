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

    def sub_region(self, from_x: float, from_y: float, to_x: float, to_y: float) -> Region:
        new_left = self.left + int(self.width * from_x)
        new_top = self.top + int(self.height * from_y)
        new_width = int(self.width * (to_x - from_x))
        new_height = int(self.height * (to_y - from_y))
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
