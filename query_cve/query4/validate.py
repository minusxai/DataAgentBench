def validate(llm_output: str):
    """Q4 expects a single canonical vendor name. To avoid being fooled by
    ranked-list mentions, we extract the vendor declared as the *answer* —
    looking for declarative patterns near the end of the response.
    """
    import re
    from pathlib import Path
    gt = Path(__file__).parent.joinpath("ground_truth.csv").read_text().strip().lower()

    # Look for declarative answer patterns: "answer is X", "is: X", "is `X`",
    # "**X**", "vendor: X". Prefer matches near the end of the output.
    text = llm_output.lower()
    patterns = [
        r"answer\s*(?:is|:)\s*[`*\"' ]*([a-z][a-z0-9_-]*)[`*\"'.\s]",
        r"highest[^.]*?is[: ]\s*[`*\"' ]*([a-z][a-z0-9_-]*)[`*\"'.\s]",
        r"vendor[: ]\s*[`*\"' ]*([a-z][a-z0-9_-]*)[`*\"'.\s]",
        r"\*\*([a-z][a-z0-9_-]*)\*\*",
    ]
    declared = []
    for p in patterns:
        for m in re.finditer(p, text):
            declared.append((m.start(), m.group(1)))
    if declared:
        # take the last declared vendor (closest to end of response)
        declared.sort()
        last_vendor = declared[-1][1]
        if last_vendor == gt:
            return True, f"matched declared answer {gt!r}"
        return False, f"expected {gt!r}, declared answer was {last_vendor!r}"
    # fallback: substring match if no declarative pattern found
    if gt in text:
        return True, f"matched (fallback) {gt!r}"
    return False, f"expected {gt!r}"
