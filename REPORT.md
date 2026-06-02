# KX Knowledge Graph — PoC Report

**Project:** knowledgeability-ai | **Stage:** Proof of Concept | **Date:** June 2026 | **Author:** Arvind Iyer

---

## Purpose

PoC, not production. Goal: determine if knowledge graph approach is viable for KX developer resources — test extraction quality, benchmark models, validate MCP integration, understand cost/scaling before committing to full build.

8 KX repos used as representative test sample (docs, Python libs, q code, MCP servers, reference architectures). Not the intended production corpus.

---

## Problem

KX developer resources — documentation, repos, Slack, Freshdesk, Jira — exist in isolation. Developer understanding how PyKX connects to kdb+ tickerplant, or how to use KDB.AI with LangChain, must manually search across disconnected surfaces.

**Hypothesis:** knowledge graph unifying these sources into single queryable system, exposed via MCP, lets AI assistants answer developer questions in real time.

**Two use cases:**

**Developer assistance.** Engineers spend time navigating docs/source to understand how components connect. Graph surfaces connections directly — faster path from question to answer.

**Onboarding.** kdb+ and q have steep learning curve. New engineers face large surface area with non-obvious relationships. Graph backed by full KX corpus (docs, project history, support conversations) becomes queryable institutional knowledge — explains not just what things are, but how they relate and why. Relevant as KX grows developer community and onboards engineers onto kdb+ projects.

**Success criteria:**
1. Extraction quality — graph captures meaningful entities/relationships, not noise
2. Queryability — MCP tool returns accurate, relevant results AI can use

---

## Why Knowledge Graph over RAG

RAG retrieves semantically similar text chunks. Doesn't know `.u.upd` is called by tickerplant, RDB subscribes to tickerplant, `.Q.dpft` used at end-of-day to persist RDB to HDB. These are relationships, not similarity matches.

Knowledge graph captures typed relationships between named entities. Query "how does kdb+tick handle end-of-day persistence?" → graph traverses: `tickerplant → publishes_to → RDB → writes_to → HDB → uses → .Q.dpft`. Answer assembled from structure.

Same value proposition from Graphify demo review:
> *"Graphify provides: precomputed structural model of codebase, faster/cheaper context access, graph-based navigation, architectural awareness. Points 1, 2 and 4 are much more valuable and interesting."*

PoC tests whether same principle applies at org level — entire KX knowledge surface, not one codebase.

---

## Technical Approach

| Component | Technology |
|---|---|
| Knowledge graph engine | Graphiti (getzep/graphiti) |
| Graph database | Neo4j |
| LLM for extraction | Anthropic Claude Haiku 4.5 |
| Embeddings | Ollama / nomic-embed-text (local) |
| MCP server | Python, stdio transport |
| AI assistant | Claude Desktop via MCP |

Files chunked (1,500 chars), fed as episodes to Graphiti. Per episode, ~5 LLM calls: extract entities → dedup against graph → extract relationships → dedup relationships → update summaries.

Result: Neo4j graph, nodes = entities (functions, processes, tables, libs, concepts), edges = typed relationships (SUBSCRIBES_TO, WRITES_TO, CALLS, DEPENDS_ON). Both carry vector embeddings → hybrid search: graph traversal + semantic similarity.

MCP exposes `search_kx_knowledge` tool: natural language query → hybrid search → ranked results. Supports `group_ids` filter to scope by source type.

---

## Work Completed

- Ingestion pipeline: chunking, logging, error handling, progress monitoring
- Cloud (Anthropic) and local (Ollama) LLM backend support
- Token usage tracker with cost calculation per run
- Prompt caching (findings below)
- MCP server live in Claude Desktop on macOS

---

## Benchmarks

All runs on `kdb-x-mcp-server` (26 files) except gemma4. Results from timestamped logs.

| Model | Type | Episodes | Time | Speed | Hard Errors | Warnings | Cost |
|---|---|---|---|---|---|---|---|
| Haiku 4.5 | Cloud | 107 | 12.5 min | 7.0s/ep | 0 | 8 | $2.04 |
| Haiku 4.5 (cached) | Cloud | 107 | 11.9 min | 6.7s/ep | 0 | — | $2.04 |
| llama3.1:8b | Local | 16 ¹ | — | 89.2s/ep | 0 | — | $0 |
| llama3.1-fast ² | Local | 107 | 77.0 min | 43.2s/ep | 0 | 247 | $0 |
| gemma4:latest | Local | 43 ³ | — | 85.8s/ep | 5 | — | $0 |

¹ Terminated — too slow  
² Custom Ollama modelfile, context window 4,096 tokens → 2× faster than default llama3.1:8b  
³ Run on `docs` repo (451 files); abandoned — recurring JSON parse failures

### Extraction quality (107-episode runs)

| Model | Entities | Edges | Edges/Episode | Quality |
|---|---|---|---|---|
| Haiku 4.5 | 101 | 179 | 1.67 | High — precise, domain-relevant |
| llama3.1-fast | 161 | 85 | 0.79 | Low — hallucinations throughout |

**llama3.1-fast hallucinations (247 warnings):**
- 130+ fabricated relations with no connection to MCP server docs: `PLAYS_GAMES_ON` (39×), `FEELS_HAPPY_ABOUT` (23×), `LIVES_IN` (10×)
- 58 invalid dedup IDs → Graphiti skips resolutions → duplicate entities
- Garbled names: `_WORS_ONLY_WHEN_CLIENT_AND_SERVER_AXE_IN_SAME_HOST`, `_IS_REQUATED_BY_KDZX_MCPI SERE`

