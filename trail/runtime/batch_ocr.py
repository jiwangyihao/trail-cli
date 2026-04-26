from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass, field
from math import isfinite, log, sqrt
from typing import Any

from PIL import Image
import rpack

from trail.core.errors import TrailError
from trail.runtime.ocr_config import OcrRequestConfig


Rect = dict[str, int | float]


@dataclass(frozen=True)
class BatchOcrTarget:
    key: Hashable
    image: Image.Image
    padding: int = 4
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PackedOcrTarget:
    key: Hashable
    image: Image.Image
    rect: Rect
    content_rect: Rect
    padding: int = 4
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchOcrKeyResult:
    key: Hashable
    text: str | None = None
    pieces: list[Any] = field(default_factory=list)
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchOcrResult:
    atlas: Image.Image
    packed: list[PackedOcrTarget]
    by_key: dict[Hashable, BatchOcrKeyResult]
    dropped: list[dict[str, Any]] = field(default_factory=list)


def pack_batch_ocr_targets(
    targets: Sequence[BatchOcrTarget],
    *,
    gap: int = 12,
    max_width: int | None = None,
    max_height: int | None = None,
    target_aspect: float = 16 / 9,
) -> tuple[Image.Image, list[PackedOcrTarget]]:
    normalized_targets = _validate_targets(targets)
    if gap < 0:
        raise TrailError("BATCH_OCR_INVALID_TARGET_IMAGE", "batch ocr gap must be non-negative")

    rect_sizes = [
        (target.image.width + 2 * target.padding, target.image.height + 2 * target.padding)
        for target in normalized_targets
    ]
    packing_sizes = [(width + gap, height + gap) for width, height in rect_sizes]
    positions, atlas_size = _pack_best_candidate(
        rect_sizes,
        packing_sizes,
        max_width=max_width,
        max_height=max_height,
        target_aspect=target_aspect,
    )
    atlas = Image.new("RGB", atlas_size, color="white")
    packed: list[PackedOcrTarget] = []
    for target, position, rect_size in zip(normalized_targets, positions, rect_sizes, strict=True):
        left, top = position
        rect_width, rect_height = rect_size
        content_left = left + target.padding
        content_top = top + target.padding
        atlas.paste(target.image, (content_left, content_top))
        rect = _make_rect(left, top, rect_width, rect_height)
        content_rect = _make_rect(content_left, content_top, target.image.width, target.image.height)
        packed.append(
            PackedOcrTarget(
                key=target.key,
                image=target.image,
                rect=rect,
                content_rect=content_rect,
                padding=target.padding,
                metadata=target.metadata,
            )
        )
    return atlas, packed


def run_batch_ocr(
    runtime: Any,
    targets: Sequence[BatchOcrTarget],
    *,
    gap: int = 12,
    max_width: int | None = None,
    max_height: int | None = None,
    target_aspect: float = 16 / 9,
    trace_prefix: str = "batch_ocr",
) -> BatchOcrResult:
    atlas, packed = pack_batch_ocr_targets(
        targets,
        gap=gap,
        max_width=max_width,
        max_height=max_height,
        target_aspect=target_aspect,
    )
    action = _begin_debug_action(
        runtime,
        trace_prefix,
        target_count=len(packed),
        atlas_width=atlas.width,
        atlas_height=atlas.height,
    )
    try:
        pieces = runtime.ocr_image(atlas, ocr=OcrRequestConfig(ocr_mode="high", retry_high="never")) or []
    except Exception:
        _finish_debug_action(runtime, action, ok=False, drop_count=0)
        raise

    dropped: list[dict[str, Any]] = []
    by_key = {
        item.key: BatchOcrKeyResult(key=item.key, metadata=item.metadata)
        for item in packed
    }
    grouped: dict[Hashable, list[tuple[int, float, float, str, Any, bool]]] = {item.key: [] for item in packed}

    for order, piece in enumerate(pieces):
        text = _piece_text(piece)
        if text is None or not str(text).strip():
            dropped.append({"reason": "empty_text", "idx": order})
            continue
        clean_text = str(text).strip()
        box = _piece_box(piece)
        if box is None:
            if len(packed) == 1:
                target = packed[0]
                grouped[target.key].append((order, float(order), float(order), clean_text, piece, False))
            else:
                dropped.append({"reason": "missing_box", "idx": order, "text": clean_text})
            continue
        box_rect = _box_to_rect(box)
        if box_rect is None:
            dropped.append({"reason": "missing_box", "idx": order, "text": clean_text})
            continue

        overlapping = [item for item in packed if _rects_overlap(box_rect, item.content_rect)]
        if len(overlapping) > 1:
            dropped.append({"reason": "cross_target_boundary", "idx": order, "text": clean_text})
            continue
        target = _target_for_center(packed, box_rect)
        if target is None:
            dropped.append({"reason": "outside_target", "idx": order, "text": clean_text})
            continue

        local_piece = _shift_piece(piece, box, target.content_rect["left"], target.content_rect["top"])
        grouped[target.key].append((order, float(box_rect["top"]), float(box_rect["left"]), clean_text, local_piece, True))

    for target in packed:
        entries = grouped[target.key]
        boxed_entries = [entry for entry in entries if entry[5]]
        geometryless_entries = [entry for entry in entries if not entry[5]]
        boxed_entries.sort(key=lambda entry: (entry[1], entry[2], entry[0]))
        geometryless_entries.sort(key=lambda entry: entry[0])
        ordered_entries = boxed_entries + geometryless_entries if boxed_entries else geometryless_entries
        text = "".join(entry[3] for entry in ordered_entries).strip()
        by_key[target.key] = BatchOcrKeyResult(
            key=target.key,
            text=text or None,
            pieces=[entry[4] for entry in ordered_entries],
            metadata=target.metadata,
        )

    _finish_debug_action(runtime, action, ok=True, piece_count=len(pieces), drop_count=len(dropped))
    return BatchOcrResult(atlas=atlas, packed=packed, by_key=by_key, dropped=dropped)


