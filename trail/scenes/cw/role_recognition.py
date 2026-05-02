from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageStat

from trail.core.errors import TrailError
from trail.scenes.cw.role_resources import RoleCatalogEntry
from trail.scenes.cw.static_resources import (
    CW_ROLE_FEATURE_SCHEMA_VERSION,
    CW_ROLE_RECOGNIZER_ALGORITHM_VERSION,
    CW_SLOT_EMPTY_TEMPLATE_VERSION,
    CW_SLOT_GEOMETRY_VERSION,
)


SLOT_TARGET_SIZE = (103, 120)
CANONICAL_SCREENSHOT_SIZE = (1920, 1080)
ROLE_FEATURE_SIZE = (64, 64)
ROLE_AVATAR_ROI = (5, 4, 98, 108)
ROLE_HIST_BINS = (16, 16, 16)
STAR_ROI = (10, 82, 91, 114)
FEE_STRIP = (4, 112, 99, 119)
STAR_SCALES = (0.65, 0.75, 0.80)
STAR_THRESHOLD = 0.78
STAR_NMS_RADIUS = 8
STAR_MATCH_METHOD = "TM_CCOEFF_NORMED"
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.58
DEFAULT_LOW_SCORE = 0.50
DEFAULT_MIN_GAP = 0.035
DEFAULT_EMPTY_MIN_SCORE = 0.82
DEFAULT_EMPTY_MIN_GAP = 0.08


@dataclass(frozen=True)
class SlotSpec:
    area: str
    index: int
    quad: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class RoleSimilarityParts:
    masked_ncc: float
    masked_l1: float
    hist_corr: float
    score: float


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_id: str | None
    score: float
    rarity: str | None = None
    cost: str | None = None
    normalized_name: str | None = None
    front_back_type: str | None = None
    trait_ids: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleConfidence:
    match_kind: str
    confidence_reason: str


@dataclass(frozen=True)
class RoleRecognitionResult:
    name: str | None
    role_id: str | None
    candidates: list[RoleCandidate]
    score: float | None
    empty: bool
    star_count: int
    star_boxes: list[dict[str, Any]]
    fee_color: str
    match_kind: str
    confidence_reason: str
    rarity: str | None = None
    cost: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ArrayFeature:
    rgb: np.ndarray
    rgb_float: np.ndarray
    gray_float: np.ndarray
    hsv: np.ndarray
    mask_bool: np.ndarray


@dataclass(frozen=True)
class _IndexedRole:
    role_id: str | None
    name: str
    normalized_name: str | None
    rarity: str | None
    cost: str | None
    front_back_type: str | None
    trait_ids: list[str]
    icon_rgba: Image.Image
    icon_mask: Image.Image
    histogram: list[float]
    feature: _ArrayFeature
    fee_color: str


_SLOT_SPECS: tuple[SlotSpec, ...] = (
    SlotSpec("front", 0, ((690.2, 339.7), (801.2, 339.7), (794.6, 462.9), (680.5, 462.9))),
    SlotSpec("front", 1, ((832.7, 339.7), (943.5, 339.7), (942.6, 462.9), (828.7, 462.9))),
    SlotSpec("front", 2, ((975.3, 339.7), (1085.9, 339.7), (1090.7, 462.9), (976.8, 462.9))),
    SlotSpec("front", 3, ((1117.8, 339.7), (1228.2, 339.7), (1238.7, 462.9), (1125.0, 462.9))),
    SlotSpec("back", 0, ((552.1, 611.7), (664.0, 611.7), (654.2, 737.6), (536.9, 737.6))),
    SlotSpec("back", 1, ((692.7, 611.7), (805.1, 611.7), (799.9, 737.6), (682.5, 737.6))),
    SlotSpec("back", 2, ((833.2, 611.7), (946.2, 611.7), (945.6, 737.6), (828.1, 737.6))),
    SlotSpec("back", 3, ((973.7, 611.7), (1087.4, 611.7), (1091.4, 737.6), (973.8, 737.6))),
    SlotSpec("back", 4, ((1114.2, 611.7), (1228.5, 611.7), (1237.1, 737.6), (1119.4, 737.6))),
    SlotSpec("back", 5, ((1254.7, 611.7), (1369.6, 611.7), (1382.9, 737.6), (1265.0, 737.6))),
    SlotSpec("hand", 0, ((381.3, 860.0), (484.7, 860.0), (484.7, 980.0), (381.3, 980.0))),
    SlotSpec("hand", 1, ((507.5, 860.0), (610.9, 860.0), (610.9, 980.0), (507.5, 980.0))),
    SlotSpec("hand", 2, ((633.6, 860.0), (737.0, 860.0), (737.0, 980.0), (633.6, 980.0))),
    SlotSpec("hand", 3, ((759.7, 860.0), (863.1, 860.0), (863.1, 980.0), (759.7, 980.0))),
    SlotSpec("hand", 4, ((885.8, 860.0), (989.2, 860.0), (989.2, 980.0), (885.8, 980.0))),
    SlotSpec("hand", 5, ((1012.0, 860.0), (1115.4, 860.0), (1115.4, 980.0), (1012.0, 980.0))),
    SlotSpec("hand", 6, ((1138.1, 860.0), (1241.5, 860.0), (1241.5, 980.0), (1138.1, 980.0))),
    SlotSpec("hand", 7, ((1264.2, 860.0), (1367.6, 860.0), (1367.6, 980.0), (1264.2, 980.0))),
    SlotSpec("hand", 8, ((1390.3, 860.0), (1493.7, 860.0), (1493.7, 980.0), (1390.3, 980.0))),
)


