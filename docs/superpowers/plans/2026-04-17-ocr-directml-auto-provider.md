# OCR DirectML Auto Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `trail ocr read` 增加稳定 OCR 配置契约与 Windows DirectML 自动加速，同时保持默认文本协议不变。

**Architecture:** 先固定 CLI/配置契约，再把 daemon/runtime OCR 调用改成 `capture + ocr` 两段式，随后在 runtime 内实现 provider 解析、singleflight cache、request-local debug/warning，最后补 README 与协议回归测试。

**Tech Stack:** Python 3.12、Typer、daemon RPC、rapidocr-onnxruntime、ONNX Runtime / DirectML、pytest。

---

## 任务拆分

### Task 1: 固定 OCR 配置契约

- `trail/runtime/ocr_config.py`
- `trail/commands/ocr.py`
- `tests/test_ocr_config.py`
- `tests/test_atomic_commands.py`

目标：冻结 `provider/lang/use_cls/text_score`、环境变量名、错误码与 CLI 参数优先级。

### Task 2: 打通 daemon 到 runtime 的 OCR 配置接口

- `trail/daemon/command_service.py`
- `trail/runtime/operator.py`
- `trail/scenes/cw/shop.py`
- `trail/scenes/cw/slots.py`
- `tests/test_runtime_backends.py`
- `tests/test_daemon_protocol.py`

目标：把 OCR payload 从“截图 kwargs”改成 `capture + ocr` 两段式，并拒绝非法/未知 OCR payload 字段。

### Task 3: 实现 DirectML provider、singleflight cache 与 request-local 调试

- `trail/runtime/operator.py`
- `trail/output/capture.py`
- `tests/test_runtime_backends.py`
- `tests/test_output_envelope.py`
- `tests/test_output_debug.py`

目标：实现 `provider=auto|cpu|dml` 语义、`text_score` 透传、DML build/run 失败处理、坏缓存驱逐、显式 `OcrRunResult`、request-local debug/warning。

### Task 4: 收口 failure 语义、README 与协议回归

- `trail/daemon/client.py`
- `README.md`
- `tests/test_atomic_commands.py`
- `tests/test_daemon_bootstrap.py`
- `tests/test_output_rendering.py`
- `tests/test_output_debug.py`

目标：锁住 `request id=...`、`OCR_PROVIDER_UNAVAILABLE` / `OCR_LANG_UNSUPPORTED` 的默认 failure 语义、README 文案与 `ocr.read --help` 说明。

## 验证约束

- 所有 pytest 命令统一使用 `--basetemp .trail/pytest-tmp/...`
- 关键回归应覆盖：
  - `tests/test_ocr_config.py`
  - `tests/test_atomic_commands.py`
  - `tests/test_runtime_backends.py`
  - `tests/test_output_rendering.py`
  - `tests/test_output_debug.py`
  - `tests/test_output_envelope.py -k "with_auto_capture"`
  - `tests/test_daemon_protocol.py -k "ocr"`
  - `tests/test_daemon_bootstrap.py`

## 执行方式

- 使用 project-local worktree
- 每个任务使用 fresh implementer 子代理
- 每个任务后执行 spec review + code quality review
- 未经用户明确要求，不创建 git commit
