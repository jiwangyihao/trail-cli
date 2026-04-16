from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from trail.artifacts.models import ArtifactMeta


ARTIFACT_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
PATH_FIELD_KEYS = {"path", "screenshot", "last_screenshot", "workspace"}


class ArtifactStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _workspace_root(self) -> Path:
        if self.workspace.parent.name == ".trail":
            return self.workspace.parent.parent
        return self.workspace

    def _to_workspace_relative(self, path_value: Path | str | None) -> str | None:
        if path_value is None:
            return None
        path = Path(path_value)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(self._workspace_root().resolve()).as_posix()
        except ValueError:
            return str(path)

    def _normalize_payload_paths(self, value):
        if isinstance(value, Path):
            return self._to_workspace_relative(value)
        if isinstance(value, list):
            return [self._normalize_payload_paths(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._to_workspace_relative(item)
                if key in PATH_FIELD_KEYS
                else self._normalize_payload_paths(item)
                for key, item in value.items()
            }
        return deepcopy(value)

    def _validate_artifact_id(self, artifact_id: str) -> str:
        if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise ValueError("invalid artifact_id")
        return artifact_id

    def _path_for(self, artifact_id: str) -> Path:
        return self.workspace / f"{self._validate_artifact_id(artifact_id)}.json"

    def create(self, *, scene: str, kind: str, payload: dict) -> ArtifactMeta:
        artifact_id = uuid4().hex
        path = self._path_for(artifact_id)
        data = self._normalize_payload_paths(payload)
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
