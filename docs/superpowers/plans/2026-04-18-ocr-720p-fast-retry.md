# OCR 720p Fast Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 OCR 增加 `fast=1280x720` / `high=1.0x` 两档模式与保守自动高精度重试，同时保持默认文本协议不扩张。

**Architecture:** 在现有 `provider=auto|cpu|dml` 体系之外，再增加一层“预处理与重试控制层”。CLI/环境变量负责解析 `ocr_mode` 与 `retry_high`，runtime 在拿到截图后先决定是否 downscale，再按规则执行一次或两次 OCR，并把最终结果与 request-local debug 事实收口到当前输出链路。

**Tech Stack:** Python 3.12、Typer、daemon RPC、RapidOCR/ONNX Runtime、现有 `trail.output.debug` / `trail.output.rendering` 协议、pytest。

---

## File Map

- Modify: `trail/runtime/ocr_config.py`
  责任：新增 `ocr_mode` / `retry_high` 的常量、环境变量、默认值、payload allowlist 与归一化逻辑。
- Modify: `trail/commands/ocr.py`
  责任：新增 CLI 参数与 help，解析 env 默认值并生成 daemon payload。
- Modify: `trail/runtime/operator.py`
  责任：实现 `fast=1280x720` downscale、`retry_high=auto|never|always`、debug facts、重试后结果收口与 warning 语义。
- Modify: `trail/output/capture.py`
  责任：确保 request-local debug/warning 汇聚能携带新的模式与重试事实。
- Modify: `README.md`
  责任：补充 `ocr_mode` / `retry_high`、Windows DirectML profile 与默认行为说明。
- Modify: `tests/test_ocr_config.py`
  责任：覆盖新配置面、环境变量、非法枚举与优先级。
- Modify: `tests/test_atomic_commands.py`
  责任：覆盖 CLI 参数、help 文案、env 默认值、payload 透传。
- Modify: `tests/test_daemon_protocol.py`
  责任：覆盖 daemon 层对 `ocr_mode` / `retry_high` 新 payload 字段的接收与 request_id/debug 兼容性。
- Modify: `tests/test_runtime_backends.py`
  责任：覆盖 downscale、region OCR 不 upscale、自动重试、最终结果选择、debug facts、box 稳定性相关行为。
- Create: `tests/fixtures/ocr/fast-sparse-login.jpg`
  责任：固定文本稀疏页质量回归样本。
- Create: `tests/fixtures/ocr/fast-dense-notice.jpg`
  责任：固定文本密集页质量回归样本。
- Modify: `tests/test_output_debug.py`
  责任：覆盖 `ocr_mode_requested` / `ocr_mode_effective` / `ocr_scale_applied` / `ocr_retry_high` / `ocr_retry_reason` 全部通过现有 debug 管线追加。
- Modify: `tests/test_output_rendering.py`
  责任：覆盖默认文本协议不泄漏新字段、`ocr.read --format yaml` 不变。

## Baseline Note

- 当前完整测试命令应统一使用 `--basetemp .trail/pytest-tmp/...`，避免系统临时目录权限噪声。
- 当前仓库已合入上一轮 OCR provider/DirectML 配置化实现，完整测试基线为 `464 passed`。
- 未经用户明确要求，不创建 git commit。

### Task 1: 固定 `ocr_mode` / `retry_high` 配置面

**Files:**
- Modify: `trail/runtime/ocr_config.py`
- Modify: `trail/commands/ocr.py`
- Modify: `tests/test_ocr_config.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，冻结新配置字段与 env/CLI 优先级**

```python
def test_resolve_ocr_request_config_supports_mode_and_retry_defaults(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_MODE", "fast")
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "auto")

    cfg = resolve_ocr_request_config()

    assert cfg.ocr_mode == "fast"
    assert cfg.retry_high == "auto"


def test_resolve_ocr_request_config_rejects_invalid_retry_mode():
    with pytest.raises(ValueError, match="invalid ocr retry_high"):
        resolve_ocr_request_config(retry_high="sometimes", use_env_defaults=False)
