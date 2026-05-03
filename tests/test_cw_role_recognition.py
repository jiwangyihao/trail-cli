from __future__ import annotations

import json
from pathlib import Path

import pytest
import cv2
import numpy as np
from PIL import Image
import trail.scenes.cw.role_recognition as role_recognition

from trail.core.errors import TrailError
from trail.scenes.cw.role_recognition import (
    CW_SLOT_GEOMETRY_VERSION,
    SLOT_TARGET_SIZE,
    STAR_MATCH_METHOD,
    STAR_NMS_RADIUS,
    STAR_SCALES,
    STAR_THRESHOLD,
    RoleCandidate,
    VectorRoleIconRecognizer,
    _star_template_gray,
    build_precomputed_role_features,
    choose_role_candidate_by_fee,
    classify_role_confidence,
    detect_slot_stars,
    empty_template_score,
    fee_color_for_tier,
    fee_color_from_crop,
    iter_slot_specs,
    role_card_base_mask,
    role_similarity_parts,
    warp_slot_crop,
)
from trail.scenes.cw.role_resources import RoleCatalogEntry


ROOT = Path(__file__).resolve().parents[1]


def _fill_fee_strip(crop: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    result = crop.copy().convert("RGBA")
    left, top, right, bottom = role_recognition.FEE_STRIP
    for x in range(left, right):
        for y in range(top, bottom):
            result.putpixel((x, y), color)
    return result


def _loaded_real_recognizer() -> VectorRoleIconRecognizer:
    payload = json.loads(
        (ROOT / "trail/scenes/cw/generated/4.2/roles/features.json").read_text(encoding="utf-8")
    )
    empty_templates = {
        "field": Image.open(ROOT / "trail/scenes/cw/generated/4.2/roles/empty/field-v1.png").convert("RGBA"),
        "hand": Image.open(ROOT / "trail/scenes/cw/generated/4.2/roles/empty/hand-v1.png").convert("RGBA"),
    }
    return VectorRoleIconRecognizer.from_precomputed_features(payload, empty_templates=empty_templates)


def _single_role_payload(role_id: str = "r1", name: str = "希儿", color=(200, 40, 60, 255)) -> dict:
    return build_precomputed_role_features(
        [
            RoleCatalogEntry(
                role_id=role_id,
                name=name,
                normalized_name=name,
                icon_url=f"https://example.test/{role_id}.png",
                rarity="2",
                cost=None,
                front_back_type="Common",
                trait_ids=["t1"],
            )
        ],
        {role_id: Image.new("RGBA", (64, 64), color)},
    )


def test_slot_geometry_has_19_specs_and_target_size():
    specs = list(iter_slot_specs())

    assert CW_SLOT_GEOMETRY_VERSION == "cw-slots-1920x1080-v2"
    assert SLOT_TARGET_SIZE == (103, 120)
    assert len(specs) == 19
    assert [(spec.area, spec.index) for spec in specs] == [
        ("front", 0),
        ("front", 1),
        ("front", 2),
        ("front", 3),
        ("back", 0),
        ("back", 1),
        ("back", 2),
        ("back", 3),
        ("back", 4),
        ("back", 5),
        ("hand", 0),
        ("hand", 1),
        ("hand", 2),
        ("hand", 3),
        ("hand", 4),
        ("hand", 5),
        ("hand", 6),
        ("hand", 7),
        ("hand", 8),
    ]
    assert specs[0].quad == ((690.2, 339.7), (801.2, 339.7), (794.6, 462.9), (680.5, 462.9))
    assert specs[9].quad == ((1254.7, 611.7), (1369.6, 611.7), (1382.9, 737.6), (1265.0, 737.6))
    assert specs[-1].quad == ((1390.3, 860.0), (1493.7, 860.0), (1493.7, 980.0), (1390.3, 980.0))


def test_base_mask_excludes_fixed_regions_and_only_local_star_boxes():
    mask = role_card_base_mask([{"x": 20, "y": 90, "w": 10, "h": 8}])

    assert mask.size == (103, 120)
    assert mask.mode == "L"
    assert mask.getpixel((50, 50)) == 255
    assert mask.getpixel((10, 10)) == 0
    assert mask.getpixel((90, 10)) == 0
    assert mask.getpixel((50, 115)) == 0
    assert mask.getpixel((19, 89)) == 0
    assert mask.getpixel((31, 99)) == 0
    assert mask.getpixel((50, 90)) == 255
    assert mask.getpixel((50, 105)) == 255


def test_star_constants_are_frozen():
    assert STAR_SCALES == (0.65, 0.75, 0.80)
    assert STAR_THRESHOLD == 0.78
    assert STAR_NMS_RADIUS == 8
    assert STAR_MATCH_METHOD == "TM_CCOEFF_NORMED"


def test_fee_tier_mapping_is_frozen():
    assert fee_color_for_tier(1) == "gray"
    assert fee_color_for_tier("2") == "green"
    assert fee_color_for_tier("3") == "blue"
    assert fee_color_for_tier("4") == "purple"
    assert fee_color_for_tier("5") == "gold"
    assert fee_color_for_tier(6) == "unknown"
    assert fee_color_for_tier(None) == "unknown"


def test_fee_color_from_crop_classifies_known_strip_colors():
    samples = {
        "gray": (92, 92, 92, 255),
        "green": (53, 180, 159, 255),
        "blue": (50, 112, 190, 255),
        "purple": (95, 53, 180, 255),
        "gold": (255, 212, 113, 255),
    }
    for expected, color in samples.items():
        assert fee_color_from_crop(_fill_fee_strip(Image.new("RGBA", (103, 120), (0, 0, 0, 255)), color)) == expected


def test_fee_color_from_crop_ignores_upper_border_contamination():
    crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    for x in range(4, 99):
        for y in range(112, 115):
            crop.putpixel((x, y), (50, 112, 190, 255))
        for y in range(115, 120):
            crop.putpixel((x, y), (92, 92, 92, 255))

    assert fee_color_from_crop(crop) == "gray"


def test_fee_color_from_crop_keeps_unfrozen_hues_unknown():
    hsv = np.array([[[50, 180, 180]]], dtype=np.uint8)
    rgb = tuple(int(value) for value in cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)[0, 0])

    assert fee_color_from_crop(_fill_fee_strip(Image.new("RGBA", (103, 120)), (*rgb, 255))) == "unknown"


