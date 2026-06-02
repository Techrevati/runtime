# MCP tool integration

`techrevati-runtime` can drive tools exposed by a [Model Context
Protocol](https://modelcontextprotocol.io) server. Install the optional extra:

```bash
pip install 'techrevati-runtime[mcp]'
```

The runtime has **no tool registry** — `run_tool` / `arun_tool` execute
caller-supplied callables. `MCPToolAdapter` therefore registers nothing: it wraps
a connected `mcp.ClientSession` and hands back coroutine factories that slot
straight into `arun_tool`, so MCP tool calls still pass the permission, guardrail,
governance, and hook checks.

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
- otherwise text content blocks are joined into a single string;
- otherwise the raw content list is returned;
- an error result raises `MCPToolError`.

## Lifecycle ownership

The adapter does **not** own the MCP connection — you manage `stdio_client` /
`sse_client` and `ClientSession` lifetimes (and call `initialize()`). The adapter
only bridges `call_tool` into the runtime's tool path. The runtime core stays
zero-dependency; the `mcp` package is imported behind a guard and is only needed
when this module is used.
