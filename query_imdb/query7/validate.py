import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # cool_actor_pseudonym: '!!!, Toy' — normalize to 'toy'
    if not re.search(r"\btoy\b", llm_norm):
        return False, "Pseudonym '!!!, Toy' not found in LLM output."

    # series_named_after_char: '& Teller' — normalize to 'teller'
    if not re.search(r"\bteller\b", llm_norm):
        return False, "Title '& Teller' not found in LLM output."

    return True, "Ground truth found in LLM output."
