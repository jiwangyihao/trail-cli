from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output
from trail.runtime.ocr_config import (
    OCR_LANG_UNSUPPORTED,
    TRAIL_OCR_LANG,
    TRAIL_OCR_PROVIDER,
    TRAIL_OCR_TEXT_SCORE,
    TRAIL_OCR_USE_CLS,
    resolve_ocr_request_config,
)


ocr_app = typer.Typer(no_args_is_help=True)


def _ocr_bad_parameter_hint(exc: ValueError) -> str:
    message = str(exc)
    if message.startswith(f"invalid ocr lang from {TRAIL_OCR_LANG}:"):
        return TRAIL_OCR_LANG
    if message.startswith(f"invalid ocr provider from {TRAIL_OCR_PROVIDER}:"):
        return TRAIL_OCR_PROVIDER
    if message.startswith(f"invalid ocr text score from {TRAIL_OCR_TEXT_SCORE}:"):
        return TRAIL_OCR_TEXT_SCORE
    if message.startswith(f"invalid ocr use_cls from {TRAIL_OCR_USE_CLS}:"):
        return TRAIL_OCR_USE_CLS
    if message.startswith("unsupported ocr lang:"):
        return "--lang"
    if message.startswith("unsupported ocr provider:"):
        return "--provider"
    if message.startswith("invalid ocr use_cls:"):
        return "--use-cls"
    return "--provider"


def _ocr_bad_parameter_message(exc: ValueError) -> str:
    message = str(exc)
    if message.startswith((f"invalid ocr lang from {TRAIL_OCR_LANG}:", "unsupported ocr lang:")):
        return f"{OCR_LANG_UNSUPPORTED}: {message}"
    return message


@ocr_app.command("read")
def ocr_read(
    provider: str | None = typer.Option(None, "--provider"),
    lang: str | None = typer.Option(None, "--lang", help="OCR 语言；首版仅支持 ch"),
    use_cls: bool | None = typer.Option(None, "--use-cls/--no-use-cls"),
    text_score: float | None = typer.Option(None, "--text-score"),
) -> None:
    try:
        payload = resolve_ocr_request_config(
            provider=provider,
            lang=lang,
            use_cls=use_cls,
            text_score=text_score,
        ).to_payload()
    except ValueError as exc:
        raise typer.BadParameter(_ocr_bad_parameter_message(exc), param_hint=_ocr_bad_parameter_hint(exc)) from exc
    print_output("ocr.read", call_daemon("ocr.read", payload))
