# Agent Handoff — knowledgeability-ai

This document tells an incoming LLM agent everything needed to reproduce the full working state of this project from a fresh clone. Follow steps in order.

---

## What this project is

A knowledge graph over KX/kdb+ documentation. Files from 8 KX GitHub repos are chunked and fed to [Graphiti](https://github.com/getzep/graphiti), which uses Claude to extract entities and relationships and stores them in Neo4j. A query CLI and two MCP server variants expose hybrid (vector + graph) search to Claude Desktop.

---

## Prerequisites

Install these before anything else:

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | Runtime |
| Docker | any | Neo4j container |
| Ollama | any | Local embeddings |
| git | any | Clone KX repos |

---

## Step 1 — Clone this repo

```bash
git clone <repo-url> knowledgeability-ai
cd knowledgeability-ai
```

---

## Step 2 — Install Python dependencies

```bash
pip install graphiti-core anthropic python-dotenv uvicorn starlette mcp "graphiti-core[voyageai]"
```

---

## Step 3 — Start Neo4j

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  neo4j:5
```

Verify at http://localhost:7474 (login: `neo4j` / `password123`).

---

## Step 4 — Pull embedding model

```bash
ollama pull nomic-embed-text
```

---

## Step 5 — Configure environment

```bash
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY
```

Minimum required:
```
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Step 6 — Clone KX corpus into dump/

The `dump/` directory is gitignored (large repos). Re-create it:

```bash
mkdir -p dump && cd dump
for repo in docs kx-sdk-reference-architectures pykx kx-skills nvidia-kx-samples kdb-x-mcp-server kdbai-mcp-server kx-vscode; do
  git clone --depth=1 https://github.com/KxSystems/$repo
done
cd ..
```

Expected result: 8 subdirectories under `dump/`.

---

## Step 7 — Run ingestion

```bash
# Full corpus (slow, uses Claude Opus — best quality)
python3 ingest.py

# Or ingest one repo first to verify the pipeline works
python3 ingest.py --repo pykx --model haiku
```

Monitor progress:
```bash
./progress.sh
```

Ingestion is idempotent — safe to re-run or resume by repo.

---

## Step 8 — Verify with a query

```bash
python3 query.py "What is kdb+tick?"
```

Expected: numbered list of facts extracted from the knowledge graph.

---

## Step 9 — Start the MCP server

**For Claude Desktop on Windows/WSL (stdio transport):**

Add to `%APPDATA%\Claude\claude_desktop_config.json`:
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

Restart Claude Desktop. The `search_kx_knowledge` tool should appear.

**For HTTP clients (SSE transport):**
```bash
python3 mcp_server.py
# Listens on http://localhost:8765/sse
```

---

## File map

```
ingest.py           — ingestion pipeline (file walker → chunker → Graphiti)
query.py            — CLI query tool (one-shot and interactive)
mcp_server.py       — MCP server, SSE transport (HTTP clients)
mcp_server_stdio.py — MCP server, stdio transport (Claude Desktop on Windows/WSL)
progress.sh         — polls Neo4j for node/edge counts during ingestion
.env.example        — environment variable template
dump/               — KX repos (gitignored, re-clone per Step 6)
```

---

## Current state (as of handoff)

- Infrastructure scripts and MCP servers: complete and working
- Corpus cloned: yes (`dump/` populated with 8 repos)
- Ingestion: run (full corpus or partial — check Neo4j for current node/edge counts via `./progress.sh`)
- MCP tool name: `search_kx_knowledge`

---

## Key design decisions (why things are the way they are)

| Decision | Reason |
|----------|--------|
| Graphiti over plain vector RAG | Preserves typed relationships across KX docs (e.g. tickerplant → RDB → HDB) |
| Ollama default embedder | Zero API cost; swap to Voyage AI with `--embedder voyage` |
| `PassthroughReranker` | No local cross-encoder; avoids hard dependency |
| Two MCP transports | SSE for generic HTTP; stdio because Claude Desktop on Windows can't reach WSL localhost reliably over SSE |
| Haiku at query time | Fast/cheap for entity extraction; Opus at ingest for extraction quality |

---

## Troubleshooting

**Neo4j connection refused** — container not running. Run `docker start neo4j` or repeat Step 3.

**`ANTHROPIC_API_KEY not set`** — `.env` missing or not copied. Repeat Step 5.

**`ollama: command not found` / embedding errors** — Ollama not running or model not pulled. Run `ollama serve` and repeat Step 4.

**`No results found`** — ingestion not complete or Neo4j was reset. Re-run Step 7.

**Claude Desktop can't see the tool** — restart Claude Desktop after editing `claude_desktop_config.json`. Check WSL path matches your username.
