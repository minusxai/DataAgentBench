def validate(llm_output: str):
    """For agency-name questions: extract the *declared* answer near end of text,
    not any incidental mention in a ranked list."""
    import re
    from pathlib import Path
    gt = Path(__file__).parent.joinpath("ground_truth.csv").read_text().strip().lower()
    text = llm_output.lower()
    patterns = [
        r"answer\s*(?:is|:)\s*[`*\"' ]*([a-z][a-z0-9 _'-]*?)[`*\"'.\n]",
        r"highest[^.]*?is[: ]\s*[`*\"' ]*([a-z][a-z0-9 _'-]*?)[`*\"'.\n]",
        r"agency[: ]\s*[`*\"' ]*([a-z][a-z0-9 _'-]*?)[`*\"'.\n]",
        r"\*\*([a-z][a-z0-9 _'-]*?)\*\*",
    ]
    declared = []
    for p in patterns:
        for m in re.finditer(p, text):
            cand = m.group(1).strip().rstrip(".")
            declared.append((m.start(), cand))
    if declared:
        declared.sort()
        last = declared[-1][1]
        if gt in last or last in gt:
            return True, f"matched declared answer {gt!r}"
        return False, f"expected {gt!r}, declared {last!r}"
    if gt in text:
        return True, f"matched (fallback) {gt!r}"
    return False, f"expected {gt!r}"
