"""
KX Knowledge Graph MCP Server — stdio transport for Claude Desktop.
Launched via mcp_kx.bat on Windows.
"""

import asyncio
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
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
                    "group_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter results to specific ingestion groups, e.g. ['haiku'], ['llama-fast'], or ['haiku','llama-fast']. Omit to search all groups.",
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
    group_ids = arguments.get("group_ids") or None

    client = build_graphiti()
    try:
        results = await client.search(query, num_results=num_results, group_ids=group_ids)
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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
