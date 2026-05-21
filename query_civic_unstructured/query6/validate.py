import re

GT_YEAR = 2023
GT_AMOUNT = 64171000


def extract_numeric_values(text):
    values = []
    for num in re.findall(r'\b[\d,]+(?:\.\d+)?\b', text):
        try:
            values.append(float(num.replace(",", "")))
        except ValueError:
            pass
    return values


def validate(llm_output: str):
    nums = extract_numeric_values(llm_output)
    year_found = any(abs(v - GT_YEAR) == 0 for v in nums)
    amount_found = any(abs(v - GT_AMOUNT) == 0 for v in nums)

    if year_found and amount_found:
        return True, "Ground truth year and amount found in LLM output."
    missing = []
    if not year_found:
        missing.append(f"year '{GT_YEAR}'")
    if not amount_found:
        missing.append(f"amount '{GT_AMOUNT}'")
    return False, f"Missing in LLM output: {', '.join(missing)}"
