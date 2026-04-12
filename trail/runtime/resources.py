from pathlib import Path

from trail.scenes.cw.resources import CW_RESOURCE_ALIASES


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCENE_ASSET_ROOT = PACKAGE_ROOT / "scenes"


def load_scene_aliases(scene: str) -> dict[str, str]:
    if scene == "cw":
        return CW_RESOURCE_ALIASES
    raise KeyError(f"unknown scene: {scene}")


def resolve_scene_asset(scene: str, alias: str) -> Path:
    mapping = load_scene_aliases(scene)
    relative = mapping[alias]
    return SCENE_ASSET_ROOT / scene / "assets" / relative
