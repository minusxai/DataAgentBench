import re


def validate(llm_output: str):
    expected = 319655

    matches = re.findall(r"[-+]?\d+", llm_output.replace(",", ""))
    for m in matches:
        try:
            if int(m) == expected:
                return True, f"Found expected value: {expected}"
        except ValueError:
            continue

    if matches:
        return False, f"Found numbers {matches[:5]}, but expected {expected}"
    return False, "No numeric value found in LLM output"
