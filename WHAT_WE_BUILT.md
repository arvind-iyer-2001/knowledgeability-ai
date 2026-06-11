# What We Built

KX knowledge graph system using Graphiti + Neo4j, with local and cloud LLM support, benchmarking infrastructure, token tracking, and an MCP server for Claude Desktop.

---

## Stack

| Component | Choice |
|---|---|
| Knowledge graph | Graphiti (getzep/graphiti) |
| Graph database | Neo4j (local, Docker) |
| Embeddings | Ollama → nomic-embed-text (local) |
| LLM (ingestion) | Anthropic Haiku 4.5 (cloud) or Ollama (local) |
| LLM (query) | Anthropic Haiku 4.5 |
| MCP server | stdio transport → Claude Desktop |

---

## Environment Setup

- Python venv at `.venv/` via `uv venv .venv` (required — Homebrew Python blocks global pip installs)
- Dependencies: `uv pip install --python .venv/bin/python graphiti-core anthropic python-dotenv mcp "graphiti-core[voyageai]" matplotlib`
- Secrets in `.env`: `ANTHROPIC_API_KEY`, `NEO4J_URI/USER/PASSWORD`, `OLLAMA_BASE_URL`
- Neo4j running locally via Docker on bolt://localhost:7687
- Ollama running locally on http://localhost:11434 with `nomic-embed-text` for embeddings

---

## Ingestion Pipeline (`ingest.py`)

Reads files from `dump/`, chunks them (1500 chars, 200 overlap), feeds chunks as episodes to Graphiti.

### CLI
```bash
python3 ingest.py --repo kdb-x-mcp-server --model haiku --group-id haiku
python3 ingest.py --repo pykx --llm ollama --ollama-model llama3.1:8b
python3 ingest.py --path dump/docs --model sonnet --group-id docs-sonnet
```

### Key flags
- `--repo` — single repo under `dump/`
- `--model` — `haiku`, `sonnet`, `opus` (Anthropic)
- `--llm` — `anthropic` (default) or `ollama`
- `--ollama-model` — Ollama model name
- `--group-id` — Graphiti namespace for this run (enables parallel/isolated ingestions)
- `--embedder` — `ollama` (default) or `voyage`

### What Graphiti does per episode
Each `add_episode()` call makes ~5 LLM calls internally:
1. Extract nodes (entities)
2. Deduplicate nodes against existing graph
3. Extract edges (relationships)
4. Deduplicate edges
5. Summarize nodes

### File types ingested
`.md`, `.py`, `.q`, `.rst`, `.txt`, `.yaml`, `.yml`, `.json`

---

## Local Model Support (Ollama)

Two key fixes required to make Ollama work with Graphiti:

1. **Use `OpenAIGenericClient` not `OpenAIClient`** — Ollama doesn't support the Responses API (`/v1/responses`) that `OpenAIClient` uses. `OpenAIGenericClient` uses `chat/completions` with JSON schema enforcement.

2. **Set `small_model=model` in `LLMConfig`** — Graphiti defaults `small_model` to `gpt-4.1-nano`, causing 404s. Must explicitly override to the same Ollama model.

### Custom Ollama model for speed (`llama3.1-fast`)
A Modelfile with reduced context window (`num_ctx 4096`, `num_batch 512`, `num_thread 12`) gives ~3x speedup over default llama3.1:8b by shrinking KV-cache allocation.

---

## Token Tracker (`TokenTracker`)

Tracks per-run Anthropic token usage with cost calculation.

```
Token usage — calls: 486 | input: 2,259,225 | output: 57,614 |
cache_write: 0 | cache_read: 0 | cache_hit_rate: 0.0% | estimated_cost: $2.0378
```

Logs at end of every ingestion run. Supports cache-aware pricing for Haiku, Sonnet, Opus.

---

## Prompt Caching (`CachedAnthropicClient`)

Subclass of Graphiti's `AnthropicClient` that:
- Adds `cache_control: {type: ephemeral}` to the system message block
- Tracks `cache_creation_input_tokens` and `cache_read_input_tokens` via the new SDK usage fields
- Returns `(tool_args_dict, input_tokens, output_tokens)` — same contract as base class

**Critical implementation note**: `_generate_response` must return a parsed dict from tool use, not the raw Anthropic `Message` object. The base class unpacks the tuple immediately.

---

## Benchmark Results (kdb-x-mcp-server, 107 episodes)

| Model | Entities | Edges | Time | Cost | Errors |
|---|---|---|---|---|---|
| Haiku | 101 | 179 | ~13 min | $2.04 | 0 |
| llama3.1-fast | 161 | 85 | ~105 min | $0 | 0 |

Haiku: better edge density (1.7 edges/ep vs 0.8), 8x faster, consistent quality.
Llama: more raw entities but weaker relationship extraction, much slower.

---

## MCP Server (`mcp_server_stdio.py`)

stdio MCP server exposing a `search_kx_knowledge` tool to Claude Desktop.

### Tool schema
```json
{
  "query": "string",
  "num_results": "integer (default 10)",
  "group_ids": "string[] (optional — filter by ingestion group)"
}
```

### Claude Desktop config
Located at `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "kx-knowledge-graph": {
    "command": "/path/to/.venv/bin/python3",
    "args": ["/path/to/mcp_server_stdio.py"]
  }
}
```

The script uses `load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))` — no `env` block needed in the config.

---

## Query CLI (`query.py`)

```bash
python3 query.py "How does the tickerplant log recovery work?"
python3 query.py --group haiku --group llama-fast "What is .u.upd?"
python3 query.py  # interactive mode
```

Supports `--llm`, `--model`, repeatable `--group` flags for filtering by ingestion group.

---

## Structured Logging

Every ingestion run writes a timestamped log to `logs/ingest_YYYYMMDD_HHMMSS.log`. Includes per-file progress, episode errors, retry counts, and final token summary.

---

## Corpus Overview

Costs below use the corrected Haiku 4.5 pricing ($1.00/$5.00 per Mtok) and calibration ($0.0724/episode), derived from the `group_id=production` run — see [PRODUCTION_INGEST_REPORT.md](PRODUCTION_INGEST_REPORT.md). Figures from `ingest.py --dry-run --repo <repo>`.

| Repo | Files | Chars | Episodes | Est. Cost (Haiku) | Status |
|---|---|---|---|---|---|
| kdb-x-mcp-server | 26 | 123k | 58 | $4.20 | ✓ ingested (group_id=production) |
| kdbai-mcp-server | 24 | 106k | 49 | $3.55 | ✓ ingested (group_id=production) |
| kx-skills | 27 | 143k | 63 | $4.56 | ✓ ingested (group_id=production) |
| kx-sdk-reference-architectures | 148 | 332k | 209 | $15.13 | not yet |
| kx-vscode | 35 | 570k | 218 | $15.79 | not yet |
| pykx | 261 | 2.9M | 1,136 | $82.26 | not yet |
| docs | 451 | 4.3M | 1,690 | $122.38 | not yet |
| nvidia-kx-samples | 543 | 7.6M | 2,885 | $208.91 | not yet |
| **Total** | **1,515** | **15.9M** | **6,308** | **~$457** | **170/6,308 episodes done ($12.31 actual)** |
