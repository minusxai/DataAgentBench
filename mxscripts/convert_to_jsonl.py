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
    "mongo": "mongo",
    "mongodb": "mongo",
    "bigquery": "bigquery",
    "athena": "athena",
    "csv": "csv",
}

# Defaults match mxscripts/docker-compose.yml.
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "postgres"
PG_PASSWORD = "postgres"
MONGO_HOST = "localhost"
MONGO_PORT = 27017


def build_connections(db_clients: dict, bench_dir: Path) -> list[dict]:
    connections = []
    for name, info in db_clients.items():
        db_type = info.get("db_type", "")
        dialect = DB_TYPE_TO_DIALECT.get(db_type, db_type)

        if db_type in ("postgres", "postgresql"):
            # `username` (not `user`) — MinusX's PostgresConnector reads
            # `this.config.username`. Writing `user` here leaves the
            # connector with `username: undefined`, which makes `pg.Pool`
            # fall back to `PGUSER`/OS user and fail auth against the
            # `dab-postgres` container.
            #
            # `ssl: false` — the local docker-compose Postgres is plain
            # TCP, but the connector defaults to requesting SSL with
            # `{ rejectUnauthorized: false }` (production-safe for hosted
            # Postgres, all of which require SSL). Without this opt-out
            # the connector errors with "The server does not support SSL
            # connections" before auth is attempted. Keeping it scoped
            # to per-connection config means production users with cloud
            # Postgres are unaffected.
            config = {
                "host": PG_HOST,
                "port": PG_PORT,
                "database": info.get("db_name", ""),
                "username": PG_USER,
                "password": PG_PASSWORD,
                "ssl": False,
            }
        elif db_type in ("mongo", "mongodb"):
            config = {
                "host": MONGO_HOST,
                "port": MONGO_PORT,
                "database": info.get("db_name", ""),
            }
        else:
            db_path = info.get("db_path", "")
            config = {
                "file_path": str((bench_dir / db_path).resolve()),
            }

        connections.append({
            "name": name,
            "dialect": dialect,
            "config": config,
        })
    return connections


def convert_one(benchmark: str, repo_root: Path, out_dir: Path) -> bool:
    bench_dir = repo_root / f"query_{benchmark}"

    if not bench_dir.is_dir():
        print(f"Error: benchmark directory not found: {bench_dir}", file=sys.stderr)
        return False

    config_path = bench_dir / "db_config.yaml"
    if not config_path.exists():
        print(f"Error: db_config.yaml not found in {bench_dir}", file=sys.stderr)
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f)

    db_clients = config.get("db_clients", {})
    allowed_connections = list(db_clients.keys())

    connections = build_connections(db_clients, bench_dir)
    connections_path = out_dir / f"{benchmark}_connections.json"
    with open(connections_path, "w") as f:
        json.dump(connections, f, indent=2)
        f.write("\n")
    print(f"[{benchmark}] Wrote {len(connections)} connections to {connections_path}")

    # --- Read documentation and additional docs ---
    docs = ""
    docs_path = bench_dir / "db_description.txt"
    if docs_path.exists():
        docs = docs_path.read_text().strip()

    additional_docs = ""
    additional_docs_path = bench_dir / "db_description_withhint.txt"
    if additional_docs_path.exists():
        additional_docs = additional_docs_path.read_text().strip()

    # --- Write input JSONL ---
    query_dirs = sorted(
        [d for d in bench_dir.iterdir() if d.is_dir() and d.name.startswith("query") and d.name != "query_dataset"],
        key=lambda d: int("".join(filter(str.isdigit, d.name)) or "0"),
    )

    if not query_dirs:
        print(f"Error: no query directories found in {bench_dir}", file=sys.stderr)
        return False

    input_path = out_dir / f"{benchmark}_input.jsonl"
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
                "docs": docs,
                "additional_docs": additional_docs,
            }
            out.write(json.dumps(record) + "\n")
            count += 1

    print(f"[{benchmark}] Wrote {count} queries to {input_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Convert sub-benchmark(s) to eval-agent format")
    parser.add_argument("benchmarks", nargs="*", help="Sub-benchmark name(s), e.g. stockindex stockmarket")
    parser.add_argument("--all", action="store_true", help="Convert every query_* directory in the repo")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "mxdatasets"

    if args.all:
        benchmarks = sorted(
            d.name[len("query_"):]
            for d in repo_root.iterdir()
            if d.is_dir() and d.name.startswith("query_") and (d / "db_config.yaml").exists()
        )
    else:
        benchmarks = args.benchmarks

    if not benchmarks:
        parser.error("Specify one or more benchmarks, or pass --all")

    failures = [b for b in benchmarks if not convert_one(b, repo_root, out_dir)]
    if failures:
        print(f"\nFailed: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
