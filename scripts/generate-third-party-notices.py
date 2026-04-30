from __future__ import annotations

import importlib.metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTICES_PATH = ROOT / "THIRD_PARTY_NOTICES.txt"

RUNTIME_DISTS = {
    "annotated-doc",
    "click",
    "colorama",
    "flatbuffers",
    "importlib_metadata",
    "markdown-it-py",
    "mdurl",
    "mouseinfo",
    "mpmath",
    "numpy",
    "onnxruntime",
    "onnxruntime-directml",
    "opencv-python",
    "packaging",
    "pillow",
    "protobuf",
    "pyautogui",
    "pyclipper",
    "pygetwindow",
    "pygments",
    "pymsgbox",
    "pyperclip",
    "pyrect",
    "pyscreeze",
    "pytweening",
    "pywin32",
    "pyyaml",
    "rapidocr-onnxruntime",
    "rectangle-packer",
    "rich",
    "shapely",
    "shellingham",
    "six",
    "sympy",
    "typer",
    "typing-extensions",
    "tqdm",
    "windows-capture",
}

LICENSE_FILE_PREFIXES = ("LICENSE", "LICENCE", "NOTICE", "COPYING")
KNOWN_LICENSE_FILE_OMISSIONS = {
    "flatbuffers",
    "mouseinfo",
    "pygetwindow",
    "rapidocr-onnxruntime",
}


def normalize_dist_name(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


def _is_license_file(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name.upper()
    return any(
        name == prefix
        or name.startswith(f"{prefix}.")
        or name.startswith(f"{prefix}-")
        or name.startswith(f"{prefix}_")
        for prefix in LICENSE_FILE_PREFIXES
    )


def _license_value(meta: importlib.metadata.PackageMetadata) -> str:
    for key in ("License-Expression", "License"):
        value = meta.get(key)
        if value and value.strip().upper() != "UNKNOWN":
            return " ".join(value.split())

    classifiers = [
        value
        for value in meta.get_all("Classifier") or []
        if value.startswith("License ::")
    ]
    return "; ".join(classifiers)


def _license_files(dist: importlib.metadata.Distribution) -> list[str]:
    files: set[str] = set()

    for license_file in dist.metadata.get_all("License-File") or []:
        if _is_license_file(license_file):
            files.add(license_file)

    for file in dist.files or []:
        value = str(file)
        if _is_license_file(value):
            files.add(value)

    return sorted(files, key=str.lower)


def _runtime_distributions() -> list[importlib.metadata.Distribution]:
    installed: dict[str, importlib.metadata.Distribution] = {}
    for dist in importlib.metadata.distributions():
        package = dist.metadata.get("Name")
        if package:
            installed[normalize_dist_name(package)] = dist

    selected = []
    for package in sorted(RUNTIME_DISTS, key=normalize_dist_name):
        dist = installed.get(normalize_dist_name(package))
        if dist is not None:
            selected.append(dist)
    return selected


def _render_distribution(dist: importlib.metadata.Distribution) -> list[str]:
    package = dist.metadata.get("Name") or "UNKNOWN"
    license_value = _license_value(dist.metadata)
    if not license_value:
        raise SystemExit(f"missing license metadata for notices: {package}")

    license_files = _license_files(dist)
    if not license_files:
        normalized = normalize_dist_name(package)
        if normalized not in KNOWN_LICENSE_FILE_OMISSIONS:
            raise SystemExit(f"missing license file for notices: {package}")
        license_files = ["not provided by installed distribution"]

    summary = dist.metadata.get("Summary") or "UNKNOWN"
    return [
        f"Package: {package}",
        f"Version: {dist.version}",
        f"License: {license_value}",
        f"License-Files: {'; '.join(license_files)}",
        f"Notice: {summary}",
        "",
    ]


def main() -> None:
    lines = [
        "Trail CLI third-party notices",
        "",
        "This file is generated from installed runtime distributions.",
        "Do not list allowlist packages that are not installed in the current build environment.",
        "",
    ]

    for dist in _runtime_distributions():
        lines.extend(_render_distribution(dist))

    NOTICES_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
