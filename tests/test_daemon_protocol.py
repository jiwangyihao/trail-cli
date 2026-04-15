from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from tests.support.fake_daemon import (
    FakeDaemonClient,
    build_success_response,
    fake_round_trip_transport,
    start_fake_daemon_server,
)


def test_protocol_version_is_fixed():
    assert PROTOCOL_VERSION == 1


def test_fake_daemon_client_records_request_metadata():
    client = FakeDaemonClient(
        responses={
            "ocr.read": {
                "ok": True,
                "data": {"result": [{"text": "点击进入"}]},
                "screenshot": ".trail/shots/req-1.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )

    payload = client.call(
        method="ocr.read",
        payload={},
        workspace_root="C:/repo",
        session_id=None,
        verbose=False,
    )

    assert payload["ok"] is True
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {},
            "workspace_root": "C:/repo",
            "session_id": None,
            "verbose": False,
        }
    ]


def test_fake_round_trip_transport_forwards_request_metadata_and_auth():
    server = start_fake_daemon_server(
        {
            "ocr.read": build_success_response(
                request_id="req-1",
                data={"result": [{"text": "点击进入"}]},
                screenshot=".trail/shots/req-1.png",
            )
        }
    )
    transport = fake_round_trip_transport(server)

    payload = transport(
        DaemonRequest(
            request_id="req-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root="C:/repo",
            session_id=None,
            verbose=False,
            method="ocr.read",
            payload={},
        ),
        token="token-1",
        endpoint="npipe://traild",
    )

    assert payload == {
        "request_id": "req-1",
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": ".trail/shots/req-1.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert server.requests == [
        {
            "request_id": "req-1",
            "protocol_version": PROTOCOL_VERSION,
            "workspace_root": "C:/repo",
            "session_id": None,
            "verbose": False,
            "method": "ocr.read",
            "payload": {},
            "token": "token-1",
            "endpoint": "npipe://traild",
        }
    ]
