import re

GT_PROJECT = "City Hall Roof Replacement"
GT_AMOUNT = 1340000


def extract_numeric_values(text):
    values = []
    for num in re.findall(r'\b[\d,]+(?:\.\d+)?\b', text):
        try:
            values.append(float(num.replace(",", "")))
        except ValueError:
            pass
    return values


def _norm(s):
    """Lowercase, underscores→spaces, strip non-alphanumeric, collapse whitespace."""
    s = s.lower().replace('_', ' ')
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def validate(llm_output: str):
    text_norm = _norm(llm_output)

    name_found = _norm(GT_PROJECT) in text_norm
    amount_found = any(abs(v - GT_AMOUNT) == 0 for v in extract_numeric_values(llm_output))

    if name_found and amount_found:
        return True, "Ground truth project name and amount found in LLM output."
    missing = []
    if not name_found:
        missing.append(f"project name '{GT_PROJECT}'")
    if not amount_found:
        missing.append(f"amount '{GT_AMOUNT}'")
    return False, f"Missing in LLM output: {', '.join(missing)}"
