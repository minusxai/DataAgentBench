import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # member_in_charnamed_movie / a1: "Z'Dar, Robert" — normalize to 'zdar robert'
    if normalize("Z'Dar, Robert") not in llm_norm:
        return False, "Name 'Z\\'Dar, Robert' not found in LLM output."

    return True, "Ground truth found in LLM output."
