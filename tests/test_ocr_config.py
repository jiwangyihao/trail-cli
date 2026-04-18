from __future__ import annotations

import pytest

from trail.runtime.ocr_config import (
    DEFAULT_OCR_MODE,
    DEFAULT_OCR_RETRY_HIGH,
    OCR_LANG_UNSUPPORTED,
    OCR_PROVIDER_UNAVAILABLE,
    OcrRequestConfig,
    SUPPORTED_OCR_MODES,
    SUPPORTED_OCR_PROVIDERS,
    SUPPORTED_OCR_RETRY_HIGH,
    TRAIL_OCR_MODE,
    TRAIL_OCR_RETRY_HIGH,
    read_ocr_env_defaults,
    resolve_ocr_request_config,
)


@pytest.fixture(autouse=True)
def _clear_ocr_env(monkeypatch):
    for name in (
        "TRAIL_OCR_PROVIDER",
        "TRAIL_OCR_LANG",
        "TRAIL_OCR_USE_CLS",
        "TRAIL_OCR_TEXT_SCORE",
        "TRAIL_OCR_MODE",
        "TRAIL_OCR_RETRY_HIGH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_read_ocr_env_defaults_uses_fixed_variable_names(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "dml")
    monkeypatch.setenv("TRAIL_OCR_LANG", "ch")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "1")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "0.7")

    cfg = read_ocr_env_defaults()

    assert cfg == OcrRequestConfig(provider="dml", lang="ch", use_cls=True, text_score=0.7)


def test_read_ocr_env_defaults_returns_frozen_defaults_when_missing(monkeypatch):
    cfg = read_ocr_env_defaults()

    assert cfg == OcrRequestConfig(provider="auto", lang="ch", use_cls=False, text_score=0.5)


def test_error_codes_are_frozen():
    assert OCR_LANG_UNSUPPORTED == "OCR_LANG_UNSUPPORTED"
    assert OCR_PROVIDER_UNAVAILABLE == "OCR_PROVIDER_UNAVAILABLE"


def test_supported_ocr_providers_are_frozen():
    assert SUPPORTED_OCR_PROVIDERS == {"auto", "cpu", "dml"}


def test_ocr_mode_and_retry_high_contracts_are_frozen():
    assert DEFAULT_OCR_MODE == "fast"
    assert DEFAULT_OCR_RETRY_HIGH == "auto"
    assert TRAIL_OCR_MODE == "TRAIL_OCR_MODE"
    assert TRAIL_OCR_RETRY_HIGH == "TRAIL_OCR_RETRY_HIGH"
    assert SUPPORTED_OCR_MODES == {"fast", "high"}
    assert SUPPORTED_OCR_RETRY_HIGH == {"auto", "never", "always"}


def test_validate_lang_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="auto", lang="en", use_cls=False, text_score=0.5)

    with pytest.raises(ValueError, match="unsupported ocr lang: en"):
        cfg.validate()


def test_validate_provider_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="gpu", lang="ch", use_cls=False, text_score=0.5)

    with pytest.raises(ValueError, match="unsupported ocr provider: gpu"):
        cfg.validate()


def test_validate_ocr_mode_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="auto", lang="ch", use_cls=False, text_score=0.5, ocr_mode="native")

    with pytest.raises(ValueError, match="unsupported ocr mode: native"):
        cfg.validate()


def test_validate_retry_high_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="auto", lang="ch", use_cls=False, text_score=0.5, retry_high="sometimes")

    with pytest.raises(ValueError, match="unsupported ocr retry_high: sometimes"):
        cfg.validate()


def test_read_ocr_env_defaults_rejects_invalid_lang(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_LANG", "en")

    with pytest.raises(ValueError, match="invalid ocr lang from TRAIL_OCR_LANG: en"):
        read_ocr_env_defaults()


def test_read_ocr_env_defaults_rejects_invalid_text_score(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "not-a-float")

    with pytest.raises(ValueError, match="invalid ocr text score from TRAIL_OCR_TEXT_SCORE: not-a-float"):
        read_ocr_env_defaults()


def test_resolve_ocr_request_config_prefers_explicit_values_over_conflicting_env(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "gpu")
    monkeypatch.setenv("TRAIL_OCR_LANG", "en")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "not-a-float")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "0")

    cfg = resolve_ocr_request_config(provider="dml", lang="ch", use_cls=True, text_score=0.8)

    assert cfg == OcrRequestConfig(provider="dml", lang="ch", use_cls=True, text_score=0.8)


def test_resolve_ocr_request_config_reads_ocr_mode_and_retry_high_from_env_defaults(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_MODE", "high")
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "always")

    cfg = resolve_ocr_request_config()

    assert cfg.ocr_mode == "high"
    assert cfg.retry_high == "always"


def test_resolve_ocr_request_config_prefers_explicit_ocr_mode_and_retry_high_over_conflicting_env(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_MODE", "warp")
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "sometimes")

    cfg = resolve_ocr_request_config(ocr_mode="high", retry_high="never")

    assert cfg.ocr_mode == "high"
    assert cfg.retry_high == "never"


def test_resolve_ocr_request_config_rejects_unsupported_explicit_lang(monkeypatch):
    monkeypatch.delenv("TRAIL_OCR_LANG", raising=False)

    with pytest.raises(ValueError, match="unsupported ocr lang: en"):
        resolve_ocr_request_config(lang="en")


def test_resolve_ocr_request_config_rejects_unsupported_explicit_ocr_mode():
    with pytest.raises(ValueError, match="unsupported ocr mode: turbo"):
        resolve_ocr_request_config(ocr_mode="turbo", use_env_defaults=False)


def test_resolve_ocr_request_config_rejects_unsupported_explicit_retry_high():
    with pytest.raises(ValueError, match="unsupported ocr retry_high: sometimes"):
        resolve_ocr_request_config(retry_high="sometimes", use_env_defaults=False)
