"""
Smoke test for the KX knowledge graph MCP server (stdio transport).

Spawns mcp_server_stdio.py as a subprocess and drives it the same way
Claude Desktop would: initialize -> list_tools -> call_tool.

Usage: uv run python3 test_mcp_server.py
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = Path(__file__).parent / "mcp_server_stdio.py"

TEST_CALLS = [
    {"query": "How does the tickerplant publish data to subscribers?", "group_ids": ["production"]},
    {"query": "What does .u.upd do?", "num_results": 5},
    {"query": "What index types does KDB.AI support?"},
]


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools: {names}")
            assert "search_kx_knowledge" in names, "search_kx_knowledge tool missing"

            for args in TEST_CALLS:
                print(f"\n--- call_tool {args} ---")
                result = await session.call_tool("search_kx_knowledge", args)
                assert not result.isError, f"tool call returned error for {args}"
                for block in result.content:
                    if block.type == "text":
                        print(block.text[:800])

    print("\nOK - MCP server responds correctly.")


if __name__ == "__main__":
    asyncio.run(main())
