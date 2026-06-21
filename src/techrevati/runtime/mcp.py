"""
MCP — Model Context Protocol tool integration (optional ``[mcp]`` extra).

Bridges tools exposed by an MCP server into the runtime's tool-execution
lifecycle. The runtime has no tool registry — ``run_tool`` / ``arun_tool`` execute
caller-supplied callables — so :class:`MCPToolAdapter` does not register anything;
it wraps a connected ``mcp.ClientSession`` and hands back coroutine factories that
slot straight into ``AsyncOrchestrationSession.arun_tool``:

    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from techrevati.runtime import AgentSession
    from techrevati.runtime.mcp import MCPToolAdapter

    async with stdio_client(StdioServerParameters(command="my-mcp-server")) as (r, w):
        async with ClientSession(r, w) as mcp_session:
            await mcp_session.initialize()
            adapter = MCPToolAdapter(mcp_session)

            agent = AgentSession(role="researcher", phase="gather")
            async with agent.asession() as session:
                result = await session.arun_tool(
                    "search", adapter.tool("search", {"q": "EU AI Act"})
                )

Because the tool flows through ``arun_tool``, the call still passes the
permission, guardrail, governance, and hook checks unchanged. ``run_tool`` /
``arun_tool`` signatures are not modified.

Zero-dependency invariant: the ``mcp`` package is imported behind a guard and is
only needed when this module is used (install ``techrevati-runtime[mcp]``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:
    from mcp import ClientSession

    _MCP_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - import guard
    _MCP_AVAILABLE = False
    _MCP_IMPORT_ERROR: ImportError | None = exc
else:
    _MCP_IMPORT_ERROR = None

if TYPE_CHECKING:  # pragma: no cover - type-only
    from mcp import ClientSession
    from mcp.types import CallToolResult

__all__ = [
    "MCPToolAdapter",
    "MCPToolError",
    "MCPToolSpec",
]


def _require_mcp() -> None:
    if not _MCP_AVAILABLE:
        raise ImportError(
            "MCP integration requires the [mcp] extra. "
            "Install with: pip install 'techrevati-runtime[mcp]'"
        ) from _MCP_IMPORT_ERROR


class MCPToolError(RuntimeError):
    """Raised when an MCP tool call returns an error result."""


@dataclass(frozen=True)
class MCPToolSpec:
    """A tool advertised by an MCP server."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


class MCPToolAdapter:
    """Expose a connected ``mcp.ClientSession``'s tools to ``arun_tool``.

    The caller owns the MCP connection lifecycle (``stdio_client`` / ``sse_client``
    + ``ClientSession``); the adapter only bridges ``call_tool`` into a coroutine
    factory and normalizes the result.
    """

    def __init__(self, session: ClientSession) -> None:
        _require_mcp()
        self._session = session

    async def list_tools(self) -> list[MCPToolSpec]:
        """List every tool the MCP server advertises, following pagination.

        The MCP ``tools/list`` response is paginated; this walks ``nextCursor``
        until the server stops returning one, so tools beyond the first page are
        not silently dropped. A seen-cursor guard avoids an infinite loop if a
        misbehaving server repeats a cursor.
        """
        specs: list[MCPToolSpec] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = await self._session.list_tools(cursor=cursor)
            specs.extend(
                MCPToolSpec(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.inputSchema or {}),
                )
                for tool in result.tools
            )
            cursor = result.nextCursor
            if cursor is None or cursor in seen:
                break
            seen.add(cursor)
        return specs

    def tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Callable[[], Awaitable[Any]]:
        """Return a coroutine factory for ``arun_tool(name, factory)``.

        Each call to the returned factory invokes the MCP tool once and returns
        its normalized result.
        """
        if not name.strip():
            raise ValueError("tool name must be non-empty")

        async def _factory() -> Any:
            result = await self._session.call_tool(name, arguments)
            return self._unwrap(name, result)

        return _factory

    @staticmethod
    def _unwrap(name: str, result: CallToolResult) -> Any:
        """Normalize a ``CallToolResult`` into a plain Python value.

        Order of preference:

        1. ``structuredContent`` when the server provides it.
        2. a single joined string when *every* content block is text.
        3. otherwise the raw content list — so non-text blocks (images, audio,
           embedded resources), **including those mixed with text**, are never
           silently dropped; the caller decides how to handle them.

        Raises :class:`MCPToolError` when the server flags an error.
        """
        if result.isError:
            texts = [
                getattr(block, "text", "")
                for block in result.content
                if getattr(block, "type", None) == "text"
            ]
            detail = " ".join(t for t in texts if t) or "unknown MCP tool error"
            raise MCPToolError(f"MCP tool {name!r} failed: {detail}")
        if result.structuredContent is not None:
            return result.structuredContent
        blocks = result.content
        if blocks and all(getattr(b, "type", None) == "text" for b in blocks):
            return "\n".join(str(getattr(b, "text", "")) for b in blocks)
        return blocks
