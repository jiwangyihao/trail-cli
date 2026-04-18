from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from trail.core.errors import TrailError


OCR_PROVIDER_UNAVAILABLE = "OCR_PROVIDER_UNAVAILABLE"
OCR_LANG_UNSUPPORTED = "OCR_LANG_UNSUPPORTED"
OCR_INPUT_INVALID = "OCR_INPUT_INVALID"

DEFAULT_OCR_PROVIDER = "auto"
DEFAULT_OCR_LANG = "ch"
DEFAULT_OCR_USE_CLS = False
DEFAULT_OCR_TEXT_SCORE = 0.5
DEFAULT_OCR_MODE = "fast"
DEFAULT_OCR_RETRY_HIGH = "auto"

TRAIL_OCR_PROVIDER = "TRAIL_OCR_PROVIDER"
TRAIL_OCR_LANG = "TRAIL_OCR_LANG"
TRAIL_OCR_USE_CLS = "TRAIL_OCR_USE_CLS"
TRAIL_OCR_TEXT_SCORE = "TRAIL_OCR_TEXT_SCORE"
TRAIL_OCR_MODE = "TRAIL_OCR_MODE"
TRAIL_OCR_RETRY_HIGH = "TRAIL_OCR_RETRY_HIGH"

SUPPORTED_OCR_PROVIDERS = {"auto", "cpu", "dml"}
SUPPORTED_OCR_LANGS = {DEFAULT_OCR_LANG}
SUPPORTED_OCR_MODES = {"fast", "high"}
SUPPORTED_OCR_RETRY_HIGH = {"auto", "never", "always"}
OCR_CAPTURE_PAYLOAD_KEYS = ("from_x", "from_y", "to_x", "to_y")
OCR_CONFIG_PAYLOAD_KEYS = ("provider", "lang", "use_cls", "text_score", "ocr_mode", "retry_high")
OCR_ALLOWED_PAYLOAD_KEYS = set(OCR_CAPTURE_PAYLOAD_KEYS) | set(OCR_CONFIG_PAYLOAD_KEYS)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _validate_provider(provider: str) -> str:
    if provider not in SUPPORTED_OCR_PROVIDERS:
        raise ValueError(f"unsupported ocr provider: {provider}")
    return provider


def _validate_lang(lang: str) -> str:
    if lang not in SUPPORTED_OCR_LANGS:
        raise ValueError(f"unsupported ocr lang: {lang}")
    return lang


def _validate_ocr_mode(ocr_mode: str) -> str:
    if ocr_mode not in SUPPORTED_OCR_MODES:
        raise ValueError(f"unsupported ocr mode: {ocr_mode}")
    return ocr_mode


def _validate_retry_high(retry_high: str) -> str:
    if retry_high not in SUPPORTED_OCR_RETRY_HIGH:
        raise ValueError(f"unsupported ocr retry_high: {retry_high}")
    return retry_high


def _read_env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return _normalize_use_cls(value, source_name=name)


def _normalize_provider(provider: Any, *, source_name: str | None = None) -> str:
    raw = provider if isinstance(provider, str) else str(provider)
    try:
        return _validate_provider(raw)
    except ValueError as exc:
        if source_name is not None:
            raise ValueError(f"invalid ocr provider from {source_name}: {raw}") from exc
        raise


def _normalize_lang(lang: Any, *, source_name: str | None = None) -> str:
    raw = lang if isinstance(lang, str) else str(lang)
    try:
        return _validate_lang(raw)
    except ValueError as exc:
        if source_name is not None:
            raise ValueError(f"invalid ocr lang from {source_name}: {raw}") from exc
        raise


def _normalize_ocr_mode(ocr_mode: Any, *, source_name: str | None = None) -> str:
    raw = ocr_mode if isinstance(ocr_mode, str) else str(ocr_mode)
    try:
        return _validate_ocr_mode(raw)
    except ValueError as exc:
        if source_name is not None:
            raise ValueError(f"invalid ocr mode from {source_name}: {raw}") from exc
        raise


def _normalize_retry_high(retry_high: Any, *, source_name: str | None = None) -> str:
    raw = retry_high if isinstance(retry_high, str) else str(retry_high)
    try:
        return _validate_retry_high(raw)
    except ValueError as exc:
        if source_name is not None:
            raise ValueError(f"invalid ocr retry_high from {source_name}: {raw}") from exc
        raise


def _normalize_use_cls(value: Any, *, source_name: str | None = None) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    if source_name is not None:
        raise ValueError(f"invalid ocr use_cls from {source_name}: {value}")
    raise ValueError(f"invalid ocr use_cls: {value}")


def _normalize_text_score(value: Any, *, source_name: str | None = None) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        if source_name is not None:
            raise ValueError(f"invalid ocr text score from {source_name}: {value}") from exc
        raise ValueError(f"invalid ocr text score: {value}") from exc


def _read_env_provider() -> str:
    provider = os.getenv(TRAIL_OCR_PROVIDER, DEFAULT_OCR_PROVIDER)
    return _normalize_provider(provider, source_name=TRAIL_OCR_PROVIDER)


def _read_env_lang() -> str:
    lang = os.getenv(TRAIL_OCR_LANG, DEFAULT_OCR_LANG)
    return _normalize_lang(lang, source_name=TRAIL_OCR_LANG)