```

- [ ] **Step 2: 跑测试，确认当前红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-red-config tests/test_ocr_config.py -k "ocr_mode or retry_high" -v`
Expected: FAIL，因为当前 `OcrRequestConfig` 还没有 `ocr_mode` / `retry_high`。

- [ ] **Step 3: 最小实现配置常量、字段与归一化逻辑**

```python
DEFAULT_OCR_MODE = "fast"
DEFAULT_OCR_RETRY_HIGH = "auto"
TRAIL_OCR_MODE = "TRAIL_OCR_MODE"
TRAIL_OCR_RETRY_HIGH = "TRAIL_OCR_RETRY_HIGH"
SUPPORTED_OCR_MODES = {"fast", "high"}
SUPPORTED_OCR_RETRY_HIGH = {"auto", "never", "always"}

@dataclass(frozen=True)
class OcrRequestConfig:
    provider: str = DEFAULT_OCR_PROVIDER
    lang: str = DEFAULT_OCR_LANG
    use_cls: bool = DEFAULT_OCR_USE_CLS
    text_score: float = DEFAULT_OCR_TEXT_SCORE
    ocr_mode: str = DEFAULT_OCR_MODE
    retry_high: str = DEFAULT_OCR_RETRY_HIGH
```

- [ ] **Step 4: 扩 CLI 参数与 help**

```python
def ocr_read(
    provider: str | None = typer.Option(None, "--provider"),
    lang: str | None = typer.Option(None, "--lang", help="OCR 语言；首版仅支持 ch"),
    use_cls: bool | None = typer.Option(None, "--use-cls/--no-use-cls"),
    text_score: float | None = typer.Option(None, "--text-score"),
    ocr_mode: str | None = typer.Option(None, "--ocr-mode", help="OCR 模式：fast=1280x720，high=native"),
    retry_high: str | None = typer.Option(None, "--retry-high", help="高精度重试策略：auto|never|always"),
) -> None:
    cfg = resolve_ocr_request_config(
        provider=provider,
        lang=lang,
        use_cls=use_cls,
        text_score=text_score,
        ocr_mode=ocr_mode,
        retry_high=retry_high,
    )
    print_output("ocr.read", call_daemon("ocr.read", cfg.to_payload()))
```

- [ ] **Step 5: 跑 CLI/配置测试转绿**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-green-config tests/test_ocr_config.py tests/test_atomic_commands.py tests/test_daemon_protocol.py -k "ocr_mode or retry_high or ocr_read_help or protocol_request" -v`
Expected: PASS，CLI/help/env/payload 都包含新字段，非法值会在 daemon 调用前失败，daemon 协议层也能看到新字段。

### Task 2: 实现 `fast=1280x720` downscale 与 region 不 upscale 规则

**Files:**
- Modify: `trail/runtime/operator.py`
- Modify: `tests/test_runtime_backends.py`

- [ ] **Step 1: 先写失败测试，冻结 fast/high 模式对截图尺寸的语义**

```python
def test_runtime_operator_fast_mode_downscales_fullscreen_to_1280x720(tmp_path):
    image = Image.new("RGB", (1920, 1080), color="white")
    buffer = BytesIO(); image.save(buffer, format="PNG")
    seen_sizes = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            seen_sizes.append(image.size)
            return OcrRunResult(pieces=[], warnings=[], trace=[])

    runtime = RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: buffer.getvalue(), capture_to_workspace=lambda request_id=None: tmp_path / "shot.png"),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(click=lambda *a, **k: None, drag=lambda *a, **k: None, press=lambda *a, **k: None, hotkey=lambda *a, **k: None, type_text=lambda *a, **k: None),
    )

    runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="never"))

    assert seen_sizes == [(1280, 720)]


def test_runtime_operator_fast_mode_does_not_upscale_small_region(tmp_path):
    image = Image.new("RGB", (400, 120), color="white")
    buffer = BytesIO(); image.save(buffer, format="PNG")
    seen_sizes = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            seen_sizes.append(image.size)
            return OcrRunResult(pieces=[], warnings=[], trace=[])

    runtime = RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: buffer.getvalue(), capture_to_workspace=lambda request_id=None: tmp_path / "shot.png"),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(click=lambda *a, **k: None, drag=lambda *a, **k: None, press=lambda *a, **k: None, hotkey=lambda *a, **k: None, type_text=lambda *a, **k: None),
    )

    runtime.ocr(capture={"from_x": 10, "to_x": 100}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="never"))

    assert seen_sizes == [(400, 120)]