def test_precomputed_role_features_round_trip():
    icon = Image.new("RGBA", (64, 64), (200, 40, 60, 255))
    field_empty = Image.new("RGBA", (103, 120), (20, 22, 26, 255))
    hand_empty = Image.new("RGBA", (103, 120), (32, 34, 40, 255))
    payload = build_precomputed_role_features(
        [
            RoleCatalogEntry(
                role_id="r1",
                name="希儿",
                normalized_name="希儿",
                icon_url="https://example.test/r1.png",
                rarity="2",
                cost=None,
                front_back_type="Common",
                trait_ids=["t1"],
            )
        ],
        {"r1": icon},
    )

    assert payload["role_feature_schema_version"] == 1
    assert payload["recognizer_algorithm_version"] == "role-card-mask-v1"
    assert payload["geometry_version"] == "cw-slots-1920x1080-v2"
    assert payload["empty_template_version"] == "cw-slots-empty-v1"
    assert payload["target_size"] == [103, 120]
    assert payload["avatar_roi"] == [5, 4, 98, 108]
    assert payload["feature_size"] == [64, 64]
    assert payload["hist_bins"] == [16, 16, 16]
    assert payload["min_score"] == 0.58
    assert payload["low_score"] == 0.50
    assert payload["min_gap"] == 0.035
    assert payload["empty_min_score"] == 0.82
    assert payload["empty_min_gap"] == 0.08
    assert set(payload["items"][0]) >= {
        "role_id",
        "name",
        "normalized_name",
        "rarity",
        "cost",
        "front_back_type",
        "trait_ids",
        "icon_rgba",
        "icon_mask",
        "histogram",
    }

    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={"field": field_empty, "hand": hand_empty},
    )
    assert recognizer.algorithm_version == "role-card-mask-v1"
    assert recognizer.empty_template_version == "cw-slots-empty-v1"


