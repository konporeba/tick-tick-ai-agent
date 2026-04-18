import emoji as emoji_lib

COMPLETED_STATUS = 2


def is_completed(task: dict) -> bool:
    return task.get("status") == COMPLETED_STATUS


def has_emoji_prefix(title: str) -> bool:
    """Return True if the title starts with an emoji (after stripping leading whitespace)."""
    stripped = title.lstrip()
    if not stripped:
        return False
    # Check the first few code points to handle compound emojis, ZWJ sequences, flags
    return any(emoji_lib.emoji_count(ch) > 0 for ch in stripped[:4])


def is_in_allowed_project(task: dict, allowed_project_ids: set[str]) -> bool:
    return task.get("projectId") in allowed_project_ids


def filter_tasks(tasks: list[dict], allowed_project_ids: set[str]) -> list[dict]:
    """Apply all three filters and return only tasks the agent should process."""
    result = []
    for task in tasks:
        if is_completed(task):
            continue
        if not is_in_allowed_project(task, allowed_project_ids):
            continue
        if has_emoji_prefix(task.get("title", "")):
            continue
        result.append(task)
    return result
