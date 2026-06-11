# Portable Setup — kx-portable.zip

This zip is a working copy of the knowledgeability-ai project, including
`kx-graph-export.json` — an export of the `production` Neo4j group (559
entities, 170 episodes, 824 RELATES_TO, 1167 MENTIONS, ~24MB).

Goal: recreate the full ingestion/query/MCP setup on this system without
re-running ingestion (no Anthropic API cost).

## Prerequisites
- [uv](https://docs.astral.sh/uv/)
- Docker (for Neo4j)
- Ollama

## Steps

### 1. Unzip and install deps
```bash
unzip kx-portable.zip -d knowledgeability-ai
cd knowledgeability-ai
uv sync
```

### 2. Start Neo4j
```bash
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:5
```
Wait ~30s, verify at http://localhost:7474 (neo4j / password123).

### 3. Pull the embedding model
```bash
ollama pull nomic-embed-text
```

### 4. Check `.env`
`.env` is included with working credentials (`NEO4J_*`, `ANTHROPIC_API_KEY`).
If `NEO4J_PASSWORD` doesn't match the `NEO4J_AUTH` set in step 2, fix one to
match the other.

### 5. Import the graph
```bash
uv run python3 import_graph.py --in kx-graph-export.json
```
Expect:
```
Imported group 'production': 559 entities, 170 episodes, 824 RELATES_TO, 1167 MENTIONS
Building indices and constraints...
Done.
```

### 6. Verify
```bash
uv run python3 query.py --group production "What does .u.upd do?"
uv run python3 test_mcp_server.py
```

### 7. (Optional) Claude Desktop MCP
Add to `claude_desktop_config.json` (Mac: `~/Library/Application Support/Claude/`):
```json
"mcpServers": {
  "kx-knowledge-graph": {
    "command": "<absolute-path-to-project>/.venv/bin/python3",
    "args": ["<absolute-path-to-project>/mcp_server_stdio.py"]
  }
}
```
Restart Claude Desktop.

## What's NOT carried over

- **Other Neo4j groups** (`haiku`, `sonnet-v2`, `haiku-v2`, etc) — only
  `production` was exported. To bring one over, on the source system run:
  ```bash
  uv run python3 export_graph.py --group <name> --out <name>-export.json
  ```
  then `import_graph.py --in <name>-export.json` here.
- **`dump/`** (KX repo corpus, ~230MB) — only needed if re-ingesting from
  source. If included in the zip it's already there; otherwise re-clone per
  AGENT_HANDOFF.md step 5.
- **`.venv/`** — never copy this; `uv sync` rebuilds it for this machine's
  OS/arch.
