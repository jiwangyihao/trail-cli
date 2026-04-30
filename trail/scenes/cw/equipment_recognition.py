from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Protocol

from PIL import Image, ImageChops, ImageStat

from trail.scenes.cw.equipment_resources import EquipmentCatalogEntry


FEATURE_SIZE = (32, 32)
MATCH_SIZE = (64, 64)
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.72
DEFAULT_MIN_GAP = 0.05
FEATURE_SCHEMA_VERSION = 1
RECOGNIZER_ALGORITHM_VERSION = "vector-mask-v1"


@dataclass(frozen=True)
class EquipmentCandidate:
    equipment_id: str | None
    cache_key: str
    name: str
    score: float
    backend: str = "vector"


@dataclass(frozen=True)
class EquipmentRecognitionResult:
    candidates: list[EquipmentCandidate]
    score: float | None
    gap: float | None
    uncertain: bool
    empty: bool


class EquipmentIconRecognizer(Protocol):
    def recognize(self, image: Image.Image) -> EquipmentRecognitionResult: ...


@dataclass(frozen=True)
class _IndexedIcon:
    entry: EquipmentCatalogEntry
    feature: Image.Image
    match_image: Image.Image
    feature_mask: Image.Image
    match_mask: Image.Image


def _normalized_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    background.alpha_composite(rgba)
    return background.resize(size, Image.Resampling.LANCZOS)


