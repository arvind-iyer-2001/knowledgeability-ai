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
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
_log_file = LOG_DIR / f"ingest_{_timestamp}.log"
_progress_file = LOG_DIR / f"progress_{_timestamp}.log"
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


HAIKU_INPUT_COST_PER_M  = 1.00
HAIKU_OUTPUT_COST_PER_M = 5.00
HAIKU_CACHE_WRITE_PER_M = 1.25   # 25% surcharge on cache writes
HAIKU_CACHE_READ_PER_M  = 0.10   # 90% discount on cache reads

MODEL_COSTS = {
    "claude-haiku-4-5-20251001": (HAIKU_INPUT_COST_PER_M, HAIKU_OUTPUT_COST_PER_M, HAIKU_CACHE_WRITE_PER_M, HAIKU_CACHE_READ_PER_M),
    "claude-sonnet-4-6":         (3.00, 15.00, 3.75, 0.30),
    "claude-opus-4-7":           (5.00, 25.00, 6.25, 0.50),
}


class TokenTracker:
    def __init__(self, model: str):
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_write_tokens = 0
        self.cache_read_tokens = 0
        self.calls = 0

    def record(self, input_tokens: int, output_tokens: int, cache_write: int, cache_read: int):
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_write_tokens += cache_write
        self.cache_read_tokens += cache_read
        self.calls += 1

    def cost(self) -> float:
        costs = MODEL_COSTS.get(self.model, (1.0, 5.0, 1.25, 0.10))
        in_cost, out_cost, cw_cost, cr_cost = costs
        return (
            (self.input_tokens / 1_000_000) * in_cost +
            (self.output_tokens / 1_000_000) * out_cost +
            (self.cache_write_tokens / 1_000_000) * cw_cost +
            (self.cache_read_tokens / 1_000_000) * cr_cost
        )

    def report(self):
        cache_hit_rate = (self.cache_read_tokens / max(self.input_tokens + self.cache_read_tokens, 1)) * 100
        log.info(
            f"Token usage — calls: {self.calls} | "
            f"input: {self.input_tokens:,} | output: {self.output_tokens:,} | "
            f"cache_write: {self.cache_write_tokens:,} | cache_read: {self.cache_read_tokens:,} | "
            f"cache_hit_rate: {cache_hit_rate:.1f}% | "
            f"estimated_cost: ${self.cost():.4f}"
        )


