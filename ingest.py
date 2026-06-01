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
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_log_file = LOG_DIR / f"ingest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

from graphiti_core import Graphiti
from anthropic import AsyncAnthropic
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.client import ModelSize
from graphiti_core.prompts.models import Message
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.cross_encoder.client import CrossEncoderClient


class CachedAnthropicClient(AnthropicClient):
    """AnthropicClient with prompt caching on the system message.

    Adds cache_control to the system prompt block so Anthropic caches it
    across calls — saves ~80% of input tokens since system prompts are
    identical for all Graphiti extraction calls.
    """

    async def _generate_response(
        self,
        messages: list[Message],
        response_model=None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
    ):
        import typing
        from anthropic.types import MessageParam, ToolChoiceParam, ToolUnionParam

        system_message = messages[0]
        user_messages = [{'role': m.role, 'content': m.content} for m in messages[1:]]
        user_messages_cast = typing.cast(list[MessageParam], user_messages)
        max_creation_tokens = self._resolve_max_tokens(max_tokens, self.model)
        tools, tool_choice = self._create_tool(response_model)

        result = await self.client.messages.create(
            system=[{
                "type": "text",
                "text": system_message.content,
                "cache_control": {"type": "ephemeral"},
            }],
            max_tokens=max_creation_tokens,
            temperature=self.temperature,
            messages=user_messages_cast,
            model=self.model,
            tools=tools,
            tool_choice=tool_choice,
            betas=["prompt-caching-2024-07-31"],
        )

        input_tokens = result.usage.input_tokens if result.usage else 0
        output_tokens = result.usage.output_tokens if result.usage else 0
        cache_read = getattr(result.usage, 'cache_read_input_tokens', 0) or 0
        cache_write = getattr(result.usage, 'cache_creation_input_tokens', 0) or 0
        if cache_read or cache_write:
            log.debug(f"Cache — read: {cache_read} write: {cache_write}")

        return result, input_tokens, output_tokens

DUMP_DIR = Path(__file__).parent / "dump"
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "gemma3:27b")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

INCLUDE_EXTENSIONS = {".md", ".py", ".q", ".rst", ".txt", ".yaml", ".yml", ".json"}
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".tox", "dist", "build"}

ANTHROPIC_MODELS = {
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


def build_llm(llm_provider: str, model: str) -> AnthropicClient | OpenAIGenericClient:
    if llm_provider == "ollama":
        return OpenAIGenericClient(LLMConfig(
            model=model,
            small_model=model,
            api_key="ollama",
            base_url=OLLAMA_BASE_URL,
        ))
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    return CachedAnthropicClient(LLMConfig(model=model, api_key=api_key))


async def ingest(repo: str | None, path: Path | None, model: str, embedder_name: str, llm_provider: str, group_id: str | None = None):
    llm = build_llm(llm_provider, model)
    embedder = build_embedder(embedder_name)
    cross_encoder = PassthroughReranker()

    client = Graphiti(
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    await client.build_indices_and_constraints()

    effective_group_id = group_id or llm_provider
    log.info(f"Group ID: {effective_group_id}")

    files = collect_files(DUMP_DIR, repo, path)
    log.info(f"Found {len(files)} files")

    total_episodes = 0
    for file_path in files:
        rel = file_path.relative_to(DUMP_DIR) if file_path.is_relative_to(DUMP_DIR) else file_path
        repo_name = rel.parts[0] if file_path.is_relative_to(DUMP_DIR) else (repo or "unknown")

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            log.warning(f"SKIP {rel}: {e}")
            continue

        if not text:
            continue

        chunks = chunk_text(text)
        log.info(f"[{repo_name}] {file_path.name} — {len(chunks)} chunk(s)")

        for i, chunk in enumerate(chunks):
            episode_name = f"{rel}:chunk{i}"
            source_desc = f"File: {rel} (chunk {i+1}/{len(chunks)}) from repo {repo_name}"
            try:
                await client.add_episode(
                    name=episode_name,
                    episode_body=chunk,
                    source_description=source_desc,
                    reference_time=datetime.now(timezone.utc),
                    group_id=effective_group_id,
                )
                total_episodes += 1
            except Exception as e:
                log.error(f"ERROR {episode_name}: {e}")

    log.info(f"Done. {total_episodes} episodes ingested.")
    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest KX repo files into Graphiti")
    parser.add_argument("--repo", help="Repo name under dump/ (e.g. pykx)")
    parser.add_argument("--path", help="Specific file or directory path to ingest")
    parser.add_argument(
        "--model", choices=list(ANTHROPIC_MODELS.keys()), default="opus",
        help="Anthropic model: opus (best), sonnet (balanced), haiku (fast/cheap). Ignored when --llm ollama.",
    )
    parser.add_argument(
        "--ollama-model", default=OLLAMA_LLM_MODEL,
        help=f"Ollama model name for --llm ollama (default: {OLLAMA_LLM_MODEL})",
    )
    parser.add_argument(
        "--llm", choices=["anthropic", "ollama"], default="anthropic",
        help="LLM provider for entity extraction. Default: anthropic",
    )
    parser.add_argument(
        "--embedder", choices=["ollama", "voyage"], default="ollama",
        help="Embedding backend: ollama (local, default) or voyage (API). Default: ollama",
    )
    parser.add_argument(
        "--group-id", default=None,
        help="Graphiti group ID to namespace this run (default: llm provider name). Use to isolate parallel benchmark runs.",
    )
    args = parser.parse_args()

    path_arg = Path(args.path) if args.path else None
    if path_arg and not path_arg.exists():
        raise SystemExit(f"Path not found: {path_arg}")

    if args.llm == "ollama":
        model_id = args.ollama_model
        log.info(f"LLM: ollama ({model_id}) | Embedder: {args.embedder} | Log: {_log_file}")
    else:
        model_id = ANTHROPIC_MODELS[args.model]
        log.info(f"LLM: anthropic/{args.model} ({model_id}) | Embedder: {args.embedder} | Log: {_log_file}")

    asyncio.run(ingest(repo=args.repo, path=path_arg, model=model_id, embedder_name=args.embedder, llm_provider=args.llm, group_id=args.group_id))
