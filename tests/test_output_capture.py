from trail.output.capture import with_auto_capture


class Runtime:
    def __init__(self):
        self.capture_after_action_calls = 0
        self.reference_screenshots = []

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        self.capture_after_action_calls += 1
        return "unexpected.png"

    def collect_warnings(self):
        return [{"code": "RUNTIME_WARNING", "message": "runtime warning"}]

    def match_references(self, screenshot_path, limit: int = 3):
        self.reference_screenshots.append(str(screenshot_path))
        return [{"path": str(screenshot_path), "similarity": 1.0}]


def test_with_auto_capture_consumes_precaptured_screenshot_success():
    runtime = Runtime()

    response = with_auto_capture(runtime, lambda: {"value": 1, "_screenshot": "slots-reused.png"})

    assert response["ok"] is True
    assert response["screenshot"] == "slots-reused.png"
    assert response["data"] == {"value": 1}
    assert response["warnings"] == [{"code": "RUNTIME_WARNING", "message": "runtime warning"}]
    assert response["references"] == [{"path": "slots-reused.png", "similarity": 1.0}]
    assert runtime.reference_screenshots == ["slots-reused.png"]
    assert runtime.capture_after_action_calls == 0
