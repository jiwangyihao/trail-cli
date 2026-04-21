import json
from pathlib import Path
import threading

import pytest

from trail.artifacts.store import ArtifactStore
from trail.commands.helpers import run_session_command
from trail.core.errors import TrailError
from trail.daemon.command_service import success as daemon_success
from trail.output.capture import set_capture_options, with_auto_capture
from trail.output.envelope import command_failure, command_success
from trail.session.store import SessionStore


@pytest.fixture(autouse=True)
def reset_capture_options():
    set_capture_options(verbose=False)
    yield
    set_capture_options(verbose=False)


def test_failure_envelope_contains_error_code_and_screenshot(tmp_path):
    result = command_failure(
        code="WINDOW_NOT_FOUND",
        message="window missing",
        screenshot=tmp_path / "fail.png",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "WINDOW_NOT_FOUND"
    assert result["screenshot"].endswith("fail.png")
    assert "image_guidance" not in result


def test_command_success_deep_copies_data(tmp_path):
    payload = {"items": ["银狼"]}

    result = command_success(data=payload, screenshot=tmp_path / "ok.png")
    payload["items"].append("卡芙卡")

    assert result["data"] == {"items": ["银狼"]}


def test_command_success_includes_image_guidance_when_screenshot_present():
    result = command_success(data={"done": True}, screenshot=Path("ok.png"))

    assert result["image_guidance"] == {"read_image_first": 1}
    assert "image_guidance" not in result["data"]


def test_command_failure_omits_image_guidance_with_screenshot():
    result = command_failure(
        code="WINDOW_NOT_FOUND",
        message="window missing",
        screenshot=Path("fail.png"),
    )

    assert "image_guidance" not in result


def test_command_success_omits_image_guidance_without_screenshot():
    result = command_success(data={"done": True}, screenshot=None)

    assert "image_guidance" not in result


def test_command_failure_omits_image_guidance_without_screenshot():
    result = command_failure(
        code="WINDOW_NOT_FOUND",
        message="window missing",
        screenshot=None,
    )

    assert "image_guidance" not in result


def test_daemon_success_matches_envelope_guidance_shape():
    result = daemon_success(
        {"done": True},
        request_id="req-guidance",
        screenshot="daemon.png",
    )

    assert result["image_guidance"] == {"read_image_first": 1}


class FakeRuntime:
    def __init__(self, screenshot_path: Path):
        self._shot = screenshot_path
        self.calls: list[bool] = []
        self.warnings: list[dict] = []
        self.references: list[dict] = []
        self.trace: list[dict] = []

    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        return self._shot

    def collect_warnings(self):
        warnings = list(self.warnings)
        self.warnings.clear()
        return warnings

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return list(self.references)

    def consume_debug_trace(self):
        trace = list(self.trace)
        self.trace.clear()
        return trace


class OptionalCaptureFailsRuntime(FakeRuntime):
    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        if optional:
            raise RuntimeError("capture failed")
        return self._shot


class RequiredCaptureFailsRuntime(FakeRuntime):
    def capture_after_action(self, optional: bool = False):
        self.calls.append(optional)
        if not optional:
            raise RuntimeError("capture failed")
        return self._shot


class MetadataRuntime(FakeRuntime):
    def __init__(self, screenshot_path: Path):
        super().__init__(screenshot_path)
        self.warnings = [
            {
                "code": "WINDOW_NOT_FOREGROUND",
                "message": "输入命令执行后窗口不在前台，本次操作可能失败",
            }
        ]
        self.references = [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.97}]
        self.trace = [{"step": "click", "point": [10, 20]}]


class OcrDebugRuntime(MetadataRuntime):
    def __init__(self, screenshot_path: Path):
        super().__init__(screenshot_path)
        self.debug_context = {
            "ocr_mode_requested": "fast",
            "ocr_mode_effective": "high",
            "ocr_scale_applied": "native",
            "ocr_retry_high": 1,
            "ocr_retry_reason": "low_confidence",
        }

    def consume_debug_context(self):
        debug_context = dict(self.debug_context)
        self.debug_context.clear()
        return debug_context


def test_with_auto_capture_wraps_trail_error_as_failure(tmp_path):
    runtime = FakeRuntime(tmp_path / "failed.png")

    result = with_auto_capture(
        runtime,
        lambda: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", "window missing")),
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": "window missing",
    }
    assert result["screenshot"].endswith("failed.png")
    assert runtime.calls == [True]


def test_with_auto_capture_collects_runtime_metadata_and_debug(tmp_path):
    runtime = MetadataRuntime(tmp_path / "ok.png")

    result = with_auto_capture(runtime, lambda: {"done": True}, verbose=True)

    assert result["ok"] is True
    assert result["warnings"] == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    assert result["references"] == [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.97}]
    assert result["debug"] == {"trace": [{"step": "click", "point": [10, 20]}]}


def test_with_auto_capture_omits_debug_without_verbose(tmp_path):
    runtime = MetadataRuntime(tmp_path / "ok.png")

    result = with_auto_capture(runtime, lambda: {"done": True})

    assert result["warnings"] == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    assert result["references"] == [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.97}]
    assert result["debug"] is None