def _validate_targets(targets: Sequence[BatchOcrTarget]) -> list[BatchOcrTarget]:
    if not targets:
        raise TrailError("BATCH_OCR_EMPTY_TARGETS", "batch ocr requires at least one target")
    seen: set[Hashable] = set()
    normalized_targets = list(targets)
    for target in normalized_targets:
        if target.key in seen:
            raise TrailError("BATCH_OCR_DUPLICATE_KEY", f"duplicate batch ocr target key: {target.key!r}")
        seen.add(target.key)
        if not isinstance(target.image, Image.Image) or target.padding < 0 or target.image.width <= 0 or target.image.height <= 0:
            raise TrailError("BATCH_OCR_INVALID_TARGET_IMAGE", f"invalid batch ocr target image: {target.key!r}")
    return normalized_targets


def _pack_best_candidate(
    rect_sizes: list[tuple[int, int]],
    packing_sizes: list[tuple[int, int]],
    *,
    max_width: int | None,
    max_height: int | None,
    target_aspect: float,
) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    if max_width is not None and max_width <= 0:
        raise TrailError("BATCH_OCR_PACK_FAILED", "batch ocr max_width must be positive")
    if max_height is not None and max_height <= 0:
        raise TrailError("BATCH_OCR_PACK_FAILED", "batch ocr max_height must be positive")

    hard_max_height = max_height if max_height is not None else sum(height for _, height in packing_sizes)
    candidates = _candidate_widths(packing_sizes, max_width=max_width, target_aspect=target_aspect)
    best: tuple[float, list[tuple[int, int]], tuple[int, int]] | None = None
    for candidate_width in candidates:
        try:
            positions = rpack.pack(packing_sizes, max_width=candidate_width, max_height=hard_max_height)
        except rpack.PackingImpossibleError:
            continue
        atlas_size = rpack.bbox_size(packing_sizes, positions)
        if max_width is not None and atlas_size[0] > max_width:
            continue
        if max_height is not None and atlas_size[1] > max_height:
            continue
        score = _score_candidate(rect_sizes, atlas_size, target_aspect=target_aspect)
        if best is None or score < best[0]:
            best = (score, positions, atlas_size)
    if best is None:
        raise TrailError("BATCH_OCR_PACK_FAILED", "unable to pack batch ocr targets within constraints")
    return _stabilize_equal_size_positions(packing_sizes, best[1]), best[2]


