from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BatchLocateTarget:
    key: Hashable
    template: str
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchLocateKeyResult:
    key: Hashable
    template: str
    found: bool
    box: Any = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchLocateResult:
    image: Any
    by_key: dict[Hashable, BatchLocateKeyResult]


def run_batch_locate(
    runtime: Any,
    targets: Sequence[BatchLocateTarget],
    *,
    image: Any = None,
    trace_prefix: str = "batch_locate",
    strategy: str = "sequential",
) -> BatchLocateResult:
    target_list = list(targets)
    action = _begin_debug_action(
        runtime,
        trace_prefix,
        target_count=len(target_list),
        strategy=strategy,
        screenshot="provided" if image is not None else "captured",
    )

    try:
        if strategy != "sequential":
            raise ValueError(f"unsupported batch locate strategy: {strategy}")
        if not target_list:
            raise ValueError("batch locate requires at least one target")

        shared_image = image if image is not None else runtime.screenshot()
        by_key: dict[Hashable, BatchLocateKeyResult] = {}
        found_count = 0
        for target in target_list:
            box = runtime.matcher.locate(target.template, shared_image)
            found = box is not None
            if found:
                found_count += 1
            by_key[target.key] = BatchLocateKeyResult(
                key=target.key,
                template=target.template,
                found=found,
                box=box,
                metadata=target.metadata,
            )

        _finish_debug_action(
            runtime,
            action,
            ok=True,
            found_count=found_count,
            miss_count=len(target_list) - found_count,
            targets=[
                {"key": target.key, "template": target.template, "found": by_key[target.key].found}
                for target in target_list
            ],
        )
        return BatchLocateResult(image=shared_image, by_key=by_key)
    except Exception as error:
        _finish_debug_action(runtime, action, ok=False, error_type=type(error).__name__, msg=str(error))
        raise


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
