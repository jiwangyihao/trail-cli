from importlib.metadata import PackageNotFoundError, version as distribution_version

from typer.testing import CliRunner

import trail
from trail.cli import app


runner = CliRunner()


def test_version_uses_distribution_metadata(monkeypatch) -> None:
    monkeypatch.setattr(trail, "__version__", "9.9.9", raising=False)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"trail {distribution_version('trail-cli')}\n"


def test_version_falls_back_to_source_metadata_when_distribution_missing(monkeypatch) -> None:
    import trail.cli as cli_module

    def raise_not_found(_: str) -> str:
        raise PackageNotFoundError("trail-cli")

    monkeypatch.setattr(cli_module, "distribution_version", raise_not_found)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == "trail 0.1.2\n"
