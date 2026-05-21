import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # voiced_char: 'Lola'
    if "lola" not in llm_norm:
        return False, "Character name 'Lola' not found in LLM output."

    # voicing_actress: 'Andrews, Julie'
    if normalize("Andrews, Julie") not in llm_norm:
        return False, "Actress name 'Andrews, Julie' not found in LLM output."

    # voiced_animation: 'Hoodwinked!'
    if "hoodwinked" not in llm_norm:
        return False, "Title 'Hoodwinked!' not found in LLM output."

    return True, "Ground truth found in LLM output."
