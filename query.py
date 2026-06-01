"""
Query the KX knowledge graph.
Usage: python3 query.py "What does kdb+ use for data recovery?"
       python3 query.py  # interactive mode
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from graphiti_core import Graphiti
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.client import CrossEncoderClient

NEO4J_URI     = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER    = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


class PassthroughReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 1.0) for p in passages]


async def query(question: str, num_results: int = 10):
    llm = AnthropicClient(LLMConfig(
        model="claude-haiku-4-5-20251001",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    ))
    embedder = OpenAIEmbedder(OpenAIEmbedderConfig(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        embedding_model=OLLAMA_EMBED_MODEL,
    ))
    client = Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=PassthroughReranker(),
    )

    results = await client.search(question, num_results=num_results)
    await client.close()

    if not results:
        print("No results found.")
        return

    print(f"\n{'='*60}")
    print(f"Query: {question}")
    print(f"{'='*60}")
    for i, edge in enumerate(results, 1):
        print(f"\n[{i}] {edge.name}")
        print(f"    Fact: {edge.fact}")
        print(f"    Valid: {edge.valid_at or 'unknown'}")


async def interactive():
    print("KX Knowledge Graph — type 'quit' to exit\n")
    llm = AnthropicClient(LLMConfig(
        model="claude-haiku-4-5-20251001",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    ))
    embedder = OpenAIEmbedder(OpenAIEmbedderConfig(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        embedding_model=OLLAMA_EMBED_MODEL,
    ))
    client = Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=PassthroughReranker(),
    )

    while True:
        try:
            q = input("\nAsk> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue

        results = await client.search(q, num_results=10)
        if not results:
            print("No results.")
            continue

        print(f"\n{'='*60}")
        for i, edge in enumerate(results, 1):
            print(f"[{i}] {edge.name}")
            print(f"    {edge.fact}\n")

    await client.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(query(" ".join(sys.argv[1:])))
    else:
        asyncio.run(interactive())
