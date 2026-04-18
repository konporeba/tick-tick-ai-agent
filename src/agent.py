import json
import logging
import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
TEMPERATURE = 0
TOKENS_PER_TASK = 500
VALID_PRIORITIES = {0, 1, 3, 5}


def render_allowed_tags(allowed_tags: list[str]) -> str:
    tag_list = "\n".join(f"  - {t}" for t in allowed_tags)
    return f"## Allowed Tags\n\n```yaml\n{tag_list}\n```"


def build_messages(
    tasks: list[dict],
    system_prompt: str,
    few_shot: list[dict],
    allowed_tags: list[str],
) -> tuple[list[dict], list[dict]]:
    system = [
        {
            "type": "text",
            "text": system_prompt + "\n\n" + render_allowed_tags(allowed_tags),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages = []
    for i, pair in enumerate(few_shot):
        messages.append({"role": "user", "content": json.dumps([pair["raw"]])})
        is_last = i == len(few_shot) - 1
        assistant_content = [
            {
                "type": "text",
                "text": json.dumps([pair["expected"]]),
                **({"cache_control": {"type": "ephemeral"}} if is_last else {}),
            }
        ]
        messages.append({"role": "assistant", "content": assistant_content})

    messages.append({"role": "user", "content": json.dumps(tasks)})
    return system, messages


def _coerce_priority(value) -> int:
    """Coerce an invalid priority to the nearest valid one."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid priority value '%s', defaulting to 1.", value)
        return 1
    if v in VALID_PRIORITIES:
        return v
    nearest = min(VALID_PRIORITIES, key=lambda p: abs(p - v))
    logger.warning("Priority %s is not valid, coercing to %s.", v, nearest)
    return nearest


def _validate_task(task: dict, allowed_tags: set[str], input_task: dict) -> dict | None:
    """Validate a single task from the Claude response. Returns cleaned task or None."""
    required = {"project_id", "task_id", "title", "description", "priority", "tags", "reasoning"}
    if not required.issubset(task.keys()):
        missing = required - task.keys()
        logger.warning("Task %s missing fields: %s", task.get("task_id"), missing)
        return None

    from .filters import has_emoji_prefix
    if not has_emoji_prefix(task["title"]):
        logger.warning("Task %s title lacks emoji prefix: %s", task["task_id"], task["title"])
        return None

    task["priority"] = _coerce_priority(task["priority"])

    cleaned_tags = []
    for tag in task.get("tags", []):
        if tag in allowed_tags:
            cleaned_tags.append(tag)
        else:
            logger.warning("Dropping unknown tag '%s' from task %s.", tag, task["task_id"])
    task["tags"] = cleaned_tags

    # Preserve input tags that Claude may have dropped
    for orig_tag in input_task.get("tags", []):
        if orig_tag not in task["tags"]:
            logger.warning("Re-adding dropped tag '%s' to task %s.", orig_tag, task["task_id"])
            task["tags"].append(orig_tag)

    return task


def call_claude(
    tasks: list[dict],
    system_prompt: str,
    few_shot: list[dict],
    allowed_tags: list[str],
    client: anthropic.Anthropic,
) -> list[dict]:
    if not tasks:
        return []

    system, messages = build_messages(tasks, system_prompt, few_shot, allowed_tags)
    max_tokens = max(1000, len(tasks) * TOKENS_PER_TASK)
    allowed_tags_set = set(allowed_tags)
    input_by_id = {t["id"]: t for t in tasks}

    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        system=system,
        messages=messages,
    )

    raw_text = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        results = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude response as JSON: %s\nRaw: %s", exc, raw_text)
        return []

    if not isinstance(results, list):
        logger.error("Claude response is not a JSON array.")
        return []

    valid = []
    seen_ids = {t["id"] for t in tasks}
    for item in results:
        tid = item.get("task_id")
        if tid not in seen_ids:
            logger.warning("Response contains unknown task_id '%s', skipping.", tid)
            continue
        cleaned = _validate_task(item, allowed_tags_set, input_by_id.get(tid, {}))
        if cleaned:
            valid.append(cleaned)

    skipped = seen_ids - {r["task_id"] for r in valid}
    if skipped:
        logger.warning("Tasks not returned by Claude (will retry next run): %s", skipped)

    return valid
