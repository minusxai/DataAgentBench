import re


def validate(llm_output: str):
    expected = "NWCC"

    if expected.upper() in llm_output.upper():
        return True, f"Found expected GACC abbreviation: {expected}"

    gacc_abbrevs = ["AICC", "NRCC", "NWCC", "RMCC", "SACC", "SWCC", "ONCC", "OSCC", "EACC", "GBCC"]
    found = [a for a in gacc_abbrevs if re.search(r'\b' + a + r'\b', llm_output, re.IGNORECASE)]
    if found:
        return False, f"Found GACC abbreviations {found}, but expected '{expected}'"
    return False, "No GACC abbreviation found in LLM output"