def _alpha_mask(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("RGBA").getchannel("A").resize(size, Image.Resampling.LANCZOS)


def _pixel_payload(image: Image.Image, mode: str) -> dict:
    normalized = image.convert(mode)
    return {"mode": mode, "size": list(normalized.size), "data": list(normalized.tobytes())}


def _image_payload(image: Image.Image) -> dict:
    return _pixel_payload(image, "RGBA")


def _mask_payload(image: Image.Image) -> dict:
    return _pixel_payload(image, "L")


def _image_payload_size(payload: dict) -> tuple[int, int]:
    size = payload.get("size") if isinstance(payload, dict) else None
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError("image payload size must be a two-item list")
    width, height = size
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("image payload size must contain positive integers")
    return width, height


def _image_from_pixel_payload(payload: dict, mode: str, channels: int) -> Image.Image:
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


def _image_from_payload(payload: dict) -> Image.Image:
    return _image_from_pixel_payload(payload, "RGBA", 4)


def _mask_from_payload(payload: dict) -> Image.Image:
    return _image_from_pixel_payload(payload, "L", 1)


def _finite_float_from_payload(payload: dict, key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a finite float") from exc
    if not math.isfinite(value):
        raise ValueError(f"{key} must be a finite float")
    return value


def _expected_image_from_payload(payload: dict, key: str, mode: str, channels: int, size: tuple[int, int]) -> Image.Image:
    try:
        image = _image_from_pixel_payload(payload.get(key), mode, channels)
    except ValueError as exc:
        raise ValueError(f"{key} payload invalid: {exc}") from exc
    if image.size != size:
        raise ValueError(f"{key} payload size must be {list(size)}")
    return image


def _image_data(image: Image.Image):
    get_flattened_data = getattr(image, "get_flattened_data", None)
    return get_flattened_data() if callable(get_flattened_data) else image.getdata()


def _mean_abs_similarity(left: Image.Image, right: Image.Image) -> float:
    left_rgb = left.convert("RGB")
    right_rgb = right.convert("RGB")
    diff = ImageChops.difference(left_rgb, right_rgb)
    channel_means = ImageStat.Stat(diff).mean
    mean_abs_diff = sum(channel_means) / len(channel_means)
    return max(0.0, min(1.0, 1.0 - mean_abs_diff / 255.0))


def _masked_mean_abs_similarity(left: Image.Image, right: Image.Image, mask: Image.Image) -> float:
    left_pixels = _image_data(left.convert("RGB"))
    right_pixels = _image_data(right.convert("RGB"))
    mask_pixels = _image_data(mask.convert("L"))
    total_weight = 0
    weighted_diff = 0
    for (left_red, left_green, left_blue), (right_red, right_green, right_blue), weight in zip(
        left_pixels,
        right_pixels,
        mask_pixels,
    ):
        if weight <= 8:
            continue
        total_weight += weight
        weighted_diff += (
            abs(left_red - right_red) + abs(left_green - right_green) + abs(left_blue - right_blue)
        ) * weight
    if total_weight <= 0:
        return _mean_abs_similarity(left, right)
    mean_abs_diff = weighted_diff / (3 * total_weight)
    return max(0.0, min(1.0, 1.0 - mean_abs_diff / 255.0))


def _is_empty_roi(image: Image.Image) -> bool:
    rgb = image.convert("RGB")
    stat = ImageStat.Stat(rgb)
    brightness = sum(stat.mean) / 3.0
    spread = sum(stat.stddev) / 3.0
    return brightness < 12.0 and spread < 8.0


def build_precomputed_equipment_features(icons: Iterable[tuple[EquipmentCatalogEntry, Image.Image]]) -> dict:
    items = []
    for entry, icon in icons:
        feature = _normalized_rgba(icon, FEATURE_SIZE)
        match_image = _normalized_rgba(icon, MATCH_SIZE)
        feature_mask = _alpha_mask(icon, FEATURE_SIZE)
        match_mask = _alpha_mask(icon, MATCH_SIZE)
        items.append(
            {
                "cache_key": entry.cache_key,
                "id": entry.id,
                "name": entry.name,
                "kind": entry.kind,
                "category": entry.category,
                "category_name": entry.category_name,
                "icon_url": entry.icon_url,
                "big_version": entry.big_version,
                "feature_rgba": _image_payload(feature),
                "match_rgba": _image_payload(match_image),
                "feature_mask": _mask_payload(feature_mask),
                "match_mask": _mask_payload(match_mask),
            }
        )
    return {
        "equipment_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "recognizer_algorithm_version": RECOGNIZER_ALGORITHM_VERSION,
        "feature_size": list(FEATURE_SIZE),
        "match_size": list(MATCH_SIZE),
        "min_score": DEFAULT_MIN_SCORE,
        "min_gap": DEFAULT_MIN_GAP,
        "items": items,
    }


class VectorEquipmentIconRecognizer:
    def __init__(
        self,
        icons: Iterable[tuple[EquipmentCatalogEntry, Image.Image]],
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        min_gap: float = DEFAULT_MIN_GAP,
    ) -> None:
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.min_gap = float(min_gap)
        self._icons = [
            _IndexedIcon(
                entry=entry,
                feature=_normalized_rgba(icon, FEATURE_SIZE),
                match_image=_normalized_rgba(icon, MATCH_SIZE),
                feature_mask=_alpha_mask(icon, FEATURE_SIZE),
                match_mask=_alpha_mask(icon, MATCH_SIZE),
            )
            for entry, icon in icons
        ]

    @classmethod
    def from_precomputed_features(cls, payload: dict, *, top_k: int = DEFAULT_TOP_K) -> "VectorEquipmentIconRecognizer":
        if not isinstance(payload, dict):
            raise ValueError("equipment feature payload must be an object")
        if payload.get("equipment_feature_schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("unsupported equipment feature schema version")
        if payload.get("recognizer_algorithm_version") != RECOGNIZER_ALGORITHM_VERSION:
            raise ValueError("unsupported equipment recognizer algorithm version")
        if payload.get("feature_size") != list(FEATURE_SIZE) or payload.get("match_size") != list(MATCH_SIZE):
            raise ValueError("unsupported equipment feature payload sizes")
        min_score = _finite_float_from_payload(payload, "min_score")
        min_gap = _finite_float_from_payload(payload, "min_gap")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("equipment feature items must be a list")
        recognizer = cls(
            [],
            top_k=top_k,
            min_score=min_score,
            min_gap=min_gap,
        )
        indexed = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("equipment feature item must be an object")
            entry = EquipmentCatalogEntry(
                cache_key=str(item.get("cache_key") or ""),
                id=None if item.get("id") is None else str(item.get("id")),
                name=str(item.get("name") or ""),
                kind=str(item.get("kind") or ""),
                category=None if item.get("category") is None else str(item.get("category")),
                category_name=None if item.get("category_name") is None else str(item.get("category_name")),
                icon_url=str(item.get("icon_url") or ""),
                big_version=str(item.get("big_version") or ""),
            )
            indexed.append(
                _IndexedIcon(
                    entry=entry,
                    feature=_expected_image_from_payload(item, "feature_rgba", "RGBA", 4, FEATURE_SIZE),
                    match_image=_expected_image_from_payload(item, "match_rgba", "RGBA", 4, MATCH_SIZE),
                    feature_mask=_expected_image_from_payload(item, "feature_mask", "L", 1, FEATURE_SIZE),
                    match_mask=_expected_image_from_payload(item, "match_mask", "L", 1, MATCH_SIZE),
                )
            )
        recognizer._icons = indexed
        return recognizer

    def recognize(self, image: Image.Image) -> EquipmentRecognitionResult:
        if _is_empty_roi(image):
            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

        query_feature = _normalized_rgba(image, FEATURE_SIZE)
        query_match = _normalized_rgba(image, MATCH_SIZE)
        ranked = sorted(
            ((_masked_mean_abs_similarity(query_feature, indexed.feature, indexed.feature_mask), indexed) for indexed in self._icons),
            key=lambda item: (-item[0], item[1].entry.name),
        )[: self.top_k]

        scored: list[tuple[float, EquipmentCatalogEntry]] = []
        for feature_score, indexed in ranked:
            match_score = _masked_mean_abs_similarity(query_match, indexed.match_image, indexed.match_mask)
            scored.append(((feature_score + match_score) / 2.0, indexed.entry))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        candidates = [
            EquipmentCandidate(
                equipment_id=entry.id,
                cache_key=entry.cache_key,
                name=entry.name,
                score=round(score, 4),
            )
            for score, entry in scored
        ]
        score = candidates[0].score if candidates else None
        if candidates:
            second_score = candidates[1].score if len(candidates) > 1 else 0.0
            gap = round(candidates[0].score - second_score, 4)
        else:
            gap = None
        uncertain = score is None or score < self.min_score or (gap is not None and gap < self.min_gap)
        return EquipmentRecognitionResult(candidates=candidates, score=score, gap=gap, uncertain=uncertain, empty=False)
