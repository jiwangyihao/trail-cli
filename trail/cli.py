from importlib.metadata import version as distribution_version

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    pass


@app.command()
def version() -> None:
    print(f"trail {distribution_version('trail-cli')}")