```

- [ ] **Step 2: 跑测试，确认当前红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-red-scale tests/test_runtime_backends.py -k "fast_mode_downscales or does_not_upscale" -v`
Expected: FAIL，因为当前 runtime 还没有按 `ocr_mode` 改写输入图尺寸。

- [ ] **Step 3: 最小实现缩放控制层**

```python
def _prepare_ocr_image(image: Image.Image, *, ocr: OcrRequestConfig) -> tuple[Image.Image, str]:
    if ocr.ocr_mode != "fast":
        return image, "native"
    if image.width <= 1280 and image.height <= 720:
        return image, "native"
    scaled = image.copy()
    scaled.thumbnail((1280, 720))
    return scaled, "1280x720"
```

- [ ] **Step 4: 跑尺寸语义测试转绿**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-green-scale tests/test_runtime_backends.py -k "fast_mode_downscales or does_not_upscale" -v`
Expected: PASS，full-screen 会 downscale，局部小图不会被放大。

- [ ] **Step 5: 固定质量回归样本与 box 验收线**

```python
def test_fast_mode_quality_gate_on_dense_notice_fixture():
    fixture = Path("tests/fixtures/ocr/fast-dense-notice.jpg")
    metrics = benchmark_ocr_mode_against_native(fixture, ocr_mode="fast")

    assert metrics.anchor_delta >= -1
    assert metrics.median_center_shift_px <= 2.0
    assert metrics.median_iou >= 0.85


def test_fast_mode_quality_gate_on_sparse_login_fixture():
    fixture = Path("tests/fixtures/ocr/fast-sparse-login.jpg")
    metrics = benchmark_ocr_mode_against_native(fixture, ocr_mode="fast")

    assert metrics.anchor_delta >= -1
    assert metrics.median_center_shift_px <= 2.0
    assert metrics.median_iou >= 0.85


def benchmark_ocr_mode_against_native(fixture: Path, *, ocr_mode: str):
    native = run_fixture_ocr(fixture, ocr_mode="high")
    variant = run_fixture_ocr(fixture, ocr_mode=ocr_mode)
    return compare_anchor_metrics(native, variant)


def run_fixture_ocr(fixture: Path, *, ocr_mode: str):
    image = Image.open(fixture).convert("RGB")
    adapter = RapidOcrAdapter()
    result = adapter.run(image, ocr=OcrRequestConfig(ocr_mode=ocr_mode, retry_high="never"))
    return result.pieces


def compare_anchor_metrics(native_pieces, variant_pieces):
    return compute_anchor_box_metrics(native_pieces, variant_pieces)


from dataclasses import dataclass


def compute_anchor_box_metrics(native_pieces, variant_pieces):
    @dataclass(frozen=True)
    class QualityMetrics:
        anchor_delta: int
        median_center_shift_px: float
        median_iou: float

    from statistics import median

    def compute_box_center_shift(left, right):
        left_center = (left["left"] + left["width"] / 2, left["top"] + left["height"] / 2)
        right_center = (right["left"] + right["width"] / 2, right["top"] + right["height"] / 2)
        return ((left_center[0] - right_center[0]) ** 2 + (left_center[1] - right_center[1]) ** 2) ** 0.5

    def compute_box_iou(left, right):
        inter_left = max(left["left"], right["left"])
        inter_top = max(left["top"], right["top"])
        inter_right = min(left["left"] + left["width"], right["left"] + right["width"])
        inter_bottom = min(left["top"] + left["height"], right["top"] + right["height"])
        if inter_right <= inter_left or inter_bottom <= inter_top:
            return 0.0
        inter_area = (inter_right - inter_left) * (inter_bottom - inter_top)
        left_area = left["width"] * left["height"]
        right_area = right["width"] * right["height"]
        return inter_area / float(left_area + right_area - inter_area)

    native_map = {item["text"]: item["box"] for item in native_pieces if isinstance(item, dict) and item.get("text") and item.get("box")}
    variant_map = {item["text"]: item["box"] for item in variant_pieces if isinstance(item, dict) and item.get("text") and item.get("box")}
    shared = sorted(set(native_map) & set(variant_map))
    center_shifts = [compute_box_center_shift(native_map[text], variant_map[text]) for text in shared]
    ious = [compute_box_iou(native_map[text], variant_map[text]) for text in shared]
    return QualityMetrics(
        anchor_delta=len(variant_map) - len(native_map),
        median_center_shift_px=median(center_shifts) if center_shifts else 999.0,
        median_iou=median(ious) if ious else 0.0,
    )