161 entities vs 101 is misleading — large portion attached to nonsensical relationships. Graph noise corrupts traversal.

**gemma4 failure:** recurring `Expecting value: line 1 column 1` and `NodeResolutions() argument after ** must be a mapping, not list` — model couldn't produce valid structured output for Graphiti dedup.

**Haiku 8 warnings:** all benign graph state misses, no quality impact.

**Conclusion:** Haiku 4.5 best performer among models tested. Local models tested (llama3.1, gemma4) ran on consumer hardware with default configs — results reflect those constraints, not local LLMs generally. Models like Qwen, Deepseek V3/V4 not tested; may yield different results.

---

## Test Corpus (scale reference only)

| Repository | Content | Files | Est. cost |
|---|---|---|---|
| kdb-x-mcp-server ✓ | MCP server | 25 | $2.04 (done) |
| kdbai-mcp-server | MCP server | 24 | ~$2.00 |
| kx-skills | LLM skill defs | 27 | ~$2.67 |
| kx-sdk-reference-architectures | Docker/q configs | 148 | ~$6.16 |
| kx-vscode | VS Code extension | 26 | ~$10.27 |
| pykx | Python lib + tests | 253 | ~$53.00 |
| docs | KX documentation | 449 | ~$77.82 |
| nvidia-kx-samples | NVIDIA/KX ML samples | 529 | ~$139.07 |
| **Total** | | **1,481** | **~$293** |

Production corpus would be different — internal codebases, Slack, Freshdesk, Jira — with different volume characteristics.

---

## Cost and Scaling

### Prompt caching
Caching not cost-effective for long-form document ingestion:
- Haiku 4.5 requires 4,096-token minimum cacheable block
- Graphiti system prompts ~15 tokens — threshold never reached
- Injecting large static prefix to trigger caching adds more cost than saves (dominant cost is variable document content ~4,648 tokens/call)

**Caching highly effective for short-form content.** Slack/Freshdesk (~350 variable tokens/call) + 6,000-token cached prefix → ~88% input token reduction at scale. Infrastructure already implemented, activates when short-form sources added.

### Multi-agent ingestion
Graphiti dedup scoped to `group_id` namespace. Two agents on same namespace → race condition → duplicate entities.

- **Parallel per-repo (isolated namespaces):** safe, cuts ~31h → ~4h. Cross-repo connections surface at query time via multi-group search (Graphiti supports natively).
- **Two-phase parallel + merge:** theoretically possible but requires raw Neo4j outside Graphiti — edge reconciliation complexity not justified at current scale.

**Recommendation:** sequential ingestion, single namespace. Highest quality, full cross-repo merging. Adopt parallel when ingestion frequency increases (e.g. nightly Slack updates).

---

## What PoC Validates

1. **Extraction quality with capable model is high.** Haiku 4.5: 101 entities, 179 typed relationships, 0 errors, 0 hallucinations. 1.67 edges/episode — graph captures how things connect, not just what exists.

2. **Local models tested (llama3.1-fast, gemma4) not viable at current config.** llama3.1-fast: 247 warnings, fabricated relationships, 58 dedup failures. gemma4: invalid structured output. Scope limited to models tested — stronger local models (Qwen, Deepseek V3/V4) untested and may perform differently.

3. **MCP integration works.** `search_kx_knowledge` live in Claude Desktop, returns relevant results.

4. **Cost at scale understood.** 8-repo test corpus ~$293. Short-form sources (Slack, Freshdesk) benefit ~88% from caching. Long-form document ingestion does not.

---

## If This Moves Forward

**Define production corpus.** 8 public repos were test bed. Real value from internal sources: project codebases, Slack engineering/support channels, Freshdesk tickets, Jira history.

**Demo on real questions.** 10–15 developer questions requiring manual search today → run against graph → evaluate accuracy. Onboarding scenario natural fit — "what is the role of the tickerplant?", "how does end-of-day persistence work?", "where does PyKX fit in the stack?" — each answered in single query with relationship context, vs multiple doc pages today.

**Resolve data access:**
- Public repos: ready
- Slack: export or API access + permissions
- Freshdesk: API key with ticket read
- Jira: API token + project scoping
- Internal codebases: repo access + scope decision

**Cost baseline:** Haiku 4.5 at ~$0.0185/1,000 chars. Full corpus cost once source list defined. Caching reduces Slack/Freshdesk meaningfully at scale.

---

## Broader Relevance

From demo review:
- Graph navigation over precomputed structural model more valuable than file search for architectural understanding
- Reduced token usage = concrete org benefit — graph means assistant doesn't re-read source files per query
- Applicable to any sufficiently complex codebase

PoC establishes: working ingestion pipeline, live MCP tool, benchmarked extraction quality, clear cost model. Next milestone: structured demo on real developer questions.

---

## References

- Repo: https://github.com/arvind-iyer-2001/knowledgeability-ai
- Graphiti: https://github.com/getzep/graphiti
- Technical detail: [WHAT_WE_BUILT.md](WHAT_WE_BUILT.md)
- Design decisions: [TRADEOFFS.md](TRADEOFFS.md)
