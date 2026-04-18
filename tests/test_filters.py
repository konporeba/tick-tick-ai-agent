import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.filters import has_emoji_prefix, is_completed, filter_tasks, COMPLETED_STATUS


class TestHasEmojiPrefix:
    def test_plain_title_returns_false(self):
        assert has_emoji_prefix("Prepare quarterly report") is False

    def test_emoji_prefixed_returns_true(self):
        assert has_emoji_prefix("📊 Prepare quarterly report") is True

    def test_leading_whitespace_then_emoji(self):
        assert has_emoji_prefix("  📊 Some title") is True

    def test_emoji_in_parens_not_prefix(self):
        assert has_emoji_prefix("(📊) Some title") is False

    def test_empty_string_returns_false(self):
        assert has_emoji_prefix("") is False

    def test_whitespace_only_returns_false(self):
        assert has_emoji_prefix("   ") is False

    def test_emoji_mid_title_not_prefix(self):
        assert has_emoji_prefix("Report 📊 for Q3") is False

    def test_flag_emoji(self):
        assert has_emoji_prefix("🇵🇱 Zadanie po polsku") is True

    def test_skin_tone_modifier(self):
        assert has_emoji_prefix("👋🏽 Hello there") is True

    def test_zwj_sequence(self):
        assert has_emoji_prefix("👨‍💻 Write code") is True

    def test_ascii_prefix_not_emoji(self):
        assert has_emoji_prefix(":fire: Deploy hotfix") is False

    def test_number_prefix_not_emoji(self):
        assert has_emoji_prefix("1. First task") is False


class TestIsCompleted:
    def test_completed_task(self):
        assert is_completed({"status": COMPLETED_STATUS}) is True

    def test_active_task(self):
        assert is_completed({"status": 0}) is False

    def test_missing_status_defaults_to_not_completed(self):
        assert is_completed({}) is False

    def test_other_status_values(self):
        for s in [1, 3, 4]:
            assert is_completed({"status": s}) is False


class TestFilterTasks:
    ALLOWED = {"proj_A", "proj_B"}

    def _task(self, **kwargs) -> dict:
        defaults = {
            "id": "t1",
            "projectId": "proj_A",
            "title": "Do something",
            "status": 0,
            "tags": [],
        }
        return {**defaults, **kwargs}

    def test_passes_valid_task(self):
        tasks = [self._task()]
        assert len(filter_tasks(tasks, self.ALLOWED)) == 1

    def test_drops_completed_task(self):
        tasks = [self._task(status=2)]
        assert filter_tasks(tasks, self.ALLOWED) == []

    def test_drops_task_not_in_allowlist(self):
        tasks = [self._task(projectId="proj_other")]
        assert filter_tasks(tasks, self.ALLOWED) == []

    def test_drops_emoji_prefixed_task(self):
        tasks = [self._task(title="📊 Already processed")]
        assert filter_tasks(tasks, self.ALLOWED) == []

    def test_multiple_tasks_mixed(self):
        tasks = [
            self._task(id="t1"),                                      # passes
            self._task(id="t2", status=2),                            # filtered: completed
            self._task(id="t3", projectId="proj_other"),              # filtered: not allowed
            self._task(id="t4", title="✅ Done task"),                 # filtered: emoji prefix
        ]
        result = filter_tasks(tasks, self.ALLOWED)
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_all_filters_must_pass(self):
        # Task that is both completed AND in allowed project — still filtered
        tasks = [self._task(status=2, projectId="proj_A")]
        assert filter_tasks(tasks, self.ALLOWED) == []
