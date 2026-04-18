import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Required environment variable '{name}' is not set.")
    return value


def load_allowed_tags() -> list[str]:
    data = _load_yaml(BASE_DIR / "config" / "allowed_tags.yaml")
    return data["allowed_tags"]


def load_allowed_projects() -> list[dict]:
    data = _load_yaml(BASE_DIR / "config" / "allowed_projects.yaml")
    return data["allowed_projects"]


def load_few_shot_examples() -> list[dict]:
    path = BASE_DIR / "examples" / "few_shot.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_system_prompt() -> str:
    path = BASE_DIR / "prompts" / "system_prompt.md"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
