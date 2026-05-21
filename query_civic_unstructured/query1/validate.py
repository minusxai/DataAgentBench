import re

GROUND_TRUTH = [
    "2022 Annual Street Maintenance",
    "Annual Street Maintenance",
    "Civic Center Water Treatment Facility Phase 2",
    "Marie Canyon Green Streets",
    "Michael Landon Center Roof Replacement Project",
    "PCH Median Improvements Project",
    "PCH Signal Synchronization System Improvements Project",
    "PCH at Trancas Canyon Road Right Turn Lane",
    "Permanent Skate Park",
    "Westward Beach Road Improvements Project",
    "Westward Beach Road Repair Project",
]


def _norm(s):
    """Lowercase, underscores→spaces, strip non-alphanumeric, collapse whitespace."""
    s = s.lower().replace('_', ' ')
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def validate(llm_output: str):
    text_norm = _norm(llm_output)
    missing = [p for p in GROUND_TRUTH if _norm(p) not in text_norm]
    if not missing:
        return True, "All ground truth project names found in LLM output."
    reason = f"Missing project(s) in LLM output: {missing}"
    return False, reason
