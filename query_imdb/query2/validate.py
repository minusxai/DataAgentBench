import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)
    llm_lower = llm_output.lower()

    # writer_pseudo_name: '"A.J."' — check case-insensitively preserving dots
    if "a.j." not in llm_lower and "aj" not in llm_norm:
        return False, "Pseudonym 'A.J.' not found in LLM output."

    # movie_title: '#1 Cheerleader Camp'
    if normalize("#1 Cheerleader Camp") not in llm_norm:
        return False, "Title '#1 Cheerleader Camp' not found in LLM output."

    return True, "Ground truth found in LLM output."
