import re

GROUND_TRUTH = [
    "Emergency Relief Fund",
    "Municipal Fund",
    "Private Donation",
    "Public-Private Partnership",
]


def _norm(s):
    """Lowercase, underscores→spaces, strip non-alphanumeric, collapse whitespace."""
    s = s.lower().replace('_', ' ')
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def validate(llm_output: str):
    text_norm = _norm(llm_output)
    missing = [s for s in GROUND_TRUTH if _norm(s) not in text_norm]
    if not missing:
        return True, "All ground truth funding sources found in LLM output."
    reason = f"Missing funding source(s) in LLM output: {missing}"
    return False, reason
