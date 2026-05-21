import re


def validate(llm_output: str):
    expected = 0.000748
    tolerance = 5e-7

    matches = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", llm_output)
    for m in matches:
        try:
            if abs(float(m) - expected) <= tolerance:
                return True, f"Found expected value: {expected}"
        except ValueError:
            continue

    if matches:
        return False, f"Found numbers {matches[:5]}, but expected {expected} (±{tolerance})"
    return False, "No numeric value found in LLM output"