KX_DOMAIN_CONTEXT = """
You are a specialized knowledge graph construction assistant for KX technology and kdb+ systems. Your role is to extract entities, relationships, and facts from KX/kdb+ documentation, source code, and technical content with high precision.

## Domain Overview
kdb+ is a high-performance columnar time-series database by KX Systems, widely deployed in financial services for tick data capture, algorithmic trading, risk analytics, and real-time stream processing. It uses the q programming language. kdb+ stores data in memory-mapped flat files organized by partition (typically date), using a binary columnar format for extremely fast vector operations.

## q Language Essentials
q is a declarative, array-oriented language evaluating right-to-left. Key features:
- Adverbs: each (/), over (\\), scan, each-right (/:), each-left (\\:), prior
- Table ops: select, exec, update, delete, aj (as-of join), lj, ij, uj, wj, fby
- Temporal types: timestamp (ns), timespan (ns), date (days), time (ms), minute, second, month
- Numeric types: boolean, byte, short, int, long, real, float, char, symbol, guid
- Attributes: s# (sorted), u# (unique), p# (parted), g# (grouped)
- Null values: 0N (long), 0n (float), 0Nd (date), 0Np (timestamp), 0Nt (time)
- IPC: hopen, hclose, h() sync call, neg[h]() async call
- Namespaces: .z (system), .Q (utilities), .h (HTTP), .u (pub-sub)

## kdb+tick Architecture
- **Tickerplant (TP)**: receives market data from feed handlers, timestamps messages with .z.p, publishes to subscribers via .u.upd, writes binary log. Functions: .u.init, .u.sub, .u.unsub, .u.pub, .u.upd, .u.end
- **RDB**: subscribes to TP via .u.sub, holds intraday data in memory, saves to HDB at end-of-day via .Q.dpft or .Q.hdpf
- **HDB**: stores date-partitioned data on disk, columns as separate binary files, sym enumeration, memory-mapped reads
- **WDB**: optional write-behind buffer between TP and RDB
- **Gateway**: routes queries to RDB (today) or HDB (historical), aggregates results
- **Feed Handler**: normalizes external data (FIX, Bloomberg, Refinitiv) into kdb+ tables

## PyKX
Python interface to kdb+/q replacing qPython and embedPy:
- pykx.q("expr"): execute q, pykx.QConnection(host, port): IPC connection
- conn.sendSync/sendAsync: sync/async calls
- Types: pykx.Table ↔ pandas.DataFrame (.pd()), pykx.Vector ↔ numpy array (.np())
- pykx.toq(): Python→kdb+, obj.py(): kdb+→Python
- Licensed mode (full engine) vs unlicensed mode (IPC + conversion only)
- Env: QHOME, QLIC, PYKX_UNLICENSED

## KDB.AI
Vector database on kdb+ for hybrid structured + vector search:
- Index types: Flat (exact, O(n)), HNSW (approx, O(log n), params: M, efConstruction, efSearch), IVF (clustering, params: nlist, nprobe)
- Distance metrics: CS (cosine), L2 (Euclidean), IP (inner product)
- table.search(vectors, n), table.search(vectors, n, filter=expr)
- LangChain: KDBAIVectorStore, LlamaIndex: KDBAIVectorStore

## .z Namespace (event handlers)
.z.p (UTC timestamp), .z.d (date), .z.t (time), .z.pg (sync handler), .z.ps (async handler), .z.ph (HTTP GET), .z.pp (HTTP POST), .z.ws (WebSocket), .z.po (connection open), .z.pc (connection close), .z.pw (password validation)

## .Q Namespace (utilities)
.Q.dpft (save partitioned table), .Q.hdpf (save all to HDB), .Q.fs (file streaming), .Q.par (partition path), .Q.PD (partition dates), .Q.pt (partitioned tables), .Q.w (workspace/memory stats), .Q.gc (garbage collect)

## Entity Types to Extract

**QFunction**: q/k function with full namespace — .u.upd, .Q.dpft, .z.ps, aj, select, fby
**QType**: kdb+ data type with precision — timestamp (nanosecond), time (millisecond), symbol, float, long
**QAttribute**: column attribute — s# sorted, u# unique, p# parted, g# grouped
**QNamespace**: q namespace — .z, .Q, .h, .u, .q
**KdbProcess**: named process in tick architecture — tickerplant, RDB, HDB, gateway, feedhandler, WDB
**Table**: kdb+ table — trade, quote, orderbook, nbbo; include schema details when present
**IpcHandle**: IPC handle pattern — sync h, async neg[h], hopen target
**Interface**: external integration — C API, Java API, Python, R, ODBC, REST, WebSocket, FIX, Bloomberg
**CloudPlatform**: cloud deployment — AWS, Azure, GCP, kdb+ Insights cloud
**PyKXObject**: PyKX class/method — pykx.Table, pykx.QConnection, pykx.toq
**KdbaiIndex**: KDB.AI index type — Flat, HNSW, IVF; include parameters when present
**Concept**: architectural pattern — temporal partitioning, sym enumeration, memory mapping, pub-sub, as-of join
**ConfigFlag**: startup flag or env var — -p port, -s slaves, -w workspace, QHOME, QLIC, PYKX_UNLICENSED
**Whitepaper**: KX whitepaper or technical guide topic
**ThirdPartyTool**: external tool — Solace, Kafka, MQTT, Bloomberg, Refinitiv, Grafana

## Extraction Rules
1. Always use full qualified names: .u.upd not upd, pykx.QConnection not QConnection
2. Distinguish kdb+ native tables from Python/pandas DataFrames
3. Capture directionality: TP PUBLISHES_TO RDB, RDB WRITES_TO HDB, not just bidirectional
4. Temporal precision is a property: timestamp = nanosecond, time = millisecond — always note
5. IPC handle type matters: sync h vs async neg[h] have different semantics
6. Attribute on a column is a relationship: sym HAS_ATTRIBUTE p# in splayed table
7. Namespace membership is a relationship: .Q.dpft BELONGS_TO_NAMESPACE .Q
8. Version context: note PyKX 1.x vs 2.x API differences when mentioned
9. License-dependent PyKX features: tag with LICENSE_REQUIRED relationship
10. Code comments often contain the most important relationship information — prioritize them
""".strip()


