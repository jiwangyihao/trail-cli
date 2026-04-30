# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata


ROOT = Path.cwd().resolve()
datas = []
binaries = []
hiddenimports = []

for package in ["onnxruntime", "rapidocr_onnxruntime", "windows_capture"]:
    collected_datas, collected_binaries, collected_hiddenimports = collect_all(package)
    datas += collected_datas
    binaries += collected_binaries
    hiddenimports += collected_hiddenimports

hiddenimports += ["win32api", "win32gui", "win32ui", "pythoncom", "pywintypes"]
datas += copy_metadata("trail-cli")
datas += collect_data_files("trail")

cli_analysis = Analysis(
    [str(ROOT / "packaging/trail_cli_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
daemon_analysis = Analysis(
    [str(ROOT / "packaging/traild_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)
cli_pyz = PYZ(cli_analysis.pure)
daemon_pyz = PYZ(daemon_analysis.pure)
trail = EXE(cli_pyz, cli_analysis.scripts, [], exclude_binaries=True, name="trail")
traild = EXE(daemon_pyz, daemon_analysis.scripts, [], exclude_binaries=True, name="traild")
coll = COLLECT(
    trail,
    traild,
    cli_analysis.binaries,
    daemon_analysis.binaries,
    cli_analysis.datas,
    daemon_analysis.datas,
    strip=False,
    upx=False,
    name="trail",
)
