"""Tests for the optional MCP tool adapter (requires the [mcp] extra)."""

from __future__ import annotations

from typing import Any

import pytest

# Entire module requires the optional MCP dependency.
pytest.importorskip("mcp")

from mcp.types import (  # noqa: E402
    CallToolResult,
    ImageContent,
    ListToolsResult,
    TextContent,
    Tool,
)

from techrevati.runtime import AgentSession  # noqa: E402
from techrevati.runtime.mcp import (  # noqa: E402
    MCPToolAdapter,
    MCPToolError,
    MCPToolSpec,
)


class FakeSession:
    """Duck-typed stand-in for ``mcp.ClientSession`` (no real server needed)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.list_cursors: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        # Two-page response so the adapter must follow nextCursor.
        self.list_cursors.append(cursor)
        if cursor is None:
            return ListToolsResult(
                tools=[
                    Tool(
                        name="search",
                        description="Search the web",
                        inputSchema={
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                        },
                    ),
                    Tool(name="noop", description=None, inputSchema={"type": "object"}),
                ],
                nextCursor="page2",
            )
        return ListToolsResult(
            tools=[
                Tool(
                    name="summarize",
                    description="Summarize text",
                    inputSchema={"type": "object"},
                ),
            ],
            nextCursor=None,
        )

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        if name == "boom":
            return CallToolResult(
                content=[TextContent(type="text", text="kaboom")], isError=True
            )
        if name == "structured":
            return CallToolResult(
                content=[], structuredContent={"answer": 42}, isError=False
            )
        if name == "mixed":
            return CallToolResult(
                content=[
                    TextContent(type="text", text="caption"),
                    ImageContent(type="image", data="aGk=", mimeType="image/png"),
                ],
                isError=False,
            )
        return CallToolResult(
            content=[TextContent(type="text", text=f"result for {name}")],
            isError=False,
        )


@pytest.mark.asyncio
async def test_list_tools_returns_specs() -> None:
    adapter = MCPToolAdapter(FakeSession())
    specs = await adapter.list_tools()
    # Includes the tool on page 2 — pagination is followed, not dropped.
    assert [s.name for s in specs] == ["search", "noop", "summarize"]
    assert isinstance(specs[0], MCPToolSpec)
    assert specs[0].description == "Search the web"
    assert specs[0].input_schema["type"] == "object"


@pytest.mark.asyncio
async def test_list_tools_follows_pagination_cursor() -> None:
    session = FakeSession()
    specs = await MCPToolAdapter(session).list_tools()
    assert [s.name for s in specs] == ["search", "noop", "summarize"]
    # The adapter requested the first page (None) then followed nextCursor.
    assert session.list_cursors == [None, "page2"]


@pytest.mark.asyncio
async def test_unwrap_mixed_content_preserves_non_text() -> None:
    # text + image must not collapse to text-only (no silent drop of the image).
    result = await MCPToolAdapter(FakeSession()).tool("mixed")()
    assert isinstance(result, list)
    assert len(result) == 2
    assert {b.type for b in result} == {"text", "image"}


@pytest.mark.asyncio
async def test_tool_factory_calls_and_unwraps_text() -> None:
    session = FakeSession()
    adapter = MCPToolAdapter(session)
    factory = adapter.tool("search", {"q": "EU AI Act"})
    result = await factory()
    assert result == "result for search"
    assert session.calls == [("search", {"q": "EU AI Act"})]


@pytest.mark.asyncio
async def test_tool_factory_prefers_structured_content() -> None:
    adapter = MCPToolAdapter(FakeSession())
    result = await adapter.tool("structured")()
    assert result == {"answer": 42}


@pytest.mark.asyncio
async def test_error_result_raises() -> None:
    adapter = MCPToolAdapter(FakeSession())
    with pytest.raises(MCPToolError) as exc:
        await adapter.tool("boom")()
    assert "kaboom" in str(exc.value)


@pytest.mark.asyncio
async def test_adapter_tool_runs_through_arun_tool() -> None:
    """The factory slots into arun_tool and passes permission/guardrail/hooks."""
    adapter = MCPToolAdapter(FakeSession())
    agent = AgentSession(role="researcher", phase="gather")
    async with agent.asession() as session:
        result = await session.arun_tool("search", adapter.tool("search", {"q": "x"}))
    assert result == "result for search"


def test_empty_tool_name_rejected() -> None:
    adapter = MCPToolAdapter(FakeSession())
    with pytest.raises(ValueError):
        adapter.tool("  ")
