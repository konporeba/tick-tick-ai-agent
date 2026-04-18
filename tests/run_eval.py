"""
Evaluation script for the TickTick AI Agent.

Runs eval_set.yaml through the agent and scores results in two layers:
  Layer 1 — Structural assertions (deterministic, hard pass/fail)
  Layer 2 — LLM-as-judge (semantic, scored via claude-haiku-4-5-20251001)

Usage:
    python tests/run_eval.py

Requires ANTHROPIC_API_KEY to be set.
"""

import json
import sys
import time
from pathlib import Path

import anthropic
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import call_claude
from src.config import (
    get_env,
    load_allowed_tags,
    load_few_shot_examples,
    load_system_prompt,
)
from src.filters import has_emoji_prefix

JUDGE_MODEL = "claude-haiku-4-5-20251001"
EVAL_PATH = Path(__file__).parent.parent / "examples" / "eval_set.yaml"
VALID_PRIORITIES = {0, 1, 3, 5}


# ---------------------------------------------------------------------------
# Layer 1 — Structural assertions
# ---------------------------------------------------------------------------

def check_structural(raw: dict, actual: dict, allowed_tags: list[str]) -> list[str]:
    """Return list of failure messages (empty = pass)."""
    failures = []
    allowed_set = set(allowed_tags)

    if not has_emoji_prefix(actual.get("title", "")):
        failures.append("Title does not start with an emoji.")

    content_title = actual.get("title", "")
    if content_title:
        stripped = content_title.lstrip()
        # Remove the first emoji character(s) and the following space
        import emoji as emoji_lib
        chars = list(stripped)
        i = 0
        while i < len(chars) and emoji_lib.emoji_count(chars[i]) > 0:
            i += 1
        if i < len(chars) and chars[i] == " ":
            i += 1
        content_part = "".join(chars[i:])
        if len(content_part) > 60:
            failures.append(f"Title content exceeds 60 chars: {len(content_part)} chars.")

    priority = actual.get("priority")
    if priority not in VALID_PRIORITIES:
        failures.append(f"Invalid priority value: {priority}. Must be one of {VALID_PRIORITIES}.")

    for tag in actual.get("tags", []):
        if tag not in allowed_set:
            failures.append(f"Unknown tag in output: '{tag}'.")

    original_tags = set(raw.get("tags", []))
    output_tags = set(actual.get("tags", []))
    dropped = original_tags - output_tags
    if dropped:
        failures.append(f"Original tags were dropped: {dropped}.")

    desc = actual.get("description", "")
    if "---" not in desc:
        failures.append("Description missing keywords separator '---'.")
    elif not any(line.strip().startswith("_") and line.strip().endswith("_") for line in desc.splitlines()):
        failures.append("Description missing keywords block (expected _kw1 • kw2_ format).")

    if actual.get("project_id") != raw.get("project_id"):
        failures.append("project_id was changed.")
    if actual.get("task_id") != raw.get("task_id"):
        failures.append("task_id was changed.")

    return failures


# ---------------------------------------------------------------------------
# Layer 2 — LLM-as-judge
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You are evaluating the output of a task enrichment AI agent.

You will receive:
- raw: the original task before enrichment
- expected: a reference expected output
- actual: the agent's actual output

Score the actual output on the following rubric (1–3 each):
1. Meaning preserved: Did the output preserve the task's core meaning and intent?
2. Tag relevance: Are the assigned tags relevant to the task content?
3. Priority justified: Is the priority appropriate given the task content?
4. Description quality: Is the description useful, concise, and non-generic?
5. Language consistency: Is the output in the same language as the input title?

For each criterion output:
- score: 1 (poor), 2 (acceptable), 3 (good)
- comment: one short sentence

Then output an overall verdict: "PASS" (all scores ≥ 2) or "FAIL".