class CachedAnthropicClient(AnthropicClient):
    """AnthropicClient with KX domain context injection, prompt caching, and token tracking."""

    def __init__(self, *args, tracker: TokenTracker | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracker = tracker

    async def _generate_response(
        self,
        messages: list[Message],
        response_model=None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
    ):
        import typing
        from anthropic.types import MessageParam

        system_message = messages[0]
        # Prepend KX domain context — pushes system prompt past cache threshold,
        # improves entity/relationship extraction quality
        combined_system = KX_DOMAIN_CONTEXT + "\n\n---\n\n" + system_message.content

        user_messages = [{'role': m.role, 'content': m.content} for m in messages[1:]]
        user_messages_cast = typing.cast(list[MessageParam], user_messages)
        max_creation_tokens = self._resolve_max_tokens(max_tokens, self.model)
        tools, tool_choice = self._create_tool(response_model)

        import json as _json
        result = await self.client.messages.create(
            system=[{
                "type": "text",
                "text": combined_system,
                "cache_control": {"type": "ephemeral"},
            }],
            max_tokens=max_creation_tokens,
            temperature=self.temperature,
            messages=user_messages_cast,
            model=self.model,
            tools=tools,
            tool_choice=tool_choice,
        )

        input_tokens = result.usage.input_tokens if result.usage else 0
        output_tokens = result.usage.output_tokens if result.usage else 0
        cache_read = getattr(result.usage, 'cache_read_input_tokens', 0) or 0
        cache_write = getattr(result.usage, 'cache_creation_input_tokens', 0) or 0

        if self.tracker:
            self.tracker.record(input_tokens, output_tokens, cache_write, cache_read)

        # Parse tool use response — same logic as base class _generate_response
        for content_item in result.content:
            if content_item.type == 'tool_use':
                tool_args = content_item.input if isinstance(content_item.input, dict) else _json.loads(str(content_item.input))
                return tool_args, input_tokens, output_tokens

        for content_item in result.content:
            if content_item.type == 'text':
                return self._extract_json_from_text(content_item.text), input_tokens, output_tokens

        raise ValueError(f'Could not extract structured data from model response: {result.content}')

DUMP_DIR = Path(__file__).parent / "dump"
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "gemma3:27b")

CHUNK_SIZE = 3000
CHUNK_OVERLAP = 100

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


# Calibration from the production ingest run (group_id=production: kx-skills,
# kdb-x-mcp-server, kdbai-mcp-server; 170 episodes, Haiku 4.5, 2026-06-11).
# Cost is from actual Anthropic billing ($12.31), not the tracker's pre-fix estimate.
CALIBRATION_COST_PER_EPISODE = 12.31 / 170   # $12.31 / 170 episodes (actual billed)
CALIBRATION_SECONDS_PER_EPISODE = 2524 / 170 # 42m04s / 170 episodes
CALIBRATION_CALLS_PER_EPISODE = 1955 / 170   # 1,955 LLM calls / 170 episodes


def estimate_corpus(files: list[Path]) -> dict:
    total_chars = 0
    total_episodes = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not text:
            continue
        total_chars += len(text)
        total_episodes += len(chunk_text(text))
    return {
        "files": len(files),
        "chars": total_chars,
        "episodes": total_episodes,
        "est_llm_calls": round(total_episodes * CALIBRATION_CALLS_PER_EPISODE),
        "est_cost": total_episodes * CALIBRATION_COST_PER_EPISODE,
        "est_seconds": total_episodes * CALIBRATION_SECONDS_PER_EPISODE,
    }


