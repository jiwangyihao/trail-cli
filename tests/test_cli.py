from typer.testing import CliRunner

import trail
from trail.cli import app


runner = CliRunner()


def test_version_uses_package_version(monkeypatch) -> None:
    monkeypatch.setattr(trail, "__version__", "9.9.9")

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == "trail 9.9.9\n"
