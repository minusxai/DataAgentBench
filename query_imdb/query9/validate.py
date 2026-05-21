import re


def normalize(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # movie_budget (genre): 'Horror'
    if "horror" not in llm_norm:
        return False, "Genre 'Horror' not found in LLM output."

    # movie_votes: '1040' — integer check
    matches = re.findall(r"\b\d+\b", llm_output)
    if not any(int(m) == 1040 for m in matches):
        return False, "Vote count '1040' not found in LLM output."

    # writer: 'Agnew, Jim'
    if normalize("Agnew, Jim") not in llm_norm:
        return False, "Writer name 'Agnew, Jim' not found in LLM output."

    # violent_liongate_movie: '2001 Maniacs'
    if normalize("2001 Maniacs") not in llm_norm:
        return False, "Title '2001 Maniacs' not found in LLM output."

    return True, "Ground truth found in LLM output."
