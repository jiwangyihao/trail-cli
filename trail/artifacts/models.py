from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ArtifactMeta:
    artifact_id: str
    scene: str
    kind: str
    path: Path
