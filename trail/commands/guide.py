from __future__ import annotations

import typer

from trail.commands.helpers import build_default_artifact_store, build_default_runtime, print_json, to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.scenes.cw import guide as cw_guide
from trail.scenes.cw.guide import fetch_cw_guide_config
from trail.scenes.cw.guide import fetch_cw_guide_list
from trail.scenes.cw.guide import fetch_cw_guide


artifact_store_factory = build_default_artifact_store
runtime_factory = build_default_runtime
guide_app = typer.Typer(no_args_is_help=True)


@guide_app.command("fetch")
def guide_fetch(scene: str, url: str) -> None:
    artifact_store = artifact_store_factory()
    runtime = runtime_factory()

    def action() -> dict:
        if scene != "cw":
            raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
        artifact = fetch_cw_guide(url, artifact_store=artifact_store, fetcher=cw_guide.fetch_cw_guide_payload)
        return to_jsonable(artifact)

    print_json(with_auto_capture(runtime, action))


@guide_app.command("config")
def guide_config(scene: str) -> None:
    def action() -> dict:
        if scene != "cw":
            raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
        return to_jsonable(fetch_cw_guide_config())

    print_json(with_auto_capture(None, action))


def _parse_optional_bool(value: str | None, *, option_name: str) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise TrailError("GUIDE_INPUT_INVALID", f"guide option '{option_name}' must be true or false")


@guide_app.command("list")
def guide_list(
    scene: str,
    page: int = typer.Option(1, "--page"),
    limit: int = typer.Option(20, "--limit"),
    trait_id: int | None = typer.Option(None, "--trait-id"),
    order: str | None = typer.Option(None, "--order"),
    next_page_token: str | None = typer.Option(None, "--next-page-token"),
    match_change_job: str | None = typer.Option(None, "--match-change-job"),
    match_hard: str | None = typer.Option(None, "--match-hard"),
) -> None:
    def action() -> dict:
        if scene != "cw":
            raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
        return to_jsonable(
            fetch_cw_guide_list(
                page=page,
                limit=limit,
                trait_id=trait_id,
                order=order,
                next_page_token=next_page_token,
                match_change_job=_parse_optional_bool(match_change_job, option_name="match-change-job"),
                match_hard=_parse_optional_bool(match_hard, option_name="match-hard"),
            )
        )

    print_json(with_auto_capture(None, action))
