from __future__ import annotations

import pytest

from trail.runtime.ocr_config import (
    OCR_LANG_UNSUPPORTED,
    OCR_PROVIDER_UNAVAILABLE,
    OcrRequestConfig,
    SUPPORTED_OCR_PROVIDERS,
    read_ocr_env_defaults,
    resolve_ocr_request_config,
)


def test_read_ocr_env_defaults_uses_fixed_variable_names(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "dml")
    monkeypatch.setenv("TRAIL_OCR_LANG", "ch")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "1")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "0.7")

    cfg = read_ocr_env_defaults()

    assert cfg == OcrRequestConfig(provider="dml", lang="ch", use_cls=True, text_score=0.7)


def test_read_ocr_env_defaults_returns_frozen_defaults_when_missing(monkeypatch):
    monkeypatch.delenv("TRAIL_OCR_PROVIDER", raising=False)
    monkeypatch.delenv("TRAIL_OCR_LANG", raising=False)
    monkeypatch.delenv("TRAIL_OCR_USE_CLS", raising=False)
    monkeypatch.delenv("TRAIL_OCR_TEXT_SCORE", raising=False)

    cfg = read_ocr_env_defaults()

    assert cfg == OcrRequestConfig(provider="auto", lang="ch", use_cls=False, text_score=0.5)


def test_error_codes_are_frozen():
    assert OCR_LANG_UNSUPPORTED == "OCR_LANG_UNSUPPORTED"
    assert OCR_PROVIDER_UNAVAILABLE == "OCR_PROVIDER_UNAVAILABLE"


def test_supported_ocr_providers_are_frozen():
    assert SUPPORTED_OCR_PROVIDERS == {"auto", "cpu", "dml"}


def test_validate_lang_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="auto", lang="en", use_cls=False, text_score=0.5)

    with pytest.raises(ValueError, match="unsupported ocr lang: en"):
        cfg.validate()


def test_validate_provider_rejects_unsupported_values():
    cfg = OcrRequestConfig(provider="gpu", lang="ch", use_cls=False, text_score=0.5)

    with pytest.raises(ValueError, match="unsupported ocr provider: gpu"):
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


def test_resolve_ocr_request_config_rejects_unsupported_explicit_lang(monkeypatch):
    monkeypatch.delenv("TRAIL_OCR_LANG", raising=False)

    with pytest.raises(ValueError, match="unsupported ocr lang: en"):
        resolve_ocr_request_config(lang="en")
