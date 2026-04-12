from importlib.metadata import version as distribution_version

from typer.testing import CliRunner

import trail
from trail.cli import app


runner = CliRunner()


def test_version_uses_distribution_metadata(monkeypatch) -> None:
    monkeypatch.setattr(trail, "__version__", "9.9.9", raising=False)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"trail {distribution_version('trail-cli')}\n"
