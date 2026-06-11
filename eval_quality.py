"""
Quality eval for the KX knowledge graph.

Runs a fixed set of developer questions against Graphiti search and writes
the retrieved facts to a markdown report for manual review. Use this to
judge retrieval quality for a group_id (e.g. compare `production` vs
`sonnet-v2` vs `haiku-v2`).

Usage:
  uv run python3 eval_quality.py                          # production group, default questions
  uv run python3 eval_quality.py --group sonnet-v2 --group haiku-v2
  uv run python3 eval_quality.py --num-results 8 --out report.md
"""

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from query import build_graphiti

QUESTIONS = [
    "What is the role of the tickerplant in a kdb+tick architecture?",
    "How does end-of-day persistence work, and which functions are involved?",
    "What does .u.upd do?",
    "How does an RDB subscribe to a tickerplant?",
    "What is the difference between an aj (as-of) join and a lj (left) join?",
    "How do PyKX licensed and unlicensed modes differ?",
    "How do you convert a pykx.Table to a pandas DataFrame?",
    "What index types does KDB.AI support for vector search, and when would you use HNSW vs Flat?",
    "What does the s# attribute mean on a kdb+ column?",
    "How does the kdb-x-mcp-server expose kdb+ functionality to an LLM client?",
    "What tools does the kdbai-mcp-server provide?",
    "How are null values represented for different kdb+ types?",
]


async def run(group_ids: list[str] | None, num_results: int, out_path: str):
    client = build_graphiti("anthropic")

    lines = [
        "# KX Knowledge Graph - Quality Eval",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Groups: {group_ids or 'all'}",
        f"num_results per question: {num_results}",
        "",
    ]

    for q in QUESTIONS:
        lines.append(f"## {q}")
        results = await client.search(q, num_results=num_results, group_ids=group_ids)
        if not results:
            lines.append("\n_No results._\n")
            continue
        for i, edge in enumerate(results, 1):
            lines.append(f"{i}. **{edge.name}** - {edge.fact}")
        lines.append("")

    await client.close()

    Path(out_path).write_text("\n".join(lines))
    print(f"Wrote {len(QUESTIONS)} questions -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quality eval for the KX knowledge graph")
    parser.add_argument(
        "--group", action="append", dest="groups", metavar="GROUP_ID",
        help="group_id to search (repeatable). Default: production",
    )
    parser.add_argument("--num-results", type=int, default=5)
    parser.add_argument("--out", default="eval_report.md")
    args = parser.parse_args()

    groups = args.groups or ["production"]
    asyncio.run(run(groups, args.num_results, args.out))
