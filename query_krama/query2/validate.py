import re


def validate(llm_output: str):
    expected = "Idaho"

    if re.search(r'\b' + re.escape(expected) + r'\b', llm_output, re.IGNORECASE):
        return True, f"Found expected state: {expected}"

    us_states = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
        "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
        "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
        "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
        "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
        "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
        "Wisconsin", "Wyoming"
    ]
    found = [s for s in us_states if re.search(r'\b' + re.escape(s) + r'\b', llm_output, re.IGNORECASE)]
    if found:
        return False, f"Found states {found}, but expected '{expected}'"
    return False, "No US state name found in LLM output"
