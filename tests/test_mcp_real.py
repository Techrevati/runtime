"""MCPToolAdapter against real in-memory MCP servers, on whichever SDK is installed.

The fakes in ``test_mcp.py`` encode what the adapter expects; these encode what the
SDK actually does. CI runs this file on both the 1.x and 2.x lines.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import ClientSession, types  # noqa: E402

from techrevati.runtime import AgentSession  # noqa: E402
from techrevati.runtime.mcp import MCPToolAdapter, MCPToolError  # noqa: E402

SDK_MAJOR = int(version("mcp").split(".", 1)[0])
PAGES: dict[str | None, tuple[list[str], str | None]] = {
    None: (["first", "second"], "page-2"),
    "page-2": (["third"], None),
}


def _tool(name: str) -> types.Tool:
    return types.Tool(
        name=name, description=f"{name} tool", inputSchema={"type": "object"}
    )


def _text(value: str) -> types.TextContent:
    return types.TextContent(type="text", text=value)


def _call(name: str, arguments: dict[str, Any] | None) -> list[Any] | None:
    """Shared server behaviour; None means the tool reports an error."""
    if name == "fail":
        return None
    if name == "mixed":
        image = types.ImageContent(type="image", data="aGk=", mimeType="image/png")
        return [_text("caption"), image]
    return [_text(f"echo:{(arguments or {}).get('q')}")]


def _server() -> Any:
    from mcp.server.lowlevel import Server

    if SDK_MAJOR >= 2:

        async def on_list_tools(_ctx: Any, params: Any) -> types.ListToolsResult:
            names, cursor = PAGES[params.cursor if params else None]
            return types.ListToolsResult(
                tools=[_tool(n) for n in names], next_cursor=cursor
            )

        async def on_call_tool(_ctx: Any, params: Any) -> types.CallToolResult:
            if params.name == "slow":
                await anyio.sleep(30)
            content = _call(params.name, params.arguments)
            if content is None:
                return types.CallToolResult(content=[_text("boom")], is_error=True)
            return types.CallToolResult(content=content)

        return Server("test", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    server = Server("test")

    async def list_tools(request: types.ListToolsRequest | None) -> types.ServerResult:
        # Registered directly: the decorator's request-aware form is newer than
        # the 1.x floor. The SDK passes None to fill its validation cache.
        cursor = request.params.cursor if request and request.params else None
        names, next_cursor = PAGES[cursor]
        return types.ServerResult(
            types.ListToolsResult(
                tools=[_tool(n) for n in names], nextCursor=next_cursor
            )
        )

    server.request_handlers[types.ListToolsRequest] = list_tools

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        if name == "slow":
            await anyio.sleep(30)
        content = _call(name, arguments)
        if content is None:
            raise ValueError("boom")
        return content

    return server


@asynccontextmanager
async def _client_session() -> AsyncIterator[Any]:
    server = _server()
    if SDK_MAJOR >= 2:
        from mcp.client._memory import InMemoryTransport

        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server) as session:
            yield session


@asynccontextmanager
async def _high_level_client() -> AsyncIterator[Any]:
    from mcp import Client  # type: ignore[attr-defined,unused-ignore]

    async with Client(_server()) as client:
        yield client


def _connections() -> list[Any]:
    connections: list[Any] = [pytest.param(_client_session, id="ClientSession")]
    if SDK_MAJOR >= 2:
        connections.append(pytest.param(_high_level_client, id="Client"))
    return connections


@pytest.mark.asyncio
@pytest.mark.parametrize("connect", _connections())
async def test_list_tools_walks_every_page(connect: Any) -> None:
    async with connect() as session:
        specs = await MCPToolAdapter(session).list_tools()
    assert [s.name for s in specs] == ["first", "second", "third"]
    assert specs[0].description == "first tool"
    assert specs[0].input_schema == {"type": "object"}


@pytest.mark.asyncio
@pytest.mark.parametrize("connect", _connections())
async def test_text_result_flows_through_arun_tool(connect: Any) -> None:
    async with connect() as session:
        adapter = MCPToolAdapter(session)
        agent = AgentSession(role="researcher", phase="gather")
        async with agent.asession() as run:
            result = await run.arun_tool("echo", adapter.tool("echo", {"q": "hi"}))
    assert result == "echo:hi"


@pytest.mark.asyncio
@pytest.mark.parametrize("connect", _connections())
async def test_mixed_content_is_preserved(connect: Any) -> None:
    async with connect() as session:
        blocks = await MCPToolAdapter(session).tool("mixed", {})()
    assert [b.type for b in blocks] == ["text", "image"]


@pytest.mark.asyncio
@pytest.mark.parametrize("connect", _connections())
async def test_error_result_raises_typed_error(connect: Any) -> None:
    async with connect() as session:
        with pytest.raises(MCPToolError, match="boom") as caught:
            await MCPToolAdapter(session).tool("fail", {})()
    assert caught.value.tool_name == "fail"


@pytest.mark.asyncio
@pytest.mark.parametrize("connect", _connections())
async def test_timeout_raises_typed_error_with_protocol_code(connect: Any) -> None:
    async with connect() as session:
        factory = MCPToolAdapter(session).tool("slow", {}, timeout=0.05)
        with anyio.fail_after(10):
            with pytest.raises(MCPToolError) as caught:
                await factory()
    assert caught.value.tool_name == "slow"
    assert isinstance(caught.value.code, int)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize("timeout", [0, -1.0, float("nan")])
def test_rejects_non_positive_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        MCPToolAdapter(object()).tool("x", timeout=timeout)


@pytest.mark.skipif(SDK_MAJOR < 2, reason="InputRequiredResult is a 2.x type")
def test_input_required_result_is_a_clear_tool_error() -> None:
    result = types.InputRequiredResult(request_state="pending")
    with pytest.raises(MCPToolError, match=r"InputRequiredResult.*mcp\.Client"):
        MCPToolAdapter._unwrap("ask", result)


def test_tool_error_survives_pickling() -> None:
    error = MCPToolError("failed", tool_name="t", code=408)
    clone = copy.copy(error)
    assert (str(clone), clone.tool_name, clone.code) == ("failed", "t", 408)


class _RepeatingCursorSession:
    """Misbehaving servers; a fake because real SDK servers cannot send these."""

    def __init__(self, cursors: list[str | None]) -> None:
        self.cursors = cursors
        self.requested: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> Any:
        self.requested.append(cursor)
        next_cursor = self.cursors[len(self.requested) - 1]
        tool = _tool("only")

        class Page:
            tools = [tool]
            nextCursor = next_cursor  # noqa: N815

        return Page()


@pytest.mark.asyncio
async def test_empty_cursor_ends_pagination() -> None:
    session = _RepeatingCursorSession([""])
    specs = await MCPToolAdapter(session).list_tools()
    assert session.requested == [None]
    assert [s.name for s in specs] == ["only"]


@pytest.mark.asyncio
async def test_repeated_cursor_terminates_without_duplicate_tools() -> None:
    session = _RepeatingCursorSession(["a", "a"])
    specs = await MCPToolAdapter(session).list_tools()
    assert session.requested == [None, "a"]
    assert [s.name for s in specs] == ["only"]