def _stabilize_equal_size_positions(
    packing_sizes: list[tuple[int, int]], positions: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    adjusted = list(positions)
    by_size: dict[tuple[int, int], list[int]] = {}
    for index, size in enumerate(packing_sizes):
        by_size.setdefault(size, []).append(index)
    for indexes in by_size.values():
        if len(indexes) < 2:
            continue
        sorted_positions = sorted((positions[index] for index in indexes), key=lambda position: (position[1], position[0]))
        for index, position in zip(indexes, sorted_positions, strict=True):
            adjusted[index] = position
    return adjusted


def _candidate_widths(
    packing_sizes: list[tuple[int, int]],
    *,
    max_width: int | None,
    target_aspect: float,
) -> list[int]:
    min_width = max(width for width, _ in packing_sizes)
    total_width = sum(width for width, _ in packing_sizes)
    total_area = sum(width * height for width, height in packing_sizes)
    desired_width = int(round(sqrt(total_area * max(target_aspect, 0.1))))
    widths = {min_width, total_width, desired_width}
    for factor in (0.5, 0.66, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, 3.0):
        widths.add(int(round(desired_width * factor)))
    for columns in range(1, len(packing_sizes) + 1):
        widths.add(int(round(total_width / columns)))
    if max_width is not None:
        widths.add(max_width)
    return sorted(width for width in widths if width >= min_width and (max_width is None or width <= max_width))


def _score_candidate(
    rect_sizes: list[tuple[int, int]], atlas_size: tuple[int, int], *, target_aspect: float) -> float:
    width, height = atlas_size
    if width <= 0 or height <= 0:
        return float("inf")
    rect_area = sum(rect_width * rect_height for rect_width, rect_height in rect_sizes)
    atlas_area = width * height
    blank_ratio = max(0.0, (atlas_area - rect_area) / atlas_area)
    aspect = width / height
    aspect_penalty = abs(log(aspect / max(target_aspect, 0.1)))
    long_side_penalty = max(width, height) / max(sqrt(rect_area), 1.0)
    return blank_ratio + aspect_penalty * 1.5 + long_side_penalty * 0.05


def _make_rect(left: int | float, top: int | float, width: int | float, height: int | float) -> Rect:
    return {
        "left": _clean_number(left),
        "top": _clean_number(top),
        "width": _clean_number(width),
        "height": _clean_number(height),
        "right": _clean_number(left + width),
        "bottom": _clean_number(top + height),
    }


def _clean_number(value: int | float) -> int | float:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _piece_text(piece: Any) -> Any | None:
    if isinstance(piece, Mapping):
        return piece.get("text")
    if isinstance(piece, Sequence) and not isinstance(piece, (str, bytes)) and len(piece) >= 2:
        return piece[1]
    return getattr(piece, "text", None)


def _piece_box(piece: Any) -> Any | None:
    if isinstance(piece, Mapping):
        return piece.get("box")
    if isinstance(piece, Sequence) and not isinstance(piece, (str, bytes)) and piece:
        return piece[0]
    return getattr(piece, "box", None)


def _box_to_rect(box: Any) -> Rect | None:
    if isinstance(box, Mapping):
        return _mapped_box_to_rect(box)
    if _looks_like_points_box(box):
        points = [(float(point[0]), float(point[1])) for point in box]
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points)
        bottom = max(point[1] for point in points)
        return _valid_rect(left, top, right - left, bottom - top)
    return _object_box_to_rect(box)


def _mapped_box_to_rect(box: Mapping[str, Any]) -> Rect | None:
    left = _number_or_none(box.get("left", box.get("x")))
    top = _number_or_none(box.get("top", box.get("y")))
    width = _number_or_none(box.get("width"))
    height = _number_or_none(box.get("height"))
    if width is None and "right" in box and left is not None:
        width = _number_or_none(box.get("right")) - left if _number_or_none(box.get("right")) is not None else None
    if height is None and "bottom" in box and top is not None:
        height = _number_or_none(box.get("bottom")) - top if _number_or_none(box.get("bottom")) is not None else None
    if left is None or top is None or width is None or height is None:
        return None
    return _valid_rect(left, top, width, height)


def _object_box_to_rect(box: Any) -> Rect | None:
    left = _number_or_none(getattr(box, "left", getattr(box, "x", None)))
    top = _number_or_none(getattr(box, "top", getattr(box, "y", None)))
    width = _number_or_none(getattr(box, "width", None))
    height = _number_or_none(getattr(box, "height", None))
    right = _number_or_none(getattr(box, "right", None))
    bottom = _number_or_none(getattr(box, "bottom", None))
    if width is None and right is not None and left is not None:
        width = right - left
    if height is None and bottom is not None and top is not None:
        height = bottom - top
    if left is None or top is None or width is None or height is None:
        return None
    return _valid_rect(left, top, width, height)


