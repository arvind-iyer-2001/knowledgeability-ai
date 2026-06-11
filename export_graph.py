"""
Export a Graphiti group_id from Neo4j to a portable JSON file.

For moving a graph to another (air-gapped) system: run this here, copy the
JSON file over (USB/cloud), then run import_graph.py there.

Usage:
  uv run python3 export_graph.py --group production --out kx-graph-export.json
"""

import argparse
import json
import os

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase

DATETIME_FIELDS = {"created_at", "valid_at", "expired_at", "invalid_at", "reference_time"}


def serialize(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        if k in DATETIME_FIELDS and v is not None:
            out[k] = v.iso_format()
        else:
            out[k] = v
    return out


def run(group_id: str, out_path: str):
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password123")
    driver = GraphDatabase.driver(uri, auth=(user, password))

    data = {"group_id": group_id, "entities": [], "episodes": [], "relates_to": [], "mentions": []}

    with driver.session() as session:
        for label, key in [("Entity", "entities"), ("Episodic", "episodes")]:
            for record in session.run(f"MATCH (n:{label} {{group_id: $gid}}) RETURN n", gid=group_id):
                props = dict(record["n"])
                props.pop("labels", None)
                data[key].append(serialize(props))

        for rel_type, key in [("RELATES_TO", "relates_to"), ("MENTIONS", "mentions")]:
            for record in session.run(
                f"""
                MATCH (a {{group_id: $gid}})-[r:{rel_type}]->(b {{group_id: $gid}})
                RETURN a.uuid AS src, b.uuid AS tgt, r AS r
                """,
                gid=group_id,
            ):
                rel = serialize(dict(record["r"]))
                rel["source_node_uuid"] = record["src"]
                rel["target_node_uuid"] = record["tgt"]
                data[key].append(rel)

    driver.close()

    with open(out_path, "w") as f:
        json.dump(data, f)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(
        f"Exported group '{group_id}': "
        f"{len(data['entities'])} entities, {len(data['episodes'])} episodes, "
        f"{len(data['relates_to'])} RELATES_TO, {len(data['mentions'])} MENTIONS "
        f"-> {out_path} ({size_mb:.1f} MB)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a Graphiti group from Neo4j to JSON")
    parser.add_argument("--group", default="production", help="group_id to export (default: production)")
    parser.add_argument("--out", default="kx-graph-export.json")
    args = parser.parse_args()
    run(args.group, args.out)
