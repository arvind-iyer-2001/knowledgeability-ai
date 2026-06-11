# Production Ingest Report — `group_id=production`

**Date:** 2026-06-11 | **Model:** Claude Haiku 4.5 | **Repos:** `kx-skills`, `kdb-x-mcp-server`, `kdbai-mcp-server`

---

## Run Summary

| Metric | Value |
|---|---|
| Files | 77 |
| Chars | 371,290 |
| Episodes | 170 / 170 (100%) |
| Elapsed time | 42m04s (14.85s/ep) |
| LLM calls | 1,955 (11.5 calls/ep) |
| Tokens | in=11,246,041 / out=270,258 |
| Hard errors | 0 |
| Cost (tracker, pre-fix) | $10.0779 |
| **Cost (actual billed)** | **$12.31** |

Log: `logs/ingest_20260611_165434.log` · Progress: `logs/progress_20260611_165434.log`

---

## Pricing Bug Found and Fixed

Tracker estimated $10.08; Anthropic billing CSV (`claude_api_cost_2026_06_05_to_2026_06_11.csv`, key `aiyer-kx`, 2026-06-11) showed **$12.31** ($10.99 input + $1.32 output) — 22% higher.

**Cause:** `ingest.py` had Claude **3.5** Haiku rates ($0.80/$4.00 per Mtok) hardcoded for the **4.5** model entry. Correct rate is $1.00/$5.00 per Mtok.

**Fix applied** (`ingest.py:50-53`):
```python
HAIKU_INPUT_COST_PER_M  = 1.00   # was 0.80
HAIKU_OUTPUT_COST_PER_M = 5.00   # was 4.00
HAIKU_CACHE_WRITE_PER_M = 1.25   # was 1.00
HAIKU_CACHE_READ_PER_M  = 0.10   # was 0.08
```

Cache write/read are unaffected by token volume in this run (cache_write=0, cache_read=0), so the entire $2.23 gap is the input/output rate fix (1.25× both → uniform 25% scale, matching $10.0779 × 1.25 ≈ $12.60, within ~2% of the $12.31 actual — remaining gap is tracker vs. billing token-count rounding).

**Calibration constants also updated** (`ingest.py:319-324`) using this run (170 eps, real numbers) in place of the old 58-ep Haiku v2 benchmark:

| Constant | Old | New |
|---|---|---|
| Cost/episode | $0.0526 | $0.0724 |
| Seconds/episode | 13.4 | 14.85 |
| Calls/episode | 10.8 | 11.5 |

---

## Graph State (`group_id=production`)

| Node/Edge type | Count |
|---|---|
| Entities | 729 |
| Relations (`RELATES_TO`) | 824 |
| Episode→entity links (`MENTIONS`) | 1,167 |

1.13 relations/episode — between the original Haiku v2 (4.45 edges/ep, 3000-char chunks + domain context, single small repo) and the original 107-ep Haiku baseline (1.67 edges/ep). Larger, more heterogeneous corpus (3 repos) likely dilutes per-episode relation density vs. the single-repo v2 benchmark.

---

## Errors and Warnings

**0 hard failures.** All 170 episodes ingested successfully.

- **32 ERROR-level lines** — all `Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists` from `build_indices_and_constraints()` re-running against an already-initialized schema. Benign, idempotent, expected (consistent with prior runs per REPORT.md).
- **111 WARNING-level lines**, breakdown:
  - 7× Neo4j `fact_embedding`/`episodes` property-not-found notifications (cosmetic — properties don't exist until first edge written, self-resolves)
  - 6× `LLM returned invalid duplicate_facts idx values` — same benign dedup-miss class seen in prior Haiku runs
  - 5× `Target entity not found in nodes for edge relation: RELATED_SYSTEM_VARIABLE`
  - 3× `Source entity not found in nodes for edge relation: SUPPORTED_ON`
  - Plus a handful of `RETURNS`/`CAN_RETURN` source-entity-not-found warnings near end of run

No quality-impacting failures observed — consistent with REPORT.md's "Haiku warnings: all benign graph state misses."

---

## Updated Full-Corpus Cost Projection (corrected pricing/calibration)

Recomputed via `ingest.py --dry-run --repo <repo>` for each of the 8 KX repos:

| Repository | Files | Chars | Episodes | Est. cost | Est. time |
|---|---|---|---|---|---|
| kdb-x-mcp-server | 26 | 122,567 | 58 | $4.20 | 14m21s |
| kdbai-mcp-server | 24 | 105,599 | 49 | $3.55 | 12m07s |
| kx-skills | 27 | 143,124 | 63 | $4.56 | 15m35s |
| kx-sdk-reference-architectures | 148 | 332,067 | 209 | $15.13 | 51m43s |
| kx-vscode | 35 | 570,484 | 218 | $15.79 | 53m56s |
| pykx | 261 | 2,859,069 | 1,136 | $82.26 | 4h41m |
| docs | 451 | 4,252,245 | 1,690 | $122.38 | 6h58m |
| nvidia-kx-samples | 543 | 7,556,062 | 2,885 | $208.91 | 11h54m |
| **Total (full `dump/`)** | **1,515** | **15,941,217** | **6,308** | **$456.77** | **~26h01m** |

vs. the old WHAT_WE_BUILT.md estimate of ~$293 (38% higher under corrected pricing/calibration).

`kx-skills` + `kdb-x-mcp-server` + `kdbai-mcp-server` (this run, 170 eps) was estimated at $12.31 — **170/6,308 = 2.7%** of the full corpus, **$12.31/$456.77 = 2.7%** of projected full-corpus cost. Consistent.

---

## Next Steps

- [ ] Decide whether to proceed with full 8-repo ingest at ~$457 (Haiku) — `kx-sdk-reference-architectures`, `kx-vscode`, `pykx`, `docs`, `nvidia-kx-samples` remain
- [ ] Consider pruning `nvidia-kx-samples` (largest single line item at $208.91, 46% of total) per AGENT_HANDOFF.md's existing recommendation
- [ ] Re-baseline Sonnet pricing constants (`MODEL_COSTS["claude-sonnet-4-6"]`) against actual billing if Sonnet is used for a production run — same class of bug may exist there
