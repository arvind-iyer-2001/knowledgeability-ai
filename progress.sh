#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python3"

running=$(ps aux | grep "ingest.py" | grep -v grep | wc -l)
status=$( [ "$running" -gt 0 ] && echo "RUNNING" || echo "STOPPED" )

echo "Ingest: $status"
DOTENV_PATH="$SCRIPT_DIR/.env" "$PYTHON" - <<'EOF'
from neo4j import GraphDatabase
from dotenv import load_dotenv
import os
load_dotenv(os.environ['DOTENV_PATH'])
d = GraphDatabase.driver(
    os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
)
with d.session() as s:
    # Total counts
    nodes = s.run('MATCH (n:Entity) RETURN count(n) AS c').single()['c']
    edges = s.run('MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS c').single()['c']
    eps   = s.run('MATCH (e:Episodic) RETURN count(e) AS c').single()['c']
    print(f'Total  — Entities: {nodes} | Edges: {edges} | Episodes: {eps}')
    # Per-group counts
    groups = s.run('MATCH (e:Episodic) RETURN DISTINCT e.group_id AS g').value()
    for g in sorted(g for g in groups if g):
        n = s.run('MATCH (n:Entity {group_id:$g}) RETURN count(n) AS c', g=g).single()['c']
        e = s.run('MATCH ()-[r:RELATES_TO {group_id:$g}]->() RETURN count(r) AS c', g=g).single()['c']
        ep = s.run('MATCH (x:Episodic {group_id:$g}) RETURN count(x) AS c', g=g).single()['c']
        print(f'  [{g}] Entities: {n} | Edges: {e} | Episodes: {ep}')
d.close()
EOF
