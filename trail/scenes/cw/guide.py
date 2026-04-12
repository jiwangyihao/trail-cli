from __future__ import annotations

import json
from pathlib import Path

from trail.artifacts.models import ArtifactMeta
from trail.artifacts.store import ArtifactStore
from trail.scenes.cw.models import CwSceneState, ensure_cw_state
from trail.session.models import SessionModel


def fetch_cw_guide(url: str, *, artifact_store: ArtifactStore, fetcher) -> ArtifactMeta:
    payload = fetcher(url)
    return artifact_store.create(scene="cw", kind="guide", payload=payload)


def resolve_guide_input(value: str, *, artifact_store: ArtifactStore) -> dict:
    candidate = Path(value)
    if candidate.exists():
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        payload.setdefault("artifact_id", candidate.stem)
        return payload

    meta = artifact_store.load(value)
    payload = json.loads(meta.path.read_text(encoding="utf-8"))
    payload["artifact_id"] = meta.artifact_id
    return payload


def apply_cw_guide(session: SessionModel, guide_data: dict) -> SessionModel:
    cw_state = ensure_cw_state(session)
    defaults = CwSceneState().model_dump()
    cw_state["guide"] = {
        "artifact": guide_data.get("artifact_id"),
        "share_code": guide_data["share_code"],
        "remaining_purchases": {
            **guide_data.get("on_field", {}),
            **guide_data.get("off_field", {}),
        },
    }
    cw_state["constraints"] = {
        "min_coins": guide_data.get("min_coins", 40),
        "min_level": guide_data.get("min_level", 7),
        "mid_level": guide_data.get("mid_level", 7),
        "priority": guide_data.get("priority", {}),
        "positioning": guide_data.get("positioning", {}),
    }
    cw_state["slots"] = defaults["slots"]
    cw_state["shop"] = defaults["shop"]
    cw_state["stage"] = defaults["stage"]
    return session
