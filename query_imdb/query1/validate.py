import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # movie_kind: 'movie'
    if "movie" not in llm_norm:
        return False, "Kind 'movie' not found in LLM output."

    # complete_us_internet_movie: 'Dirt Merchant'
    if normalize("Dirt Merchant") not in llm_norm:
        return False, "Title 'Dirt Merchant' not found in LLM output."

    return True, "Ground truth found in LLM output."
