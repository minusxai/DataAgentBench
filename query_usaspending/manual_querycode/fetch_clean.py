"""Fetch a bounded USAspending snapshot into clean/clean.sqlite.

Scope (kept small to keep dataset under ~50MB):
  - Contract awards: FY2024 Q4 (2024-07-01 .. 2024-09-30), bounded to ~25k by amount
  - Derived recipient registry from those awards
  - Derived awarding-agency hierarchy from those awards
  - NAICS hierarchy: top-level (2-digit) sector descriptions, public reference

Sources:
  - https://api.usaspending.gov/api/v2/search/spending_by_award/   (paginated)
  - https://api.usaspending.gov/api/v2/references/naics/           (reference)

This is the canonical un-corrupted snapshot. Never seen by the agent.
"""
from __future__ import annotations
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN_DB = ROOT / "clean" / "clean.sqlite"
SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

START = "2024-07-01"
END   = "2024-09-30"
PAGE  = 100
TARGET_ROWS = 25000  # cap


# Fields to request from the API. The API will return them under these keys.
FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "Recipient Location",
    "Awarding Agency", "Awarding Sub Agency", "Funding Agency",
    "Award Amount", "Total Outlays", "Description",
    "Start Date", "End Date",
    "NAICS", "PSC", "Contract Award Type", "recipient_id",
]

AWARD_TYPES = ["A", "B", "C", "D"]  # Definitive contracts: BPA Call, Purchase Order, Delivery Order, Definitive Contract


def _post(url: str, body: dict, retries: int = 5) -> dict:
    data = json.dumps(body).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "DataAgentBench/usaspending",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == retries - 1:
                raise
            wait = 2 ** i
            print(f"  retry {i+1}/{retries} after {wait}s: {str(e)[:120]}", file=sys.stderr)
            time.sleep(wait)


