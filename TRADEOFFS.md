# Tradeoffs & Considerations

Design decisions, cost analysis, and architectural options explored during development.

---

## Model Selection for Ingestion

### Haiku vs Llama vs Opus

| | Haiku 4.5 | llama3.1-fast (local) | Opus 4.7 |
|---|---|---|---|
| Speed | ~13 min/107 eps | ~105 min/107 eps | ~60+ min (est.) |
| Cost | $2.04/107 eps | $0 | ~$8.50/107 eps |
| Entities/ep | 0.94 | 1.50 | ~1.2 (est.) |
| Edges/ep | 1.67 | 0.79 | ~2.0 (est.) |
| Errors | 0 | 0 | 0 |
| Retries | 0 | 0 | 0 |

**Recommendation: Haiku.** Best edge density, fastest, 0 errors. Llama produces more raw entities but weaker relationship extraction and is 8x slower. Opus costs 4x more than Haiku with marginal quality gains for structured extraction tasks.

### Why local models are harder
- Must use `OpenAIGenericClient` (not `OpenAIClient`) — Ollama doesn't support the Responses API
- Structured output quality is lower — smaller models struggle with strict JSON schemas
- No parallelism — local inference is CPU/GPU bound
- Context window matters a lot — llama3.1 with default 128k context is 3x slower than with 4k context

---

## Prompt Caching

### How it works
Anthropic caches the exact prefix of a request. If the system prompt is identical across calls, calls 2–N read from cache at 10x cheaper rate ($0.08/MTok vs $0.80/MTok).

### Why it doesn't help for document ingestion

Graphiti's system prompts are ~15 tokens (`"You are an entity extraction specialist..."`). The 4,096-token minimum threshold for Haiku 4.5 is never reached. Even if you inject a large static KX domain context (6,195 tokens), the math shows:

| Approach | Cost (70k calls) |
|---|---|
| No cache, no injection | $293 |
| 6k injection + cache (5min TTL) | $330 |
| 6k injection + cache (1hr TTL) | $328 |

**Caching costs more** because the injected tokens are *added on top* of existing variable content — they don't replace anything. The dominant cost is always the document chunk + graph context per call (~4,648 tokens), which changes every call and can never be cached.

### When caching does help massively

Short-form content where the cached prefix dominates the variable content:

| Content type | Variable tokens/call | Cache savings at 1M calls |
|---|---|---|
| Codebases / docs | ~4,648 | -12% |
| Slack / Freshdesk / tickets | ~350 | -88% |

**Build caching infrastructure now for future Slack/Freshdesk ingestion.** The 6k KX domain prompt injected there saves ~88% on input token costs at scale.

### Caching threshold: Haiku 4.5 requires 4,096 tokens minimum
The cacheable block itself must exceed 4,096 tokens — not the total input. With Graphiti's 15-token system prompts, caching is impossible without injection.

---

## Multi-Agent Architecture

### Why parallelism is constrained by Graphiti

Graphiti's dedup step runs a vector similarity search against the existing graph to decide whether to merge or create new entities. This search is scoped to `group_id`. Two concurrent agents writing to the same `group_id` create a race condition: both may extract `"PyKX"` before either writes it, resulting in duplicate nodes.

### Option 1: Sequential, single group_id (current)
- ✅ Full cross-repo connections
- ✅ Stays 100% within Graphiti
- ✅ Progressive dedup — later episodes benefit from earlier ones
- ❌ ~31 hours for full corpus

### Option 2: Parallel per-repo, isolated group_ids
- ✅ ~4 hours (bounded by nvidia-kx-samples)
- ✅ No dedup race conditions
- ✅ Stays 100% within Graphiti
- ❌ No cross-repo entity merging during ingestion
- ⚠️ Cross-repo connections only surface at query time via multi-group search

### Option 3: 2-Phase (parallel ingestion → merge pass)
- ✅ ~4 hours Phase 1 + ~30 min Phase 2
- ✅ Cross-repo connections
- ❌ **Phase 2 is not Graphiti** — requires raw Neo4j Cypher
- ❌ Edge reconciliation is hard: after merging `pykx:PyKX` + `docs:PyKX`, all edges pointing to the old nodes must be rewritten
- ❌ Not idempotent — re-running merge risks double-merging
- ❌ Merged edges have no natural group_id — breaks group-filtered queries
- ❌ Phase 1 extraction quality is lower (no cross-repo dedup context during ingestion)

### Practical recommendation
Use **Option 2** with multi-group queries at search time. Graphiti's search API accepts `group_ids=["pykx", "docs", "nvidia"]` — you get cross-repo results without pre-merging the graph. For the initial corpus build, sequential (Option 1) gives higher quality and is the safer default.

### Cost vs agents
Total cost is flat regardless of agent count — more agents = same work done faster, not cheaper. Cache efficiency marginally improves with more agents (more reads per write window) but already sits at 99.5%+ with a single agent.

---

## group_id Design

`group_id` is Graphiti's namespace for isolating ingestion runs in Neo4j. Key properties:

- Dedup is **scoped to group_id** — entities in different groups are never merged during ingestion
- Search can span **multiple group_ids** — `group_ids=["haiku", "llama"]` queries both
- Nodes and edges carry their source `group_id` as a property
- Deleting a group: `MATCH (n {group_id:'name'}) DETACH DELETE n`

### Recommended group_id strategy

| Use case | group_id |
|---|---|
| Production corpus | `production` (single, sequential) |
| Benchmarking | `haiku`, `llama-fast`, etc. |
| Per-repo parallel | `pykx`, `docs`, `nvidia`, etc. |
| Incremental updates | `pykx-2026-06`, `slack-2026-06`, etc. |

---

## Embedder Choice

| | Ollama (nomic-embed-text) | Voyage AI |
|---|---|---|
| Cost | Free (local) | ~$0.06/MTok |
| Speed | Fast (local) | API latency |
| Quality | Good for code/tech | Better for semantic search |
| Setup | Already running | Needs `VOYAGE_API_KEY` |

For a knowledge graph primarily queried by developers on KX topics, `nomic-embed-text` is sufficient. Voyage becomes relevant if query recall quality is noticeably poor on complex multi-hop questions.

---

## Full Corpus Cost Projection

At current Haiku pricing ($0.80/MTok input, $4.00/MTok output):

- **Full 8-repo corpus**: ~$293 (70k LLM calls, 15.8M chars)
- **Recurring ingestion** (new sources weekly): cost scales linearly with data volume
- **With Slack/Freshdesk at scale** (1M+ calls): caching saves ~88% on short-form content

The dominant cost driver is nvidia-kx-samples (7.5M chars, ~$139) and docs (4.2M chars, ~$78). Pruning test files, changelogs, and auto-generated content from those two repos before ingestion could cut total cost by 30-40%.