def test_recognizer_does_not_disable_role_like_hand_empty_template():
    payload = _single_role_payload(role_id="role-like", name="像角色", color=(24, 180, 120, 255))
    role_like_card = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    role_like_card.paste(Image.new("RGBA", (64, 64), (50, 150, 105, 255)).resize((93, 104)), (5, 4))
    field_empty = Image.new("RGBA", (103, 120), (20, 22, 26, 255))

    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={"field": field_empty, "hand": role_like_card},
    )
    result = recognizer.recognize_crop(role_like_card, "hand")

    assert result.empty is True
    assert result.match_kind == "empty"
    assert "disabled_empty_templates" not in result.diagnostics


def test_role_score_uses_ncc_l1_and_hist_weights_for_exact_match():
    left = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
    for x in range(64):
        for y in range(64):
            left.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, 255))
    right = left.copy()
    mask = Image.new("L", (64, 64), 255)

    parts = role_similarity_parts(left, right, mask)

    assert parts.masked_ncc == 1.0
    assert parts.masked_l1 == 1.0
    assert parts.hist_corr == 1.0
    assert parts.score == 1.0


def test_star_detection_detects_template_and_returns_schema():
    crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    star = Image.open(ROOT / "trail/scenes/cw/assets/star.png").convert("RGBA")
    crop.alpha_composite(
        star.resize((round(star.width * 0.75), round(star.height * 0.75))),
        dest=(20, 90),
    )

    detections = detect_slot_stars(crop)

    assert len(detections) == 1
    assert set(detections[0]) == {"x", "y", "w", "h", "score", "scale"}
    assert detections[0]["scale"] in {0.65, 0.75, 0.80}
    assert 10 <= detections[0]["x"] < 91
    assert 82 <= detections[0]["y"] < 114


def test_star_template_resize_uses_cv2_inter_area(monkeypatch):
    assert hasattr(_star_template_gray, "cache_clear")
    _star_template_gray.cache_clear()
    calls = []
    original_resize = cv2.resize

    def spy_resize(src, dsize, *args, **kwargs):
        calls.append(kwargs.get("interpolation") if "interpolation" in kwargs else args[0] if args else None)
        return original_resize(src, dsize, *args, **kwargs)

    monkeypatch.setattr(cv2, "resize", spy_resize)

    detect_slot_stars(Image.new("RGBA", (103, 120), (0, 0, 0, 255)))
    detect_slot_stars(Image.new("RGBA", (103, 120), (0, 0, 0, 255)))

    assert calls == [cv2.INTER_AREA] * len(STAR_SCALES)
    _star_template_gray.cache_clear()


def test_fee_tie_break_and_confidence_rules():
    assert (
        choose_role_candidate_by_fee(
            [
                RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
                RoleCandidate(name="B", role_id="b", score=0.55, rarity="3"),
            ],
            fee_color="blue",
        ).role_id
        == "b"
    )

    high = classify_role_confidence(
        top=RoleCandidate(name="A", role_id="a", score=0.70, rarity="3"),
        second=RoleCandidate(name="B", role_id="b", score=0.60, rarity="2"),
        fee_color="blue",
        empty_score=0.10,
    )
    assert high.match_kind == "icon"
    assert high.confidence_reason == "icon_fee_match"

    empty = classify_role_confidence(
        top=RoleCandidate(name="A", role_id="a", score=0.20, rarity="2"),
        second=None,
        fee_color="unknown",
        empty_score=0.90,
    )
    assert empty.match_kind == "empty"
    assert empty.confidence_reason == "empty_template_match"

    conflict = classify_role_confidence(
        top=RoleCandidate(name="A", role_id="a", score=0.70, rarity="2"),
        second=RoleCandidate(name="B", role_id="b", score=0.60, rarity="3"),
        fee_color="blue",
        empty_score=0.10,
    )
    assert conflict.match_kind == "low_confidence"
    assert conflict.confidence_reason == "icon_fee_conflict"

    unknown = classify_role_confidence(
        top=RoleCandidate(name="A", role_id="a", score=0.49, rarity="2"),
        second=None,
        fee_color="green",
        empty_score=0.10,
    )
    assert unknown.match_kind == "unknown"
    assert unknown.confidence_reason == "role_score_below_low_threshold"


