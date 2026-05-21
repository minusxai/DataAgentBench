import re


def normalize(text):
    # strip thousands-separator commas, then remove non-alphanumeric
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate(llm_output: str):
    llm_norm = normalize(llm_output)

    # alternative_name: '!!!, Toy' — normalize to 'toy'
    if not re.search(r"\btoy\b", llm_norm):
        return False, "Alternate name '!!!, Toy' not found in LLM output."

    # voiced_char_name: '"Cockamamie\'s" Salesgirl' — normalize to 'cockamamies salesgirl'
    if "cockamamies salesgirl" not in llm_norm:
        return False, "Character name 'Cockamamie\\'s Salesgirl' not found in LLM output."

    # voicing_actress: 'Aaron, Caroline'
    if normalize("Aaron, Caroline") not in llm_norm:
        return False, "Actress name 'Aaron, Caroline' not found in LLM output."

    # american_movie: '$15,000.00 Error' — normalize to '15000 00 error'
    if "15000" not in llm_norm or "error" not in llm_norm:
        return False, "Title '$15,000.00 Error' not found in LLM output."

    return True, "Ground truth found in LLM output."
