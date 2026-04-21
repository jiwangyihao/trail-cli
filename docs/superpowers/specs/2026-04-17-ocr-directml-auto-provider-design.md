# OCR DirectML Auto Provider 设计

## 目标

- 保持默认 OCR 模型基线：`PP-OCRv4 mobile + ch + use_cls=false`
- 为 `trail ocr read` 固定配置面：`provider`、`lang`、`use_cls`、`text_score`
- 在 Windows 上优先尝试 `DirectML`，不可用时在 `provider=auto` 下回退 CPU
- `provider=dml` 必须硬失败，不允许静默回退
- 默认文本协议不扩张，provider/debug 只允许出现在现有 `--verbose` debug 管线中

## 冻结契约

- `provider`: `auto | cpu | dml`
- `lang`: 首版仅支持 `ch`
- `use_cls`: 布尔
- `text_score`: 浮点
- 环境变量：`TRAIL_OCR_PROVIDER`、`TRAIL_OCR_LANG`、`TRAIL_OCR_USE_CLS`、`TRAIL_OCR_TEXT_SCORE`
- 稳定错误码：
  - `OCR_PROVIDER_UNAVAILABLE`
  - `OCR_LANG_UNSUPPORTED`
  - `OCR_INPUT_INVALID`

## 行为语义

- `provider=auto`：优先尝试 DML；DML 不可用、初始化失败或推理期失败时回退 CPU
- `provider=cpu`：强制走 CPU
- `provider=dml`：把 DML 视为硬约束；不可用、初始化失败或推理期失败都返回 `OCR_PROVIDER_UNAVAILABLE`
- `lang!=ch`：返回 `OCR_LANG_UNSUPPORTED`
- 默认成功输出继续保持：`ok ocr.read hits=<n>`，有截图时先 `shot`，再 `text`
- 默认 failure 继续遵守既有 `request -> shot -> why -> warn -> ref -> recover` 顺序；只要 failure 携带 `request_id`，就必须输出 `request id=<id>`

## 运行时设计

- CLI 负责读取环境变量默认值并把最终 OCR 配置写入 daemon payload
- daemon 通过 `split_ocr_call()` 把 `capture` 与 `ocr` 两类参数拆开
- runtime 使用 `OcrRequestConfig` 做边界校验
- OCR 引擎返回显式 `OcrRunResult`，不再依赖隐式 side-channel
- provider/debug/warning 必须 request-local，不能依赖共享 `_trace/_warnings`
- OCR engine cache 以 engine 身份字段为 key，包含 singleflight 初始化与坏 DML wrapper 驱逐
- `text_score` 会透传到 `RapidOCR.__call__(..., text_score=...)`，但不进入 cache key

## 文档/安装口径

- Windows 默认安装改为 DirectML 基线，CPU 只保留为显式兼容/排障路径
- DirectML 是 Windows 定向的受支持 profile
- 使用 DirectML 时，需要保证环境里的 ORT 变体是明确的，不能让 `onnxruntime` 与 `onnxruntime-directml` 处于模糊共存状态

## 验证重点

- CLI / daemon / runtime 三层 OCR 配置透传一致
- `provider=auto|cpu|dml` 语义、DML build/run 失败与坏缓存驱逐
- `lang!=ch` 与非法 OCR payload 的稳定错误码
- request-local debug/warning 与并发隔离
- `ocr.read --format yaml` 仍返回 `OUTPUT_FORMAT_NOT_SUPPORTED`
- README、renderer 测试、CLI stdout 测试同步覆盖
