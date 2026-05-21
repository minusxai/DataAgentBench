import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # cast_member: 'Abell, Alistair'
    if normalize("Abell, Alistair") not in llm_norm:
        return False, "Cast member 'Abell, Alistair' not found in LLM output."

    # complete_dynamic_hero_movie: '...And Then I...' — normalize to 'and then i'
    if normalize("And Then I") not in llm_norm:
        return False, "Title '...And Then I...' not found in LLM output."

    return True, "Ground truth found in LLM output."