def test_fee_tie_break_requires_unique_fee_match():
    duplicate_fee_matches = [
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.55, rarity="3"),
        RoleCandidate(name="C", role_id="c", score=0.54, rarity="3"),
    ]
    assert choose_role_candidate_by_fee(duplicate_fee_matches, fee_color="blue").role_id == "a"

    top_also_matches = [
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="3"),
        RoleCandidate(name="B", role_id="b", score=0.55, rarity="3"),
        RoleCandidate(name="C", role_id="c", score=0.54, rarity="2"),
    ]
    assert choose_role_candidate_by_fee(top_also_matches, fee_color="blue").role_id == "a"

    unique_second_match = [
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.55, rarity="3"),
        RoleCandidate(name="C", role_id="c", score=0.54, rarity="4"),
    ]
    assert choose_role_candidate_by_fee(unique_second_match, fee_color="blue").role_id == "b"


def test_fee_tie_break_handles_variable_cost_silver_wolf_lv999_variants():
    variants = [
        RoleCandidate(name="银狼LV.999", role_id="15061", score=0.80, rarity="3"),
        RoleCandidate(name="银狼LV.999", role_id="15062", score=0.80, rarity="4"),
        RoleCandidate(name="银狼LV.999", role_id="15063", score=0.80, rarity="5"),
    ]

    assert choose_role_candidate_by_fee(variants, fee_color="purple").role_id == "15062"
    assert choose_role_candidate_by_fee(variants, fee_color="gold").role_id == "15063"


def test_recognize_crop_fee_selected_second_never_returns_empty_match_kind(monkeypatch):
    payload = _single_role_payload()
    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={
            "field": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
            "hand": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
        },
    )
    candidates = [
        RoleCandidate(name="A", role_id="a", score=0.80, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.77, rarity="3"),
    ]
    monkeypatch.setattr(recognizer, "_rank_candidates", lambda query_image, query_mask: candidates)
    monkeypatch.setattr(role_recognition, "fee_color_from_crop", lambda crop: "blue")
    monkeypatch.setattr(role_recognition, "empty_template_score", lambda crop, empty_template, star_boxes: 0.86)

    result = recognizer.recognize_crop(Image.new("RGBA", (103, 120)), "hand")

    assert result.name == "B"
    assert result.empty is False
    assert result.match_kind != "empty"


def test_recognizer_uses_payload_min_gap_for_fee_tie_break(monkeypatch):
    payload = _single_role_payload()
    payload["min_score"] = 0.55
    payload["min_gap"] = 0.01
    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={
            "field": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
            "hand": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
        },
    )
    candidates = [
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.54, rarity="3"),
    ]
    monkeypatch.setattr(recognizer, "_rank_candidates", lambda query_image, query_mask: candidates)
    monkeypatch.setattr(role_recognition, "fee_color_from_crop", lambda crop: "blue")
    monkeypatch.setattr(role_recognition, "empty_template_score", lambda crop, empty_template, star_boxes: 0.10)

    result = recognizer.recognize_crop(Image.new("RGBA", (103, 120)), "hand")

    assert result.name == "A"
    assert result.confidence_reason == "icon_fee_conflict"


def test_recognizer_uses_payload_confidence_and_empty_thresholds(monkeypatch):
    payload = _single_role_payload()
    payload["min_score"] = 0.55
    payload["min_gap"] = 0.01
    payload["empty_min_score"] = 0.95
    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={
            "field": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
            "hand": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
        },
    )
    candidates = [
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.54, rarity="3"),
    ]
    monkeypatch.setattr(recognizer, "_rank_candidates", lambda query_image, query_mask: candidates)
    monkeypatch.setattr(role_recognition, "fee_color_from_crop", lambda crop: "green")
    monkeypatch.setattr(role_recognition, "empty_template_score", lambda crop, empty_template, star_boxes: 0.90)

    result = recognizer.recognize_crop(Image.new("RGBA", (103, 120)), "hand")

    assert result.name == "A"
    assert result.empty is False
    assert result.match_kind == "icon"
    assert result.confidence_reason == "icon_fee_match"


