# knowledgeability-ai

A knowledge graph system for KX/kdb+ documentation using [Graphiti](https://github.com/getzep/graphiti). Ingests KX GitHub repositories into a Neo4j graph and exposes hybrid retrieval (vector + graph traversal) via a query CLI and MCP server.

## Architecture

```
KX GitHub Repos → ingest.py → Graphiti → Neo4j
                                            ↓
                              query.py / MCP Server → Claude
```

- **LLM extraction**: Claude (Haiku/Sonnet/Opus) extracts entities and relationships per chunk
- **Embeddings**: Ollama (`nomic-embed-text`) or Voyage AI
- **Graph DB**: Neo4j 5 (Docker)
- **Retrieval**: Graphiti hybrid search (vector similarity + graph traversal)

## Prerequisites

- Docker (Neo4j)
- Ollama with `nomic-embed-text` pulled
- Anthropic API key
- Python 3.10+

## Setup

**1. Start Neo4j**
```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  neo4j:5
```

**2. Pull embedding model**
```bash
ollama pull nomic-embed-text
```

**3. Install dependencies**

Use [`uv`](https://github.com/astral-sh/uv) — system/Homebrew Python blocks global `pip install`:
```bash
uv sync
```
Deps are pinned in `pyproject.toml` / `uv.lock`. This creates `.venv` for you — don't create it manually or use `pip` directly. Run all Python commands below via `uv run`.

**4. Configure environment**
```bash
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY
```

## Corpus

KX repositories cloned into `dump/`:

| Repo | Content |
|------|---------|
| `docs` | Core kdb+/q documentation and whitepapers |
| `pykx` | Python interface to kdb+ |
| `kx-sdk-reference-architectures` | Reference architecture patterns |
| `kdb-x-mcp-server` | MCP server for kdb+ |
| `kdbai-mcp-server` | MCP server for KDB.AI |
| `kx-skills` | Training and learning materials |
| `nvidia-kx-samples` | KX + NVIDIA GPU integration samples |
| `kx-vscode` | VS Code extension for KX |

Re-clone:
```bash
mkdir -p dump && cd dump
for repo in docs kx-sdk-reference-architectures pykx kx-skills nvidia-kx-samples kdb-x-mcp-server kdbai-mcp-server kx-vscode; do
  git clone --depth=1 https://github.com/KxSystems/$repo
done
```

## Ingestion

```bash
# Preview cost/time/episode estimate without running (no LLM calls)
uv run python3 ingest.py --repo pykx --dry-run

# Single repo
uv run python3 ingest.py --repo pykx --model haiku --group-id production

# Multiple repos in one run (repeatable --repo)
uv run python3 ingest.py --repo kx-skills --repo kdb-x-mcp-server --model haiku --group-id production

# Full corpus (all repos under dump/)
uv run python3 ingest.py --model haiku --group-id production

# Specific path
uv run python3 ingest.py --path dump/docs/docs/wp --model haiku

# Embedder selection (default: ollama)
uv run python3 ingest.py --repo pykx --embedder voyage  # Voyage AI (requires VOYAGE_API_KEY)
```

`--model` choices: `haiku` (recommended — fastest, cheapest, 0 errors; see [TRADEOFFS.md](TRADEOFFS.md)), `sonnet`, `opus` (default).

Check ingestion progress (live, updated per episode):
```bash
cat logs/progress_<timestamp>.log
```
Full per-episode log: `logs/ingest_<timestamp>.log`. Neo4j node/edge counts: `./progress.sh`.

## Querying

```bash
# One-shot
uv run python3 query.py "What compression options exist for kdb+?"

# Filter by ingestion group (repeatable)
uv run python3 query.py --group production "How does tickerplant log recovery work?"

# Interactive mode
uv run python3 query.py
```

## MCP Server

Exposes `search_kx_knowledge` tool (`query`, `num_results`, `group_ids`).

**stdio server** (Claude Desktop, macOS — current setup):
```json
{
  "mcpServers": {
    "kx-knowledge-graph": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["/path/to/mcp_server_stdio.py"]
    }
  }
}
```
Config at `~/Library/Application Support/Claude/claude_desktop_config.json`. Restart Claude Desktop after editing. Loads `.env` automatically — no `env` block needed.

**SSE server** (generic HTTP MCP clients):
```bash
uv run python3 mcp_server.py
# Runs on http://localhost:8765/sse
```

## More

- [AGENT_HANDOFF.md](AGENT_HANDOFF.md) — current project status, setup from scratch, file map, troubleshooting
- [WHAT_WE_BUILT.md](WHAT_WE_BUILT.md) — full system description and corpus overview
- [TRADEOFFS.md](TRADEOFFS.md) — model/caching/architecture decisions with numbers
- [PRODUCTION_INGEST_REPORT.md](PRODUCTION_INGEST_REPORT.md) — latest production ingest run, current pricing
- [REPORT.md](REPORT.md) — original PoC report

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Anthropic API key |
| `VOYAGE_API_KEY` | — | Required only with `--embedder voyage` |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password123` | Neo4j password |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base URL |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `MCP_PORT` | `8765` | SSE MCP server port |
