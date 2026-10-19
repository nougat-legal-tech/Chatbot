"""The MCP bridge must preserve the authenticated caller for Django policy checks."""

from types import SimpleNamespace

import mcp_server.server as server


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class _Client:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return _Response()

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return _Response()


def _run_mock_async(coroutine):
    """Run a mocked async call that must finish without real I/O or scheduling."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("Mocked MCP forwarding test attempted asynchronous I/O")


def test_mcp_case_tool_passes_caller_token_and_case_scope_to_django(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(server.get_case_metadata(ctx, "case-123"))

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        ("POST", "/api/core/get-case-metadata/", {"content": '{"caseId": "case-123"}'}),
    ]


def test_mcp_create_new_unfiled_matter_forwards_without_case_or_docket_id(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.create_new_case(
            ctx,
            app_id="2",
            case_name="Rowan Glass LLC v. Alpine Demo Services, Inc.",
            user_id="user-1",
        )
    )

    assert result == {"ok": True}
    assert calls == [
        (
            "POST",
            "/api/core/create-new-case/",
            {
                "content": (
                    '{"appId": "2", "caseName": '
                    '"Rowan Glass LLC v. Alpine Demo Services, Inc.", "userId": "user-1"}'
                )
            },
        ),
    ]


def test_mcp_zoom_disconnect_forwards_selected_app_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.disconnect_scheduling_conferencing(ctx, "zoom-2", app_id="4"),
    )

    assert result == {"ok": True}
    assert calls == [
            ("POST", "/api/core/integrations/conferencing/zoom-2/disconnect/", {"content": '{"appId": "4"}'}),
    ]


def test_mcp_client_does_not_create_an_authorization_header_without_a_caller_token():
    client = server._client(None)

    assert "Authorization" not in client.headers



def test_mcp_client_forwards_bearer_token_to_django():
    client = server._client("signed-user-token")

    assert client.headers["Authorization"] == "Bearer signed-user-token"
