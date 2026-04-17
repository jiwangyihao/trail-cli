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

TRAIL_OCR_PROVIDER = "TRAIL_OCR_PROVIDER"
TRAIL_OCR_LANG = "TRAIL_OCR_LANG"
TRAIL_OCR_USE_CLS = "TRAIL_OCR_USE_CLS"
TRAIL_OCR_TEXT_SCORE = "TRAIL_OCR_TEXT_SCORE"

SUPPORTED_OCR_PROVIDERS = {"auto", "cpu", "dml"}
SUPPORTED_OCR_LANGS = {DEFAULT_OCR_LANG}
OCR_CAPTURE_PAYLOAD_KEYS = ("from_x", "from_y", "to_x", "to_y")
OCR_CONFIG_PAYLOAD_KEYS = ("provider", "lang", "use_cls", "text_score")
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


@dataclass(frozen=True)
class OcrRequestConfig:
    provider: str = DEFAULT_OCR_PROVIDER
    lang: str = DEFAULT_OCR_LANG
    use_cls: bool = DEFAULT_OCR_USE_CLS
    text_score: float = DEFAULT_OCR_TEXT_SCORE

    def validate(self) -> None:
        self.normalized()

    def normalized(self) -> OcrRequestConfig:
        return OcrRequestConfig(
            provider=_normalize_provider(self.provider),
            lang=_normalize_lang(self.lang),
            use_cls=_normalize_use_cls(self.use_cls),
            text_score=_normalize_text_score(self.text_score),
        )

    def to_payload(self) -> dict[str, str | bool | float]:
        return {
            "provider": self.provider,
            "lang": self.lang,
            "use_cls": self.use_cls,
            "text_score": self.text_score,
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
    )


def resolve_ocr_request_config(
    *,
    provider: Any | None = None,
    lang: Any | None = None,
    use_cls: Any | None = None,
    text_score: Any | None = None,
    use_env_defaults: bool = True,
) -> OcrRequestConfig:
    return OcrRequestConfig(
        provider=_read_env_provider() if use_env_defaults and provider is None else DEFAULT_OCR_PROVIDER if provider is None else _normalize_provider(provider),
        lang=_read_env_lang() if use_env_defaults and lang is None else DEFAULT_OCR_LANG if lang is None else _normalize_lang(lang),
        use_cls=_read_env_flag(TRAIL_OCR_USE_CLS, default=DEFAULT_OCR_USE_CLS) if use_env_defaults and use_cls is None else DEFAULT_OCR_USE_CLS if use_cls is None else _normalize_use_cls(use_cls),
        text_score=_read_env_text_score() if use_env_defaults and text_score is None else DEFAULT_OCR_TEXT_SCORE if text_score is None else _normalize_text_score(text_score),
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
