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


def _guide_input_invalid_response(message: str) -> dict:
    return with_auto_capture(
        None,
        lambda: (_ for _ in ()).throw(TrailError("GUIDE_INPUT_INVALID", message)),
    )


def _require_cw_scene(scene: str) -> bool:
    return scene == "cw"


@guide_app.command("fetch")
def guide_fetch(
    scene: str,
    url: str,
    select: bool = typer.Option(False, "--select", help="拉取后把攻略写入当前 session，建立当前已选攻略，不执行 UI 应用。"),
    session: str | None = typer.Option(None, "--session", help="只在 --select 时必填；与 --select 一起使用的 session id。"),
) -> None:
    """拉取攻略内容并直接返回给 Agent，默认只做预览/查看；货币战争支持 lineup_url 或 lineup_id。"""
    if not _require_cw_scene(scene):
        print_output(f"guide.fetch.{scene}", _unsupported_scene_response(scene))
        return
    if session == "":
        print_output(f"guide.fetch.{scene}", _guide_input_invalid_response("guide.fetch.cw --session must be a non-empty string"))
        return
    if select and session is None:
        print_output(f"guide.fetch.{scene}", _guide_input_invalid_response("guide.fetch.cw --select requires --session"))
        return
    if session is not None and not select:
        print_output(f"guide.fetch.{scene}", _guide_input_invalid_response("guide.fetch.cw --session requires --select"))
        return
    payload = {"url": url}
    if select:
        payload["select"] = True
    print_output(
        f"guide.fetch.{scene}",
        call_daemon(f"guide.fetch.{scene}", payload, session_id=session),
    )


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
    trait: str | None = typer.Option(None, "--trait", help="按羁绊名称筛选，免查 config。与 --trait-id 互斥。"),
    trait_id: int | None = typer.Option(None, "--trait-id", help="按羁绊 id 精确筛选。与 --trait 互斥。"),
    role: list[str] | None = typer.Option(None, "--role", help="按角色名称筛选，可重复传入多个值。与 --role-id 互斥。"),
    role_id: list[str] | None = typer.Option(None, "--role-id", help="按角色 id 精确筛选，可重复传入多个值。与 --role 互斥。"),
    order: str | None = typer.Option(None, "--order"),
    next_page_token: str | None = typer.Option(None, "--next-page-token"),
    match_change_job: str | None = typer.Option(None, "--match-change-job", help="保留当前布尔筛选语义，传 true/false 以按是否换装筛选。"),
    match_hard: str | None = typer.Option(None, "--match-hard", help="保留当前布尔筛选语义，传 true/false 以按是否支持硬需求筛选。"),
    portal: list[str] | None = typer.Option(None, "--portal", help="按投资环境筛选，名称用于免查 config。与 --portal-id 互斥。"),
    portal_id: list[str] | None = typer.Option(None, "--portal-id", help="按投资环境筛选，id 用于精确复现。与 --portal 互斥。"),
) -> None:
    """列出可选攻略。默认摘要保留攻略ID、攻略标签、主C、版本、点赞/收藏与最终阵容。"""

    if not _require_cw_scene(scene):
        print_output(f"guide.list.{scene}", _unsupported_scene_response(scene))
        return
    role_values = list(role or [])
    role_id_values = list(role_id or [])
    portal_values = list(portal or [])
    portal_id_values = list(portal_id or [])
    if trait is not None and trait_id is not None:
        print_output(
            f"guide.list.{scene}",
            _guide_input_invalid_response("guide options '--trait' and '--trait-id' are mutually exclusive"),
        )
        return
    if role_values and role_id_values:
        print_output(
            f"guide.list.{scene}",
            _guide_input_invalid_response("guide options '--role' and '--role-id' are mutually exclusive"),
        )
        return
    if portal_values and portal_id_values:
        print_output(
            f"guide.list.{scene}",
            _guide_input_invalid_response("guide options '--portal' and '--portal-id' are mutually exclusive"),
        )
        return
    payload = {
        "page": page,
        "limit": limit,
        "trait": trait,
        "trait_id": trait_id,
        "role": role_values or None,
        "role_id": role_id_values or None,
        "order": order,
        "next_page_token": next_page_token,
        "match_change_job": match_change_job,
        "match_hard": match_hard,
    }
    if portal_values:
        payload["portal"] = portal_values[0] if len(portal_values) == 1 else portal_values
    if portal_id_values:
        payload["portal_id"] = portal_id_values[0] if len(portal_id_values) == 1 else portal_id_values
    print_output(
        f"guide.list.{scene}",
        call_daemon(f"guide.list.{scene}", payload)
    )
