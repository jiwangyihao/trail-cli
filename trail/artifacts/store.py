from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from trail.artifacts.models import ArtifactMeta


ARTIFACT_ID_PATTERN = re.compile(r"[a-f0-9]{32}")


class ArtifactStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _validate_artifact_id(self, artifact_id: str) -> str:
        if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise ValueError("invalid artifact_id")
        return artifact_id

    def _path_for(self, artifact_id: str) -> Path:
        return self.workspace / f"{self._validate_artifact_id(artifact_id)}.json"

    def create(self, *, scene: str, kind: str, payload: dict) -> ArtifactMeta:
        artifact_id = uuid4().hex
        path = self._path_for(artifact_id)
        data = deepcopy(payload)
        data["artifact_id"] = artifact_id
        data["scene"] = scene
        data["kind"] = kind
        data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return ArtifactMeta(artifact_id=artifact_id, scene=scene, kind=kind, path=path)

    def load(self, artifact_id: str) -> ArtifactMeta:
        path = self._path_for(artifact_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("artifact_id") != path.stem:
            raise ValueError("artifact_id mismatch")
        return ArtifactMeta(
            artifact_id=path.stem,
            scene=str(payload["scene"]),
            kind=str(payload["kind"]),
            path=path,
        )
