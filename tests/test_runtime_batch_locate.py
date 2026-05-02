from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from trail.runtime.batch_locate import BatchLocateTarget, run_batch_locate


@dataclass
class MatcherStub:
    hits: dict[str, dict | None]
    calls: list[tuple[str, tuple[int, int]]] | None = None

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    def locate(self, template: str, image):
        self.calls.append((template, image.size))
        return self.hits.get(template)


class RuntimeStub:
    def __init__(self, *, matcher: MatcherStub):
        self.matcher = matcher
        self.screenshot_calls = 0
        self.debug = []

    def screenshot(self, **kwargs):
        del kwargs
        self.screenshot_calls += 1
        return Image.new("RGB", (1280, 720), "white")

    def _begin_debug_action(self, step: str, **payload):
        self.debug.append(("begin", step, payload))
        return {"step": step}

    def _finish_debug_action(self, action, *, ok: bool, **payload):
        self.debug.append(("finish", action["step"], ok, payload))


def test_run_batch_locate_uses_provided_image_without_screenshot():
    runtime = RuntimeStub(matcher=MatcherStub({"prep.png": {"left": 1, "top": 2, "width": 3, "height": 4}}))
    shared_image = Image.new("RGB", (1920, 1080), "black")

    result = run_batch_locate(
        runtime,
        [BatchLocateTarget("prep", "prep.png"), BatchLocateTarget("shop", "shop.png")],
        image=shared_image,
        trace_prefix="cw_stage_batch_locate",
    )

    assert runtime.screenshot_calls == 0
    assert result.image is shared_image
    assert result.by_key["prep"].found is True
    assert result.by_key["prep"].box == {"left": 1, "top": 2, "width": 3, "height": 4}
    assert result.by_key["shop"].found is False
    assert runtime.matcher.calls == [("prep.png", (1920, 1080)), ("shop.png", (1920, 1080))]


def test_run_batch_locate_captures_once_when_image_not_provided():
    runtime = RuntimeStub(matcher=MatcherStub({}))

    result = run_batch_locate(runtime, [BatchLocateTarget("settle", "settle.png")], trace_prefix="cw_stage_batch_locate")

    assert runtime.screenshot_calls == 1
    assert result.image.size == (1280, 720)
    assert runtime.matcher.calls == [("settle.png", (1280, 720))]


def test_run_batch_locate_emits_stable_debug_shape():
    runtime = RuntimeStub(matcher=MatcherStub({"prep.png": {"left": 1, "top": 2, "width": 3, "height": 4}}))

    run_batch_locate(
        runtime,
        [BatchLocateTarget("prep", "prep.png"), BatchLocateTarget("shop", "shop.png")],
        image=Image.new("RGB", (1920, 1080), "black"),
        trace_prefix="cw_stage_batch_locate",
    )

    assert runtime.debug[0] == (
        "begin",
        "cw_stage_batch_locate",
        {"target_count": 2, "strategy": "sequential", "screenshot": "provided"},
    )
    assert runtime.debug[1] == (
        "finish",
        "cw_stage_batch_locate",
        True,
        {
            "found_count": 1,
            "miss_count": 1,
            "targets": [
                {"key": "prep", "template": "prep.png", "found": True},
                {"key": "shop", "template": "shop.png", "found": False},
            ],
        },
    )


def test_run_batch_locate_rejects_unsupported_strategy_without_screenshot():
    runtime = RuntimeStub(matcher=MatcherStub({}))

    with pytest.raises(ValueError, match="unsupported batch locate strategy"):
        run_batch_locate(runtime, [BatchLocateTarget("prep", "prep.png")], strategy="parallel")

    assert runtime.screenshot_calls == 0
    assert runtime.matcher.calls == []


def test_run_batch_locate_rejects_empty_targets_without_screenshot():
    runtime = RuntimeStub(matcher=MatcherStub({}))

    with pytest.raises(ValueError, match="batch locate requires at least one target"):
        run_batch_locate(runtime, [])

    assert runtime.screenshot_calls == 0
    assert runtime.matcher.calls == []
