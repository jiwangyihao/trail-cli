from pathlib import Path

from trail.scenes.cw.resources import CW_RESOURCE_ALIASES


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCENE_ASSET_ROOT = PACKAGE_ROOT / "scenes"


def _get_scene_asset_root(scene: str) -> Path:
    return (SCENE_ASSET_ROOT / scene / "assets").resolve()


def load_scene_aliases(scene: str) -> dict[str, str]:
    if scene == "cw":
        return CW_RESOURCE_ALIASES
    raise KeyError(f"unknown scene: {scene}")


def resolve_scene_asset(scene: str, alias: str) -> Path:
    mapping = load_scene_aliases(scene)
    relative = mapping[alias]
    asset_root = _get_scene_asset_root(scene)
    resolved = (asset_root / relative).resolve()

    if not resolved.is_relative_to(asset_root):
        raise ValueError(f"asset alias resolves outside local asset root: {alias}")
    if not resolved.is_file():
        raise FileNotFoundError(f"missing local asset for alias: {alias}")

    return resolved
