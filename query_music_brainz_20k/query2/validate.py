def validate(llm_output: str):
    gt = "iTunes"
    cleaned_gt = "".join(gt.lower().split())
    cleaned_llm_output = "".join(llm_output.lower().split())
    if cleaned_gt in cleaned_llm_output:
        return True, "Ground truth found in LLM output."
    else:
        reason = f"Ground truth '{gt}' not found in LLM output: {llm_output}"
        return False, reason

        