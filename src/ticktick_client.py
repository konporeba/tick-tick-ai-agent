import time
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ticktick.com/open/v1"
RATE_LIMIT_DELAY = 0.2  # seconds between update calls
MAX_RETRIES = 3


class TickTickClient:
    def __init__(self, access_token: str):
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {access_token}"})
        logger.info("TickTick client initialized.")

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{BASE_URL}{path}"
        for attempt in range(MAX_RETRIES):
            resp = self._session.request(method, url, timeout=15, **kwargs)
            if resp.status_code == 401:
                raise RuntimeError(
                    "TickTick access token is invalid or expired. "
                    "Re-run get_refresh_token.py to obtain a new one, "
                    "then update TICKTICK_ACCESS_TOKEN in .env / GitHub Actions secrets."
                )
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning("Rate limited (429). Waiting %ss before retry.", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        raise RuntimeError(f"Request to {path} failed after {MAX_RETRIES} retries.")

    def get_projects(self) -> list[dict]:
        return self._request("GET", "/project")

    def get_project_data(self, project_id: str) -> dict:
        return self._request("GET", f"/project/{project_id}/data")

    def get_task(self, project_id: str, task_id: str) -> dict:
        return self._request("GET", f"/project/{project_id}/task/{task_id}")

    def update_task(self, task_id: str, project_id: str, payload: dict) -> dict:
        time.sleep(RATE_LIMIT_DELAY)
        return self._request("POST", f"/task/{task_id}", json={**payload, "id": task_id, "projectId": project_id})

    def fetch_allowed_tasks(self, allowed_project_ids: list[str]) -> list[dict]:
        """Fetch all tasks from allowed projects, returning only incomplete ones (status != 2)."""
        tasks = []
        for pid in allowed_project_ids:
            try:
                data = self.get_project_data(pid)
                project_tasks = data.get("tasks", [])
                for task in project_tasks:
                    task["projectId"] = pid
                    if task.get("status") != 2:
                        tasks.append(task)
            except Exception as exc:
                logger.error("Failed to fetch tasks for project %s: %s", pid, exc)
        return tasks
