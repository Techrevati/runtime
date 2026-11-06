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

Works with both SDK lines: a 1.x or 2.x ``ClientSession``, and the 2.x high-level
``mcp.Client``, which also handles servers that ask the client for more input.

Zero-dependency invariant: the ``mcp`` package is imported behind a guard and is
only needed when this module is used (install ``techrevati-runtime[mcp]``).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version as _pkg_version
from typing import Any

try:
    from mcp import types as _mcp_types
    from mcp.shared import exceptions as _mcp_exceptions

    _MCP_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - import guard
    _MCP_AVAILABLE = False
    _MCP_IMPORT_ERROR: ImportError | None = exc
else:
    _MCP_IMPORT_ERROR = None

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
    """Raised when an MCP tool call fails.

    Covers error results, protocol errors (including request timeouts), and
    results that cannot be used as a tool's outcome. ``tool_name`` names the tool;
    ``code`` carries the protocol error code when the failure was a protocol error.
    """

    def __init__(
        self, message: str, *, tool_name: str | None = None, code: int | None = None
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.code = code

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild_tool_error, (self.args[0], self.tool_name, self.code))


def _rebuild_tool_error(
    message: str, tool_name: str | None, code: int | None
) -> MCPToolError:
    return MCPToolError(message, tool_name=tool_name, code=code)


def _sdk_major_version() -> int:
    return int(_pkg_version("mcp").split(".", 1)[0])


def _protocol_error_types() -> tuple[type[BaseException], ...]:
    # 2.x renamed McpError to MCPError; each line only defines its own spelling.
    return tuple(
        error_type
        for error_type in (
            getattr(_mcp_exceptions, "MCPError", None),
            getattr(_mcp_exceptions, "McpError", None),
        )
        if isinstance(error_type, type) and issubclass(error_type, BaseException)
    )


def _protocol_error_detail(exc: BaseException) -> tuple[str, int | None]:
    # 2.x exposes .message / .code directly, 1.x nests them under .error.
    source = getattr(exc, "error", exc)
    code = getattr(source, "code", None)
    message = getattr(source, "message", None)
    return (
        message if isinstance(message, str) and message else str(exc),
        code if isinstance(code, int) else None,
    )


def _result_field(result: Any, snake: str, camel: str) -> Any:
    """Read a result field under its 2.x (snake_case) or 1.x (camelCase) name."""
    for name in (snake, camel):
        value = getattr(result, name, None)
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class MCPToolSpec:
    """A tool advertised by an MCP server."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


class MCPToolAdapter:
    """Expose a connected MCP client's tools to ``arun_tool``.

    ``session`` is a connected ``mcp.ClientSession`` or, on SDK 2.x, an entered
    ``mcp.Client``. It is typed loosely because the two SDK lines share no class.
    The caller owns the connection lifecycle; the adapter only bridges ``call_tool``
    into a coroutine factory and normalizes the result.
    """

    def __init__(self, session: Any) -> None:
        _require_mcp()
        self._session = session

    async def list_tools(self) -> list[MCPToolSpec]:
        """List every tool the MCP server advertises, following pagination.

        The MCP ``tools/list`` response is paginated; this walks ``nextCursor``
        until the server stops returning one, so tools beyond the first page are
        not silently dropped. A seen-cursor guard avoids an infinite loop if a
        misbehaving server repeats a cursor, and tools are keyed by name so that a
        repeated page does not yield duplicates. An empty cursor ends the walk.
        """
        specs: dict[str, MCPToolSpec] = {}
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = await self._list_tools_page(cursor)
            for tool in result.tools:
                specs.setdefault(
                    tool.name,
                    MCPToolSpec(
                        name=tool.name,
                        description=tool.description,
                        input_schema=dict(
                            _result_field(tool, "input_schema", "inputSchema") or {}
                        ),
                    ),
                )
            cursor = _result_field(result, "next_cursor", "nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return list(specs.values())

    async def _list_tools_page(self, cursor: str | None) -> Any:
        parameters = inspect.signature(self._session.list_tools).parameters
        if "cursor" in parameters:
            return await self._session.list_tools(cursor=cursor)
        # 2.x ClientSession: pagination moved into a request-params model.
        if cursor is None:
            return await self._session.list_tools()
        return await self._session.list_tools(
            params=_mcp_types.PaginatedRequestParams(cursor=cursor)
        )

    def tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Callable[[], Awaitable[Any]]:
        """Return a coroutine factory for ``arun_tool(name, factory)``.

        Each call to the returned factory invokes the MCP tool once and returns
        its normalized result. ``timeout`` is a per-call limit in seconds, enforced
        by the SDK; exceeding it raises :class:`MCPToolError`.
        """
        if not name.strip():
            raise ValueError("tool name must be non-empty")
        if timeout is not None and not timeout > 0:
            raise ValueError("timeout must be positive")

        async def _factory() -> Any:
            options: dict[str, Any] = {}
            if timeout is not None:
                options["read_timeout_seconds"] = (
                    timedelta(seconds=timeout)
                    if _sdk_major_version() < 2
                    else float(timeout)
                )
            try:
                result = await self._session.call_tool(name, arguments, **options)
            except _protocol_error_types() as exc:
                detail, code = _protocol_error_detail(exc)
                raise MCPToolError(
                    f"MCP tool {name!r} failed: {detail}", tool_name=name, code=code
                ) from exc
            return self._unwrap(name, result)

        return _factory

    @staticmethod
    def _unwrap(name: str, result: Any) -> Any:
        """Normalize a ``CallToolResult`` into a plain Python value.

        Order of preference:

        1. ``structuredContent`` when the server provides it.
        2. a single joined string when *every* content block is text.
        3. otherwise the raw content list — so non-text blocks (images, audio,
           embedded resources), **including those mixed with text**, are never
           silently dropped; the caller decides how to handle them.

        Raises :class:`MCPToolError` when the server flags an error, or when the
        result is not a finished tool result: on 2.x a ``ClientSession`` returns an
        ``InputRequiredResult`` when the server needs client input, and only
        ``mcp.Client`` answers those rounds.
        """
        blocks = getattr(result, "content", None)
        if blocks is None:
            raise MCPToolError(
                f"MCP tool {name!r} returned {type(result).__name__} instead of a "
                "tool result; if the server asks for client input, connect "
                "through mcp.Client, which handles those rounds",
                tool_name=name,
            )
        if _result_field(result, "is_error", "isError"):
            texts = [
                getattr(block, "text", "")
                for block in blocks
                if getattr(block, "type", None) == "text"
            ]
            detail = " ".join(t for t in texts if t) or "unknown MCP tool error"
            raise MCPToolError(f"MCP tool {name!r} failed: {detail}", tool_name=name)
        structured = _result_field(result, "structured_content", "structuredContent")
        if structured is not None:
            return structured
        if blocks and all(getattr(b, "type", None) == "text" for b in blocks):
            return "\n".join(str(getattr(b, "text", "")) for b in blocks)
        return blocks
