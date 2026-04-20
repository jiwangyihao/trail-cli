from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("box must use left,top,width,height")

    try:
        left, top, width, height = (int(part) for part in parts)
    except ValueError as error:
        raise ValueError("box values must be integers") from error

    if width <= 0 or height <= 0:
        raise ValueError("box width and height must be positive")

    return left, top, width, height


def _mosaic_region(image: Image.Image, box: tuple[int, int, int, int], *, cell_size: int) -> None:
    left, top, width, height = box
    region = image.crop((left, top, left + width, top + height))
    reduced_width = max(1, region.width // cell_size)
    reduced_height = max(1, region.height // cell_size)
    mosaic = region.resize((reduced_width, reduced_height), Image.Resampling.NEAREST)
    mosaic = mosaic.resize(region.size, Image.Resampling.NEAREST)
    image.paste(mosaic, (left, top, left + width, top + height))


def sanitize_image(
    source: Path,
    destination: Path,
    *,
    boxes: list[tuple[int, int, int, int]],
    cell_size: int = 10,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    image = Image.open(source).convert("RGB")
    for box in boxes:
        _mosaic_region(image, box, cell_size=cell_size)

    image.save(destination, quality=90, optimize=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitize stage reference screenshots with mosaic boxes.")
    parser.add_argument("source", type=Path, help="Input image path")
    parser.add_argument("--box", dest="boxes", action="append", required=True, help="Box as left,top,width,height")
    parser.add_argument("--output", type=Path, help="Optional output path; defaults to in-place")
    parser.add_argument("--cell-size", type=int, default=10, help="Mosaic cell size")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    output = args.output or args.source
    boxes = [parse_box(value) for value in args.boxes]
    sanitize_image(args.source, output, boxes=boxes, cell_size=args.cell_size)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
