"""Corrupt clean/clean.sqlite into 4 agent-visible DBs in query_dataset/.

Engines:
  contracts_db    postgres  query_dataset/contracts.sql
  recipients_db   sqlite    query_dataset/recipients.db
  agencies_db     duckdb    query_dataset/agencies.duckdb
  descriptions_db mongo     query_dataset/descriptions/

NO date-format corruption. All non-date corruptions are hash-deterministic.

Corruption layers:

  contracts_db (postgres):
    - amount column DROPPED. Replaced by amount_text TEXT in sibling table
      contract_amounts. 105 possible formats: plain integers, comma-separated,
      dollar-prefixed, USD-suffixed, K/M/B-scaled abbreviations, full English
      word form, and sum-of-two-parts expressions. Each row picks one
      deterministically. ~1/30 rows also get a second superseded-amount row
      whose award_id has "_OLD" appended and whose amount_text encodes a
      different (scaled) value. A join from contracts to contract_amounts via
      normalized award_id naturally skips these _OLD rows.
    - award_id, UEI, and NAICS codes have OCR-like character substitutions
      applied (0↔O, 1↔I, 5↔S, 2↔Z, 6↔G, 8↔B) at a ~1/6 rate per eligible
      character, independently per table. On top of that, 207 prefix variants,
      3 case variants, and 5 separator variants are applied. Joining across
      tables requires entity resolution (loading all rows and fuzzy-matching),
      not a simple regex.
    - awarding_agency replaced with surface-form variants (DoD / DOD / etc.).
    - naics_code reformatted: "336411" / "naics-336411" / "33-6411", plus OCR.

  recipients_db (sqlite):
    - recipient UEI format varies between contracts_db and recipients_db for
      the same canonical UEI. Joins require normalization.
    - name has corporate-suffix variants (Inc / Incorporated / Corp / etc.).
    - state has surface-form variants ("CA" / "California" / "Calif.").

  agencies_db (duckdb):
    - agency_aliases lookup table maps all surface-form variants to canonical.
    - naics_sectors table stores canonical NAICS codes (agent must normalize
      the corrupted codes in contracts to match).

  descriptions_db (mongo):
    - One document per award_id (in descriptions-specific format).
    - ~10% of contracts have English description replaced by Spanish stub.

Manifest (clean/manifest.sqlite — never agent-visible):
    canonical_award_id  (canonical, contracts_form, amounts_form, descriptions_form)
    canonical_recipient (canonical_uei, canonical_name, contracts_uei, recipients_uei)
    canonical_agency    (award_id, canonical_agency, corrupted_surface)
    canonical_naics     (canonical_code, corrupted_code)
    canonical_amount    (canonical_award_id, canonical_amount, amount_text)

    NOTE: planted_eng_dropped and planted_duplicate are NOT stored in the
    manifest because both are fully deterministic (should_drop_english /
    should_duplicate). compute_ground_truth.py calls those functions directly.
"""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
CLEAN_DB = ROOT / "clean" / "clean.sqlite"
MANIFEST_DB = ROOT / "clean" / "manifest.sqlite"
QD = ROOT / "query_dataset"

CONTRACTS_SQL = QD / "contracts.sql"
RECIPIENTS_DB = QD / "recipients.db"
AGENCIES_DB   = QD / "agencies.duckdb"
DESC_DUMP     = QD / "descriptions"


def h(*parts) -> int:
    s = "|".join(str(p) for p in parts)
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


# ---------------------------------------------------------------------------
# Amount word-form helper
# ---------------------------------------------------------------------------

