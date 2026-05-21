import re


def validate(llm_output: str):
    expected = "PFN1"

    if re.search(r'\b' + re.escape(expected) + r'\b', llm_output, re.IGNORECASE):
        return True, f"Found expected protein: {expected}"

    protein_pattern = r'\b[A-Z][A-Z0-9]{1,9}\b'
    found = re.findall(protein_pattern, llm_output)
    if found:
        return False, f"Found protein-like tokens {found[:5]}, but expected '{expected}'"
    return False, "No protein identifier found in LLM output"
