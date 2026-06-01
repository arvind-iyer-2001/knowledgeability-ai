"""
Ingestion pipeline: reads files from dump/, chunks them, feeds to Graphiti.

Usage:
  python3 ingest.py                                          # all repos, ollama embedder
  python3 ingest.py --repo pykx                             # one repo
  python3 ingest.py --path dump/docs/docs/wp --model haiku  # specific path, haiku LLM
  python3 ingest.py --embedder voyage                       # use Voyage AI for embeddings
"""

import argparse
import asyncio
import os
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from graphiti_core import Graphiti
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.cross_encoder.client import CrossEncoderClient

DUMP_DIR = Path(__file__).parent / "dump"
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

INCLUDE_EXTENSIONS = {".md", ".py", ".q", ".rst", ".txt", ".yaml", ".yml", ".json"}
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".tox", "dist", "build"}

MODELS = {
    "opus":   "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}


class PassthroughReranker(CrossEncoderClient):
    """No-op reranker — returns passages in original order with equal scores."""
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 1.0) for p in passages]


def build_embedder(embedder_name: str) -> EmbedderClient:
    if embedder_name == "voyage":
        from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig
        voyage_key = os.environ.get("VOYAGE_API_KEY")
        if not voyage_key:
            raise ValueError("VOYAGE_API_KEY not set")
        return VoyageAIEmbedder(VoyageAIEmbedderConfig(api_key=voyage_key))

    if embedder_name == "ollama":
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        return OpenAIEmbedder(OpenAIEmbedderConfig(
            api_key="ollama",  # Ollama ignores the key but openai client requires a non-empty value
            base_url=OLLAMA_BASE_URL,
            embedding_model=OLLAMA_EMBED_MODEL,
        ))

    raise ValueError(f"Unknown embedder: {embedder_name}. Choose: ollama, voyage")


def collect_files(root: Path, repo: str | None, path: Path | None) -> list[Path]:
    if path is not None:
        if path.is_file():
            return [path] if path.suffix in INCLUDE_EXTENSIONS else []
        search_root = path
    elif repo is not None:
        search_root = root / repo
        if not search_root.exists():
            available = [d.name for d in root.iterdir() if d.is_dir()]
            raise ValueError(f"Repo '{repo}' not found. Available: {available}")
    else:
        search_root = root

    files = []
    for f in search_root.rglob("*"):
        if f.is_file() and f.suffix in INCLUDE_EXTENSIONS:
            if not any(p in EXCLUDE_DIRS for p in f.parts):
                files.append(f)
    return sorted(files)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


async def ingest(repo: str | None, path: Path | None, model: str, embedder_name: str):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    llm = AnthropicClient(LLMConfig(model=model, api_key=api_key))
    embedder = build_embedder(embedder_name)
    cross_encoder = PassthroughReranker()

    client = Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    await client.build_indices_and_constraints()

    files = collect_files(DUMP_DIR, repo, path)
    print(f"Found {len(files)} files")

    total_episodes = 0
    for file_path in files:
        rel = file_path.relative_to(DUMP_DIR) if file_path.is_relative_to(DUMP_DIR) else file_path
        repo_name = rel.parts[0] if file_path.is_relative_to(DUMP_DIR) else (repo or "unknown")

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            print(f"  SKIP {rel}: {e}")
            continue

        if not text:
            continue

        chunks = chunk_text(text)
        print(f"[{repo_name}] {file_path.name} — {len(chunks)} chunk(s)")

        for i, chunk in enumerate(chunks):
            episode_name = f"{rel}:chunk{i}"
            source_desc = f"File: {rel} (chunk {i+1}/{len(chunks)}) from repo {repo_name}"
            try:
                await client.add_episode(
                    name=episode_name,
                    episode_body=chunk,
                    source_description=source_desc,
                    reference_time=datetime.now(timezone.utc),
                )
                total_episodes += 1
            except Exception as e:
                print(f"  ERROR {episode_name}: {e}")

    print(f"\nDone. {total_episodes} episodes ingested.")
    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest KX repo files into Graphiti")
    parser.add_argument("--repo", help="Repo name under dump/ (e.g. pykx)")
    parser.add_argument("--path", help="Specific file or directory path to ingest")
    parser.add_argument(
        "--model", choices=MODELS.keys(), default="opus",
        help="LLM for extraction: opus (best), sonnet (balanced), haiku (fast/cheap). Default: opus",
    )
    parser.add_argument(
        "--embedder", choices=["ollama", "voyage"], default="ollama",
        help="Embedding backend: ollama (local, default) or voyage (API). Default: ollama",
    )
    args = parser.parse_args()

    path_arg = Path(args.path) if args.path else None
    if path_arg and not path_arg.exists():
        raise SystemExit(f"Path not found: {path_arg}")

    model_id = MODELS[args.model]
    print(f"Model: {args.model} ({model_id}) | Embedder: {args.embedder}")
    asyncio.run(ingest(repo=args.repo, path=path_arg, model=model_id, embedder_name=args.embedder))
