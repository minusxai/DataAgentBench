# query_usaspending provenance

## Source data

| Source | URL | Fetched | Scope |
|---|---|---|---|
| USAspending.gov contract awards (REST API v2) | https://api.usaspending.gov/api/v2/search/spending_by_award/ | 2026-05-01 | Definitive contracts (award_type_codes A/B/C/D) with start dates 2024-07-01 .. 2024-09-30, sorted by Start Date desc. 10,000 awards paginated 100 / page (9,921 with non-null amount; 9,563 with non-null award_id after filtering); 3,317 unique recipients derived from those awards; 47 unique awarding agencies; NAICS sector hierarchy from `references/naics/`. |

The fetch sorts by start date, NOT by amount, so award amounts span 5+ orders of magnitude (p5 ≈ $890; p50 ≈ $59K; p95 ≈ $5M; max ≈ $1.6B). This makes threshold filters in queries (>$1M, >$5M, >$9M) meaningfully selective.

## Database engine assignment

| DB | Engine | Rationale |
|---|---|---|
| `contracts_db` | PostgreSQL | Transactional fact table (~9.9k contract rows) plus a sibling `contract_amounts` table |
| `recipients_db` | SQLite | Small entity registry (~3.3k recipients) |
| `agencies_db` | DuckDB | Analytical reference: agency hierarchy + agency-alias lookup + NAICS sector hierarchy |
| `descriptions_db` | MongoDB | Free-text contract descriptions, organized as one document per award with nested descriptions[] |

## Corruption layers (categories only)

All transforms are hash-deterministic (no LLM involvement). Each row's format
choice is keyed by a stable SHA-1 hash of a salted identifier.

| Layer | Surface |
|---|---|
| Award ID corruption **across tables** | Each table independently applies: OCR-like char substitutions (0↔O, 1↔I, 5↔S, 2↔Z, 6↔G, 8↔B at ~1/6 rate per eligible char), one of 207 prefix variants, upper/lower/original case, and a separator at letter↔digit boundaries. After stripping prefix and separator, matched IDs from different tables can still differ at 2-3 OCR-substituted positions. Joining requires entity resolution across all rows. |
| Recipient UEI corruption **across tables** | Same OCR + prefix + case + separator pipeline applied independently per table. |
| Awarding-agency surface-form variants | `contracts_db.contracts.awarding_agency` (with `agencies_db.agency_aliases` lookup for canonicalization) |
| NAICS code reformatting (6-digit / `naics-XXXXXX` / `XX-XXXX`) | `contracts_db.contracts.naics_code` |
| Recipient name fuzzification (corporate-suffix variants, case, whitespace) | `recipients_db.recipients.name` |
| State surface-form variants | `recipients_db.recipients.state` |
| Amount-as-text (105 formats, one chosen per row) | `contracts_db.contract_amounts.amount_text`, `recipients_db.recipients.total_amount_text` — see format table below |
| Superseded-amount rows in `contract_amounts` | ~3.5% of awards get a second row whose `award_id` has `_OLD` appended and whose `amount_text` encodes a different (scaled) amount. A join from `contracts` via normalized award_id naturally skips `_OLD` rows; Q10 specifically queries for them. |
| English-description dropping | `descriptions_db.contract_documents` (~10% of contracts, deterministic) |

## Amount format table (105 formats)

Each row's `amount_text` is chosen deterministically from 105 formats:

| Family | Count | Examples |
|---|---|---|
| Unscaled plain / dollar / USD | 20 | `1500000`, `$1,500,000`, `1,500,000.00 USD`, `US$1,500,000` |
| K-scaled (÷1,000) | 16 | `1500K`, `$1,500k`, `1,500.0K`, `1,500 thousand dollars` |
| M-scaled (÷1,000,000) | 25 | `1.5M`, `$1.50M`, `1.5MM`, `1.5 million dollars`, `1.50 mil` |
| B-scaled (÷1,000,000,000) | 10 | `0.0015B`, `$0.0015 billion`, `0.0015 Billion Dollars` |
| Full English word form | 4 | `one million five hundred thousand dollars`, `One Million Five Hundred Thousand` |
| Sum of two parts | 30 | `$1,050,000 + $450,000`, `1.0M + 0.5M`, `$1,050K and $450K`, `1,050,000 plus 450,000` |

All 105 formats encode the **exact** dollar amount — the agent can always
recover the precise value. There is no band-level ambiguity.

For the sum-of-parts formats, the two addends are chosen deterministically
(hash-based ratio from `{0.25, 0.30, 1/3, 0.40, 0.50, 0.60, 2/3, 0.70, 0.75}`)
and always sum exactly to the original amount.

## What the corruption preserves vs. discards

Every `amount_text` value encodes the exact dollar amount unambiguously. An
agent that correctly parses the format can recover the precise figure needed
to evaluate any threshold filter (e.g. `> $1,000,000`, `>= $10,000,000`).

The challenge is format diversity: the agent must handle all 105 representations
rather than assuming a single canonical form.

The ~3.5% of contracts with two `contract_amounts` rows have genuinely
conflicting amounts (one is the real amount, the other is a scaled version at
0.5×, 0.75×, 1.5×, or 2.0×). Q10 is explicitly designed to identify these
conflicting-entry contracts.

## Shipped artifact hashes (SHA-256)

Hashes below are stale — regenerate the dataset with `corrupt.py` and update
with `shasum -a 256 query_usaspending/query_dataset/{agencies.duckdb,contracts.sql,recipients.db,descriptions/usaspending_descriptions/contract_documents.bson}`.

## Reproducibility notes

- The canonical pre-corruption snapshot (`clean/clean.sqlite`) and the corruption manifest (`clean/manifest.sqlite`) — which together constitute the answer key — are kept local-only (gitignored).
- Construction code lives in `manual_querycode/` and IS shipped in this repo.
- The entire pipeline is deterministic — re-running from the same `clean.sqlite` produces bit-identical output.

## Construction-code reference

Full source: `manual_querycode/fetch_clean.py`, `manual_querycode/corrupt.py`, `manual_querycode/compute_ground_truth.py`.

Pipeline order to regenerate from scratch:
1. `python manual_querycode/fetch_clean.py` — paginates the USAspending search-by-award API into `clean/clean.sqlite`
2. `python manual_querycode/corrupt.py` — emits the 4 agent-visible DBs into `query_dataset/` (fully deterministic; no external calls)
3. `python manual_querycode/compute_ground_truth.py` — emits `queryN/ground_truth.csv` for all 10 queries from the canonical clean data

### Ground-truth-computation SQL highlights

Q4 (canonical agency with highest >$1M-share among agencies with ≥10 contracts):

```sql
SELECT awarding_agency,
       COUNT(*) AS n,
       SUM(CASE WHEN amount > 1000000 THEN 1 ELSE 0 END) AS n_big
FROM contracts
WHERE awarding_agency IS NOT NULL
GROUP BY awarding_agency
HAVING n >= 10
ORDER BY (1.0 * n_big / n) DESC, awarding_agency ASC
LIMIT 1
```

Q6 (distinct canonical recipients with multiple UEIs after corporate-suffix
normalization):

```python
import re
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
result = sum(1 for ueis in canon.values() if len(ueis) > 1)
```

Other queries' GT SQL is in `manual_querycode/compute_ground_truth.py` (one
function per query, each returning a string that becomes the queryN/ground_truth.csv).

