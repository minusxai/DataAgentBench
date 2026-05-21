import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # producing_company: '"O" Films' — normalize to 'o films'
    if normalize('"O" Films') not in llm_norm:
        return False, "Company '\"O\" Films' not found in LLM output."

    # rating: '1.0' — numeric check
    matches = re.findall(r"\d+\.\d+", llm_output)
    if not any(abs(float(m) - 1.0) < 0.01 for m in matches):
        return False, "Rating '1.0' not found in LLM output."

    # movie: '#54 Meets #47' — normalize to '54 meets 47'
    if normalize("#54 Meets #47") not in llm_norm:
        return False, "Title '#54 Meets #47' not found in LLM output."

    return True, "Ground truth found in LLM output."