def _number_or_none(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not isfinite(value):
        return None
    return value


def _valid_rect(left: float, top: float, width: float, height: float) -> Rect | None:
    if width <= 0 or height <= 0:
        return None
    return _make_rect(left, top, width, height)


def _looks_like_points_box(box: Any) -> bool:
    if not isinstance(box, Sequence) or isinstance(box, (str, bytes)) or len(box) == 0:
        return False
    for point in box:
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) < 2:
            return False
        if _number_or_none(point[0]) is None or _number_or_none(point[1]) is None:
            return False
    return True


def _rects_overlap(left: Rect, right: Rect) -> bool:
    return (
        float(left["left"]) < float(right["right"])
        and float(left["right"]) > float(right["left"])
        and float(left["top"]) < float(right["bottom"])
        and float(left["bottom"]) > float(right["top"])
    )


def _target_for_center(packed: list[PackedOcrTarget], box_rect: Rect) -> PackedOcrTarget | None:
    center_x = (float(box_rect["left"]) + float(box_rect["right"])) / 2
    center_y = (float(box_rect["top"]) + float(box_rect["bottom"])) / 2
    for target in packed:
        rect = target.content_rect
        if float(rect["left"]) <= center_x < float(rect["right"]) and float(rect["top"]) <= center_y < float(rect["bottom"]):
            return target
    return None


def _shift_piece(piece: Any, box: Any, offset_x: int | float, offset_y: int | float) -> Any:
    shifted_box = _shift_box(box, float(offset_x), float(offset_y))
    if isinstance(piece, Mapping):
        shifted_piece = dict(piece)
        shifted_piece["box"] = shifted_box
        return shifted_piece
    if isinstance(piece, tuple):
        return (shifted_box, *piece[1:])
    if isinstance(piece, list):
        return [shifted_box, *piece[1:]]
    try:
        shifted_piece = copy(piece)
        setattr(shifted_piece, "box", shifted_box)
    except Exception:
        return _normalized_shifted_piece(piece, shifted_box)
    return shifted_piece


def _shift_box(box: Any, offset_x: float, offset_y: float) -> Any:
    if isinstance(box, Mapping):
        shifted = dict(box)
        _shift_mapping_number(shifted, "left", offset_x)
        _shift_mapping_number(shifted, "top", offset_y)
        _shift_mapping_number(shifted, "x", offset_x)
        _shift_mapping_number(shifted, "y", offset_y)
        _shift_mapping_number(shifted, "right", offset_x)
        _shift_mapping_number(shifted, "bottom", offset_y)
        return shifted
    if _looks_like_points_box(box):
        return [
            (_clean_number(float(point[0]) - offset_x), _clean_number(float(point[1]) - offset_y))
            for point in box
        ]
    try:
        shifted = copy(box)
        for attr, offset in (("left", offset_x), ("x", offset_x), ("right", offset_x), ("top", offset_y), ("y", offset_y), ("bottom", offset_y)):
            if hasattr(shifted, attr):
                value = _number_or_none(getattr(shifted, attr))
                if value is not None:
                    setattr(shifted, attr, _clean_number(value - offset))
        return shifted
    except Exception:
        return _shifted_rect_mapping(box, offset_x, offset_y)


def _normalized_shifted_piece(piece: Any, shifted_box: Any) -> dict[str, Any]:
    return {"text": _piece_text(piece), "box": shifted_box}


def _shift_mapping_number(values: dict[Any, Any], key: str, offset: float) -> None:
    if key not in values:
        return
    value = _number_or_none(values[key])
    if value is None:
        return
    values[key] = _clean_number(value - offset)


def _shifted_rect_mapping(box: Any, offset_x: float, offset_y: float) -> Rect:
    rect = _box_to_rect(box)
    if rect is None:
        return _make_rect(0, 0, 0, 0)
    return _make_rect(
        float(rect["left"]) - offset_x,
        float(rect["top"]) - offset_y,
        float(rect["width"]),
        float(rect["height"]),
    )


def _begin_debug_action(runtime: Any, step: str, **payload: Any) -> Any | None:
    begin = getattr(runtime, "_begin_debug_action", None)
    if not callable(begin):
        return None
    try:
        return begin(step, **payload)
    except Exception:
        return None


def _finish_debug_action(runtime: Any, action: Any | None, *, ok: bool, **payload: Any) -> None:
    if action is None:
        return
    finish = getattr(runtime, "_finish_debug_action", None)
    try:
        if callable(finish):
            finish(action, ok=ok, **payload)
        elif hasattr(action, "finish"):
            action.finish(ok=ok, **payload)
    except Exception:
        return
