from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageStat

from trail.scenes.cw.equipment_resources import EquipmentCatalogEntry


FEATURE_SIZE = (32, 32)
MATCH_SIZE = (64, 64)
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.72
DEFAULT_MIN_GAP = 0.05


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


def _normalized_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    background.alpha_composite(rgba)
    return background.resize(size, Image.Resampling.LANCZOS)


def _mean_abs_similarity(left: Image.Image, right: Image.Image) -> float:
    left_rgb = left.convert("RGB")
    right_rgb = right.convert("RGB")
    diff = ImageChops.difference(left_rgb, right_rgb)
    channel_means = ImageStat.Stat(diff).mean
    mean_abs_diff = sum(channel_means) / len(channel_means)
    return max(0.0, min(1.0, 1.0 - mean_abs_diff / 255.0))


def _is_empty_roi(image: Image.Image) -> bool:
    rgb = image.convert("RGB")
    stat = ImageStat.Stat(rgb)
    brightness = sum(stat.mean) / 3.0
    spread = sum(stat.stddev) / 3.0
    return brightness < 12.0 and spread < 8.0


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
            )
            for entry, icon in icons
        ]

    def recognize(self, image: Image.Image) -> EquipmentRecognitionResult:
        if _is_empty_roi(image):
            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

        query_feature = _normalized_rgba(image, FEATURE_SIZE)
        query_match = _normalized_rgba(image, MATCH_SIZE)
        ranked = sorted(
            ((_mean_abs_similarity(query_feature, indexed.feature), indexed) for indexed in self._icons),
            key=lambda item: (-item[0], item[1].entry.name),
        )[: self.top_k]

        scored: list[tuple[float, EquipmentCatalogEntry]] = []
        for feature_score, indexed in ranked:
            match_score = _mean_abs_similarity(query_match, indexed.match_image)
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
