import re

GROUND_TRUTH = 0.31
TOL = 1e-2


def extract_numeric_values(text):
    values = []
    for num in re.findall(r'\b\d+\.\d+|\b\d+\b', text):
        try:
            values.append(float(num))
        except ValueError:
            pass
    return values


def validate(llm_output: str):
    if any(abs(v - GROUND_TRUTH) < TOL for v in extract_numeric_values(llm_output)):
        return True, "Ground truth numeric value found in LLM output."
    return False, f"Ground truth '{GROUND_TRUTH}' (tol={TOL}) not found in LLM output: {llm_output}"