```

- [ ] **Step 5a: 先把已验证截图固定成测试 fixture**

Run:
- `Copy-Item "C:\Users\34404\source\repos\trail-cli\.trail\shots\534963d1d67d494399f35eb9d1c87d9d-866e37c2e2.jpg" "tests/fixtures/ocr/fast-dense-notice.jpg"`
- `Copy-Item "C:\Users\34404\source\repos\trail-cli\.trail\shots\cd7c57d779b146cb9f39a590fbf260ae-7cd9d12613.jpg" "tests/fixtures/ocr/fast-sparse-login.jpg"`

Expected: 两个固定样本存在，后续实现与 review 不再依赖临时截图路径。

Note: 这一步完成后，fixture 文件应直接进入仓库树；后续 fresh worktree 直接读取 `tests/fixtures/ocr/*.jpg`，不再依赖 `.trail/shots/...`。

- [ ] **Step 6: 跑质量样本红灯再转绿**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-green-quality tests/test_runtime_backends.py -k "quality_gate_on_dense_notice or quality_gate_on_sparse_login" -v`
Expected: PASS，两张固定样本都能守住 spec 里的锚点/漂移/IoU 验收线。

### Task 3: 实现 `retry_high=auto|never|always` 与最终结果收口

**Files:**
- Modify: `trail/runtime/operator.py`
- Modify: `trail/output/capture.py`
- Modify: `tests/test_runtime_backends.py`
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_output_envelope.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先在 `tests/test_runtime_backends.py` 内定义本任务专用 helper**

```python
class RecordingEngine:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, image, *, ocr=None):
        self.calls.append({"size": image.size, "ocr": ocr})
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def build_runtime_with_recording_engine(engine):
    image = Image.new("RGB", (1920, 1080), color="white")
    buffer = BytesIO(); image.save(buffer, format="PNG")
    return RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: buffer.getvalue(), capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=engine,
        input_driver=SimpleNamespace(click=lambda *a, **k: None, drag=lambda *a, **k: None, press=lambda *a, **k: None, hotkey=lambda *a, **k: None, type_text=lambda *a, **k: None),
    )


def build_runtime_with_bytes(image_bytes: bytes, engine):
    return RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: image_bytes, capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=engine,
        input_driver=SimpleNamespace(click=lambda *a, **k: None, drag=lambda *a, **k: None, press=lambda *a, **k: None, hotkey=lambda *a, **k: None, type_text=lambda *a, **k: None),
    )
```

- [ ] **Step 2: 先写失败测试，冻结 `OCR_LOW_CONFIDENCE` 生产逻辑**

```python
def test_runtime_operator_marks_low_confidence_warning_before_auto_retry():
    engine = RecordingEngine([
        OcrRunResult(pieces=[{"text": "模糊", "score": 0.3}], warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="never"))

    assert runtime.collect_warnings() == [{"code": "OCR_LOW_CONFIDENCE", "message": "ocr confidence below retry threshold"}]
```

- [ ] **Step 3: 再写失败测试，冻结三种重试模式和最终结果语义**

```python
def test_runtime_operator_retry_high_always_returns_high_result():
    engine = RecordingEngine([
        OcrRunResult(pieces=[{"text": "fast"}], warnings=[], trace=[]),
        OcrRunResult(pieces=[{"text": "high"}], warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="always"))

    assert result == [{"text": "high"}]


def test_runtime_operator_retry_high_auto_keeps_fast_result_when_high_retry_fails():
    engine = RecordingEngine([
        OcrRunResult(pieces=[{"text": "fast-ok", "score": 0.1}], warnings=[], trace=[]),
        OcrRunFailure("OCR_BACKEND_UNAVAILABLE", "retry failed", warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto"))

    assert result == [{"text": "fast-ok", "score": 0.1}]


def test_command_service_returns_ocr_no_result_when_fast_has_no_hits_and_high_also_fails(tmp_path):
    runtime = _CommandRuntimeStub()
    runtime.ocr_result = []
    service = CommandService(runtime_service=_CommandRuntimeServiceStub(runtime))

    payload = service.handle(
        _command_request(workspace_root=tmp_path, method="ocr.read", payload={"ocr_mode": "fast", "retry_high": "auto"})
    )

    assert payload["ok"] is False
    assert payload["error"] == {"code": "OCR_NO_RESULT", "message": "OCR 无结果"}


def test_runtime_operator_returns_empty_result_when_fast_has_no_hits_and_high_also_fails():
    engine = RecordingEngine([
        OcrRunResult(pieces=[], warnings=[], trace=[]),
        OcrRunFailure("OCR_BACKEND_UNAVAILABLE", "retry failed", warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto"))

    assert result == []


def test_runtime_operator_high_mode_never_retries_even_when_retry_high_is_always():
    engine = RecordingEngine([
        OcrRunResult(pieces=[{"text": "native"}], warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="high", retry_high="always"))

    assert result == [{"text": "native"}]
    assert len(engine.calls) == 1


def test_runtime_operator_retry_reason_priority_prefers_no_hits_over_warning():
    engine = RecordingEngine([
        OcrRunResult(pieces=[], warnings=[{"code": "OCR_LOW_CONFIDENCE", "message": "low"}], trace=[]),
        OcrRunResult(pieces=[{"text": "high"}], warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    runtime.begin_capture_scope()
    try:
        runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto"))
        trace = runtime.consume_debug_trace()
    finally:
        runtime.end_capture_scope()

    assert any(item.get("ocr_retry_reason") == "no_hits" for item in trace if isinstance(item, dict))


def test_runtime_operator_high_success_suppresses_fast_warning_from_default_output_path():
    engine = RecordingEngine([
        OcrRunResult(pieces=[{"text": "fast", "score": 0.1}], warnings=[{"code": "OCR_LOW_CONFIDENCE", "message": "low"}], trace=[]),
        OcrRunResult(pieces=[{"text": "high", "score": 0.99}], warnings=[], trace=[]),
    ])
    runtime = build_runtime_with_recording_engine(engine)

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto"))

    assert result == [{"text": "high", "score": 0.99}]
    assert runtime.collect_warnings() == []
```

- [ ] **Step 4: 先跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-red-retry tests/test_runtime_backends.py tests/test_daemon_protocol.py -k "retry_high or OCR_LOW_CONFIDENCE or retry_reason_priority or suppresses_fast_warning or returns_empty_result_when_fast_has_no_hits or returns_ocr_no_result_when_fast_has_no_hits" -v`
Expected: FAIL，因为当前没有完整的重试控制层、低置信 warning 生产与 warning 抑制语义。

- [ ] **Step 5: 最小实现重试决策、低置信 warning 与 debug facts**

```python
def _should_retry_high(result: OcrRunResult, *, ocr: OcrRequestConfig) -> tuple[bool, str]:
    if ocr.ocr_mode != "fast":
        return False, "none"
    if ocr.retry_high == "never":
        return False, "none"
    if ocr.retry_high == "always":
        return True, "none"
    if not result.pieces:
        return True, "no_hits"
    scores = [item.get("score") for item in result.pieces if isinstance(item, dict) and item.get("score") is not None]
    if scores and sum(scores) / len(scores) < 0.92:
        return True, "low_confidence"
    if any(w.get("code") == "OCR_LOW_CONFIDENCE" for w in result.warnings):
        return True, "warning"
    return False, "none"


def _maybe_append_low_confidence_warning(result: OcrRunResult) -> OcrRunResult:
    scores = [item.get("score") for item in result.pieces if isinstance(item, dict) and item.get("score") is not None]
    if scores and sum(scores) / len(scores) < 0.92:
        return OcrRunResult(
            pieces=result.pieces,
            warnings=[*result.warnings, {"code": "OCR_LOW_CONFIDENCE", "message": "ocr confidence below retry threshold"}],
            trace=result.trace,
        )
    return result


def _collect_ocr_debug_context(*, requested_mode: str, effective_mode: str, scale_applied: str, retried: bool, retry_reason: str) -> dict[str, Any]:
    return {
        "ocr_mode_requested": requested_mode,
        "ocr_mode_effective": effective_mode,
        "ocr_scale_applied": scale_applied,
        "ocr_retry_high": int(retried),
        "ocr_retry_reason": retry_reason,
    }


def consume_debug_context(self) -> dict[str, Any]:
    context = dict(self._request_local.debug_context)
    self._request_local.debug_context = {}
    return context
```

- [ ] **Step 5a: 先写 debug context 汇聚的红灯测试**

```python
def test_with_auto_capture_promotes_ocr_context_keys_to_top_level_debug():
    runtime = SimpleNamespace(
        begin_capture_scope=lambda: None,
        end_capture_scope=lambda: None,
        collect_warnings=lambda: [],
        consume_debug_trace=lambda: [],
        consume_debug_context=lambda: {
            "ocr_mode_requested": "fast",
            "ocr_mode_effective": "high",
            "ocr_scale_applied": "1280x720",
            "ocr_retry_high": 1,
            "ocr_retry_reason": "low_confidence",
        },
        capture_after_action=lambda optional=False, request_id=None: None,
        match_references=lambda screenshot_path, limit=3: [],
    )
    payload = with_auto_capture(
        runtime,
        lambda: {"result": [{"text": "点击进入", "box": {"left": 1, "top": 2, "width": 3, "height": 4}}]},
        verbose=True,
    )
    assert payload["debug"]["ocr_mode_requested"] == "fast"
    assert payload["debug"]["ocr_mode_effective"] == "high"
    assert payload["debug"]["ocr_scale_applied"] == "1280x720"
    assert payload["debug"]["ocr_retry_high"] == 1
    assert payload["debug"]["ocr_retry_reason"] == "low_confidence"
    lines = render_output("ocr.read", payload, verbose=True).splitlines()

    assert any("key=ocr_mode_requested" in line for line in lines)
    assert any("key=ocr_mode_effective" in line for line in lines)
    assert any("key=ocr_scale_applied" in line for line in lines)
    assert any("key=ocr_retry_high" in line for line in lines)
    assert any("key=ocr_retry_reason" in line for line in lines)
```

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-red-debug-context tests/test_output_envelope.py -k "promotes_ocr_context_keys_to_top_level_debug" -v`
Expected: FAIL，因为当前 `with_auto_capture` 还不会把 `consume_debug_context()` 合并到顶层 `debug`。

- [ ] **Step 5b: 最小修改 `trail/output/capture.py`，把 `consume_debug_context()` 合并到顶层 `debug`**

```python
def _collect_capture_metadata(runtime, *, screenshot, verbose):
    trace = runtime.consume_debug_trace() if hasattr(runtime, "consume_debug_trace") else []
    context = runtime.consume_debug_context() if hasattr(runtime, "consume_debug_context") else {}
    debug = None
    if verbose and (trace or context):
        debug = {**context}
        if trace:
            debug["trace"] = trace
    references = runtime.match_references(screenshot) if screenshot and hasattr(runtime, "match_references") else []
    return {"warnings": runtime.collect_warnings(), "references": references, "debug": debug}
```

- [ ] **Step 6: 覆盖 debug / envelope / rendering 汇聚**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-green-retry tests/test_runtime_backends.py tests/test_daemon_protocol.py tests/test_output_debug.py tests/test_output_envelope.py tests/test_output_rendering.py -k "retry_high or ocr_mode or ocr_scale_applied or ocr_mode_effective or ocr_retry_reason or OCR_LOW_CONFIDENCE or promotes_ocr_context_keys_to_top_level_debug or returns_empty_result_when_fast_has_no_hits or returns_ocr_no_result_when_fast_has_no_hits" -v`
Expected: PASS，`retry_high` 三种模式固定，低置信 warning 会被生产，5 个 verbose-only key 全部通过现有 debug 管线落出，默认输出不泄漏中间 warning 与模式事实。

- [ ] **Step 7: 同步更新现有 raw-bytes 基线测试**

```python
def test_runtime_operator_existing_bytes_contract_still_works_in_high_mode():
    image = Image.new("RGB", (20, 20), color="white")
    buffer = BytesIO(); image.save(buffer, format="PNG")
    runtime = build_runtime_with_bytes(buffer.getvalue(), RecordingEngine([OcrRunResult(pieces=[], warnings=[], trace=[])]))

    runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"))

    assert runtime.ocr_engine.calls[0]["size"] == (20, 20)
```

### Task 4: README 与协议回归

**Files:**
- Modify: `README.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 先写失败测试，锁住默认协议不泄漏新字段**

```python
def test_render_output_ocr_read_success_does_not_leak_mode_or_retry_fields():
    payload = build_success_response(
        request_id="req-ocr-fast",
        data={"result": [{"text": "点击进入", "box": {"left": 10, "top": 20, "width": 30, "height": 12}}]},
    )
    payload["debug"] = {
        "ocr_mode_requested": "fast",
        "ocr_mode_effective": "fast",
        "ocr_scale_applied": "1280x720",
        "ocr_retry_high": 1,
        "ocr_retry_reason": "low_confidence",
    }

    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        "text value=点击进入 box=10,20,30,12 center=25,26",
    ]
```

- [ ] **Step 2: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-red-protocol tests/test_output_rendering.py tests/test_output_debug.py tests/test_atomic_commands.py -k "ocr_mode or retry_high or yaml or recover or success_does_not_leak_mode_or_retry_fields" -v`
Expected: FAIL，因为 README/help/协议回归尚未覆盖新字段与 must-keep 协议点。

- [ ] **Step 3: 更新 README 与 CLI 帮助断言**

```markdown
- `trail ocr read --ocr-mode fast|high`
- `trail ocr read --retry-high auto|never|always`
- `TRAIL_OCR_MODE`、`TRAIL_OCR_RETRY_HIGH` 作为低优先级默认值
- 默认 `fast=1280x720`
- 默认 `retry_high=auto`
- `ocr_mode=high` 下 `retry_high` 为 no-op
- `retry_high=always` 会在 `fast` 后强制补跑一次 `high`
- `retry_high=auto` 只在 `hits==0`、平均分过低、或出现 `OCR_LOW_CONFIDENCE` 时触发
- 模式与重试事实只在 `--verbose` 下出现
```

- [ ] **Step 4: 跑任务综合回归**

Run: `uv run pytest --basetemp .trail/pytest-tmp/720p-plan-final tests/test_ocr_config.py tests/test_atomic_commands.py tests/test_daemon_protocol.py tests/test_runtime_backends.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_output_envelope.py -v`
Expected: PASS，默认文本协议不被污染，`yaml` / `recover` / `verbose-only` 事实 / README 新入口 / downscale / 重试与 debug 全部收口。

## Self-Review Checklist

- Spec coverage:
  - `ocr_mode=fast|high` 与 `retry_high=auto|never|always`：Task 1 覆盖
  - `fast=1280x720` 与 region 不 upscale：Task 2 覆盖
  - 自动重试触发、失败收口、最终结果语义：Task 3 覆盖
  - verbose-only 事实与 README / 协议回归：Task 3-4 覆盖
- Placeholder scan: 无 `TBD` / `TODO` / “后续再补” 占位。
- Type consistency: 统一使用 `ocr_mode`、`retry_high`、`ocr_scale_applied`、`ocr_retry_reason`，不要在实现里另起别名。

## Execution Mode

按用户当前约束，后续继续使用 **Subagent-Driven**：

- 使用项目内 worktree 执行
- 每个任务 fresh implementer 子代理
- 每个任务后做 spec review + code quality review
- review 通过后自动进入下一任务
