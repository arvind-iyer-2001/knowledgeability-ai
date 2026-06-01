"""
KX Knowledge Graph MCP Server — SSE transport.
Runs on http://localhost:8765 so Claude Desktop can connect from Windows.

Usage: python3 mcp_server.py
"""

import asyncio
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, Mount

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types

from graphiti_core import Graphiti
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.client import CrossEncoderClient

NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
PORT = int(os.getenv("MCP_PORT", "8765"))


class PassthroughReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 1.0) for p in passages]


def build_graphiti() -> Graphiti:
    llm = AnthropicClient(LLMConfig(
        model="claude-haiku-4-5-20251001",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    ))
    embedder = OpenAIEmbedder(OpenAIEmbedderConfig(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        embedding_model=OLLAMA_EMBED_MODEL,
    ))
    return Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=PassthroughReranker(),
    )


app = Server("kx-knowledge-graph")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_kx_knowledge",
            description=(
                "Search the KX/kdb+ knowledge graph built from official KX documentation, "
                "whitepapers, and repositories. Use for questions about kdb+, q language, "
                "PyKX, KDB.AI, kdb+tick, APIs, architecture patterns, and KX integrations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language question about KX/kdb+ technology",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "search_kx_knowledge":
        raise ValueError(f"Unknown tool: {name}")

    query = arguments["query"]
    num_results = arguments.get("num_results", 10)

    client = build_graphiti()
    try:
        results = await client.search(query, num_results=num_results)
    finally:
        await client.close()

    if not results:
        return [types.TextContent(type="text", text="No results found in the knowledge graph.")]

    lines = [f"Knowledge graph results for: {query}\n"]
    for i, edge in enumerate(results, 1):
        lines.append(f"{i}. [{edge.name}] {edge.fact}")
        if edge.valid_at:
            lines.append(f"   (valid from: {edge.valid_at.date()})")

    return [types.TextContent(type="text", text="\n".join(lines))]


def make_starlette_app() -> Starlette:
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())

    return Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ])


if __name__ == "__main__":
    print(f"KX Knowledge Graph MCP server running on http://localhost:{PORT}/sse")
    uvicorn.run(make_starlette_app(), host="0.0.0.0", port=PORT)
