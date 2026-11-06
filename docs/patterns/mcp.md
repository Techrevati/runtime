# MCP tool integration

`techrevati-runtime` can drive tools exposed by a [Model Context
Protocol](https://modelcontextprotocol.io) server. Install the optional extra:

```bash
pip install 'techrevati-runtime[mcp]'
```

The runtime has **no tool registry** — `run_tool` / `arun_tool` execute
caller-supplied callables. `MCPToolAdapter` therefore registers nothing: it wraps
a connected client and hands back coroutine factories that slot straight into
`arun_tool`, so MCP tool calls still pass the permission, guardrail, governance,
and hook checks.

The adapter works with a `ClientSession` on both SDK lines (1.x and 2.x) and with
the 2.x high-level `mcp.Client`. On 2.x, prefer `Client`: it negotiates the protocol
version and answers servers that ask for more input, while a `ClientSession` you
initialize yourself uses the pre-2026 handshake and cannot answer those requests.

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from techrevati.runtime import AgentSession
from techrevati.runtime.mcp import MCPToolAdapter

async def main() -> None:
    params = StdioServerParameters(command="my-mcp-server", args=[])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            adapter = MCPToolAdapter(mcp_session)

            for spec in await adapter.list_tools():
                print(spec.name, "-", spec.description)

            agent = AgentSession(role="researcher", phase="gather")
            async with agent.asession() as session:
                result = await session.arun_tool(
                    "search", adapter.tool("search", {"q": "EU AI Act"})
                )
                print(result)
```

## Result normalization

`adapter.tool(name, arguments)` returns a factory; each call invokes the MCP tool
once and normalizes its `CallToolResult`:

- `structuredContent` is returned as-is when present;
- otherwise, if **every** content block is text, they are joined into a single
  string;
- otherwise the raw content list is returned — non-text blocks (images, audio,
  embedded resources), **including those mixed with text**, are preserved rather
  than silently dropped, and the caller decides how to handle them;
- a failure raises `MCPToolError`, with `tool_name` set. This covers error results,
  protocol errors (`code` then holds the protocol error code, and the SDK's
  exception is the `__cause__`), and results that are not a finished tool result,
  such as the `InputRequiredResult` a 2.x `ClientSession` returns when the server
  needs client input.

`adapter.tool(name, arguments, timeout=30.0)` sets a per-call limit in seconds,
enforced by the SDK; exceeding it raises `MCPToolError`.

`adapter.list_tools()` walks the MCP pagination to completion, so it returns every
advertised tool, not just the first page.

## Lifecycle ownership

The adapter does **not** own the MCP connection — you manage the transport and
client lifetimes (including `initialize()` on a `ClientSession`). The adapter
only bridges `call_tool` into the runtime's tool path. The runtime core stays
zero-dependency; the `mcp` package is imported behind a guard and is only needed
when this module is used.