def test_with_auto_capture_keeps_capture_trace_request_local_under_concurrency(tmp_path):
    barrier = threading.Barrier(2)
    payloads: dict[str, dict] = {}

    class RequestScopedRuntime(FakeRuntime):
        def __init__(self, screenshot_path: Path):
            super().__init__(screenshot_path)
            self._local = threading.local()

        def begin_capture_scope(self):
            self._local.scoped = True
            self._local.trace = []

        def end_capture_scope(self):
            self._local.scoped = False

        def capture_after_action(self, optional: bool = False):
            self.calls.append(optional)
            trace = {"step": "capture_after_action", "thread": threading.current_thread().name}
            if getattr(self._local, "scoped", False):
                self._local.trace.append(trace)
            else:
                self.trace.append(trace)
            barrier.wait()
            return tmp_path / f"{threading.current_thread().name}.png"

        def consume_debug_trace(self):
            if getattr(self._local, "scoped", False):
                trace = list(getattr(self._local, "trace", []))
                self._local.trace = []
                return trace
            trace = list(self.trace)
            self.trace.clear()
            return trace

    runtime = RequestScopedRuntime(tmp_path / "shared.png")

    def worker(name: str):
        payloads[name] = with_auto_capture(runtime, lambda: {"thread": name}, verbose=True)

    left = threading.Thread(target=worker, name="left", args=("left",))
    right = threading.Thread(target=worker, name="right", args=("right",))
    left.start()
    right.start()
    left.join(timeout=2)
    right.join(timeout=2)

    assert payloads["left"]["debug"] == {"trace": [{"step": "capture_after_action", "thread": "left"}]}
    assert payloads["right"]["debug"] == {"trace": [{"step": "capture_after_action", "thread": "right"}]}


def test_with_auto_capture_promotes_ocr_context_keys_to_top_level_debug(tmp_path):
    runtime = OcrDebugRuntime(tmp_path / "ocr-debug.png")

    result = with_auto_capture(runtime, lambda: {"done": True}, verbose=True)

    assert result["debug"] == {
        "trace": [{"step": "click", "point": [10, 20]}],
        "ocr_mode_requested": "fast",
        "ocr_mode_effective": "high",
        "ocr_scale_applied": "native",
        "ocr_retry_high": 1,
        "ocr_retry_reason": "low_confidence",
    }


def test_with_auto_capture_ocr_context_allowlist_ignores_reserved_and_non_ocr_keys(tmp_path):
    runtime = OcrDebugRuntime(tmp_path / "ocr-debug-allowlist.png")
    runtime.debug_context.update(
        {
            "trace": [{"step": "override"}],
            "request_id": "req-from-context",
            "detail": "context detail",
            "unexpected": "ignored",
        }
    )

    result = with_auto_capture(runtime, lambda: {"done": True}, verbose=True)

    assert result["debug"] == {
        "trace": [{"step": "click", "point": [10, 20]}],
        "ocr_mode_requested": "fast",
        "ocr_mode_effective": "high",
        "ocr_scale_applied": "native",
        "ocr_retry_high": 1,
        "ocr_retry_reason": "low_confidence",
    }