def iter_slot_specs() -> Iterable[SlotSpec]:
    return iter(_SLOT_SPECS)


def _clamp_score(value: float) -> float:
    if abs(value - 1.0) < 1e-9:
        return 1.0
    if abs(value) < 1e-9:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _pixel_payload(image: Image.Image, mode: str) -> dict[str, Any]:
    normalized = image.convert(mode)
    return {"mode": mode, "size": list(normalized.size), "data": list(normalized.tobytes())}


def _image_payload_size(payload: dict[str, Any]) -> tuple[int, int]:
    size = payload.get("size") if isinstance(payload, dict) else None
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError("image payload size must be a two-item list")
    width, height = size
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("image payload size must contain positive integers")
    return width, height


def _image_from_pixel_payload(payload: dict[str, Any], mode: str, channels: int) -> Image.Image:
    if not isinstance(payload, dict) or payload.get("mode") != mode:
        raise ValueError(f"image payload mode must be {mode}")
    width, height = _image_payload_size(payload)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("image payload data must be a list")
    try:
        raw = bytes(data)
    except (TypeError, ValueError) as exc:
        raise ValueError("image payload data must contain bytes") from exc
    if len(raw) != width * height * channels:
        raise ValueError("image payload data length does not match size")
    return Image.frombytes(mode, (width, height), raw)


def _expected_image_from_payload(
    payload: dict[str, Any],
    key: str,
    mode: str,
    channels: int,
    size: tuple[int, int],
) -> Image.Image:
    try:
        image = _image_from_pixel_payload(payload.get(key), mode, channels)
    except ValueError as exc:
        raise ValueError(f"{key} payload invalid: {exc}") from exc
    if image.size != size:
        raise ValueError(f"{key} payload size must be {list(size)}")
    return image


