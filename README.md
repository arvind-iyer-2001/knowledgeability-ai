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
```bash
pip install graphiti-core anthropic python-dotenv uvicorn starlette mcp "graphiti-core[voyageai]"
```

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
# Full corpus (all repos)
python3 ingest.py

# Single repo
python3 ingest.py --repo pykx

# Specific path
python3 ingest.py --path dump/docs/docs/wp

# Model selection (default: opus)
python3 ingest.py --repo pykx --model haiku     # fast/cheap
python3 ingest.py --repo pykx --model sonnet    # balanced
python3 ingest.py --repo pykx --model opus      # best quality

# Embedder selection (default: ollama)
python3 ingest.py --repo pykx --embedder voyage  # Voyage AI (requires VOYAGE_API_KEY)
```

Check ingestion progress:
```bash
./progress.sh
```

## Querying

```bash
# One-shot
python3 query.py "What compression options exist for kdb+?"

# Interactive mode
python3 query.py
```

## MCP Server

Exposes `search_kx_knowledge` tool for Claude Desktop.

**SSE server** (for HTTP clients):
```bash
python3 mcp_server.py
# Runs on http://localhost:8765/sse
```

**stdio server** (for Claude Desktop on Windows/WSL):
```bash
python3 mcp_server_stdio.py
```

Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kx-knowledge-graph": {
      "command": "C:\\Windows\\System32\\wsl.exe",
      "args": ["bash", "-c", "cd /home/aiyer/knowledgeability-ai && python3 mcp_server_stdio.py"]
    }
  }
}
```

## How This Was Built

Chronological steps taken to build the system:

### Step 1 — Problem definition

Goal: make KX/kdb+ documentation queryable by Claude via an MCP tool. Plain RAG loses relationships between concepts (e.g. `kdb+tick` → `tickerplant` → `RDB` → `HDB`). Chose **Graphiti** (getzep/graphiti) for hybrid retrieval — it stores both embeddings and an explicit knowledge graph in Neo4j, so searches return typed facts with provenance rather than raw chunks.

### Step 2 — Infrastructure

Spun up Neo4j 5 in Docker (ports 7474/7687). Set up Ollama locally and pulled `nomic-embed-text` for free local embeddings with no API cost during development.

### Step 3 — Corpus collection

Cloned 8 KX GitHub repos (shallow, `--depth=1`) into `dump/`:
- `docs`, `pykx`, `kx-sdk-reference-architectures`, `kdb-x-mcp-server`, `kdbai-mcp-server`, `kx-skills`, `nvidia-kx-samples`, `kx-vscode`

Covered file types: `.md`, `.py`, `.q`, `.rst`, `.txt`, `.yaml`, `.yml`, `.json`.

### Step 4 — Ingestion pipeline (`ingest.py`)

Built a file walker that:
1. Recursively collects files from `dump/` (filtering by extension, skipping `.git`/`__pycache__` etc.)
2. Chunks each file at 1500 chars with 200-char overlap
3. Feeds each chunk to `graphiti.add_episode()` — Graphiti calls Claude to extract entities/relationships and stores them as graph edges with vector embeddings

Added `--repo`, `--path`, `--model` (haiku/sonnet/opus), and `--embedder` (ollama/voyage) flags for flexible partial ingestion. Implemented `PassthroughReranker` as a no-op `CrossEncoderClient` since no cross-encoder model was available locally.

### Step 5 — Progress monitoring (`progress.sh`)

Shell script to poll Neo4j node/edge counts so ingestion progress is visible without parsing Python output.

### Step 6 — Query CLI (`query.py`)

Built a thin wrapper around `graphiti.search()` with two modes:
- **One-shot**: `python3 query.py "question"` — prints ranked facts
- **Interactive REPL**: `python3 query.py` — loop until `quit`

Uses Haiku for query-time entity extraction (cheap, fast) and Ollama for embeddings.

### Step 7 — MCP server, SSE transport (`mcp_server.py`)

Wrapped the Graphiti search in an MCP `Server` exposing one tool: `search_kx_knowledge`. Used SSE transport (Starlette + uvicorn) on port 8765 so any HTTP MCP client can connect. This was the initial transport for testing.

### Step 8 — MCP server, stdio transport (`mcp_server_stdio.py`)

Claude Desktop on Windows cannot reach WSL localhost over SSE reliably. Built a stdio-transport variant that Claude Desktop launches directly via `wsl.exe`, so the process speaks MCP over stdin/stdout instead of HTTP. Same tool implementation, different transport layer.

### Key design decisions

| Decision | Reason |
|----------|--------|
| Graphiti over plain vector RAG | Preserves typed relationships between KX concepts across documents |
| Ollama as default embedder | Zero API cost; Voyage AI available as a drop-in upgrade |
| Two MCP transport modes | SSE for generic HTTP clients; stdio for Claude Desktop on Windows/WSL |
| `PassthroughReranker` | No local cross-encoder available; avoids a hard dependency |
| Haiku at query time | Fast and cheap for entity extraction; quality difference vs Opus is negligible for search |
| Opus as default at ingest time | Entity/relationship extraction quality matters most during ingestion |

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
