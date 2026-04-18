from __future__ import annotations

import typer

from trail.commands.helpers import call_daemon
from trail.core.errors import TrailError
from trail.output.capture import with_auto_capture
from trail.output.rendering import print_output


guide_app = typer.Typer(no_args_is_help=True)


def _unsupported_scene_response(scene: str) -> dict:
    return with_auto_capture(
        None,
        lambda: (_ for _ in ()).throw(TrailError("SCENE_NOT_SUPPORTED", f"暂不支持场景 {scene}")),
    )


def _require_cw_scene(scene: str) -> bool:
    return scene == "cw"


@guide_app.command("fetch")
def guide_fetch(scene: str, url: str) -> None:
    """拉取攻略内容并直接返回给 Agent；货币战争支持 lineup_url 或 lineup_id。"""
    if not _require_cw_scene(scene):
        print_output(f"guide.fetch.{scene}", _unsupported_scene_response(scene))
        return
    print_output(f"guide.fetch.{scene}", call_daemon(f"guide.fetch.{scene}", {"url": url}))


@guide_app.command("config")
def guide_config(scene: str) -> None:
    """返回货币战争攻略筛选会用到的动态枚举字典。"""
    if not _require_cw_scene(scene):
        print_output(f"guide.config.{scene}", _unsupported_scene_response(scene))
        return
    print_output(f"guide.config.{scene}", call_daemon(f"guide.config.{scene}", {}))


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
    portal: str | None = typer.Option(None, "--portal"),
    portal_id: str | None = typer.Option(None, "--portal-id"),
) -> None:
    """列出可选攻略。默认字段面向“选攻略”，会保留 has_change_equip / has_expert / support_hard / final_role_cards 等高价值信息。"""

    if not _require_cw_scene(scene):
        print_output(f"guide.list.{scene}", _unsupported_scene_response(scene))
        return
    if portal is not None and portal_id is not None:
        print_output(
            f"guide.list.{scene}",
            with_auto_capture(
                None,
                lambda: (_ for _ in ()).throw(
                    TrailError("GUIDE_INPUT_INVALID", "guide options '--portal' and '--portal-id' are mutually exclusive")
                ),
            ),
        )
        return
    payload = {
        "page": page,
        "limit": limit,
        "trait_id": trait_id,
        "order": order,
        "next_page_token": next_page_token,
        "match_change_job": match_change_job,
        "match_hard": match_hard,
    }
    if portal is not None:
        payload["portal"] = portal
    if portal_id is not None:
        payload["portal_id"] = portal_id
    print_output(
        f"guide.list.{scene}",
        call_daemon(f"guide.list.{scene}", payload)
    )
