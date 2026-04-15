from __future__ import annotations

from copy import deepcopy
from typing import Any

from trail.core.errors import TrailError


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
        if request.method == "ocr.read":
            runtime = self._runtime(request)
            return success(
                {"result": runtime.ocr(**request.payload)},
                request_id=request.request_id,
            )

        raise TrailError("DAEMON_METHOD_NOT_SUPPORTED", f"unsupported method: {request.method}")