def _read_env_text_score() -> float:
    value = os.getenv(TRAIL_OCR_TEXT_SCORE)
    if value is None:
        return DEFAULT_OCR_TEXT_SCORE
    return _normalize_text_score(value, source_name=TRAIL_OCR_TEXT_SCORE)


def _read_env_ocr_mode() -> str:
    value = os.getenv(TRAIL_OCR_MODE, DEFAULT_OCR_MODE)
    return _normalize_ocr_mode(value, source_name=TRAIL_OCR_MODE)


def _read_env_retry_high() -> str:
    value = os.getenv(TRAIL_OCR_RETRY_HIGH, DEFAULT_OCR_RETRY_HIGH)
    return _normalize_retry_high(value, source_name=TRAIL_OCR_RETRY_HIGH)


@dataclass(frozen=True)
class OcrRequestConfig:
    provider: str = DEFAULT_OCR_PROVIDER
    lang: str = DEFAULT_OCR_LANG
    use_cls: bool = DEFAULT_OCR_USE_CLS
    text_score: float = DEFAULT_OCR_TEXT_SCORE
    ocr_mode: str = DEFAULT_OCR_MODE
    retry_high: str = DEFAULT_OCR_RETRY_HIGH

    def validate(self) -> None:
        self.normalized()

    def normalized(self) -> OcrRequestConfig:
        return OcrRequestConfig(
            provider=_normalize_provider(self.provider),
            lang=_normalize_lang(self.lang),
            use_cls=_normalize_use_cls(self.use_cls),
            text_score=_normalize_text_score(self.text_score),
            ocr_mode=_normalize_ocr_mode(self.ocr_mode),
            retry_high=_normalize_retry_high(self.retry_high),
        )

    def to_payload(self) -> dict[str, str | bool | float]:
        return {
            "provider": self.provider,
            "lang": self.lang,
            "use_cls": self.use_cls,
            "text_score": self.text_score,
            "ocr_mode": self.ocr_mode,
            "retry_high": self.retry_high,
        }


@dataclass(frozen=True)
class RuntimeOcrCall:
    capture: dict[str, Any]
    ocr: OcrRequestConfig


def read_ocr_env_defaults() -> OcrRequestConfig:
    return OcrRequestConfig(
        provider=_read_env_provider(),
        lang=_read_env_lang(),
        use_cls=_read_env_flag(TRAIL_OCR_USE_CLS, default=DEFAULT_OCR_USE_CLS),
        text_score=_read_env_text_score(),
        ocr_mode=_read_env_ocr_mode(),
        retry_high=_read_env_retry_high(),
    )


def resolve_ocr_request_config(
    *,
    provider: Any | None = None,
    lang: Any | None = None,
    use_cls: Any | None = None,
    text_score: Any | None = None,
    ocr_mode: Any | None = None,
    retry_high: Any | None = None,
    use_env_defaults: bool = True,
) -> OcrRequestConfig:
    return OcrRequestConfig(
        provider=_read_env_provider() if use_env_defaults and provider is None else DEFAULT_OCR_PROVIDER if provider is None else _normalize_provider(provider),
        lang=_read_env_lang() if use_env_defaults and lang is None else DEFAULT_OCR_LANG if lang is None else _normalize_lang(lang),
        use_cls=_read_env_flag(TRAIL_OCR_USE_CLS, default=DEFAULT_OCR_USE_CLS) if use_env_defaults and use_cls is None else DEFAULT_OCR_USE_CLS if use_cls is None else _normalize_use_cls(use_cls),
        text_score=_read_env_text_score() if use_env_defaults and text_score is None else DEFAULT_OCR_TEXT_SCORE if text_score is None else _normalize_text_score(text_score),
        ocr_mode=_read_env_ocr_mode() if use_env_defaults and ocr_mode is None else DEFAULT_OCR_MODE if ocr_mode is None else _normalize_ocr_mode(ocr_mode),
        retry_high=_read_env_retry_high() if use_env_defaults and retry_high is None else DEFAULT_OCR_RETRY_HIGH if retry_high is None else _normalize_retry_high(retry_high),
    )


def split_ocr_call(payload: dict[str, Any]) -> RuntimeOcrCall:
    unknown_keys = sorted(key for key in payload if key not in OCR_ALLOWED_PAYLOAD_KEYS)
    if unknown_keys:
        raise ValueError(f"unknown ocr payload fields: {', '.join(unknown_keys)}")
    capture = {key: value for key, value in payload.items() if key in OCR_CAPTURE_PAYLOAD_KEYS}
    return RuntimeOcrCall(
        capture=capture,
        ocr=resolve_ocr_request_config(
            provider=payload.get("provider"),
            lang=payload.get("lang"),
            use_cls=payload.get("use_cls"),
            text_score=payload.get("text_score"),
            ocr_mode=payload.get("ocr_mode"),
            retry_high=payload.get("retry_high"),
            use_env_defaults=False,
        ),
    )


def normalize_runtime_ocr_request_config(config: OcrRequestConfig | None = None) -> OcrRequestConfig:
    try:
        return (OcrRequestConfig() if config is None else config).normalized()
    except ValueError as exc:
        message = str(exc)
        if message.startswith("unsupported ocr lang:"):
            raise TrailError(OCR_LANG_UNSUPPORTED, message) from exc
        raise TrailError(OCR_INPUT_INVALID, message) from exc