def test_session_store_persists_relative_last_screenshot(tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.last_screenshot = str(tmp_path / ".trail" / "shots" / "req-123.png")

    store.save(session)

    payload = json.loads((tmp_path / ".trail" / "sessions" / f"{session.session_id}.json").read_text(encoding="utf-8"))
    loaded = store.load(session.session_id)

    assert payload["workspace"] == ".trail/sessions"
    assert payload["last_screenshot"] == ".trail/shots/req-123.png"
    assert loaded.workspace == tmp_path / ".trail" / "sessions"
    assert loaded.last_screenshot == ".trail/shots/req-123.png"


def test_session_store_load_rebinds_legacy_absolute_workspace_and_screenshot_to_current_store(tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    path = tmp_path / ".trail" / "sessions" / f"{session.session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["workspace"] = str(tmp_path / "legacy-workspace" / ".trail" / "sessions")
    payload["last_screenshot"] = str(tmp_path / "legacy-workspace" / ".trail" / "shots" / "legacy.png")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = store.load(session.session_id)

    assert loaded.workspace == store.workspace
    assert loaded.last_screenshot == ".trail/shots/legacy.png"


def test_artifact_store_persists_relative_path_fields(tmp_path):
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")

    artifact = store.create(
        scene="cw",
        kind="guide",
        payload={
            "path": str(tmp_path / ".trail" / "artifacts" / "guide.json"),
            "screenshot": str(tmp_path / ".trail" / "shots" / "req-123.png"),
            "references": [
                {
                    "path": str(tmp_path / "trail" / "scenes" / "cw" / "references" / "1.png"),
                    "screenshot": str(tmp_path / ".trail" / "shots" / "req-123.png"),
                }
            ],
        },
    )

    payload = json.loads(artifact.path.read_text(encoding="utf-8"))

    assert payload["path"] == ".trail/artifacts/guide.json"
    assert payload["screenshot"] == ".trail/shots/req-123.png"
    assert payload["references"] == [
        {
            "path": "trail/scenes/cw/references/1.png",
            "screenshot": ".trail/shots/req-123.png",
        }
    ]


def test_run_session_command_persists_last_result_and_last_screenshot(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after.png"),
        command_name="session.inspect",
        action=lambda loaded: {"session_id": loaded.session_id},
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is True
    assert loaded.last_result["command"] == "session.inspect"
    assert loaded.last_screenshot.endswith("after.png")


def test_run_session_command_persists_failure_result_and_last_screenshot(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after-fail.png"),
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", loaded.session_id)),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {"code": "WINDOW_NOT_FOUND", "message": session.session_id},
    }
    assert loaded.last_screenshot.endswith("after-fail.png")


def test_run_session_command_does_not_persist_partial_state_on_failure(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state = {"cw": {"coins": 5}}
    session.last_stage = {"scene": "cw", "value": "shop"}
    store.save(session)

    def fail_after_mutation(loaded):
        loaded.scene_state["cw"]["coins"] = 99
        loaded.last_stage = {"scene": "cw", "value": "battle"}
        raise TrailError("WINDOW_NOT_FOUND", loaded.session_id)

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after-fail.png"),
        command_name="session.inspect",
        action=fail_after_mutation,
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert loaded.scene_state == {"cw": {"coins": 5}}
    assert loaded.last_stage == {"scene": "cw", "value": "shop"}


def test_run_session_command_can_persist_safe_failure_state_when_enabled(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state = {"cw": {"stage": {"value": "event", "stale": False}}}
    session.last_stage = {"scene": "cw", "value": "event"}
    store.save(session)

    def fail_after_invalidating_stage(loaded):
        loaded.scene_state["cw"]["stage"] = {
            "stale": True,
            "error": {
                "code": "STAGE_AMBIGUOUS",
                "message": "当前资源无法区分阶段: shop, replenish",
            },
        }
        loaded.last_stage = None
        raise TrailError("STAGE_AMBIGUOUS", "当前资源无法区分阶段: shop, replenish")

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=FakeRuntime(tmp_path / "after-fail.png"),
        command_name="cw.stage.detect",
        action=fail_after_invalidating_stage,
        failure_persistence=lambda loaded, failure: failure["error"] == {
            "code": "STAGE_AMBIGUOUS",
            "message": "当前资源无法区分阶段: shop, replenish",
        }
        and loaded.scene_state["cw"]["stage"]["stale"] is True,
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert loaded.scene_state["cw"]["stage"] == {
        "stale": True,
        "error": {
            "code": "STAGE_AMBIGUOUS",
            "message": "当前资源无法区分阶段: shop, replenish",
        },
    }
    assert loaded.last_stage is None


def test_run_session_command_wraps_unexpected_exception_and_persists_failure(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = FakeRuntime(tmp_path / "after-crash.png")

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(RuntimeError(f"boom:{loaded.session_id}")),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert result["error"] == {
        "code": "UNEXPECTED_ERROR",
        "message": f"RuntimeError: boom:{session.session_id}",
    }
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {
            "code": "UNEXPECTED_ERROR",
            "message": f"RuntimeError: boom:{session.session_id}",
        },
    }
    assert loaded.last_screenshot.endswith("after-crash.png")
    assert runtime.calls == [True]


def test_run_session_command_keeps_success_when_required_capture_fails(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = RequiredCaptureFailsRuntime(tmp_path / "after-success.png")

    def succeed_and_mutate(loaded):
        loaded.scene_state["cw"] = {"coins": 12}
        return {"session_id": loaded.session_id}

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=succeed_and_mutate,
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is True
    assert result["error"] is None
    assert result["screenshot"] is None
    assert loaded.scene_state == {"cw": {"coins": 12}}
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": True,
        "data": {"session_id": session.session_id},
        "error": None,
    }
    assert loaded.last_screenshot is None
    assert runtime.calls == [False]


def test_run_session_command_persists_failure_when_optional_capture_also_fails(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    runtime = OptionalCaptureFailsRuntime(tmp_path / "after-fail.png")

    result = run_session_command(
        store=store,
        session_id=session.session_id,
        runtime=runtime,
        command_name="session.inspect",
        action=lambda loaded: (_ for _ in ()).throw(TrailError("WINDOW_NOT_FOUND", loaded.session_id)),
    )
    loaded = store.load(session.session_id)

    assert result["ok"] is False
    assert result["error"] == {
        "code": "WINDOW_NOT_FOUND",
        "message": session.session_id,
    }
    assert result["screenshot"] is None
    assert loaded.last_result == {
        "command": "session.inspect",
        "ok": False,
        "data": {},
        "error": {"code": "WINDOW_NOT_FOUND", "message": session.session_id},
    }
    assert loaded.last_screenshot is None
    assert runtime.calls == [True]
