#!/usr/bin/env python3
"""Load benchmark dumps into the docker-compose postgres/mongo containers.

Prereq: `docker compose -f mxscripts/docker-compose.yml up -d` (and wait healthy).

Usage:
    python mxscripts/setup_dbs.py <benchmark> [<benchmark> ...]
    python mxscripts/setup_dbs.py --all
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PG_CONTAINER = "dab-postgres"
MONGO_CONTAINER = "dab-mongo"
PG_USER = "postgres"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def load_postgres(db_name: str, sql_file_in_container: str) -> None:
    # Drop+create for idempotency.
    run([
        "docker", "exec", PG_CONTAINER,
        "psql", "-U", PG_USER, "-d", "postgres",
        "-c", f'DROP DATABASE IF EXISTS "{db_name}";',
    ])
    run([
        "docker", "exec", PG_CONTAINER,
        "psql", "-U", PG_USER, "-d", "postgres",
        "-c", f'CREATE DATABASE "{db_name}";',
    ])
    run([
        "docker", "exec", PG_CONTAINER,
        "psql", "-U", PG_USER, "-d", db_name, "-v", "ON_ERROR_STOP=1",
        "-f", sql_file_in_container,
    ])


def load_mongo(db_name: str, dump_folder_in_container: str) -> None:
    # mongorestore --drop replaces existing collections.
    run([
        "docker", "exec", MONGO_CONTAINER,
        "mongorestore", "--drop", "--db", db_name, dump_folder_in_container,
    ])


def setup_one(benchmark: str, repo_root: Path) -> bool:
    bench_dir = repo_root / f"query_{benchmark}"
    config_path = bench_dir / "db_config.yaml"
    if not config_path.exists():
        print(f"[{benchmark}] no db_config.yaml at {config_path}", file=sys.stderr)
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    db_clients = config.get("db_clients", {})
    did_anything = False

    for name, info in db_clients.items():
        db_type = info.get("db_type", "")
        if db_type == "postgres":
            db_name = info["db_name"]
            sql_file = info["sql_file"]  # repo-relative under bench_dir
            in_container = f"/workspace/query_{benchmark}/{sql_file}"
            print(f"[{benchmark}] postgres: {name} -> db={db_name} from {sql_file}")
            load_postgres(db_name, in_container)
            did_anything = True
        elif db_type == "mongo":
            db_name = info["db_name"]
            dump_folder = info["dump_folder"]
            # dump folders contain a subfolder named after the db; restore points there.
            in_container = f"/workspace/query_{benchmark}/{dump_folder}/{db_name}"
            print(f"[{benchmark}] mongo: {name} -> db={db_name} from {dump_folder}")
            load_mongo(db_name, in_container)
            did_anything = True
        else:
            # sqlite/duckdb -- file-based, nothing to load
            continue

    if not did_anything:
        print(f"[{benchmark}] no postgres/mongo clients; nothing to do")
    return True


def main():
    parser = argparse.ArgumentParser(description="Load postgres/mongo benchmark dumps into docker containers")
    parser.add_argument("benchmarks", nargs="*")
    parser.add_argument("--all", action="store_true", help="Process every query_* directory")
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("Error: docker not found in PATH", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent

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

    failures = []
    for b in benchmarks:
        try:
            if not setup_one(b, repo_root):
                failures.append(b)
        except subprocess.CalledProcessError as e:
            print(f"[{b}] failed: {e}", file=sys.stderr)
            failures.append(b)

    if failures:
        print(f"\nFailed: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