def test_empty_template_validation_rejects_missing_or_wrong_size():
    payload = build_precomputed_role_features(
        [
            RoleCatalogEntry(
                role_id="r1",
                name="希儿",
                normalized_name="希儿",
                icon_url="https://example.test/r1.png",
                rarity="2",
                cost=None,
                front_back_type="Common",
                trait_ids=["t1"],
            )
        ],
        {"r1": Image.new("RGBA", (64, 64), (200, 40, 60, 255))},
    )

    with pytest.raises(ValueError, match="empty template"):
        VectorRoleIconRecognizer.from_precomputed_features(
            payload,
            empty_templates={"field": Image.new("RGBA", (103, 120))},
        )

    with pytest.raises(ValueError, match="103x120"):
        VectorRoleIconRecognizer.from_precomputed_features(
            payload,
            empty_templates={
                "field": Image.new("RGBA", (103, 120)),
                "hand": Image.new("RGBA", (100, 120)),
            },
        )


def test_warp_slot_crop_rejects_non_canonical_screenshot_size():
    with pytest.raises(TrailError) as exc_info:
        warp_slot_crop(Image.new("RGBA", (1280, 720)), next(iter_slot_specs()))

    assert exc_info.value.code == "SLOTS_LAYOUT_MISMATCH"


def test_recognize_crop_rejects_unknown_area():
    payload = _single_role_payload()
    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={
            "field": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
            "hand": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
        },
    )

    with pytest.raises(ValueError, match="area"):
        recognizer.recognize_crop(Image.new("RGBA", (103, 120)), "bench")


def test_recognize_crop_star_output_matches_detector():
    payload = _single_role_payload()
    recognizer = VectorRoleIconRecognizer.from_precomputed_features(
        payload,
        empty_templates={
            "field": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
            "hand": Image.new("RGBA", (103, 120), (20, 22, 26, 255)),
        },
    )
    crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    star = Image.open(ROOT / "trail/scenes/cw/assets/star.png").convert("RGBA")
    crop.alpha_composite(
        star.resize((round(star.width * 0.75), round(star.height * 0.75))),
        dest=(20, 90),
    )

    detections = detect_slot_stars(crop)
    result = recognizer.recognize_crop(crop, "hand")

    assert result.star_count == len(detections)
    assert result.star_boxes == detections


def test_real_fixture_field_empty_score_exceeds_threshold():
    image = Image.open(ROOT / "tests/fixtures/cw/slots-icon/current-prep.jpg").convert("RGBA")
    front_2 = next(spec for spec in iter_slot_specs() if spec.area == "front" and spec.index == 1)
    crop = warp_slot_crop(image, front_2)
    empty = Image.open(ROOT / "trail/scenes/cw/assets/slots/empty-field-v1.png").convert("RGBA")

    assert empty_template_score(crop, empty, []) >= 0.82


@pytest.mark.slow
def test_real_fixture_full_role_recognition_visible_facts():
    recognizer = _loaded_real_recognizer()
    image = Image.open(ROOT / "tests/fixtures/cw/slots-icon/current-prep.jpg").convert("RGBA")
    expected = {
        ("front", 0): ("银枝", 2),
        ("front", 1): (None, 0),
        ("front", 2): ("星期日", 2),
        ("front", 3): ("灵砂", 2),
        ("back", 0): ("忘归人", 2),
        ("back", 1): (None, 0),
        ("back", 2): ("缇宝", 2),
        ("back", 3): ("那刻夏", 2),
        ("back", 4): (None, 0),
        ("back", 5): ("阮•梅", 2),
        ("hand", 0): ("大丽花", 2),
        ("hand", 1): ("藿藿", 1),
        ("hand", 2): ("大丽花", 1),
        ("hand", 3): ("娜塔莎", 1),
        ("hand", 4): ("希儿", 1),
        ("hand", 5): ("遐蝶", 1),
        ("hand", 6): ("海瑟音", 1),
        ("hand", 7): ("波提欧", 1),
        ("hand", 8): ("藿藿", 1),
    }

    actual = {}
    diagnostics = {}
    for spec in iter_slot_specs():
        result = recognizer.recognize_crop(warp_slot_crop(image, spec), spec.area)
        actual[(spec.area, spec.index)] = (result.name, result.star_count)
        diagnostics[(spec.area, spec.index)] = result.diagnostics

    assert actual == expected, diagnostics
