#!/usr/bin/env python3
"""Convert a DataAgentBench sub-benchmark into files consumable by the eval agent.

Usage:
    python mxscripts/convert_to_jsonl.py stockindex

Outputs (in mxscripts/):
    <benchmark>_input.jsonl      - one query per line
    <benchmark>_connections.json  - connection configs for the databases
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

DB_TYPE_TO_DIALECT = {
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "bigquery": "bigquery",
    "athena": "athena",
    "csv": "csv",
}


def build_connections(db_clients: dict, bench_dir: Path) -> list[dict]:
    connections = []
    for name, info in db_clients.items():
        db_type = info.get("db_type", "")
        dialect = DB_TYPE_TO_DIALECT.get(db_type, db_type)
        db_path = info.get("db_path", "")

        # Resolve to absolute path relative to bench_dir
        abs_path = str((bench_dir / db_path).resolve())

        conn = {
            "name": name,
            "dialect": dialect,
            "config": {
                "file_path": abs_path,
            },
        }
        connections.append(conn)
    return connections


def main():
    parser = argparse.ArgumentParser(description="Convert a sub-benchmark to eval-agent format")
    parser.add_argument("benchmark", help="Sub-benchmark name, e.g. stockindex")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    bench_dir = repo_root / f"query_{args.benchmark}"
    out_dir = repo_root / "mxdatasets"

    if not bench_dir.is_dir():
        print(f"Error: benchmark directory not found: {bench_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse db_config.yaml
    config_path = bench_dir / "db_config.yaml"
    if not config_path.exists():
        print(f"Error: db_config.yaml not found in {bench_dir}", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    db_clients = config.get("db_clients", {})
    allowed_connections = list(db_clients.keys())

    # --- Write connections config ---
    connections = build_connections(db_clients, bench_dir)
    connections_path = out_dir / f"{args.benchmark}_connections.json"
    with open(connections_path, "w") as f:
        json.dump(connections, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(connections)} connections to {connections_path}")

    # --- Write input JSONL ---
    query_dirs = sorted(
        [d for d in bench_dir.iterdir() if d.is_dir() and d.name.startswith("query") and d.name != "query_dataset"],
        key=lambda d: int("".join(filter(str.isdigit, d.name)) or "0"),
    )

    if not query_dirs:
        print(f"Error: no query directories found in {bench_dir}", file=sys.stderr)
        sys.exit(1)

    input_path = out_dir / f"{args.benchmark}_input.jsonl"
    count = 0
    with open(input_path, "w") as out:
        for qdir in query_dirs:
            query_file = qdir / "query.json"
            if not query_file.exists():
                print(f"Warning: skipping {qdir.name}, no query.json found", file=sys.stderr)
                continue

            with open(query_file) as f:
                user_message = json.load(f)

            record = {
                "query_id": qdir.name,
                "user_message": user_message,
                "allowed_connections": allowed_connections,
            }
            out.write(json.dumps(record) + "\n")
            count += 1

    print(f"Wrote {count} queries to {input_path}")


if __name__ == "__main__":
    main()
