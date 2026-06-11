"""
Import a Graphiti group exported by export_graph.py into Neo4j on another system.

Idempotent (MERGE on uuid) - safe to re-run. Rebuilds Graphiti's indices and
constraints afterwards so vector search works.

Usage:
  uv run python3 import_graph.py --in kx-graph-export.json
"""

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase
from graphiti_core.driver.neo4j_driver import Neo4jDriver

DATETIME_FIELDS = {"created_at", "valid_at", "expired_at", "invalid_at", "reference_time"}


def merge_node(session, label: str, props: dict):
    plain = {k: v for k, v in props.items() if k not in DATETIME_FIELDS}
    dt = {k: v for k, v in props.items() if k in DATETIME_FIELDS and v is not None}

    set_clauses = ["n += $plain"]
    params = {"uuid": props["uuid"], "plain": plain}
    for k, v in dt.items():
        set_clauses.append(f"n.{k} = datetime($dt_{k})")
        params[f"dt_{k}"] = v

    session.run(
        f"MERGE (n:{label} {{uuid: $uuid}}) SET " + ", ".join(set_clauses),
        **params,
    )


def merge_rel(session, rel_type: str, props: dict):
    src, tgt, uuid = props["source_node_uuid"], props["target_node_uuid"], props["uuid"]
    plain = {
        k: v for k, v in props.items()
        if k not in DATETIME_FIELDS and k not in ("source_node_uuid", "target_node_uuid")
    }
    dt = {k: v for k, v in props.items() if k in DATETIME_FIELDS and v is not None}

    set_clauses = ["r += $plain"]
    params = {"src": src, "tgt": tgt, "uuid": uuid, "plain": plain}
    for k, v in dt.items():
        set_clauses.append(f"r.{k} = datetime($dt_{k})")
        params[f"dt_{k}"] = v

    session.run(
        f"""
        MATCH (a {{uuid: $src}}), (b {{uuid: $tgt}})
        MERGE (a)-[r:{rel_type} {{uuid: $uuid}}]->(b)
        SET """ + ", ".join(set_clauses),
        **params,
    )


async def run(in_path: str):
    with open(in_path) as f:
        data = json.load(f)

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password123")
    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session() as session:
        for n in data["entities"]:
            merge_node(session, "Entity", n)
        for n in data["episodes"]:
            merge_node(session, "Episodic", n)
        for r in data["relates_to"]:
            merge_rel(session, "RELATES_TO", r)
        for r in data["mentions"]:
            merge_rel(session, "MENTIONS", r)

    driver.close()

    print(
        f"Imported group '{data['group_id']}': "
        f"{len(data['entities'])} entities, {len(data['episodes'])} episodes, "
        f"{len(data['relates_to'])} RELATES_TO, {len(data['mentions'])} MENTIONS"
    )

    print("Building indices and constraints...")
    graph_driver = Neo4jDriver(uri, user, password)
    await graph_driver.build_indices_and_constraints()
    await graph_driver.close()
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import a Graphiti group JSON export into Neo4j")
    parser.add_argument("--in", dest="in_path", default="kx-graph-export.json")
    args = parser.parse_args()
    asyncio.run(run(args.in_path))
