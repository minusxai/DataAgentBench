import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # voicing_actress: 'Aaron, Caroline'
    if normalize("Aaron, Caroline") not in llm_norm:
        return False, "Actress name 'Aaron, Caroline' not found in LLM output."

    # jap_engl_voiced_movie: '$9.99' — check for '9.99' as a float
    matches = re.findall(r"\d+\.\d+", llm_output)
    if not any(abs(float(m) - 9.99) < 0.01 for m in matches):
        return False, "Movie title '$9.99' not found in LLM output."

    return True, "Ground truth found in LLM output."
