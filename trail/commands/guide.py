from __future__ import annotations

import typer

from trail.commands.helpers import build_default_artifact_store, build_default_runtime, print_json, to_jsonable
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.scenes.cw.guide import fetch_cw_guide


artifact_store_factory = build_default_artifact_store
runtime_factory = build_default_runtime
guide_app = typer.Typer(no_args_is_help=True)


def fetch_guide_payload(scene: str, url: str) -> dict:
    if scene != "cw":
        raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
    return {
        "source_url": url,
        "share_code": "##stub##",
        "on_field": {},
        "off_field": {},
    }


def fetch_cw_guide_payload(url: str) -> dict:
    return fetch_guide_payload("cw", url)


@guide_app.command("fetch")
def guide_fetch(scene: str, url: str) -> None:
    artifact_store = artifact_store_factory()
    runtime = runtime_factory()

    def action() -> dict:
        if scene != "cw":
            raise TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")
        artifact = fetch_cw_guide(url, artifact_store=artifact_store, fetcher=fetch_cw_guide_payload)
        return to_jsonable(artifact)

    print_json(with_auto_capture(runtime, action))
