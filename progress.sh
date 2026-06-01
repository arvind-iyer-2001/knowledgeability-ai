#!/usr/bin/env bash
set -euo pipefail

running=$(ps aux | grep "ingest.py" | grep -v grep | wc -l)
status=$( [ "$running" -gt 0 ] && echo "RUNNING" || echo "STOPPED" )

echo "Ingest: $status"
python3 - <<'EOF'
from neo4j import GraphDatabase
from dotenv import load_dotenv
import os
load_dotenv('/home/aiyer/knowledgeability-ai/.env')
d = GraphDatabase.driver(
    os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
)
with d.session() as s:
    nodes = s.run('MATCH (n:Entity) RETURN count(n) AS c').single()['c']
    edges = s.run('MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS c').single()['c']
    eps   = s.run('MATCH (e:Episodic) RETURN count(e) AS c').single()['c']
    print(f'Entities: {nodes} | Edges: {edges} | Episodes: {eps}')
d.close()
EOF