def init_schema(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.executescript("""
    DROP TABLE IF EXISTS contracts;
    DROP TABLE IF EXISTS recipients;
    DROP TABLE IF EXISTS agencies;
    DROP TABLE IF EXISTS naics;

    CREATE TABLE contracts (
      award_id TEXT PRIMARY KEY,
      generated_internal_id TEXT,
      recipient_name TEXT,
      recipient_uei TEXT,
      recipient_state TEXT,
      awarding_agency TEXT,
      awarding_sub_agency TEXT,
      funding_agency TEXT,
      amount REAL,
      total_outlays REAL,
      description TEXT,
      start_date TEXT,
      end_date TEXT,
      naics_code TEXT,
      naics_description TEXT,
      psc_code TEXT,
      psc_description TEXT,
      award_type TEXT
    );
    CREATE TABLE recipients (
      uei TEXT PRIMARY KEY,
      name TEXT,
      state TEXT,
      n_contracts INTEGER,
      total_amount REAL
    );
    CREATE TABLE agencies (
      name TEXT PRIMARY KEY,
      n_contracts INTEGER,
      total_amount REAL
    );
    CREATE TABLE naics (
      code TEXT PRIMARY KEY,
      description TEXT,
      sector TEXT
    );

    CREATE INDEX idx_contracts_recipient ON contracts(recipient_uei);
    CREATE INDEX idx_contracts_agency ON contracts(awarding_agency);
    CREATE INDEX idx_contracts_naics ON contracts(naics_code);
    """)
    conn.commit()


def fetch_contracts(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    page = 1
    total = 0
    while total < TARGET_ROWS:
        body = {
            "filters": {
                "award_type_codes": AWARD_TYPES,
                "time_period": [{"start_date": START, "end_date": END}],
            },
            "fields": FIELDS,
            "page": page,
            "limit": PAGE,
            # Sort by start date (not amount) so we get a representative spread
            # of contract sizes — small, medium, and large — instead of only the
            # top-N by amount.
            "sort": "Start Date",
            "order": "desc",
        }
        print(f"contracts page={page} total={total}", flush=True)
        resp = _post(SEARCH_URL, body)
        items = resp.get("results", [])
        if not items:
            break
        for it in items:
            naics = it.get("NAICS") or {}
            psc = it.get("PSC") or {}
            recip_loc = it.get("Recipient Location") or {}
            cur.execute(
                "INSERT OR REPLACE INTO contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    it.get("Award ID"),
                    it.get("generated_internal_id"),
                    it.get("Recipient Name"),
                    it.get("Recipient UEI") or it.get("recipient_id"),
                    recip_loc.get("state_code") if isinstance(recip_loc, dict) else None,
                    it.get("Awarding Agency"),
                    it.get("Awarding Sub Agency"),
                    it.get("Funding Agency"),
                    it.get("Award Amount"),
                    it.get("Total Outlays"),
                    it.get("Description"),
                    it.get("Start Date"),
                    it.get("End Date"),
                    (naics.get("code") if isinstance(naics, dict) else None),
                    (naics.get("description") if isinstance(naics, dict) else None),
                    (psc.get("code") if isinstance(psc, dict) else None),
                    (psc.get("description") if isinstance(psc, dict) else None),
                    it.get("Contract Award Type"),
                ),
            )
        conn.commit()
        total += len(items)
        if not resp.get("page_metadata", {}).get("hasNext", False):
            break
        page += 1
        time.sleep(0.5)
    print(f"contracts: {total} rows ingested", flush=True)
    return total


def derive_recipients(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("DELETE FROM recipients")
    cur.execute("""
        INSERT INTO recipients (uei, name, state, n_contracts, total_amount)
        SELECT recipient_uei,
               MAX(recipient_name),
               MAX(recipient_state),
               COUNT(*),
               COALESCE(SUM(amount), 0)
        FROM contracts
        WHERE recipient_uei IS NOT NULL
        GROUP BY recipient_uei
    """)
    n = cur.execute("SELECT COUNT(*) FROM recipients").fetchone()[0]
    conn.commit()
    print(f"recipients: {n} derived")
    return n


def derive_agencies(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("DELETE FROM agencies")
    cur.execute("""
        INSERT INTO agencies (name, n_contracts, total_amount)
        SELECT awarding_agency, COUNT(*), COALESCE(SUM(amount), 0)
        FROM contracts
        WHERE awarding_agency IS NOT NULL
        GROUP BY awarding_agency
    """)
    n = cur.execute("SELECT COUNT(*) FROM agencies").fetchone()[0]
    conn.commit()
    print(f"agencies: {n} derived")
    return n


def fetch_naics(conn: sqlite3.Connection) -> int:
    """Pull NAICS sector hierarchy from USAspending's reference endpoint."""
    cur = conn.cursor()
    cur.execute("DELETE FROM naics")
    url = "https://api.usaspending.gov/api/v2/references/naics/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DataAgentBench"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"naics ref: failed ({e}); using contract-derived NAICS only", file=sys.stderr)
        cur.execute("""
            INSERT INTO naics (code, description, sector)
            SELECT DISTINCT naics_code, naics_description,
                   substr(naics_code, 1, 2)
            FROM contracts WHERE naics_code IS NOT NULL
        """)
        n = cur.execute("SELECT COUNT(*) FROM naics").fetchone()[0]
        conn.commit()
        return n
    n = 0
    def walk(node, sector=None):
        nonlocal n
        code = node.get("naics")
        desc = node.get("naics_description")
        if code:
            sec = sector if sector else (str(code)[:2] if len(str(code)) >= 2 else None)
            cur.execute(
                "INSERT OR REPLACE INTO naics VALUES (?,?,?)",
                (str(code), desc, sec),
            )
            n += 1
            for child in (node.get("children") or []):
                walk(child, sec)
    for top in data.get("results", []):
        walk(top)
    conn.commit()
    print(f"naics: {n} codes")
    return n


def main():
    CLEAN_DB.parent.mkdir(parents=True, exist_ok=True)
    if CLEAN_DB.exists():
        CLEAN_DB.unlink()
    conn = sqlite3.connect(CLEAN_DB)
    init_schema(conn)
    fetch_contracts(conn)
    derive_recipients(conn)
    derive_agencies(conn)
    fetch_naics(conn)
    conn.close()
    print(f"OK: clean snapshot at {CLEAN_DB}")


if __name__ == "__main__":
    main()
