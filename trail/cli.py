from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
import tomllib

import typer

from trail.commands.cw import cw_app
from trail.commands.daemon import daemon_app
from trail.commands.guide import guide_app
from trail.commands.helpers import set_daemon_control_options
from trail.commands.image import image_app
from trail.commands.input import input_app
from trail.commands.ocr import ocr_app
from trail.commands.screen import screen_app
from trail.commands.session import session_app
from trail.commands.start import start_app
from trail.commands.state import state_app
from trail.commands.window import window_app
from trail.output.capture import set_capture_options
from trail.output.rendering import OutputFormat, set_output_options

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", help="输出复杂操作的中间流程，便于开发期调试"),
    output_format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="text 或 yaml"),
    request_id: str | None = typer.Option(None, "--request-id", help="查询或等待既有 daemon request/job。"),
    wait_timeout: float | None = typer.Option(None, "--wait-timeout", help="daemon 异步请求等待秒数。"),
    no_wait: bool = typer.Option(False, "--no-wait", help="提交 daemon 异步请求后立即返回 running 状态。"),
) -> None:
    set_capture_options(verbose=verbose)
    set_output_options(output_format=output_format, verbose=verbose)
    set_daemon_control_options(request_id=request_id, wait_timeout=wait_timeout, no_wait=no_wait)


@app.command()
def version() -> None:
    try:
        resolved = distribution_version("trail-cli")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        resolved = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    print(f"trail {resolved}")


def _selfcheck_release() -> tuple[bool, bool, bool]:
    from trail.output.rendering import render_output
    from trail.runtime.resources import resolve_scene_asset

    asset = resolve_scene_asset("cw", "guide.strategy")
    assets_ok = asset.exists()
    entry_text = render_output("cw.enter", {"ok": True, "data": {}})
    entry_handoff_ok = "handoff_skill=trail-cw-entry" in entry_text
    prep_text = render_output(
        "cw.portal.select",
        {"ok": True, "data": {"card_idx": 1, "portal_title": "测试环境"}},
    )
    prep_handoff_ok = "handoff_skill=trail-cw-prep" in prep_text
    return assets_ok, entry_handoff_ok, prep_handoff_ok


@app.command("selfcheck", hidden=True)
def selfcheck_release(kind: str = typer.Argument(...)) -> None:
    if kind != "release":
        raise typer.BadParameter("expected release")
    assets_ok, entry_handoff_ok, prep_handoff_ok = _selfcheck_release()
    if not assets_ok or not entry_handoff_ok or not prep_handoff_ok:
        raise typer.Exit(2)
    typer.echo("ok release selfcheck assets=1 handoff=1")


app.add_typer(session_app, name="session")
app.add_typer(start_app, name="start")
app.add_typer(daemon_app, name="daemon")
app.add_typer(guide_app, name="guide")
app.add_typer(window_app, name="window")
app.add_typer(screen_app, name="screen")
app.add_typer(ocr_app, name="ocr")
app.add_typer(image_app, name="image")
app.add_typer(input_app, name="input")
app.add_typer(state_app, name="state")
app.add_typer(cw_app, name="cw")
