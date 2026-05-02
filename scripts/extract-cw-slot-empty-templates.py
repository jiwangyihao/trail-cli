from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

TARGET_SIZE = (103, 120)
QUADS = {
    "front:2": ((832.7, 339.7), (943.5, 339.7), (942.6, 462.9), (828.7, 462.9)),
    "hand:9": ((1390.3, 860.0), (1493.7, 860.0), (1493.7, 980.0), (1390.3, 980.0)),
}


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
    if rgba.size != (1920, 1080):
        raise SystemExit(f"expected 1920x1080 screenshot: {path}")
    return rgba


def _warp_slot(image: Image.Image, slot: str) -> Image.Image:
    if slot not in QUADS:
        raise SystemExit(f"unsupported extraction slot: {slot}")
    source = np.array(QUADS[slot], dtype=np.float32)
    target = np.array(((0, 0), (102, 0), (102, 119), (0, 119)), dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(np.array(image.convert("RGBA")), matrix, TARGET_SIZE)
    return Image.fromarray(warped, "RGBA")


def _save_checked(image: Image.Image, path: Path) -> None:
    if image.size != TARGET_SIZE or image.mode != "RGBA":
        raise SystemExit(f"invalid empty template output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field-shot", required=True, type=Path)
    parser.add_argument("--field-slot", required=True)
    parser.add_argument("--hand-shot", required=True, type=Path)
    parser.add_argument("--hand-slot", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    field = _warp_slot(_load_image(args.field_shot), args.field_slot)
    hand = _warp_slot(_load_image(args.hand_shot), args.hand_slot)
    _save_checked(field, args.output_root / "empty-field-v1.png")
    _save_checked(hand, args.output_root / "empty-hand-v1.png")


if __name__ == "__main__":
    main()
