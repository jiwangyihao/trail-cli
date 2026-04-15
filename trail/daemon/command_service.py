from __future__ import annotations

from copy import deepcopy
from typing import Any

from trail.commands.helpers import to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture


def success(
    data: dict[str, Any],
    *,
    request_id: str | None = None,
    screenshot: str | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ok": True,
        "data": deepcopy(data),
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": deepcopy(references or []),
        "debug": deepcopy(debug),
        "error": None,
    }


class CommandService:
    def __init__(self, *, runtime_service, session_service=None, cw_service=None):
        self.runtime_service = runtime_service
        self.session_service = session_service
        self.cw_service = cw_service

    def _runtime(self, request):
        return self.runtime_service.get_runtime(
            workspace_root=request.workspace_root,
            window_binding=None,
        )

    def handle(self, request):
        if request.method == "daemon.ping":
            return success(
                {"alive": True},
                request_id=request.request_id,
            )

        if request.method == "ocr.read":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._read_ocr(runtime, request.payload),
            )

        if request.method == "window.attach":
            runtime_holder = {"runtime": None}

            def action():
                binding = to_jsonable(self.runtime_service.attach_window(window_title=request.payload["window_title"]))
                runtime_holder["runtime"] = self.runtime_service.get_runtime(
                    workspace_root=request.workspace_root,
                    window_binding=binding,
                )
                return binding

            return self._capture_response(request, lambda: runtime_holder["runtime"], action)

        if request.method == "window.launch":
            return self._capture_response(
                request,
                None,
                lambda: to_jsonable(self.runtime_service.launch_game(**request.payload)),
            )

        if request.method == "screen.shot":
            runtime = self._runtime(request)
            return self._capture_response(request, runtime, lambda: {"captured": True})

        if request.method == "image.locate":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._locate_image(runtime, request.payload["template"]),
            )

        if request.method == "image.wait":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._wait_image(
                    runtime,
                    request.payload["template"],
                    timeout=request.payload.get("timeout", 10),
                ),
            )

        if request.method == "input.click":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._click(runtime, x=request.payload["x"], y=request.payload["y"]),
            )

        if request.method == "input.drag":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._drag(
                    runtime,
                    from_x=request.payload["from_x"],
                    from_y=request.payload["from_y"],
                    to_x=request.payload["to_x"],
                    to_y=request.payload["to_y"],
                ),
            )

        if request.method == "input.key":
            runtime = self._runtime(request)
            return self._capture_response(
                request,
                runtime,
                lambda: self._press_key(
                    runtime,
                    key=request.payload["key"],
                    presses=request.payload.get("presses", 1),
                ),
            )

        raise TrailError("DAEMON_METHOD_NOT_SUPPORTED", f"unsupported method: {request.method}")

    def _capture_response(self, request, runtime, action):
        response = with_auto_capture(runtime, action, verbose=request.verbose)
        response["request_id"] = request.request_id
        return response

    def _read_ocr(self, runtime, payload: dict[str, Any]) -> dict[str, Any]:
        result = runtime.ocr(**payload)
        if not result:
            raise TrailError("OCR_NO_RESULT", "OCR 无结果")
        return {"result": to_jsonable(result)}

    def _locate_image(self, runtime, template: str) -> dict[str, Any]:
        box = runtime.locate(template)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    def _wait_image(self, runtime, template: str, *, timeout: int) -> dict[str, Any]:
        box = runtime.wait_img(template, timeout=timeout)
        if box is None:
            raise TrailError("IMAGE_NOT_FOUND", f"未找到 {template}")
        return {"box": to_jsonable(box)}

    def _click(self, runtime, *, x: int, y: int) -> dict[str, Any]:
        runtime.click_point(x, y)
        return {"clicked": [x, y]}

    def _drag(self, runtime, *, from_x: int, from_y: int, to_x: int, to_y: int) -> dict[str, Any]:
        runtime.drag_to(from_x, from_y, to_x, to_y)
        return {"dragged": [from_x, from_y, to_x, to_y]}

    def _press_key(self, runtime, *, key: str, presses: int) -> dict[str, Any]:
        runtime.press_key(key, presses=presses)
        return {"key": key, "presses": presses}
