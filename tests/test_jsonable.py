from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from trail.core.jsonable import to_jsonable


@dataclass
class RecursiveNode:
    name: str
    child: object | None = None


class RecursiveMapping:
    def to_dict(self):
        return {"self": self}

    def __str__(self) -> str:
        return "recursive-mapping"


class BrokenStr:
    def __str__(self) -> str:
        raise RuntimeError("cannot stringify")


class BrokenDescriptor:
    @property
    def item(self):
        raise RuntimeError("descriptor boom")

    def __str__(self) -> str:
        return "broken-descriptor"


def test_to_jsonable_handles_common_non_json_values_and_cycles(tmp_path: Path) -> None:
    node = RecursiveNode("root")
    node.child = node
    when = datetime(2026, 4, 25, 13, 50, tzinfo=timezone.utc)

    payload = to_jsonable(
        {
            "path": tmp_path / "artifact.png",
            "when": when,
            "tags": {"zeta", "alpha"},
            "node": node,
            "mapping": RecursiveMapping(),
            "broken": BrokenStr(),
            "descriptor": BrokenDescriptor(),
        }
    )

    json.dumps(payload, ensure_ascii=False)
    assert payload["path"] == str(tmp_path / "artifact.png")
    assert payload["when"] == "2026-04-25T13:50:00+00:00"
    assert payload["tags"] == ["alpha", "zeta"]
    assert payload["node"]["name"] == "root"
    assert payload["node"]["child"] == "RecursiveNode(name='root', child=...)"
    assert payload["mapping"] == {"self": "recursive-mapping"}
    assert payload["broken"] == "<unjsonable BrokenStr: RuntimeError: cannot stringify>"
    assert payload["descriptor"] == "broken-descriptor"
