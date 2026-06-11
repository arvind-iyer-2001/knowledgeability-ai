# Agent Handoff — knowledgeability-ai

Everything an incoming agent or developer needs to pick up this project cold.

---

## What this is

A knowledge graph over KX/kdb+ documentation and source code. Files from 8 KX repos are chunked and fed to [Graphiti](https://github.com/getzep/graphiti), which uses an LLM to extract entities and relationships and stores them in Neo4j with vector embeddings. A query CLI and an MCP server expose hybrid (vector + graph) search to Claude Desktop.

**Read these first:**
- [WHAT_WE_BUILT.md](WHAT_WE_BUILT.md) — full system description, CLI flags, benchmarks, corpus overview
- [TRADEOFFS.md](TRADEOFFS.md) — every design decision with numbers: model selection, caching, multi-agent options
- [PRODUCTION_INGEST_REPORT.md](PRODUCTION_INGEST_REPORT.md) — first production run (170 eps, group_id=production), pricing bug fix, recalibrated full-corpus cost (~$457)

---

## Current State

| Item | Status |
|---|---|
| Infrastructure | Complete |
| Ingestion pipeline | Complete — `ingest.py` |
| Query CLI | Complete — `query.py` |
| MCP server (Claude Desktop) | Complete — `mcp_server_stdio.py` |
| Benchmarks run | `haiku` (107 eps), `llama-fast` (107 eps), `llama` (16 eps), `haiku-cached` (107 eps), `haiku-v2` (58 eps), `sonnet-v2` (58 eps), `production` (170 eps) |
| Full corpus ingested | Not yet — 3/8 repos done (`production` group_id) |
| Prompt caching | Implemented but ineffective for long-form content (see TRADEOFFS.md) |
| Cost tracker pricing | Fixed 2026-06-11 — was using Haiku 3.5 rates ($0.80/$4.00 per Mtok) for Haiku 4.5 (correct: $1.00/$5.00). See PRODUCTION_INGEST_REPORT.md |

### Neo4j groups in the graph right now
| group_id | Episodes | Entities | Edges | Notes |
|---|---|---|---|---|
| haiku | 107 | 101 | 179 | kdb-x-mcp-server, baseline |
| llama-fast | 107 | 161 | 85 | kdb-x-mcp-server, local model |
| llama | 16 | ~20 | ~15 | kdb-x-mcp-server, partial run |
| haiku-cached | 107 | ~100 | ~170 | kdb-x-mcp-server, caching test |
| haiku-v2 | 58 | 164 | 258 | kdb-x-mcp-server, 3000-char chunks, KX domain context |
| sonnet-v2 | 58 | 219 | 435 | kdb-x-mcp-server, 3000-char chunks, KX domain context, cache 36.8% |
| production | 170 | 729 | 824 | kx-skills + kdb-x-mcp-server + kdbai-mcp-server, Haiku 4.5, $12.31 actual |

---

## Setup (fresh clone)

### Prerequisites
- Python 3.10+
- Docker (Neo4j)
- Ollama (local embeddings)

### 1. Venv + dependencies
```bash
uv venv .venv
uv pip install --python .venv/bin/python graphiti-core anthropic python-dotenv mcp "graphiti-core[voyageai]" matplotlib
```

> **Do not use system Python or Homebrew Python** — externally managed environment will block pip. Use `uv`, not `python3 -m venv` + `pip`.

### 2. Neo4j
```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  neo4j:5
```
Verify at http://localhost:7474 (neo4j / password123).

### 3. Ollama embeddings
```bash
ollama pull nomic-embed-text
```

### 4. Environment
```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY=sk-ant-...
```

### 5. KX corpus
`dump/` is gitignored. Re-clone:
```bash
mkdir -p dump && cd dump
for repo in docs kx-sdk-reference-architectures pykx kx-skills nvidia-kx-samples kdb-x-mcp-server kdbai-mcp-server kx-vscode; do
  git clone --depth=1 https://github.com/KxSystems/$repo
done
cd ..
```

---

## Running Things

### Ingest a single repo (recommended first run)
```bash
source .venv/bin/activate
python3 ingest.py --repo kdb-x-mcp-server --model haiku --group-id haiku
```

### Ingest full corpus (remaining 5 repos)
```bash
python3 ingest.py --repo kx-sdk-reference-architectures --repo kx-vscode --repo pykx --repo docs --repo nvidia-kx-samples --model haiku --group-id production
# ~24h, ~$444 at Haiku pricing (corrected) — see PRODUCTION_INGEST_REPORT.md
# 3/8 repos (kx-skills, kdb-x-mcp-server, kdbai-mcp-server) already done: 170 eps, $12.31, group_id=production
```

### Monitor progress
```bash
./progress.sh
```

### Query the graph
```bash
python3 query.py "How does tickerplant log recovery work?"
python3 query.py --group haiku "What is .u.upd?"
python3 query.py  # interactive mode
```

### MCP server for Claude Desktop
Config at `~/Library/Application Support/Claude/claude_desktop_config.json` (already set up on this machine):
```json
{
  "kx-knowledge-graph": {
    "command": "/path/to/.venv/bin/python3",
    "args": ["/path/to/mcp_server_stdio.py"]
  }
}
```
Tool name: `search_kx_knowledge`. Supports `query`, `num_results`, `group_ids` parameters.

---

## File Map

```
ingest.py               ingestion pipeline — file walker → chunker → Graphiti
query.py                CLI query tool — one-shot and interactive
mcp_server_stdio.py     MCP server, stdio transport for Claude Desktop
mcp_server.py           MCP server, SSE transport (HTTP clients)
progress.sh             polls Neo4j for node/edge counts
WHAT_WE_BUILT.md        full system documentation
TRADEOFFS.md            design decisions, cost analysis, architecture options
cost_scaling.png        cache vs no-cache cost curves (long-form vs short-form content)
multiagent_scaling.png  time/cost/cache efficiency across agent counts
multiagent_architecture.png  sequential vs 2-phase parallel architecture diagram
.env.example            environment variable template
dump/                   KX repos (gitignored — re-clone per setup step 5)
logs/                   timestamped ingestion logs (gitignored)
```

---

## Key Implementation Details

### Graphiti makes ~5 LLM calls per episode
`extract_nodes` → `dedupe_nodes` → `extract_edges` → `dedupe_edges` → `summarize_nodes`

Dedup searches are **scoped to `group_id`** — entities in different groups are never merged during ingestion but can be queried together via `group_ids=[...]`.

### Two LLM client classes
- `OpenAIGenericClient` — use for Ollama (uses `chat/completions` + JSON schema)
- `CachedAnthropicClient` — use for Anthropic (subclass with cache_control + token tracking)

Never use `OpenAIClient` with Ollama — it targets the Responses API (`/v1/responses`) which Ollama doesn't support.

### Prompt caching: works but doesn't save money for this workload
- Haiku 4.5 requires 4,096 token minimum cacheable block
- Graphiti's system prompts are ~15 tokens — caching never fires
- Injecting a 6k-token KX domain prefix enables caching but adds more cost than it saves (long-form content)
- **Will save ~88% for future short-form ingestion (Slack, Freshdesk)** — infrastructure already in place
- Sonnet 4.6 caching fires with KX domain context (~1,612 tokens clears 1,024-token threshold) — 36.8% hit rate observed on sonnet-v2 run

### Local Ollama models: two required fixes
1. Set `small_model=model` in `LLMConfig` — otherwise Graphiti defaults to `gpt-4.1-nano` (404)
2. Use `OpenAIGenericClient` not `OpenAIClient`

### Token tracking
`TokenTracker` logs at end of each run:
```
Token usage — calls: 628 | input: 2,329,448 | output: 113,000 | cache_write: 47,616 | cache_read: 1,355,561 | cache_hit_rate: 36.8% | estimated_cost: $9.2686
```

---

## What To Do Next

### Immediate
- [x] First production batch ingested: `kx-skills` + `kdb-x-mcp-server` + `kdbai-mcp-server`, Haiku 4.5, group_id=production (170 eps, $12.31, 729 entities, 824 edges) — 2026-06-11
- [ ] Decide production model for remaining 5 repos: sonnet-v2 quality (219 entities, 435 edges, 7.5 edges/ep) vs haiku cost (~$444 for remaining repos at corrected pricing)
- [ ] Ingest remaining repos: `python3 ingest.py --repo kx-sdk-reference-architectures --repo kx-vscode --repo pykx --repo docs --repo nvidia-kx-samples --model haiku --group-id production`
- [ ] Prune test files / changelogs from `nvidia-kx-samples` ($208.91, 46% of remaining cost) and `docs` ($122.38) before ingesting — could cut ~30% cost

### When adding Slack / Freshdesk
- [ ] Enable `CachedAnthropicClient` caching — will save ~88% at scale for short messages
- [ ] Use a separate `group_id` per source (e.g. `slack-2026-06`, `freshdesk-2026-06`)
- [ ] Query across sources: `--group production --group slack-2026-06`

### If ingestion speed becomes a constraint
- Use **parallel per-repo ingestion** (one agent per repo, isolated `group_ids`) — cuts 31h → 4h
- Do **not** try to merge group_ids post-hoc — edge reconciliation is unsolved without forking Graphiti
- See [TRADEOFFS.md](TRADEOFFS.md) for full multi-agent analysis

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Neo4j connection refused` | `docker start neo4j` |
| `ANTHROPIC_API_KEY not set` | Check `.env` exists and is populated |
| `model gpt-4.1-nano not found` | Missing `small_model=model` in Ollama LLMConfig |
| `NodeResolutions - Input should be an object` | Using `OpenAIClient` instead of `OpenAIGenericClient` for Ollama |
| `argument after ** must be a mapping` | `CachedAnthropicClient._generate_response` returning raw Message — must return `(tool_args_dict, input_tokens, output_tokens)` |
| `betas: unexpected keyword argument` | Remove `betas=["prompt-caching-2024-07-31"]` — not needed in SDK 0.105.2+ |
| `No results found` on query | Ingestion incomplete or Neo4j was reset — check `./progress.sh` |
| Claude Desktop can't see tool | Restart Claude Desktop after editing config |