Return ONLY valid JSON in this format:
{
  "meaning_preserved": {"score": 3, "comment": "..."},
  "tag_relevance": {"score": 2, "comment": "..."},
  "priority_justified": {"score": 3, "comment": "..."},
  "description_quality": {"score": 2, "comment": "..."},
  "language_consistency": {"score": 3, "comment": "..."},
  "verdict": "PASS"
}"""


def judge_with_llm(raw: dict, expected: dict, actual: dict, client: anthropic.Anthropic) -> dict:
    user_content = json.dumps({"raw": raw, "expected": expected, "actual": actual}, ensure_ascii=False)
    try:
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=600,
            temperature=0,
            system=JUDGE_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(text)
    except Exception as exc:
        return {"error": str(exc), "verdict": "ERROR"}


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_eval() -> None:
    with open(EVAL_PATH, "r", encoding="utf-8") as f:
        eval_set = yaml.safe_load(f)

    allowed_tags = load_allowed_tags()
    few_shot = load_few_shot_examples()
    system_prompt = load_system_prompt()
    client = anthropic.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))

    results = []
    print(f"\nRunning eval on {len(eval_set)} examples...\n")

    for example in eval_set:
        scenario = example["scenario"]
        raw = example["raw"]
        expected = example["expected"]

        # Build agent input (mimicking main.py's _task_to_agent_input)
        agent_input = {
            "id": raw["task_id"],
            "project_id": raw["project_id"],
            "task_id": raw["task_id"],
            "title": raw["title"],
            "description": raw["description"],
            "priority": raw["priority"],
            "tags": raw["tags"],
        }

        # Call agent
        actuals = call_claude([agent_input], system_prompt, few_shot, allowed_tags, client)
        actual = actuals[0] if actuals else {}

        # Layer 1
        structural_failures = check_structural(raw, actual, allowed_tags) if actual else ["No output returned."]
        layer1_pass = len(structural_failures) == 0

        # Layer 2
        time.sleep(0.3)  # small delay between judge calls
        judge_result = judge_with_llm(raw, expected, actual, client) if actual else {"verdict": "FAIL", "error": "No output"}
        layer2_pass = judge_result.get("verdict") == "PASS"

        results.append({
            "scenario": scenario,
            "layer1_pass": layer1_pass,
            "layer1_failures": structural_failures,
            "layer2_pass": layer2_pass,
            "judge": judge_result,
            "actual": actual,
        })

        status = "✓" if (layer1_pass and layer2_pass) else "✗"
        print(f"  {status} {scenario}")
        if not layer1_pass:
            for f in structural_failures:
                print(f"      [L1] {f}")
        if not layer2_pass:
            verdict = judge_result.get("verdict", "?")
            print(f"      [L2] verdict={verdict}")

    # Summary
    total = len(results)
    l1_pass = sum(1 for r in results if r["layer1_pass"])
    l2_pass = sum(1 for r in results if r["layer2_pass"])
    both_pass = sum(1 for r in results if r["layer1_pass"] and r["layer2_pass"])

    print(f"\n{'='*50}")
    print(f"EVAL SUMMARY  ({total} examples)")
    print(f"{'='*50}")
    print(f"  Layer 1 (structural):  {l1_pass}/{total} passed")
    print(f"  Layer 2 (LLM judge):   {l2_pass}/{total} passed")
    print(f"  Both layers passed:    {both_pass}/{total}")
    print(f"{'='*50}\n")

    # Detailed judge scores
    print("Per-criterion averages (Layer 2):")
    criteria = ["meaning_preserved", "tag_relevance", "priority_justified", "description_quality", "language_consistency"]
    for criterion in criteria:
        scores = [r["judge"].get(criterion, {}).get("score", 0) for r in results if "score" in r["judge"].get(criterion, {})]
        if scores:
            avg = sum(scores) / len(scores)
            print(f"  {criterion}: {avg:.2f}/3")


if __name__ == "__main__":
    run_eval()
