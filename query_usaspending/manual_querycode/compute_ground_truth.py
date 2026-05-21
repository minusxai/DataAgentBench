"""Compute ground-truth answers from clean.sqlite.

Each query exercises >=2 DAB properties (multi-DB join, ID normalization,
amount parsing, fuzzy entity matching, etc.).

Run: python manual_querycode/compute_ground_truth.py
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path
from corrupt import should_drop_english, should_duplicate

ROOT = Path(__file__).resolve().parent.parent
CLEAN_DB = ROOT / "clean" / "clean.sqlite"


def q1(c):
    n = c.execute("""
        SELECT COUNT(*) FROM contracts
        WHERE awarding_agency = 'Department of Defense' AND amount > 1000000
    """).fetchone()[0]
    return str(n)


def q2(c):
    n = c.execute("""
        SELECT COUNT(*)
        FROM contracts c
        JOIN recipients r ON r.uei = c.recipient_uei
        WHERE r.state = 'CA' AND c.amount IS NOT NULL AND c.amount > 1000000
    """).fetchone()[0]
    return str(n)


def q3(c):
    n = c.execute("""
        SELECT COUNT(DISTINCT recipient_uei)
        FROM contracts
        WHERE substr(naics_code, 1, 2) = '33'
          AND recipient_uei IS NOT NULL
    """).fetchone()[0]
    return str(n)


def q4(c):
    rows = c.execute("""
        SELECT awarding_agency,
               COUNT(*) AS n,
               SUM(CASE WHEN amount > 1000000 THEN 1 ELSE 0 END) AS n_big
        FROM contracts
        WHERE awarding_agency IS NOT NULL
        GROUP BY awarding_agency
        HAVING n >= 10
        ORDER BY (1.0 * n_big / n) DESC, awarding_agency ASC
        LIMIT 1
    """).fetchall()
    return rows[0][0] if rows else "NONE"


def q5(c):
    n = c.execute("""
        SELECT COUNT(DISTINCT substr(naics_code, 1, 2))
        FROM contracts
        WHERE amount >= 10000000 AND naics_code IS NOT NULL
    """).fetchone()[0]
    return str(n)


def q6(c):
    suffix_re = re.compile(
        r"[\s,]+(inc\.?|incorporated|corp\.?|corporation|llc\.?|l\.l\.c\.?|"
        r"co\.?|company|ltd\.?|limited)$",
        re.IGNORECASE,
    )
    canon: dict[str, set[str]] = {}
    for uei, name in c.execute(
        "SELECT uei, name FROM recipients WHERE name IS NOT NULL"
    ):
        n = name.strip().lower()
        while True:
            new_n = suffix_re.sub("", n).strip().rstrip(",")
            if new_n == n:
                break
            n = new_n
        canon.setdefault(n, set()).add(uei)
    return str(sum(1 for ueis in canon.values() if len(ueis) > 1))


def q7(c):
    row = c.execute("""
        SELECT substr(naics_code, 1, 2) AS sec, COUNT(*) AS n
        FROM contracts
        WHERE naics_code IS NOT NULL AND amount IS NOT NULL AND amount >= 10000000
        GROUP BY sec
        ORDER BY n DESC, sec ASC
        LIMIT 1
    """).fetchone()
    return row[0]


def q8(c):
    n = c.execute("""
        SELECT COUNT(*)
        FROM contracts
        WHERE upper(recipient_name) LIKE '%LOCKHEED MARTIN%'
          AND awarding_agency = 'Department of Defense'
          AND amount IS NOT NULL AND amount > 1000000
    """).fetchone()[0]
    return str(n)


def q9(c):
    rows = c.execute("""
        SELECT award_id FROM contracts WHERE amount > 1000000 AND award_id IS NOT NULL
    """).fetchall()
    return str(sum(1 for (aid,) in rows if should_drop_english(aid)))


def q10(c):
    top10_ueis = {r[0] for r in c.execute("""
        SELECT recipient_uei, COUNT(*) AS n
        FROM contracts
        WHERE recipient_uei IS NOT NULL
        GROUP BY recipient_uei
        ORDER BY n DESC, recipient_uei ASC
        LIMIT 10
    """)}
    n = sum(
        1 for (award_id, uei) in c.execute(
            "SELECT award_id, recipient_uei FROM contracts "
            "WHERE recipient_uei IS NOT NULL AND award_id IS NOT NULL"
        )
        if should_duplicate(award_id) and uei in top10_ueis
    )
    return str(n)


QUERIES = {"1": q1, "2": q2, "3": q3, "4": q4, "5": q5,
           "6": q6, "7": q7, "8": q8, "9": q9, "10": q10}


def main():
    c = sqlite3.connect(CLEAN_DB)
    for qid, fn in QUERIES.items():
        ans = fn(c)
        out = ROOT / f"query{qid}" / "ground_truth.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(ans + "\n", encoding="utf-8")
        print(f"query{qid}: {ans}")


if __name__ == "__main__":
    main()
