from datetime import datetime

import pytest

from trail.session.store import SessionStore


def test_create_session_persists_workspace_and_scene_state(tmp_path):
    store = SessionStore(tmp_path)

    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    loaded = store.load(session.session_id)

    assert loaded.session_id == session.session_id
    assert loaded.workspace == tmp_path
    assert loaded.scene_state == {}
    assert loaded.last_result is None


def test_create_session_sets_created_at_in_utc_iso_format(tmp_path):
    store = SessionStore(tmp_path)

    session = store.create(window_binding={"title": "崩坏：星穹铁道"})

    created_at = datetime.fromisoformat(session.created_at)
    assert created_at.tzinfo is not None


def test_load_rejects_session_id_mismatch_between_file_and_payload(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道"})
    path = tmp_path / f"{session.session_id}.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(session.session_id, "0" * 32, 1), encoding="utf-8")

    with pytest.raises(ValueError, match="session_id mismatch"):
        store.load(session.session_id)
