from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
import tomllib

import typer

from trail.commands.cw import cw_app
from trail.commands.guide import guide_app
from trail.commands.image import image_app
from trail.commands.input import input_app
from trail.commands.ocr import ocr_app
from trail.commands.screen import screen_app
from trail.commands.session import session_app
from trail.commands.state import state_app
from trail.commands.window import window_app

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    pass


@app.command()
def version() -> None:
    try:
        resolved = distribution_version("trail-cli")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        resolved = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    print(f"trail {resolved}")


app.add_typer(session_app, name="session")
app.add_typer(guide_app, name="guide")
app.add_typer(window_app, name="window")
app.add_typer(screen_app, name="screen")
app.add_typer(ocr_app, name="ocr")
app.add_typer(image_app, name="image")
app.add_typer(input_app, name="input")
app.add_typer(state_app, name="state")
app.add_typer(cw_app, name="cw")
