from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "cw-stage-reference"
    / "sanitize_stage_reference.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("sanitize_stage_reference", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_box_rejects_invalid_shape():
    module = _load_module()

    try:
        module.parse_box("1,2,3")
    except ValueError as error:
        assert "left,top,width,height" in str(error)
    else:
        raise AssertionError("parse_box should reject invalid box values")


def test_sanitize_image_applies_mosaic_and_preserves_dimensions(tmp_path):
    module = _load_module()
    src = tmp_path / "source.jpg"
    dst = tmp_path / "sanitized.jpg"

    image = Image.new("RGB", (200, 120), color=(10, 20, 30))
    for x in range(40, 100):
        for y in range(70, 95):
            image.putpixel((x, y), ((x * 3) % 255, (y * 5) % 255, ((x + y) * 7) % 255))
    image.save(src, quality=95)

    module.sanitize_image(src, dst, boxes=[(40, 70, 60, 25)], cell_size=10)

    original = Image.open(src).convert("RGB")
    sanitized = Image.open(dst).convert("RGB")

    assert sanitized.size == original.size
    assert sanitized.getpixel((20, 20)) == original.getpixel((20, 20))
    assert sanitized.getpixel((50, 80)) != original.getpixel((50, 80))
