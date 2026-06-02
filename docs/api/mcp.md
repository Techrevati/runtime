# MCP Integration

Requires `pip install 'techrevati-runtime[mcp]'`.

`MCPToolAdapter` wraps a connected `mcp.ClientSession` and exposes its tools as
coroutine factories for `AsyncOrchestrationSession.arun_tool`. See the
[MCP pattern](../patterns/mcp.md) for a worked example.

::: techrevati.runtime.mcp
