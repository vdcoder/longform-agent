"""Tests for context assembly."""
import json
import tempfile
from pathlib import Path

import pytest

from longform_agent.config import Config, AgentConfig
from longform_agent.context import ContextManager, tool_note, _strip_tools_from_history


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory structure."""
    (tmp_path / "chapters").mkdir()
    (tmp_path / "bible.md").write_text("# Bible\nWrite clearly.", encoding="utf-8")
    (tmp_path / "agent_memory.md").write_text(
        "# Memory\n- Prefer short sentences.", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def ctx(project_dir: Path) -> ContextManager:
    cfg = Config()
    return ContextManager(cfg, str(project_dir), active_chapter=None)


# ---------------------------------------------------------------------------
# tool_note
# ---------------------------------------------------------------------------

class TestToolNote:
    def test_web_search_with_results(self):
        results = json.dumps([
            {"title": "Article One", "url": "https://example.com", "snippet": "..."},
            {"title": "Article Two", "url": "https://other.com", "snippet": "..."},
        ])
        note = tool_note("web_search", {"query": "technical debt"}, results)
        assert "web_search" in note
        assert "technical debt" in note
        assert "2 result" in note

    def test_web_search_no_result(self):
        note = tool_note("web_search", {"query": "xyz"}, "[]")
        assert "0 result" in note

    def test_edit_file(self):
        patch = "--- a/chapters/01_intro.md\n+++ b/chapters/01_intro.md\n@@ -1 +1 @@\n-old\n+new\n"
        note = tool_note("edit_file", {"patch": patch}, "Patch accepted and applied to 01_intro.md.")
        assert "edit_file" in note
        assert "01_intro.md" in note

    def test_run_shell(self):
        note = tool_note("run_shell", {"command": "ls -la"}, "exit=0\nfile.md")
        assert "run_shell" in note
        assert "exit=0" in note

    def test_unknown_tool(self):
        note = tool_note("custom_tool", {}, None)
        assert "custom_tool" in note


# ---------------------------------------------------------------------------
# _strip_tools_from_history
# ---------------------------------------------------------------------------

class TestStripToolsFromHistory:
    def test_removes_tool_role_messages(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "tool", "tool_call_id": "abc", "content": "result"},
        ]
        clean = _strip_tools_from_history(history)
        assert all(m["role"] in ("user", "assistant") for m in clean)

    def test_enforces_alternation(self):
        history = [
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Second (duplicate user)"},
            {"role": "assistant", "content": "Response"},
        ]
        clean = _strip_tools_from_history(history)
        roles = [m["role"] for m in clean]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], "Adjacent messages have same role"

    def test_drops_trailing_user_message(self):
        history = [
            {"role": "user", "content": "Prompt"},
            {"role": "assistant", "content": "Answer"},
            {"role": "user", "content": "Follow-up"},  # should be dropped
        ]
        clean = _strip_tools_from_history(history)
        assert clean[-1]["role"] == "assistant"

    def test_empty_history(self):
        assert _strip_tools_from_history([]) == []


# ---------------------------------------------------------------------------
# ContextManager.build_system_prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_includes_base_instructions(self, ctx):
        prompt = ctx.build_system_prompt("BE HELPFUL.")
        assert "BE HELPFUL." in prompt

    def test_includes_bible(self, ctx):
        prompt = ctx.build_system_prompt("")
        assert "Write clearly." in prompt

    def test_includes_chapter_summaries(self, project_dir):
        (project_dir / "chapters" / "01_intro.md").write_text("# Intro\n", encoding="utf-8")
        (project_dir / "chapters" / "01_intro.summary.md").write_text(
            "Introduces the topic.", encoding="utf-8"
        )
        cfg = Config()
        ctx = ContextManager(cfg, str(project_dir), active_chapter=None)
        prompt = ctx.build_system_prompt("")
        assert "Introduces the topic." in prompt

    def test_active_chapter_excluded_from_summaries(self, project_dir):
        (project_dir / "chapters" / "01_intro.md").write_text("# Intro\n", encoding="utf-8")
        (project_dir / "chapters" / "01_intro.summary.md").write_text(
            "Summary text here.", encoding="utf-8"
        )
        cfg = Config()
        ctx = ContextManager(cfg, str(project_dir), active_chapter="01_intro")
        prompt = ctx.build_system_prompt("")
        assert "Summary text here." not in prompt


# ---------------------------------------------------------------------------
# ContextManager.build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_user_message_is_last(self, ctx):
        messages = ctx.build_messages([], "", "What should I write next?")
        assert messages[-1]["role"] == "user"
        assert "What should I write next?" in messages[-1]["content"]

    def test_includes_memory(self, ctx):
        messages = ctx.build_messages([], "", "Hello")
        full_content = " ".join(m.get("content", "") or "" for m in messages)
        assert "short sentences" in full_content

    def test_includes_conversation_summary(self, ctx):
        messages = ctx.build_messages([], "Prior summary text.", "Hello")
        full_content = " ".join(m.get("content", "") or "" for m in messages)
        assert "Prior summary text." in full_content

    def test_active_chapter_in_messages(self, project_dir):
        (project_dir / "chapters" / "01_intro.md").write_text(
            "# Intro\nChapter content here.", encoding="utf-8"
        )
        cfg = Config()
        ctx = ContextManager(cfg, str(project_dir), active_chapter="01_intro")
        messages = ctx.build_messages([], "", "Continue.")
        full_content = " ".join(m.get("content", "") or "" for m in messages)
        assert "Chapter content here." in full_content


# ---------------------------------------------------------------------------
# ContextManager persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load_history(self, ctx, project_dir):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        ctx.save_conversation_history(history)
        loaded = ctx.load_conversation_history()
        assert loaded == history

    def test_save_and_load_summary(self, ctx, project_dir):
        ctx.save_conversation_summary("Summary of events.")
        loaded = ctx.load_conversation_summary()
        assert loaded == "Summary of events."

    def test_load_missing_history_returns_empty(self, ctx):
        assert ctx.load_conversation_history() == []

    def test_is_chapter_summary_stale(self, project_dir):
        import time
        ch = project_dir / "chapters" / "01_intro.md"
        sm = project_dir / "chapters" / "01_intro.summary.md"
        ch.write_text("content", encoding="utf-8")
        time.sleep(0.01)
        sm.write_text("summary", encoding="utf-8")
        cfg = Config()
        ctx = ContextManager(cfg, str(project_dir), active_chapter="01_intro")
        assert not ctx.is_chapter_summary_stale()

        # Touch chapter after summary
        time.sleep(0.01)
        ch.write_text("updated content", encoding="utf-8")
        assert ctx.is_chapter_summary_stale()
