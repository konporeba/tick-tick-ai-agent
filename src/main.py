import json
import logging
import sys
from pathlib import Path

import anthropic

from .config import (
    get_env,
    load_allowed_projects,
    load_allowed_tags,
    load_few_shot_examples,
    load_system_prompt,
)
from .filters import filter_tasks
from .ticktick_client import TickTickClient
from .agent import call_claude

BATCH_SIZE = 10
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def chunk(lst: list, size: int) -> list[list]:
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def _task_to_agent_input(task: dict) -> dict:
    return {
        "id": task.get("id", ""),
        "project_id": task.get("projectId", ""),
        "task_id": task.get("id", ""),
        "title": task.get("title", ""),
        "description": task.get("content", ""),
        "priority": task.get("priority", 0),
        "tags": task.get("tags", []),
    }


def _write_job_summary(processed: list[dict], skipped: int, total_fetched: int) -> None:
    summary_path = LOG_DIR / "job_summary.md"
    lines = [
        "# TickTick Agent Run Summary\n",
        f"- Tasks fetched: {total_fetched}",
        f"- Tasks processed: {len(processed)}",
        f"- Tasks skipped (already processed or concurrent edit): {skipped}\n",
        "## Changes\n",
    ]
    for item in processed:
        lines.append(f"- `{item['task_id']}`: {item.get('old_title', '?')} → {item['title']}")
        lines.append(f"  - Reasoning: {item.get('reasoning', '')}\n")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Job summary written to %s", summary_path)


def run() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logger.info("=== TickTick AI Agent starting ===")

    # 1. Load config
    allowed_projects = load_allowed_projects()
    allowed_tags = load_allowed_tags()
    few_shot = load_few_shot_examples()
    system_prompt = load_system_prompt()
    allowed_project_ids = {p["id"] for p in allowed_projects}

    # 2. Initialize clients
    tt = TickTickClient(access_token=get_env("TICKTICK_ACCESS_TOKEN"))
    claude = anthropic.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))

    # 3-4. Fetch tasks from allowed projects
    raw_tasks = tt.fetch_allowed_tasks(list(allowed_project_ids))
    logger.info("Fetched %d incomplete tasks across allowed projects.", len(raw_tasks))

    # 5-6. Filter: drop completed and emoji-prefixed tasks
    workload = filter_tasks(raw_tasks, allowed_project_ids)
    logger.info("%d tasks need processing.", len(workload))

    # 7. Nothing to do?
    if not workload:
        logger.info("Nothing to do. Exiting.")
        _write_job_summary([], 0, len(raw_tasks))
        return

    # 8. Batch
    batches = chunk(workload, BATCH_SIZE)
    logger.info("Processing %d batch(es) of up to %d tasks.", len(batches), BATCH_SIZE)

    all_results: list[dict] = []
    skipped_count = 0

    for batch_num, batch in enumerate(batches, start=1):
        logger.info("Batch %d/%d: sending %d tasks to Claude.", batch_num, len(batches), len(batch))
        agent_inputs = [_task_to_agent_input(t) for t in batch]

        # 9. Call Claude
        results = call_claude(agent_inputs, system_prompt, few_shot, allowed_tags, claude)

        # 10. Update tasks in TickTick
        for result in results:
            tid = result["task_id"]
            pid = result["project_id"]

            # 10a. Optimistic concurrency check
            try:
                current = tt.get_task(pid, tid)
            except Exception as exc:
                logger.error("Could not re-fetch task %s: %s. Skipping.", tid, exc)
                skipped_count += 1
                continue

            original = next((t for t in batch if t.get("id") == tid), None)
            if original is None:
                skipped_count += 1
                continue

            # Compare modifiedTime to detect concurrent edits
            if current.get("modifiedTime") != original.get("modifiedTime"):
                logger.warning(
                    "Task %s was modified mid-run (user edit detected). Skipping update.", tid
                )
                skipped_count += 1
                continue

            # 10b. Send update to TickTick
            payload = {
                "title": result["title"],
                "content": result["description"],
                "priority": result["priority"],
                "tags": result["tags"],
            }
            try:
                tt.update_task(tid, pid, payload)
                result["old_title"] = original.get("title", "")
                all_results.append(result)
                logger.info(
                    "Updated task %s: '%s' → '%s' | %s",
                    tid,
                    result["old_title"],
                    result["title"],
                    result.get("reasoning", ""),
                )
            except Exception as exc:
                logger.error("Failed to update task %s: %s", tid, exc)
                skipped_count += 1

    logger.info(
        "Done. %d tasks updated, %d skipped.", len(all_results), skipped_count
    )
    _write_job_summary(all_results, skipped_count, len(raw_tasks))


if __name__ == "__main__":
    run()