def format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def format_estimate(est: dict) -> str:
    return (
        f"files={est['files']} | chars={est['chars']:,} | episodes={est['episodes']:,} | "
        f"est. LLM calls≈{est['est_llm_calls']:,} | "
        f"est. cost≈${est['est_cost']:.2f} | "
        f"est. time≈{format_duration(est['est_seconds'])} "
        f"(calibrated: ${CALIBRATION_COST_PER_EPISODE:.4f}/ep, "
        f"{CALIBRATION_SECONDS_PER_EPISODE}s/ep, {CALIBRATION_CALLS_PER_EPISODE:.1f} calls/ep)"
    )


def write_progress(path: Path, start_time: float, done: int, total: int, tracker: "TokenTracker | None", est: dict, final: bool = False):
    elapsed = time.monotonic() - start_time
    pct = (done / total * 100) if total else 0.0
    eta = (elapsed / done * (total - done)) if done else est["est_seconds"]
    lines = [
        "=== Ingest Progress ===",
        f"Status: {'DONE' if final else 'running'}",
        f"Updated: {datetime.now(timezone.utc).isoformat()}",
        f"Elapsed: {format_duration(elapsed)}",
        f"Episodes: {done:,} / {total:,} ({pct:.1f}%)",
    ]
    if tracker:
        lines += [
            f"LLM calls: {tracker.calls:,} (est. total ≈{est['est_llm_calls']:,})",
            f"Tokens: in={tracker.input_tokens:,} out={tracker.output_tokens:,} "
            f"cache_write={tracker.cache_write_tokens:,} cache_read={tracker.cache_read_tokens:,}",
            f"Cost so far: ${tracker.cost():.4f} (est. total ≈${est['est_cost']:.2f})",
        ]
    lines.append("ETA: done" if final else (f"ETA: {format_duration(eta)} remaining" if done else "ETA: calculating..."))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_llm(llm_provider: str, model: str, tracker: TokenTracker | None = None) -> AnthropicClient | OpenAIGenericClient:
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
    return CachedAnthropicClient(LLMConfig(model=model, api_key=api_key), tracker=tracker)


async def ingest(repos: list[str] | None, path: Path | None, model: str, embedder_name: str, llm_provider: str, group_id: str | None = None, dry_run: bool = False):
    if repos:
        files = []
        for r in repos:
            files.extend(collect_files(DUMP_DIR, r, None))
    else:
        files = collect_files(DUMP_DIR, None, path)

    est = estimate_corpus(files)
    log.info(f"Selected: {repos or path or 'all'}")
    log.info("=== Pre-flight estimate ===")
    log.info(format_estimate(est))

    if dry_run:
        log.info("Dry run requested — exiting before ingestion.")
        return

    tracker = TokenTracker(model) if llm_provider == "anthropic" else None
    llm = build_llm(llm_provider, model, tracker=tracker)
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
    log.info(f"Found {len(files)} files")
    log.info(f"Progress file: {_progress_file}")

    start_time = time.monotonic()
    write_progress(_progress_file, start_time, 0, est["episodes"], tracker, est)

    total_episodes = 0
    for file_path in files:
        rel = file_path.relative_to(DUMP_DIR) if file_path.is_relative_to(DUMP_DIR) else file_path
        repo_name = rel.parts[0] if file_path.is_relative_to(DUMP_DIR) else (repos[0] if repos else "unknown")

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

            write_progress(_progress_file, start_time, total_episodes, est["episodes"], tracker, est)

    log.info(f"Done. {total_episodes} episodes ingested.")
    if tracker:
        tracker.report()
    write_progress(_progress_file, start_time, total_episodes, est["episodes"], tracker, est, final=True)
    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest KX repo files into Graphiti")
    parser.add_argument("--repo", action="append", help="Repo name under dump/ (e.g. pykx). Repeatable: --repo pykx --repo docs")
    parser.add_argument("--path", help="Specific file or directory path to ingest")
    parser.add_argument("--dry-run", action="store_true", help="Print pre-flight estimate (files, episodes, est. cost/time/calls) and exit")
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

    asyncio.run(ingest(repos=args.repo, path=path_arg, model=model_id, embedder_name=args.embedder, llm_provider=args.llm, group_id=args.group_id, dry_run=args.dry_run))
