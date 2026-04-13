from __future__ import annotations

import typer

from trail.commands.helpers import build_default_artifact_store, build_default_runtime, print_json, to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.scenes.cw import guide as cw_guide
from trail.scenes.cw.guide import fetch_cw_guide_config
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
