"""Tests for the optional MCP tool adapter (requires the [mcp] extra)."""

from __future__ import annotations

from typing import Any

import pytest

# Entire module requires the optional MCP dependency.
pytest.importorskip("mcp")

from mcp.types import (  # noqa: E402
    CallToolResult,
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

    async def list_tools(self) -> ListToolsResult:
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
            ]
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
        return CallToolResult(
            content=[TextContent(type="text", text=f"result for {name}")],
            isError=False,
        )


@pytest.mark.asyncio
async def test_list_tools_returns_specs() -> None:
    adapter = MCPToolAdapter(FakeSession())
    specs = await adapter.list_tools()
    assert [s.name for s in specs] == ["search", "noop"]
    assert isinstance(specs[0], MCPToolSpec)
    assert specs[0].description == "Search the web"
    assert specs[0].input_schema["type"] == "object"


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