def _int_to_words(n: int) -> str:
    """Convert a non-negative integer to English words."""
    if n == 0:
        return "zero"
    ones = [
        "", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen",
    ]
    tens_w = ["", "", "twenty", "thirty", "forty", "fifty",
              "sixty", "seventy", "eighty", "ninety"]

    def below_thousand(v: int) -> str:
        if v < 20:
            return ones[v]
        if v < 100:
            t = tens_w[v // 10]
            o = ones[v % 10]
            return t + ("-" + o if o else "")
        rest = below_thousand(v % 100)
        return ones[v // 100] + " hundred" + (" " + rest if rest else "")

    parts: list[str] = []
    for scale, label in [(1_000_000_000, "billion"), (1_000_000, "million"),
                         (1_000, "thousand")]:
        if n >= scale:
            parts.append(below_thousand(n // scale) + " " + label)
            n %= scale
    if n > 0:
        parts.append(below_thousand(n))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Amount split for sum-of-parts formats
# ---------------------------------------------------------------------------

def _sum_split(x: float, salt: str) -> tuple[float, float]:
    ratios = [0.25, 0.30, 1 / 3, 0.40, 0.50, 0.60, 2 / 3, 0.70, 0.75]
    ratio = ratios[h("split-ratio", salt) % len(ratios)]
    part1 = round(x * ratio, 2)
    part2 = round(x - part1, 2)
    if part1 <= 0 or part2 <= 0:
        part1 = round(x * 0.5, 2)
        part2 = round(x - part1, 2)
    return part1, part2


# ---------------------------------------------------------------------------
# Amount format factories
# ---------------------------------------------------------------------------

def _make_plain(prefix: str, suffix: str, comma: bool, decimals: int):
    def f(x: float) -> str:
        if decimals > 0:
            s = f"{x:,.{decimals}f}" if comma else f"{x:.{decimals}f}"
        else:
            s = f"{int(x):,}" if comma else str(int(x))
        return prefix + s + suffix
    return f


def _make_k(prefix: str, suffix: str, comma: bool, decimals: int):
    def f(x: float) -> str:
        v = x / 1_000
        if decimals > 0:
            s = f"{v:,.{decimals}f}" if comma else f"{v:.{decimals}f}"
        else:
            s = f"{v:,.0f}" if comma else f"{v:.0f}"
        return prefix + s + suffix
    return f


def _make_m(prefix: str, suffix: str, decimals: int):
    def f(x: float) -> str:
        return prefix + f"{x / 1_000_000:.{decimals}f}" + suffix
    return f


def _make_b(prefix: str, suffix: str, decimals: int):
    def f(x: float) -> str:
        return prefix + f"{x / 1_000_000_000:.{decimals}f}" + suffix
    return f


def _make_sum(fmt_a, fmt_b, sep: str = " + "):
    def f(a: float, b: float) -> str:
        return fmt_a(a) + sep + fmt_b(b)
    return f


# ---------------------------------------------------------------------------
# 75 single-value formats + 30 sum-of-two-parts formats = 105 total
# ---------------------------------------------------------------------------

AMOUNT_FORMATS: list = [
    # --- unscaled plain / dollar / USD (20) ---
    _make_plain("",     "",           False, 0),   # 1500000
    _make_plain("",     "",           True,  0),   # 1,500,000
    _make_plain("",     "",           False, 2),   # 1500000.00
    _make_plain("",     "",           True,  2),   # 1,500,000.00
    _make_plain("$",    "",           False, 0),   # $1500000
    _make_plain("$",    "",           True,  0),   # $1,500,000
    _make_plain("$",    "",           False, 2),   # $1500000.00
    _make_plain("$",    "",           True,  2),   # $1,500,000.00
    _make_plain("",     " USD",       False, 0),   # 1500000 USD
    _make_plain("",     " USD",       True,  0),   # 1,500,000 USD
    _make_plain("",     " USD",       False, 2),   # 1500000.00 USD
    _make_plain("",     " USD",       True,  2),   # 1,500,000.00 USD
    _make_plain("USD ", "",           False, 0),   # USD 1500000
    _make_plain("USD ", "",           True,  0),   # USD 1,500,000
    _make_plain("USD ", "",           True,  2),   # USD 1,500,000.00
    _make_plain("US$",  "",           True,  0),   # US$1,500,000
    _make_plain("US$",  "",           True,  2),   # US$1,500,000.00
    _make_plain("$",    " USD",       True,  2),   # $1,500,000.00 USD
    _make_plain("",     " dollars",   True,  0),   # 1,500,000 dollars
    _make_plain("$",    " dollars",   True,  0),   # $1,500,000 dollars
    # --- K-scaled / thousands (16) ---
    _make_k("",  "K",                 False, 0),   # 1500K
    _make_k("",  "K",                 True,  0),   # 1,500K
    _make_k("",  "K",                 True,  1),   # 1,500.0K
    _make_k("",  "K",                 True,  2),   # 1,500.00K
    _make_k("$", "K",                 False, 0),   # $1500K
    _make_k("$", "K",                 True,  0),   # $1,500K
    _make_k("$", "K",                 True,  1),   # $1,500.0K
    _make_k("",  "k",                 False, 0),   # 1500k
    _make_k("",  "k",                 True,  0),   # 1,500k
    _make_k("$", "k",                 True,  0),   # $1,500k
    _make_k("",  " K",                True,  0),   # 1,500 K
    _make_k("$", " K",                True,  0),   # $1,500 K
    _make_k("",  " thousand",         True,  0),   # 1,500 thousand
    _make_k("$", " thousand",         True,  0),   # $1,500 thousand
    _make_k("",  " thousand dollars", True,  0),   # 1,500 thousand dollars
    _make_k("$", " thousand dollars", True,  0),   # $1,500 thousand dollars
    # --- M-scaled / millions (25) ---
    _make_m("",  "M",               1),   # 1.5M
    _make_m("",  "M",               2),   # 1.50M
    _make_m("",  "M",               3),   # 1.500M
    _make_m("$", "M",               1),   # $1.5M
    _make_m("$", "M",               2),   # $1.50M
    _make_m("$", "M",               3),   # $1.500M
    _make_m("",  "m",               1),   # 1.5m
    _make_m("$", "m",               1),   # $1.5m
    _make_m("",  "MM",              1),   # 1.5MM  (finance double-M)
    _make_m("$", "MM",              1),   # $1.5MM
    _make_m("",  " M",              1),   # 1.5 M
    _make_m("$", " M",              1),   # $1.5 M
    _make_m("",  " M",              2),   # 1.50 M
    _make_m("",  " million",        1),   # 1.5 million
    _make_m("",  " million",        2),   # 1.50 million
    _make_m("",  " million",        3),   # 1.500 million
    _make_m("$", " million",        1),   # $1.5 million
    _make_m("$", " million",        2),   # $1.50 million
    _make_m("",  " Million",        1),   # 1.5 Million
    _make_m("$", " Million",        1),   # $1.5 Million
    _make_m("",  " million dollars", 1),  # 1.5 million dollars
    _make_m("$", " million dollars", 1),  # $1.5 million dollars
    _make_m("",  " Million Dollars", 1),  # 1.5 Million Dollars
    _make_m("",  " million USD",     2),  # 1.50 million USD
    _make_m("",  " mil",             2),  # 1.50 mil  (informal)
    # --- B-scaled / billions (10) ---
    _make_b("",  "B",               4),   # 0.0015B
    _make_b("",  "B",               5),   # 0.00150B
    _make_b("$", "B",               4),   # $0.0015B
    _make_b("$", "B",               5),   # $0.00150B
    _make_b("",  " B",              4),   # 0.0015 B
    _make_b("",  " billion",        4),   # 0.0015 billion
    _make_b("$", " billion",        4),   # $0.0015 billion
    _make_b("",  " Billion",        4),   # 0.0015 Billion
    _make_b("",  " billion dollars", 4),  # 0.0015 billion dollars
    _make_b("$", " billion dollars", 4),  # $0.0015 billion dollars
    # --- full English word form (4) ---
    lambda x: _int_to_words(round(x)) + " dollars",
    lambda x: _int_to_words(round(x)),
    lambda x: _int_to_words(round(x)).title() + " Dollars",
    lambda x: _int_to_words(round(x)).title(),
]

# 30 sum-of-two-parts formats  (part1 + part2 = original amount)
_p_int      = _make_plain("",    "",         False, 0)
_p_comma    = _make_plain("",    "",         True,  0)
_p_dec2     = _make_plain("",    "",         True,  2)
_p_d        = _make_plain("$",   "",         False, 0)
_p_d_comma  = _make_plain("$",   "",         True,  0)
_p_d_dec2   = _make_plain("$",   "",         True,  2)
_p_usd      = _make_plain("",    " USD",     True,  0)
_p_K0       = _make_k("",  "K", True,  0)
_p_K1       = _make_k("",  "K", True,  1)
_p_dK0      = _make_k("$", "K", True,  0)
_p_dK1      = _make_k("$", "K", True,  1)
_p_M1       = _make_m("",  "M", 1)
_p_M2       = _make_m("",  "M", 2)
_p_dM1      = _make_m("$", "M", 1)
_p_dM2      = _make_m("$", "M", 2)

SUM_FORMATS: list = [
    _make_sum(_p_d_comma,  _p_d_comma),                     # $1,000,000 + $500,000
    _make_sum(_p_int,      _p_int),                         # 1000000 + 500000
    _make_sum(_p_comma,    _p_comma),                       # 1,000,000 + 500,000
    _make_sum(_p_d_dec2,   _p_d_dec2),                      # $1,000,000.00 + $500,000.00
    _make_sum(_p_K1,       _p_K1),                          # 1,000.0K + 500.0K
    _make_sum(_p_dK0,      _p_dK0),                         # $1,000K + $500K
    _make_sum(_p_dK1,      _p_dK1),                         # $1,000.0K + $500.0K
    _make_sum(_p_M2,       _p_M2),                          # 1.00M + 0.50M
    _make_sum(_p_dM2,      _p_dM2),                         # $1.00M + $0.50M
    _make_sum(_p_M1,       _p_M1),                          # 1.0M + 0.5M
    _make_sum(_p_dM1,      _p_dM1),                         # $1.0M + $0.5M
    _make_sum(_p_d_comma,  _p_d_comma,  " and "),           # $1,000,000 and $500,000
    _make_sum(_p_comma,    _p_comma,    " and "),            # 1,000,000 and 500,000
    _make_sum(_p_d_dec2,   _p_d_dec2,  " and "),            # $1,000,000.00 and $500,000.00
    _make_sum(_p_d_comma,  _p_d_comma,  " plus "),          # $1,000,000 plus $500,000
    _make_sum(_p_comma,    _p_comma,    " plus "),           # 1,000,000 plus 500,000
    _make_sum(_p_M2,       _p_M2,      " and "),             # 1.00M and 0.50M
    _make_sum(_p_dM1,      _p_dM1,     " and "),             # $1.0M and $0.5M
    _make_sum(_p_dK0,      _p_dK0,     " and "),             # $1,000K and $500K
    _make_sum(_p_usd,      _p_usd),                          # 1,000,000 USD + 500,000 USD
    _make_sum(_make_plain("USD ", "", True, 0),
              _make_plain("USD ", "", True, 0)),             # USD 1,000,000 + USD 500,000
    _make_sum(_p_d_comma,  _p_dec2),                        # $1,000,000 + 500,000.00  (mixed)
    _make_sum(_p_dM2,      _p_dK0),                         # $1.00M + $500K  (mixed scale)
    _make_sum(_p_M2,       _p_K0),                          # 1.00M + 500K  (mixed scale)
    _make_sum(_make_m("",  "M", 3), _make_m("",  "M", 3)), # 1.000M + 0.500M
    _make_sum(_make_k("",  " thousand", True, 0),
              _make_k("",  " thousand", True, 0)),           # 1,000 thousand + 500 thousand
    _make_sum(_make_k("$", "K", False, 0),
              _make_k("$", "K", False, 0)),                  # $1000K + $500K (no comma)
    _make_sum(_make_k("",  "K", True, 2),
              _make_k("",  "K", True, 2)),                   # 1,000.00K + 500.00K
    _make_sum(_p_d,        _p_d),                            # $1000000 + $500000 (no comma)
    _make_sum(_p_d_comma,  _p_comma,   " + "),               # $1,000,000 + 500,000
]

assert len(AMOUNT_FORMATS) == 75, len(AMOUNT_FORMATS)
assert len(SUM_FORMATS) == 30, len(SUM_FORMATS)
_N_FORMATS = len(AMOUNT_FORMATS) + len(SUM_FORMATS)   # 105


def amount_text(value: float, salt: str) -> str:
    if value is None:
        return ""
    idx = h("amt", salt) % _N_FORMATS
    if idx < len(AMOUNT_FORMATS):
        return AMOUNT_FORMATS[idx](value)
    part1, part2 = _sum_split(value, salt)
    return SUM_FORMATS[idx - len(AMOUNT_FORMATS)](part1, part2)


# ---------------------------------------------------------------------------
# NAICS code formatting
# ---------------------------------------------------------------------------

def naics_format(code: str, salt: str) -> str:
    if not code:
        return code
    c = apply_ocr(code, "naics-" + salt)  # OCR on raw digits before prefix
    fid = h("naics", salt) % 3
    if fid == 0:
        return c
    if fid == 1:
        return f"naics-{c}"
    return f"{c[:2]}-{c[2:]}" if len(c) >= 4 else c


# ---------------------------------------------------------------------------
# Agency surface-form variants
# ---------------------------------------------------------------------------

AGENCY_VARIANTS = {
    "Department of Defense": [
        "Department of Defense", "DoD", "DOD", "Dept of Defense",
        "Department of Defense (DOD)", "Defense Department",
    ],
    "Department of Energy": [
        "Department of Energy", "DOE", "Dept of Energy", "DoE",
    ],
    "Department of Health and Human Services": [
        "Department of Health and Human Services", "HHS",
        "Dept HHS", "Health and Human Services",
    ],
    "Department of Veterans Affairs": [
        "Department of Veterans Affairs", "VA", "Dept of Veterans Affairs",
    ],
    "Department of Homeland Security": [
        "Department of Homeland Security", "DHS", "Dept Homeland Security",
    ],
    "Department of State": [
        "Department of State", "State Dept", "Dept of State", "DOS",
    ],
    "National Aeronautics and Space Administration": [
        "NASA", "National Aeronautics and Space Administration",
        "Nat'l Aeronautics & Space Administration",
    ],
    "General Services Administration": [
        "General Services Administration", "GSA", "Gen Services Admin",
    ],
    "Department of Justice": [
        "Department of Justice", "DOJ", "DoJ", "Justice Dept",
    ],
    "Department of Transportation": [
        "Department of Transportation", "DOT", "DoT", "Transportation Dept",
    ],
    "Department of the Interior": [
        "Department of the Interior", "DOI", "Interior Dept",
    ],
    "Department of Agriculture": [
        "Department of Agriculture", "USDA", "Agriculture Dept",
    ],
    "Department of Commerce": [
        "Department of Commerce", "DOC", "Commerce Dept",
    ],
    "Department of the Treasury": [
        "Department of the Treasury", "Treasury", "Treasury Dept",
    ],
    "Environmental Protection Agency": [
        "Environmental Protection Agency", "EPA",
    ],
    "Department of Education": [
        "Department of Education", "ED", "Education Dept",
    ],
    "Department of Labor": [
        "Department of Labor", "DOL", "Labor Dept",
    ],
    "Department of Housing and Urban Development": [
        "Department of Housing and Urban Development", "HUD",
    ],
    "Social Security Administration": [
        "Social Security Administration", "SSA",
    ],
}


def agency_variant(canonical: str, salt: str) -> str:
    pool = AGENCY_VARIANTS.get(canonical)
    if not pool:
        return canonical
    return pool[h("ag", salt) % len(pool)]


# ---------------------------------------------------------------------------
# Recipient name fuzzification
# ---------------------------------------------------------------------------

SUFFIX_VARIANTS = [
    ("INC",  ["INC", "INC.", "INCORPORATED", "Inc", "Inc.", "Incorporated"]),
    ("CORP", ["CORP", "CORP.", "CORPORATION", "Corp", "Corp.", "Corporation"]),
    ("LLC",  ["LLC", "LLC.", "L.L.C.", "L.L.C", "Llc"]),
    ("CO",   ["CO", "CO.", "COMPANY", "Co.", "Company"]),
    ("LTD",  ["LTD", "LTD.", "Limited", "Ltd."]),
]


def fuzz_recipient_name(name: str, salt: str) -> str:
    if not name:
        return name
    n = name.strip()
    upper = n.upper()
    for _, variants in SUFFIX_VARIANTS:
        for v in variants:
            sfx = " " + v
            if upper.endswith(sfx.upper()):
                stem = n[: len(n) - len(sfx)]
                pick = variants[h("rname", salt) % len(variants)]
                return stem + " " + pick
    if h("rname-case", salt) % 2 == 0:
        return n
    return n.title()


# ---------------------------------------------------------------------------
# OCR-like character confusion
#
# Before prefix/case/separator transforms, a subset of characters in the
# canonical ID are swapped for visually-similar look-alikes, independently
# per (character index, canonical_id, table).  This makes cross-table joins
# NOT solvable by a single regex or formula: after stripping prefix and
# separators, matched IDs from different tables can still differ at 2-3
# positions.  Entity resolution (loading all rows from both tables and
# fuzzy-matching) is the only reliable approach.
#
# Substitution pairs (bidirectional):
#   0 ↔ O    1 ↔ I    5 ↔ S    2 ↔ Z    6 ↔ G    8 ↔ B
# ---------------------------------------------------------------------------

_OCR_PAIRS: list[tuple[str, str]] = [
    ("0", "O"), ("1", "I"), ("5", "S"), ("2", "Z"), ("6", "G"), ("8", "B"),
]
# Build lookup: each char → its visual substitute
_OCR_SUB: dict[str, str] = {}
for _a, _b in _OCR_PAIRS:
    _OCR_SUB[_a] = _b
    _OCR_SUB[_b] = _a

_OCR_RATE = 6  # ~1/6 chance per eligible character per table


def apply_ocr(s: str, table: str) -> str:
    """Swap visually-similar characters in s, deterministically per (position, table)."""
    out = []
    for i, c in enumerate(s):
        sub = _OCR_SUB.get(c)
        if sub is not None and h("ocr", s, table, i) % _OCR_RATE == 0:
            out.append(sub)
        else:
            out.append(c)
    return "".join(out)


# ---------------------------------------------------------------------------
# Award ID and UEI formats
#
# Pipeline per ID:
#   1. apply_ocr(canonical_id, table)   — visual look-alike substitutions
#   2. case_transform                   — upper / lower / original
#   3. _insert_sep at letter↔digit      — "", "-", " ", "_", "."
#   4. prepend prefix                   — 207 options
#
# Steps 1-4 are all independently chosen per (id, table), so the same
# canonical id surfaces completely differently in each table.  Joining
# requires entity resolution across all rows, not a single regex.
# ---------------------------------------------------------------------------

_ID_PREFIXES: list[str] = [
    # ----- empty (just case + separator variation) -----
    "",
    # ----- abbreviation + colon -----
    "PIID:", "piid:", "Piid:",
    "PIIN:", "piin:",
    "ACQ:", "acq:", "Acq:",
    "AWARD:", "award:", "Award:",
    "CONTRACT:", "contract:", "Contract:",
    "REF:", "ref:", "Ref:",
    "ID:", "id:", "Id:",
    "NO:", "no:", "No:",
    "DOC:", "doc:", "Doc:",
    "FILE:", "file:", "File:",
    "REC:", "rec:", "Rec:",
    "TXN:", "txn:",
    "OBL:", "obl:", "Obl:",
    "OBLIG:", "oblig:",
    "PO:", "po:", "Po:",
    "TO:", "to:", "To:",
    "DO:", "do:", "Do:",
    "MOD:", "mod:", "Mod:",
    "SOL:", "sol:", "Sol:",
    "PROC:", "proc:",
    "PURCH:", "purch:",
    "INST:", "inst:",
    "AGR:", "agr:",
    "GRT:", "grt:",
    "ACRN:", "acrn:",
    "CLIN:", "clin:",
    "SLIN:", "slin:",
    "PR:", "pr:",
    "WBS:", "wbs:",
    "CAGE:", "cage:",
    "UEI:", "uei:",
    # ----- word + colon -----
    "Award:", "Contract:", "Order:", "Document:", "Reference:",
    "Identifier:", "Record:", "Transaction:", "Obligation:",
    "Solicitation:", "Procurement:", "Purchase:", "Agreement:",
    "Instrument:", "Requisition:", "Authorization:", "Modification:",
    "Delivery:", "Task:", "Grant:", "Cooperative:", "Instrument:",
    # ----- abbreviation + slash -----
    "PIID/", "piid/",
    "AWARD/", "award/",
    "CONTRACT/", "contract/",
    "REF/", "ref/",
    "ID/", "id/",
    "NO/", "no/",
    "DOC/", "doc/",
    "ACQ/", "acq/",
    "PO/", "po/",
    "TO/", "to/",
    "DO/", "do/",
    "MOD/", "mod/",
    "SOL/", "sol/",
    "awards/", "contracts/", "orders/", "documents/", "records/",
    # ----- word + slash -----
    "Award/", "Contract/", "Order/", "Reference/", "Document/",
    # ----- abbreviation + dot -----
    "PIID.", "piid.", "REF.", "ref.", "ID.", "id.",
    "NO.", "no.", "DOC.", "doc.", "PO.", "po.", "TO.", "to.", "ACQ.", "acq.",
    "Award.", "Contract.", "Ref.", "Doc.",
    # ----- word + "No." or "#" -----
    "Award No. ", "award no. ", "Contract No. ", "contract no. ",
    "Award # ", "award # ", "Contract # ", "contract # ",
    "Ref No. ", "ref no. ", "Doc No. ", "doc no. ",
    "Order No. ", "order no. ", "PO No. ", "po no. ",
    "TO No. ", "to no. ", "DO No. ", "do no. ",
    "Mod No. ", "mod no. ", "Sol No. ", "sol no. ",
    "PIID No. ", "piid no. ",
    # ----- field-name + equals -----
    "award_id=", "contract_id=", "piid=", "PIID=",
    "ref=", "REF=", "id=", "ID=", "award=", "AWARD=",
    "order_id=", "doc_id=",
    # ----- field-name + colon (snake_case style) -----
    "award_id:", "contract_id:", "award_no:", "contract_no:",
    "ref_no:", "doc_no:", "order_no:", "po_no:", "to_no:",
    # ----- hash / symbol prefix -----
    "#", "##", "# ",
]

# Separators inserted at every letter↔digit boundary in the ID.
# Empty string means no separator added (original runs stay together).
_ID_SEPS: list[str] = ["", "-", " ", "_", "."]


def _insert_sep(s: str, sep: str) -> str:
    """Insert sep at every letter↔digit or digit↔letter boundary."""
    if not sep:
        return s
    out: list[str] = []
    for i, c in enumerate(s):
        if i > 0 and sep:
            p = s[i - 1]
            if (p.isalpha() and c.isdigit()) or (p.isdigit() and c.isalpha()):
                out.append(sep)
        out.append(c)
    return "".join(out)


def award_id_format(aid: str, table: str) -> str:
    if not aid:
        return aid
    body = apply_ocr(aid, table)
    case = h("aid-case", aid, table) % 3
    body = body.upper() if case == 0 else body.lower() if case == 1 else body
    sep  = _ID_SEPS[h("aid-sep", aid, table) % len(_ID_SEPS)]
    body = _insert_sep(body, sep)
    return _ID_PREFIXES[h("aid-pre", aid, table) % len(_ID_PREFIXES)] + body


def uei_format(uei: str, table: str) -> str:
    if not uei:
        return uei
    body = apply_ocr(uei, table)
    case = h("uei-case", uei, table) % 3
    body = body.upper() if case == 0 else body.lower() if case == 1 else body
    sep  = _ID_SEPS[h("uei-sep", uei, table) % len(_ID_SEPS)]
    body = _insert_sep(body, sep)
    return _ID_PREFIXES[h("uei-pre", uei, table) % len(_ID_PREFIXES)] + body


# ---------------------------------------------------------------------------
# State surface-form variants
# ---------------------------------------------------------------------------

STATE_VARIANTS = {
    "CA": ["CA", "California", "Calif.", "Calif"],
    "NY": ["NY", "New York", "N.Y."],
    "TX": ["TX", "Texas", "Tex."],
    "VA": ["VA", "Virginia", "Va."],
    "DC": ["DC", "District of Columbia", "D.C."],
    "MA": ["MA", "Massachusetts", "Mass."],
    "MD": ["MD", "Maryland", "Md."],
    "FL": ["FL", "Florida", "Fla."],
    "WA": ["WA", "Washington", "Wash."],
    "PA": ["PA", "Pennsylvania", "Penn.", "Penna."],
    "OH": ["OH", "Ohio"],
    "IL": ["IL", "Illinois", "Ill."],
    "MI": ["MI", "Michigan", "Mich."],
    "AZ": ["AZ", "Arizona", "Ariz."],
    "GA": ["GA", "Georgia", "Ga."],
    "NJ": ["NJ", "New Jersey", "N.J."],
    "CO": ["CO", "Colorado", "Colo."],
    "MO": ["MO", "Missouri", "Mo."],
}


def state_variant(s: str, salt: str) -> str:
    if not s:
        return s
    pool = STATE_VARIANTS.get(s)
    if not pool:
        return s
    return pool[h("st", salt) % len(pool)]


# ---------------------------------------------------------------------------
# Duplicate / language-drop selectors
# ---------------------------------------------------------------------------

DROP_ENGLISH_RATE = 10
DUPLICATE_RATE    = 30


def should_drop_english(award_id: str) -> bool:
    return h("drop-eng", award_id) % DROP_ENGLISH_RATE == 0


def should_duplicate(award_id: str) -> bool:
    return h("dup", award_id) % DUPLICATE_RATE == 0


def conflicting_amount(orig: float, salt: str) -> float:
    factor = [0.5, 1.5, 2.0, 0.75][h("dup-amt", salt) % 4]
    return round(orig * factor, 2)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def init_manifest(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    DROP TABLE IF EXISTS canonical_award_id;
    DROP TABLE IF EXISTS canonical_recipient;
    DROP TABLE IF EXISTS canonical_agency;
    DROP TABLE IF EXISTS canonical_naics;
    DROP TABLE IF EXISTS canonical_amount;
    DROP TABLE IF EXISTS planted_eng_dropped;
    DROP TABLE IF EXISTS planted_duplicate;

    CREATE TABLE canonical_award_id (
      canonical_award_id   TEXT PRIMARY KEY,
      contracts_award_id   TEXT,
      amounts_award_id     TEXT,
      descriptions_award_id TEXT
    );
    CREATE TABLE canonical_recipient (
      canonical_uei   TEXT PRIMARY KEY,
      canonical_name  TEXT,
      contracts_uei   TEXT,
      recipients_uei  TEXT
    );
    CREATE TABLE canonical_agency (
      award_id TEXT, canonical_agency TEXT, corrupted_surface TEXT
    );
    CREATE TABLE canonical_naics (
      canonical_code TEXT PRIMARY KEY, corrupted_code TEXT
    );
    CREATE TABLE canonical_amount (
      canonical_award_id TEXT PRIMARY KEY,
      canonical_amount   REAL,
      amount_text        TEXT
    );
    """)
    conn.commit()


def pgesc(s):
    if s is None:
        return "NULL"
    s = str(s).replace("'", "''")
    return "'" + s + "'"


def build_contracts(clean: sqlite3.Connection, manifest: sqlite3.Connection) -> None:
    if CONTRACTS_SQL.exists():
        CONTRACTS_SQL.unlink()
    cur = clean.cursor()
    mcur = manifest.cursor()
    rows = cur.execute("""
        SELECT award_id, generated_internal_id, recipient_name, recipient_uei,
               recipient_state, awarding_agency, awarding_sub_agency,
               funding_agency, amount, total_outlays, start_date, end_date,
               naics_code, naics_description, psc_code, psc_description,
               award_type
        FROM contracts
    """).fetchall()

    lines = [
        "CREATE TABLE contracts (",
        "  award_id TEXT,",
        "  generated_internal_id TEXT,",
        "  recipient_uei TEXT,",
        "  awarding_agency TEXT,",
        "  awarding_sub_agency TEXT,",
        "  funding_agency TEXT,",
        "  start_date TEXT,",
        "  end_date TEXT,",
        "  naics_code TEXT,",
        "  psc_code TEXT,",
        "  award_type TEXT",
        ");",
        "CREATE TABLE contract_amounts (",
        "  award_id TEXT,",
        "  amount_text TEXT",
        ");",
    ]

    n_dup = 0
    for r in rows:
        (award_id, gen_id, rname, ruei, rstate, agency, sub_agency, fund_agency,
         amt, outlays, sd, ed, naics, naics_desc, psc, psc_desc, atype) = r

        # Same canonical ID, independently chosen surface form per table.
        a_corr_c = award_id_format(award_id, "contracts")    if award_id else None
        a_corr_a = award_id_format(award_id, "amounts")      if award_id else None
        a_corr_d = award_id_format(award_id, "descriptions") if award_id else None

        uei_corr_c = uei_format(ruei, "contracts")  if ruei else None
        uei_corr_r = uei_format(ruei, "recipients") if ruei else None

        agency_corr   = agency_variant(agency or "", award_id or "")
        sub_corr      = agency_variant(sub_agency or "", award_id or "")
        fund_corr     = agency_variant(fund_agency or "", award_id or "")
        naics_corr    = naics_format(naics or "", award_id or "")

        lines.append(
            "INSERT INTO contracts VALUES (" + ", ".join([
                pgesc(a_corr_c), pgesc(gen_id), pgesc(uei_corr_c),
                pgesc(agency_corr), pgesc(sub_corr), pgesc(fund_corr),
                pgesc(sd), pgesc(ed),
                pgesc(naics_corr), pgesc(psc), pgesc(atype),
            ]) + ");"
        )

        if amt is not None:
            atext = amount_text(amt, award_id or "")
            lines.append(
                f"INSERT INTO contract_amounts VALUES ({pgesc(a_corr_a)}, {pgesc(atext)});"
            )
            mcur.execute(
                "INSERT OR REPLACE INTO canonical_amount VALUES (?,?,?)",
                (award_id, amt, atext),
            )
            if should_duplicate(award_id or ""):
                dup_amt   = conflicting_amount(amt, award_id or "")
                dup_atext = amount_text(dup_amt, (award_id or "") + "-dup")
                # Superseded-amount rows stay in contract_amounts but the award_id
                # gets "_OLD" appended (in the same surface-form style), so a
                # proper join from contracts → contract_amounts via normalized
                # award_id naturally skips these rows (Q1-Q9 are unambiguous).
                # Q10 specifically queries for entries whose award_id ends with _OLD.
                old_aid = a_corr_a + "_OLD" if a_corr_a else None
                lines.append(
                    f"INSERT INTO contract_amounts VALUES ({pgesc(old_aid)}, {pgesc(dup_atext)});"
                )
                n_dup += 1

        mcur.execute(
            "INSERT OR REPLACE INTO canonical_award_id VALUES (?,?,?,?)",
            (award_id, a_corr_c, a_corr_a, a_corr_d),
        )
        mcur.execute(
            "INSERT OR REPLACE INTO canonical_recipient VALUES (?,?,?,?)",
            (ruei, rname, uei_corr_c, uei_corr_r),
        )
        if agency:
            mcur.execute(
                "INSERT INTO canonical_agency VALUES (?,?,?)",
                (award_id, agency, agency_corr),
            )

    CONTRACTS_SQL.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest.commit()
    print(f"contracts.sql: built ({len(rows)} rows, {n_dup} duplicate-amount injections)")


def build_recipients(clean: sqlite3.Connection, manifest: sqlite3.Connection) -> None:
    if RECIPIENTS_DB.exists():
        RECIPIENTS_DB.unlink()
    out = sqlite3.connect(RECIPIENTS_DB)
    out.execute("""
        CREATE TABLE recipients (
          uei TEXT,
          name TEXT,
          state TEXT,
          n_contracts INTEGER,
          total_amount_text TEXT
        );
    """)
    cur = clean.cursor()
    rows = cur.execute(
        "SELECT uei, name, state, n_contracts, total_amount FROM recipients"
    ).fetchall()
    for uei, name, state, ncon, total in rows:
        uei_corr_r = uei_format(uei, "recipients") if uei else None
        name_corr  = fuzz_recipient_name(name or "", uei or "")
        state_corr = state_variant(state or "", uei or "")
        total_text = amount_text(total or 0, uei or "")
        out.execute(
            "INSERT INTO recipients VALUES (?,?,?,?,?)",
            (uei_corr_r, name_corr, state_corr, ncon, total_text),
        )
    out.commit()
    out.close()
    manifest.commit()
    print(f"recipients.db: built ({len(rows)} rows)")


def build_agencies(clean: sqlite3.Connection, manifest: sqlite3.Connection) -> None:
    if AGENCIES_DB.exists():
        AGENCIES_DB.unlink()
    con = duckdb.connect(str(AGENCIES_DB))
    con.execute("""
        CREATE TABLE agencies (
          name TEXT,
          n_contracts INTEGER,
          total_amount_text TEXT
        );
        CREATE TABLE agency_aliases (
          surface_form TEXT PRIMARY KEY,
          canonical_name TEXT
        );
        CREATE TABLE naics_sectors (
          code TEXT,
          description TEXT,
          sector TEXT
        );
    """)
    cur = clean.cursor()
    mcur = manifest.cursor()
    rows = cur.execute(
        "SELECT name, n_contracts, total_amount FROM agencies"
    ).fetchall()
    agency_rows = []
    alias_pairs: set[tuple[str, str]] = set()
    for name, ncon, total in rows:
        agency_rows.append((name, ncon, amount_text(total or 0, name)))
        for v in AGENCY_VARIANTS.get(name, [name]):
            alias_pairs.add((v, name))
    con.executemany("INSERT INTO agencies VALUES (?,?,?)", agency_rows)
    con.executemany("INSERT INTO agency_aliases VALUES (?,?)", sorted(alias_pairs))

    naics_rows = []
    for r in cur.execute("SELECT code, description, sector FROM naics"):
        naics_rows.append((r[0], r[1], r[2]))
        mcur.execute(
            "INSERT OR REPLACE INTO canonical_naics VALUES (?,?)",
            (r[0], naics_format(r[0], "naics-row-" + r[0])),
        )
    con.executemany("INSERT INTO naics_sectors VALUES (?,?,?)", naics_rows)
    con.close()
    manifest.commit()
    print(
        f"agencies.duckdb: built ({len(agency_rows)} agencies, "
        f"{len(alias_pairs)} aliases, {len(naics_rows)} naics rows)"
    )


def build_descriptions(clean: sqlite3.Connection, manifest: sqlite3.Connection) -> None:
    if DESC_DUMP.exists():
        shutil.rmtree(DESC_DUMP)
    DESC_DUMP.mkdir(parents=True, exist_ok=True)

    cur = clean.cursor()
    docs = []
    n_eng_dropped = 0
    for award_id, desc in cur.execute(
        "SELECT award_id, description FROM contracts"
    ):
        a_corr_d = award_id_format(award_id or "", "descriptions")
        descs = []
        if desc:
            if should_drop_english(award_id or ""):
                descs.append({"language": "es",
                              "value": "[contrato federal] " + desc[:200]})
                n_eng_dropped += 1
            else:
                descs.append({"language": "en", "value": desc})
        docs.append({"award_id": a_corr_d, "descriptions": descs})

    from bson import encode as bson_encode
    coll_dir = DESC_DUMP / "usaspending_descriptions"
    coll_dir.mkdir(parents=True, exist_ok=True)
    bson_path = coll_dir / "contract_documents.bson"
    with bson_path.open("wb") as f:
        for d in docs:
            f.write(bson_encode(d))
    meta = {
        "options": {},
        "indexes": [
            {"v": 2, "key": {"_id": 1}, "name": "_id_"},
            {"v": 2, "key": {"award_id": 1}, "name": "award_id_1"},
        ],
        "uuid": "",
        "collectionName": "contract_documents",
        "type": "collection",
    }
    (coll_dir / "contract_documents.metadata.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    manifest.commit()
    print(
        f"descriptions/: built ({len(docs)} docs, {n_eng_dropped} english-dropped)"
    )


def main():
    if not CLEAN_DB.exists():
        raise SystemExit(f"missing clean snapshot: {CLEAN_DB}")
    QD.mkdir(parents=True, exist_ok=True)
    clean = sqlite3.connect(CLEAN_DB)
    manifest = sqlite3.connect(MANIFEST_DB)
    init_manifest(manifest)
    build_contracts(clean, manifest)
    build_recipients(clean, manifest)
    build_agencies(clean, manifest)
    build_descriptions(clean, manifest)
    clean.close()
    manifest.close()
    print("OK")


if __name__ == "__main__":
    main()
