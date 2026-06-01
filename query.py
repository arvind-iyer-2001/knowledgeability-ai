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
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.client import CrossEncoderClient

NEO4J_URI     = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER    = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_LLM_MODEL  = os.getenv("OLLAMA_LLM_MODEL", "gemma3:27b")


class PassthroughReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 1.0) for p in passages]


def build_llm(llm_provider: str, model: str | None = None):
    if llm_provider == "ollama":
        m = model or OLLAMA_LLM_MODEL
        return OpenAIGenericClient(LLMConfig(
            model=m,
            small_model=m,
            api_key="ollama",
            base_url=OLLAMA_BASE_URL,
        ))
    return AnthropicClient(LLMConfig(
        model=model or "claude-haiku-4-5-20251001",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    ))


def build_graphiti(llm_provider: str, model: str | None = None) -> Graphiti:
    embedder = OpenAIEmbedder(OpenAIEmbedderConfig(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        embedding_model=OLLAMA_EMBED_MODEL,
    ))
    return Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=build_llm(llm_provider, model),
        embedder=embedder,
        cross_encoder=PassthroughReranker(),
    )


async def query(question: str, num_results: int = 10, llm_provider: str = "anthropic", model: str | None = None, group_ids: list[str] | None = None):
    client = build_graphiti(llm_provider, model)
    results = await client.search(question, num_results=num_results, group_ids=group_ids)
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


async def interactive(llm_provider: str = "anthropic", model: str | None = None, group_ids: list[str] | None = None):
    label = f" [{', '.join(group_ids)}]" if group_ids else " [all groups]"
    print(f"KX Knowledge Graph{label} — type 'quit' to exit\n")
    client = build_graphiti(llm_provider, model)

    while True:
        try:
            q = input("\nAsk> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue

        results = await client.search(q, num_results=10, group_ids=group_ids)
        if not results:
            print("No results.")
            continue

        print(f"\n{'='*60}")
        for i, edge in enumerate(results, 1):
            print(f"[{i}] {edge.name}")
            print(f"    {edge.fact}\n")

    await client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Query the KX knowledge graph")
    parser.add_argument("question", nargs="*", help="Question to ask (omit for interactive mode)")
    parser.add_argument(
        "--llm", choices=["anthropic", "ollama"], default="anthropic",
        help="LLM provider for query entity extraction. Default: anthropic",
    )
    parser.add_argument(
        "--model", default=None,
        help="Model name override (e.g. gemma3:27b for ollama, claude-haiku-... for anthropic)",
    )
    parser.add_argument(
        "--group", action="append", dest="groups", metavar="GROUP_ID",
        help="Filter results to a specific group (repeatable, e.g. --group haiku --group llama-fast)",
    )
    args = parser.parse_args()

    if args.question:
        asyncio.run(query(" ".join(args.question), llm_provider=args.llm, model=args.model, group_ids=args.groups))
    else:
        asyncio.run(interactive(llm_provider=args.llm, model=args.model, group_ids=args.groups))
