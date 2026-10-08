"""The MCP bridge must preserve the authenticated caller for Django policy checks."""

import asyncio
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


def test_mcp_case_tool_passes_caller_token_and_case_scope_to_django(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = asyncio.run(server.get_case_metadata(ctx, "case-123"))

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        ("POST", "/api/core/get-case-metadata/", {"content": '{"caseId": "case-123"}'}),
    ]


def test_mcp_client_does_not_create_an_authorization_header_without_a_caller_token():
    client = server._client(None)

    assert "Authorization" not in client.headers

    asyncio.run(client.aclose())


def test_mcp_client_forwards_bearer_token_to_django():
    client = server._client("signed-user-token")

    assert client.headers["Authorization"] == "Bearer signed-user-token"

    asyncio.run(client.aclose())
