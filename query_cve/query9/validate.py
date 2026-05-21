def validate(llm_output: str):
    import re
    from pathlib import Path
    gt = int(Path(__file__).parent.joinpath("ground_truth.csv").read_text().strip())
    # accept comma-formatted numbers like "1,262"
    text = re.sub(r"(\d),(\d)", r"\1\2", llm_output)
    nums = re.findall(r"(?<![\w.-])(\d+)(?![\w.-])", text)
    if any(int(n) == gt for n in nums):
        return True, f"matched {gt}"
    return False, f"expected {gt}"
