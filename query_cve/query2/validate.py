def validate(llm_output: str):
    from pathlib import Path
    gt = Path(__file__).parent.joinpath("ground_truth.csv").read_text().strip().lower()
    if gt in llm_output.lower():
        return True, f"matched {gt!r}"
    return False, f"expected {gt!r}"
