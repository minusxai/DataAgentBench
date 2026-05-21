import re

GROUND_TRUTH = [
    "Clover Heights Storm Drain",
    "Encinal Canyon Road Drainage Improvements",
    "Encinal Canyon Road Drainage Improvements (FEMA/CalOES Project)",
    "Latigo Canyon Road Culvert Repairs",
    "Latigo Canyon Road Culvert Repairs (FEMA/CalOES Project)",
    "Outdoor Warning Sirens (FEMA)",
    "Storm Drain Master Plan",
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