def _finite_float_from_payload(payload: dict[str, Any], key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a finite float") from exc
    if not math.isfinite(value):
        raise ValueError(f"{key} must be a finite float")
    return value


def _flatten_rgba_on_black(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    background.alpha_composite(rgba)
    return background


def _normalized_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return _flatten_rgba_on_black(image).resize(size, Image.Resampling.LANCZOS)


def _alpha_mask(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("RGBA").getchannel("A").resize(size, Image.Resampling.LANCZOS)


def _image_data(image: Image.Image):
    get_flattened_data = getattr(image, "get_flattened_data", None)
    return get_flattened_data() if callable(get_flattened_data) else image.getdata()


def role_card_base_mask(star_boxes: Iterable[Mapping[str, Any]] | None) -> Image.Image:
    mask = Image.new("L", SLOT_TARGET_SIZE, 0)
    pixels = mask.load()
    for y in range(4, 108):
        for x in range(5, 98):
            pixels[x, y] = 255
    for y in range(0, 34):
        for x in range(0, 30):
            pixels[x, y] = 0
        for x in range(76, 103):
            pixels[x, y] = 0
    for y in range(112, 120):
        for x in range(0, 103):
            pixels[x, y] = 0
    for box in star_boxes or []:
        try:
            left = int(box["x"]) - 2
            top = int(box["y"]) - 2
            right = int(box["x"]) + int(box["w"]) + 2
            bottom = int(box["y"]) + int(box["h"]) + 2
        except (KeyError, TypeError, ValueError):
            continue
        for y in range(max(0, top), min(SLOT_TARGET_SIZE[1], bottom)):
            for x in range(max(0, left), min(SLOT_TARGET_SIZE[0], right)):
                pixels[x, y] = 0
    return mask


def fee_color_for_tier(value: object) -> str:
    tier = str(value).strip() if value is not None else ""
    return {
        "1": "gray",
        "2": "green",
        "3": "blue",
        "4": "purple",
        "5": "gold",
    }.get(tier, "unknown")


def fee_color_from_crop(crop: Image.Image) -> str:
    strip = crop.convert("RGB").crop(FEE_STRIP)
    rgb = np.array(strip, dtype=np.uint8)
    if rgb.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mean_hue = float(np.mean(hsv[:, :, 0]))
    mean_saturation = float(np.mean(hsv[:, :, 1]))
    if mean_saturation < 28:
        return "gray"
    if 75 <= mean_hue <= 98:
        return "green"
    if 105 <= mean_hue <= 122:
        return "blue"
    if 123 <= mean_hue <= 137:
        return "purple"
    return "unknown"


def _hsv_histogram_from_image(image: Image.Image, mask: Image.Image | None = None) -> list[float]:
    rgb = np.array(image.convert("RGB"), dtype=np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask_array = None if mask is None else np.array(mask.convert("L"), dtype=np.uint8)
    hist = cv2.calcHist(
        [hsv],
        [0, 1, 2],
        mask_array,
        list(ROLE_HIST_BINS),
        [0, 180, 0, 256, 0, 256],
    )
    total = float(hist.sum())
    if total <= 0.0:
        return [0.0] * (ROLE_HIST_BINS[0] * ROLE_HIST_BINS[1] * ROLE_HIST_BINS[2])
    return [float(value) for value in (hist / total).reshape(-1)]


def _array_feature(image: Image.Image, mask: Image.Image) -> _ArrayFeature:
    rgb = np.array(image.convert("RGB"), dtype=np.uint8)
    rgb_float = rgb.astype(np.float32)
    gray_float = 0.299 * rgb_float[:, :, 0] + 0.587 * rgb_float[:, :, 1] + 0.114 * rgb_float[:, :, 2]
    return _ArrayFeature(
        rgb=rgb,
        rgb_float=rgb_float,
        gray_float=gray_float,
        hsv=cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV),
        mask_bool=np.array(mask.convert("L"), dtype=np.uint8) > 8,
    )


def _hsv_histogram_from_arrays(hsv: np.ndarray, mask_bool: np.ndarray) -> np.ndarray:
    mask = np.where(mask_bool, 255, 0).astype(np.uint8)
    hist = cv2.calcHist(
        [hsv],
        [0, 1, 2],
        mask,
        list(ROLE_HIST_BINS),
        [0, 180, 0, 256, 0, 256],
    )
    total = float(hist.sum())
    if total <= 0.0:
        return np.zeros(ROLE_HIST_BINS[0] * ROLE_HIST_BINS[1] * ROLE_HIST_BINS[2], dtype=np.float32)
    return (hist / total).reshape(-1).astype(np.float32)


def _hist_intersection(left: list[float], right: list[float]) -> float:
    return _clamp_score(sum(min(float(a), float(b)) for a, b in zip(left, right)))


def _hist_intersection_arrays(left: np.ndarray, right: np.ndarray) -> float:
    return _clamp_score(float(np.minimum(left, right).sum()))


def _masked_mean_abs_similarity(left: Image.Image, right: Image.Image, mask: Image.Image) -> float:
    left_rgb = left.convert("RGB")
    right_rgb = right.convert("RGB")
    mask_l = mask.convert("L")
    mask_array = np.array(mask_l, dtype=np.uint8)
    valid = mask_array > 8
    if not np.any(valid):
        diff = ImageChops.difference(left_rgb, right_rgb)
        channel_means = ImageStat.Stat(diff).mean
        return _clamp_score(1.0 - (sum(channel_means) / len(channel_means)) / 255.0)
    left_array = np.array(left_rgb, dtype=np.float32)
    right_array = np.array(right_rgb, dtype=np.float32)
    mean_abs = float(np.mean(np.abs(left_array[valid] - right_array[valid])))
    return _clamp_score(1.0 - mean_abs / 255.0)


def _masked_ncc(left: Image.Image, right: Image.Image, mask: Image.Image) -> float:
    left_rgb = np.array(left.convert("RGB"), dtype=np.float32)
    right_rgb = np.array(right.convert("RGB"), dtype=np.float32)
    weights = np.array(mask.convert("L"), dtype=np.float32) / 255.0
    valid = weights > (8.0 / 255.0)
    if not np.any(valid):
        return 0.0
    left_gray = 0.299 * left_rgb[:, :, 0] + 0.587 * left_rgb[:, :, 1] + 0.114 * left_rgb[:, :, 2]
    right_gray = 0.299 * right_rgb[:, :, 0] + 0.587 * right_rgb[:, :, 1] + 0.114 * right_rgb[:, :, 2]
    w = weights[valid]
    left_values = left_gray[valid]
    right_values = right_gray[valid]
    weight_total = float(w.sum())
    if weight_total <= 0.0:
        return 0.0
    left_mean = float((w * left_values).sum() / weight_total)
    right_mean = float((w * right_values).sum() / weight_total)
    left_centered = left_values - left_mean
    right_centered = right_values - right_mean
    numerator = float((w * left_centered * right_centered).sum())
    left_denom = float((w * left_centered * left_centered).sum())
    right_denom = float((w * right_centered * right_centered).sum())
    denominator = math.sqrt(left_denom * right_denom)
    if denominator <= 0.0:
        return 0.0
    return _clamp_score((numerator / denominator + 1.0) / 2.0)


def _masked_ncc_arrays(left_gray: np.ndarray, right_gray: np.ndarray, valid: np.ndarray) -> float:
    if not np.any(valid):
        return 0.0
    left_values = left_gray[valid]
    right_values = right_gray[valid]
    left_centered = left_values - float(left_values.mean())
    right_centered = right_values - float(right_values.mean())
    numerator = float(np.dot(left_centered, right_centered))
    denominator = math.sqrt(float(np.dot(left_centered, left_centered)) * float(np.dot(right_centered, right_centered)))
    if denominator <= 0.0:
        return 0.0
    return _clamp_score((numerator / denominator + 1.0) / 2.0)


def _masked_mean_abs_similarity_arrays(left_rgb: np.ndarray, right_rgb: np.ndarray, valid: np.ndarray) -> float:
    if not np.any(valid):
        return 0.0
    mean_abs = float(np.mean(np.abs(left_rgb[valid] - right_rgb[valid])))
    return _clamp_score(1.0 - mean_abs / 255.0)


def _role_similarity_parts_fast(query: _ArrayFeature, role: _ArrayFeature) -> RoleSimilarityParts:
    final_mask = query.mask_bool & role.mask_bool
    if int(np.count_nonzero(final_mask)) < 64:
        return RoleSimilarityParts(masked_ncc=0.0, masked_l1=0.0, hist_corr=0.0, score=0.0)
    masked_ncc = _masked_ncc_arrays(query.gray_float, role.gray_float, final_mask)
    masked_l1 = _masked_mean_abs_similarity_arrays(query.rgb_float, role.rgb_float, final_mask)
    hist_corr = _hist_intersection_arrays(
        _hsv_histogram_from_arrays(query.hsv, final_mask),
        _hsv_histogram_from_arrays(role.hsv, final_mask),
    )
    return RoleSimilarityParts(
        masked_ncc=masked_ncc,
        masked_l1=masked_l1,
        hist_corr=hist_corr,
        score=_clamp_score(0.50 * masked_ncc + 0.30 * masked_l1 + 0.20 * hist_corr),
    )


def role_similarity_parts(left: Image.Image, right: Image.Image, mask: Image.Image) -> RoleSimilarityParts:
    valid_pixels = int(np.count_nonzero(np.array(mask.convert("L"), dtype=np.uint8) > 8))
    if valid_pixels < 64:
        return RoleSimilarityParts(masked_ncc=0.0, masked_l1=0.0, hist_corr=0.0, score=0.0)
    masked_ncc = _masked_ncc(left, right, mask)
    masked_l1 = _masked_mean_abs_similarity(left, right, mask)
    hist_corr = _hist_intersection(
        _hsv_histogram_from_image(left, mask),
        _hsv_histogram_from_image(right, mask),
    )
    return RoleSimilarityParts(
        masked_ncc=masked_ncc,
        masked_l1=masked_l1,
        hist_corr=hist_corr,
        score=_clamp_score(0.50 * masked_ncc + 0.30 * masked_l1 + 0.20 * hist_corr),
    )


def _card_feature(crop: Image.Image, star_boxes: Iterable[Mapping[str, Any]] | None) -> tuple[Image.Image, Image.Image]:
    mask = role_card_base_mask(star_boxes).crop(ROLE_AVATAR_ROI)
    image = _flatten_rgba_on_black(crop).crop(ROLE_AVATAR_ROI)
    return (
        image.resize(ROLE_FEATURE_SIZE, Image.Resampling.LANCZOS),
        mask.resize(ROLE_FEATURE_SIZE, Image.Resampling.NEAREST),
    )


def _combined_mask(left: Image.Image, right: Image.Image) -> Image.Image:
    left_valid = np.array(left.convert("L"), dtype=np.uint8) > 8
    right_valid = np.array(right.convert("L"), dtype=np.uint8) > 8
    return Image.fromarray(np.where(left_valid & right_valid, 255, 0).astype(np.uint8), "L")


def empty_template_score(
    crop: Image.Image,
    empty_template: Image.Image,
    star_boxes: Iterable[Mapping[str, Any]] | None,
) -> float:
    query_image, query_mask = _card_feature(crop, star_boxes)
    template_image, _ = _card_feature(empty_template, star_boxes)
    mean_abs = _masked_mean_abs_similarity(query_image, template_image, query_mask)
    hist_corr = _hist_intersection(
        _hsv_histogram_from_image(query_image, query_mask),
        _hsv_histogram_from_image(template_image, query_mask),
    )
    return _clamp_score(0.70 * mean_abs + 0.30 * hist_corr)


def _role_payload_base(entry: RoleCatalogEntry) -> dict[str, Any]:
    return {
        "role_id": entry.role_id,
        "name": entry.name,
        "normalized_name": entry.normalized_name,
        "rarity": entry.rarity,
        "cost": entry.cost,
        "front_back_type": entry.front_back_type,
        "trait_ids": list(entry.trait_ids),
    }


def build_precomputed_role_features(
    catalog: Iterable[RoleCatalogEntry],
    icons_by_role_id: Mapping[str, Image.Image],
) -> dict[str, Any]:
    items = []
    for entry in catalog:
        icon = icons_by_role_id[entry.role_id]
        feature = _normalized_rgba(icon, ROLE_FEATURE_SIZE)
        mask = _alpha_mask(icon, ROLE_FEATURE_SIZE)
        items.append(
            {
                **_role_payload_base(entry),
                "icon_rgba": _pixel_payload(feature, "RGBA"),
                "icon_mask": _pixel_payload(mask, "L"),
                "histogram": _hsv_histogram_from_image(feature, mask),
            }
        )
    return {
        "role_feature_schema_version": CW_ROLE_FEATURE_SCHEMA_VERSION,
        "recognizer_algorithm_version": CW_ROLE_RECOGNIZER_ALGORITHM_VERSION,
        "geometry_version": CW_SLOT_GEOMETRY_VERSION,
        "empty_template_version": CW_SLOT_EMPTY_TEMPLATE_VERSION,
        "target_size": list(SLOT_TARGET_SIZE),
        "avatar_roi": list(ROLE_AVATAR_ROI),
        "feature_size": list(ROLE_FEATURE_SIZE),
        "hist_bins": list(ROLE_HIST_BINS),
        "min_score": DEFAULT_MIN_SCORE,
        "low_score": DEFAULT_LOW_SCORE,
        "min_gap": DEFAULT_MIN_GAP,
        "empty_min_score": DEFAULT_EMPTY_MIN_SCORE,
        "empty_min_gap": DEFAULT_EMPTY_MIN_GAP,
        "items": items,
    }


def _candidate_fee_color(candidate: RoleCandidate) -> str:
    return fee_color_for_tier(candidate.rarity if candidate.rarity is not None else candidate.cost)


def choose_role_candidate_by_fee(
    candidates: list[RoleCandidate],
    fee_color: str,
    *,
    min_gap: float = DEFAULT_MIN_GAP,
) -> RoleCandidate | None:
    if not candidates:
        return None
    if len(candidates) < 2:
        return candidates[0]
    top, second = candidates[0], candidates[1]
    if top.score - second.score >= min_gap:
        return top
    if fee_color in {"unknown", "gold"}:
        return top
    fee_matches = [candidate for candidate in candidates if _candidate_fee_color(candidate) == fee_color]
    if len(fee_matches) == 1 and fee_matches[0] is not top and top.score - fee_matches[0].score < min_gap:
        return fee_matches[0]
    return top


def classify_role_confidence(
    top: RoleCandidate | None,
    second: RoleCandidate | None,
    fee_color: str,
    empty_score: float,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    low_score: float = DEFAULT_LOW_SCORE,
    min_gap: float = DEFAULT_MIN_GAP,
    empty_min_score: float = DEFAULT_EMPTY_MIN_SCORE,
    empty_min_gap: float = DEFAULT_EMPTY_MIN_GAP,
    allow_empty: bool = True,
) -> RoleConfidence:
    best_score = top.score if top is not None else 0.0
    if allow_empty and empty_score >= empty_min_score and empty_score - best_score >= empty_min_gap:
        return RoleConfidence(match_kind="empty", confidence_reason="empty_template_match")
    if top is None or best_score < low_score:
        return RoleConfidence(match_kind="unknown", confidence_reason="role_score_below_low_threshold")
    second_score = second.score if second is not None else 0.0
    gap = best_score - second_score
    top_fee_color = _candidate_fee_color(top)
    fee_conflict = fee_color not in {"unknown", "gold"} and top_fee_color not in {"unknown", fee_color}
    icon_clear = best_score >= min_score and gap >= min_gap
    if icon_clear and fee_conflict:
        return RoleConfidence(match_kind="low_confidence", confidence_reason="icon_fee_conflict")
    if icon_clear:
        reason = "icon_fee_match" if fee_color not in {"unknown", "gold"} and top_fee_color == fee_color else "icon_score_clear"
        return RoleConfidence(match_kind="icon", confidence_reason=reason)
    if fee_color not in {"unknown", "gold"} and top_fee_color == fee_color:
        return RoleConfidence(match_kind="low_confidence", confidence_reason="icon_fee_match")
    if gap < min_gap:
        return RoleConfidence(match_kind="low_confidence", confidence_reason="role_score_gap_too_small")
    return RoleConfidence(match_kind="low_confidence", confidence_reason="role_score_below_min_threshold")


@lru_cache(maxsize=len(STAR_SCALES))
def _star_template_gray(scale: float) -> np.ndarray:
    star = Image.open(Path(__file__).resolve().parent / "assets" / "star.png").convert("RGBA")
    width = max(1, round(star.width * scale))
    height = max(1, round(star.height * scale))
    star_rgba = np.array(star, dtype=np.uint8)
    resized = cv2.resize(star_rgba, (width, height), interpolation=cv2.INTER_AREA)
    flattened = _flatten_rgba_on_black(Image.fromarray(resized, "RGBA")).convert("RGB")
    return cv2.cvtColor(np.array(flattened, dtype=np.uint8), cv2.COLOR_RGB2GRAY)


def detect_slot_stars(crop: Image.Image) -> list[dict[str, Any]]:
    roi = _flatten_rgba_on_black(crop).crop(STAR_ROI).convert("RGB")
    roi_gray = cv2.cvtColor(np.array(roi, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    candidates: list[dict[str, Any]] = []
    for scale in STAR_SCALES:
        template = _star_template_gray(scale)
        height, width = template.shape[:2]
        if height > roi_gray.shape[0] or width > roi_gray.shape[1]:
            continue
        result = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= STAR_THRESHOLD)
        for x, y in zip(xs, ys):
            candidates.append(
                {
                    "x": int(STAR_ROI[0] + x),
                    "y": int(STAR_ROI[1] + y),
                    "w": int(width),
                    "h": int(height),
                    "score": round(float(result[y, x]), 4),
                    "scale": scale,
                }
            )
    candidates.sort(key=lambda item: (-float(item["score"]), int(item["x"]), int(item["y"])))
    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        center = (candidate["x"] + candidate["w"] / 2.0, candidate["y"] + candidate["h"] / 2.0)
        if any(
            math.hypot(center[0] - (item["x"] + item["w"] / 2.0), center[1] - (item["y"] + item["h"] / 2.0))
            < STAR_NMS_RADIUS
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= 3:
            break
    selected.sort(key=lambda item: (int(item["x"]), int(item["y"])))
    return selected


def warp_slot_crop(image: Image.Image, slot_spec: SlotSpec) -> Image.Image:
    if image.size != CANONICAL_SCREENSHOT_SIZE:
        raise TrailError("SLOTS_LAYOUT_MISMATCH", "slots icon reader requires canonical 1920x1080 screenshot")
    source = np.array(slot_spec.quad, dtype=np.float32)
    target = np.array(((0, 0), (102, 0), (102, 119), (0, 119)), dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(np.array(image.convert("RGBA"), dtype=np.uint8), matrix, SLOT_TARGET_SIZE)
    return Image.fromarray(warped, "RGBA")


class VectorRoleIconRecognizer:
    def __init__(
        self,
        roles: Iterable[_IndexedRole],
        *,
        empty_templates: Mapping[str, Image.Image],
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        low_score: float = DEFAULT_LOW_SCORE,
        min_gap: float = DEFAULT_MIN_GAP,
        empty_min_score: float = DEFAULT_EMPTY_MIN_SCORE,
        empty_min_gap: float = DEFAULT_EMPTY_MIN_GAP,
    ) -> None:
        self.algorithm_version = CW_ROLE_RECOGNIZER_ALGORITHM_VERSION
        self.empty_template_version = CW_SLOT_EMPTY_TEMPLATE_VERSION
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.low_score = float(low_score)
        self.min_gap = float(min_gap)
        self.empty_min_score = float(empty_min_score)
        self.empty_min_gap = float(empty_min_gap)
        self._roles = list(roles)
        self._empty_templates = {key: image.convert("RGBA") for key, image in empty_templates.items()}

    @classmethod
    def from_precomputed_features(
        cls,
        payload: dict[str, Any],
        *,
        empty_templates: Mapping[str, Image.Image],
        top_k: int = DEFAULT_TOP_K,
    ) -> "VectorRoleIconRecognizer":
        if not isinstance(payload, dict):
            raise ValueError("role feature payload must be an object")
        if payload.get("role_feature_schema_version") != CW_ROLE_FEATURE_SCHEMA_VERSION:
            raise ValueError("unsupported role feature schema version")
        if payload.get("recognizer_algorithm_version") != CW_ROLE_RECOGNIZER_ALGORITHM_VERSION:
            raise ValueError("unsupported role recognizer algorithm version")
        if payload.get("geometry_version") != CW_SLOT_GEOMETRY_VERSION:
            raise ValueError("unsupported role geometry version")
        if payload.get("empty_template_version") != CW_SLOT_EMPTY_TEMPLATE_VERSION:
            raise ValueError("unsupported role empty template version")
        if payload.get("target_size") != list(SLOT_TARGET_SIZE) or payload.get("feature_size") != list(ROLE_FEATURE_SIZE):
            raise ValueError("unsupported role feature payload sizes")
        if payload.get("avatar_roi") != list(ROLE_AVATAR_ROI) or payload.get("hist_bins") != list(ROLE_HIST_BINS):
            raise ValueError("unsupported role feature payload regions")
        normalized_templates = _validate_empty_templates(empty_templates)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("role feature items must be a list")
        indexed = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("role feature item must be an object")
            icon = _expected_image_from_payload(item, "icon_rgba", "RGBA", 4, ROLE_FEATURE_SIZE)
            icon_mask = _expected_image_from_payload(item, "icon_mask", "L", 1, ROLE_FEATURE_SIZE)
            indexed.append(
                _IndexedRole(
                    role_id=None if item.get("role_id") is None else str(item.get("role_id")),
                    name=str(item.get("name") or ""),
                    normalized_name=None if item.get("normalized_name") is None else str(item.get("normalized_name")),
                    rarity=None if item.get("rarity") is None else str(item.get("rarity")),
                    cost=None if item.get("cost") is None else str(item.get("cost")),
                    front_back_type=None if item.get("front_back_type") is None else str(item.get("front_back_type")),
                    trait_ids=[str(value) for value in item.get("trait_ids") or []],
                    icon_rgba=icon,
                    icon_mask=icon_mask,
                    histogram=_hsv_histogram_from_image(icon, icon_mask),
                    feature=_array_feature(icon, icon_mask),
                    fee_color=fee_color_for_tier(item.get("rarity") if item.get("rarity") is not None else item.get("cost")),
                )
            )
        return cls(
            indexed,
            empty_templates=normalized_templates,
            top_k=top_k,
            min_score=_finite_float_from_payload(payload, "min_score"),
            low_score=_finite_float_from_payload(payload, "low_score"),
            min_gap=_finite_float_from_payload(payload, "min_gap"),
            empty_min_score=_finite_float_from_payload(payload, "empty_min_score"),
            empty_min_gap=_finite_float_from_payload(payload, "empty_min_gap"),
        )

    def recognize_crop(self, crop: Image.Image, area: str) -> RoleRecognitionResult:
        if crop.size != SLOT_TARGET_SIZE:
            raise ValueError("role crop must be 103x120")
        if area not in {"front", "back", "hand"}:
            raise ValueError(f"unknown role slot area: {area}")
        crop = crop.convert("RGBA")
        star_boxes = detect_slot_stars(crop)
        fee_color = fee_color_from_crop(crop)
        query_image, query_mask = _card_feature(crop, star_boxes)
        candidates = self._rank_candidates(query_image, query_mask)
        best_role_score = candidates[0].score if candidates else 0.0
        empty_key = "hand" if area == "hand" else "field"
        empty_score_value = empty_template_score(crop, self._empty_templates[empty_key], star_boxes)

        if empty_score_value >= self.empty_min_score and empty_score_value - best_role_score >= self.empty_min_gap:
            return RoleRecognitionResult(
                name=None,
                role_id=None,
                candidates=candidates,
                score=None,
                empty=True,
                star_count=len(star_boxes),
                star_boxes=star_boxes,
                fee_color=fee_color,
                match_kind="empty",
                confidence_reason="empty_template_match",
                diagnostics=_diagnostics(candidates, empty_score_value),
            )

        selected = choose_role_candidate_by_fee(candidates, fee_color, min_gap=self.min_gap)
        second = _second_candidate(candidates, selected)
        confidence = classify_role_confidence(
            selected,
            second,
            fee_color,
            empty_score_value,
            min_score=self.min_score,
            low_score=self.low_score,
            min_gap=self.min_gap,
            empty_min_score=self.empty_min_score,
            empty_min_gap=self.empty_min_gap,
            allow_empty=False,
        )
        if selected is None or confidence.match_kind == "unknown":
            return RoleRecognitionResult(
                name=None,
                role_id=None,
                candidates=candidates,
                score=selected.score if selected is not None else None,
                empty=False,
                star_count=len(star_boxes),
                star_boxes=star_boxes,
                fee_color=fee_color,
                match_kind="unknown",
                confidence_reason=confidence.confidence_reason,
                diagnostics=_diagnostics(candidates, empty_score_value),
            )
        return RoleRecognitionResult(
            name=selected.name,
            role_id=selected.role_id,
            candidates=candidates,
            score=selected.score,
            empty=False,
            star_count=len(star_boxes),
            star_boxes=star_boxes,
            fee_color=fee_color,
            match_kind=confidence.match_kind,
            confidence_reason=confidence.confidence_reason,
            rarity=selected.rarity,
            cost=selected.cost,
            diagnostics=_diagnostics(candidates, empty_score_value),
        )

    def _rank_candidates(self, query_image: Image.Image, query_mask: Image.Image) -> list[RoleCandidate]:
        scored: list[RoleCandidate] = []
        query_feature = _array_feature(query_image, query_mask)
        for role in self._roles:
            parts = _role_similarity_parts_fast(query_feature, role.feature)
            scored.append(
                RoleCandidate(
                    name=role.name,
                    role_id=role.role_id,
                    score=round(parts.score, 4),
                    rarity=role.rarity,
                    cost=role.cost,
                    normalized_name=role.normalized_name,
                    front_back_type=role.front_back_type,
                    trait_ids=list(role.trait_ids),
                    diagnostics={
                        "masked_ncc": round(parts.masked_ncc, 4),
                        "masked_l1": round(parts.masked_l1, 4),
                        "hist_corr": round(parts.hist_corr, 4),
                    },
                )
            )
        scored.sort(key=lambda item: (-item.score, item.name, item.role_id or ""))
        return scored[: self.top_k]

def _validate_empty_templates(empty_templates: Mapping[str, Image.Image]) -> dict[str, Image.Image]:
    normalized: dict[str, Image.Image] = {}
    for key in ("field", "hand"):
        if key not in empty_templates:
            raise ValueError(f"empty template missing: {key}")
        image = empty_templates[key].convert("RGBA")
        if image.size != SLOT_TARGET_SIZE:
            raise ValueError(f"empty template {key} must be 103x120")
        normalized[key] = image
    return normalized


def _second_candidate(candidates: list[RoleCandidate], selected: RoleCandidate | None) -> RoleCandidate | None:
    for candidate in candidates:
        if candidate is not selected:
            return candidate
    return None


def _diagnostics(candidates: list[RoleCandidate], empty_score_value: float) -> dict[str, Any]:
    return {
        "empty_score": round(empty_score_value, 4),
        "candidates": [
            {
                "name": candidate.name,
                "role_id": candidate.role_id,
                "score": candidate.score,
                **candidate.diagnostics,
            }
            for candidate in candidates[:DEFAULT_TOP_K]
        ],
    }
