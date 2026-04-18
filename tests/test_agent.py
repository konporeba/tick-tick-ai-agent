import json
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import (
    build_messages,
    _coerce_priority,
    _validate_task,
    render_allowed_tags,
    VALID_PRIORITIES,
)

ALLOWED_TAGS = ["work", "analytics", "ai", "automation", "learning"]

FEW_SHOT = [
    {
        "raw": {"project_id": "p1", "task_id": "t1", "title": "Do thing", "description": "", "priority": 0, "tags": []},
        "expected": {"project_id": "p1", "task_id": "t1", "title": "✅ Do thing", "description": "desc\n\n---\n\n_kw_", "priority": 1, "tags": ["work"], "reasoning": "Added emoji."},
    }
]

SYSTEM_PROMPT = "You are a task assistant."


class TestRenderAllowedTags:
    def test_contains_all_tags(self):
        rendered = render_allowed_tags(ALLOWED_TAGS)
        for tag in ALLOWED_TAGS:
            assert tag in rendered

    def test_has_yaml_block(self):
        rendered = render_allowed_tags(ALLOWED_TAGS)
        assert "```yaml" in rendered


class TestBuildMessages:
    def test_returns_system_and_messages(self):
        tasks = [{"id": "t1", "title": "test", "description": "", "priority": 0, "tags": []}]
        system, messages = build_messages(tasks, SYSTEM_PROMPT, FEW_SHOT, ALLOWED_TAGS)
        assert isinstance(system, list)
        assert isinstance(messages, list)

    def test_system_has_cache_control(self):
        tasks = [{}]
        system, _ = build_messages(tasks, SYSTEM_PROMPT, FEW_SHOT, ALLOWED_TAGS)
        assert system[0].get("cache_control") == {"type": "ephemeral"}

    def test_few_shot_last_assistant_has_cache_control(self):
        tasks = [{}]
        _, messages = build_messages(tasks, SYSTEM_PROMPT, FEW_SHOT, ALLOWED_TAGS)
        # Last few-shot assistant message should have cache_control
        last_assistant = None
        for msg in messages[:-1]:  # exclude the final user message
            if msg["role"] == "assistant":
                last_assistant = msg
        assert last_assistant is not None
        content = last_assistant["content"]
        assert isinstance(content, list)
        assert content[0].get("cache_control") == {"type": "ephemeral"}

    def test_last_message_is_user_with_tasks(self):
        tasks = [{"id": "t1", "title": "test", "description": "", "priority": 0, "tags": []}]
        _, messages = build_messages(tasks, SYSTEM_PROMPT, FEW_SHOT, ALLOWED_TAGS)
        last = messages[-1]
        assert last["role"] == "user"
        parsed = json.loads(last["content"])
        assert parsed == tasks

    def test_few_shot_alternates_roles(self):
        tasks = [{}]
        _, messages = build_messages(tasks, SYSTEM_PROMPT, FEW_SHOT, ALLOWED_TAGS)
        # All messages except the last should alternate user/assistant
        few_shot_messages = messages[:-1]
        for i, msg in enumerate(few_shot_messages):
            expected_role = "user" if i % 2 == 0 else "assistant"
            assert msg["role"] == expected_role


class TestCoercePriority:
    def test_valid_priorities_unchanged(self):
        for p in VALID_PRIORITIES:
            assert _coerce_priority(p) == p

    def test_invalid_2_coerced(self):
        result = _coerce_priority(2)
        assert result in VALID_PRIORITIES

    def test_invalid_4_coerced(self):
        result = _coerce_priority(4)
        assert result in VALID_PRIORITIES

    def test_none_defaults_to_1(self):
        assert _coerce_priority(None) == 1

    def test_string_number(self):
        assert _coerce_priority("3") == 3

    def test_invalid_string(self):
        assert _coerce_priority("abc") == 1

    def test_negative_coerced(self):
        result = _coerce_priority(-1)
        assert result in VALID_PRIORITIES


class TestValidateTask:
    def _valid_task(self, **overrides) -> dict:
        base = {
            "project_id": "p1",
            "task_id": "t1",
            "title": "📊 Good title",
            "description": "desc\n\n---\n\n_kw_",
            "priority": 1,
            "tags": ["work"],
            "reasoning": "Fixed it.",
        }
        return {**base, **overrides}

    def _input_task(self, **overrides) -> dict:
        base = {"id": "t1", "tags": []}
        return {**base, **overrides}

    def test_valid_task_passes(self):
        task = self._valid_task()
        result = _validate_task(task, set(ALLOWED_TAGS), self._input_task())
        assert result is not None
        assert result["task_id"] == "t1"

    def test_missing_field_returns_none(self):
        task = self._valid_task()
        del task["title"]
        result = _validate_task(task, set(ALLOWED_TAGS), self._input_task())
        assert result is None

    def test_no_emoji_prefix_returns_none(self):
        task = self._valid_task(title="No emoji here")
        result = _validate_task(task, set(ALLOWED_TAGS), self._input_task())
        assert result is None

    def test_unknown_tags_stripped(self):
        task = self._valid_task(tags=["work", "FAKE_TAG"])
        result = _validate_task(task, set(ALLOWED_TAGS), self._input_task())
        assert "FAKE_TAG" not in result["tags"]
        assert "work" in result["tags"]

    def test_input_tags_preserved_if_dropped(self):
        task = self._valid_task(tags=[])  # Claude dropped the original tag
        input_task = self._input_task(tags=["automation"])
        result = _validate_task(task, set(ALLOWED_TAGS), input_task)
        assert "automation" in result["tags"]

    def test_invalid_priority_coerced(self):
        task = self._valid_task(priority=2)
        result = _validate_task(task, set(ALLOWED_TAGS), self._input_task())
        assert result["priority"] in VALID_PRIORITIES
