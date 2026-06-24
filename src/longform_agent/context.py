"""Context assembly: builds system prompt and per-turn message list."""
from __future__ import annotations

import json
import re
from pathlib import Path

from longform_agent.config import Config


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def tool_note(tool_name: str, tool_input: dict, tool_result: str | None = None) -> str:
    """Return a compact human-readable record of a tool interaction.

    These notes are appended inline to assistant messages so that the clean
    conversation history (without raw tool-call JSON) still captures what
    the agent did on each turn.
    """
    if tool_name == "web_search":
        query = tool_input.get("query", "")
        note = f"[🔍 web_search: {query!r}"
        if tool_result:
            try:
                results = json.loads(tool_result)
                note += f" → {len(results)} result(s)"
                if results:
                    titles = ", ".join(r.get("title", "")[:40] for r in results[:2])
                    note += f": {titles}"
            except Exception:
                note += f" → {tool_result[:80]}"
        return note + "]"

    if tool_name == "edit_file":
        patch = tool_input.get("patch", "")
        m = re.search(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE)
        file_path = m.group(1).strip() if m else "unknown file"
        result_snippet = f" → {tool_result[:60]}" if tool_result else ""
        return f"[✏️ edit_file: {file_path}{result_snippet}]"

    if tool_name == "run_shell":
        command = tool_input.get("command", "")[:60]
        exit_m = re.search(r"exit=(-?\d+)", tool_result or "")
        exit_code = exit_m.group(1) if exit_m else "?"
        return f"[⚙ run_shell: {command!r} → exit={exit_code}]"

    return f"[🔧 {tool_name}]"


def _strip_tools_from_history(history: list[dict]) -> list[dict]:
    """Return a clean user/assistant message list from raw history.

    Tool-call metadata is already inlined as compact notes by the agent,
    so we only need to drop tool-role messages and enforce strict alternation.
    """
    clean: list[dict] = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        clean.append({"role": role, "content": content})

    # Enforce strict user/assistant alternation starting with "user"
    result: list[dict] = []
    expected = "user"
    for msg in clean:
        if msg["role"] == expected:
            result.append(msg)
            expected = "assistant" if expected == "user" else "user"

    # Drop a dangling user turn — the live user message is appended separately
    if result and result[-1]["role"] == "user":
        result.pop()
    return result


class ContextManager:
    """Assembles the system prompt and per-turn message list for each agent turn.

    The system prompt (stable prefix) includes:
      - base instructions + tool descriptions
      - project bible (style guide / background)
      - chapter summaries for completed chapters
      - optionally, full text of the last N completed chapters

    The message list (dynamic, changes every turn) includes:
      - full text of the active chapter being drafted
      - agent working memory
      - rolling conversation summary
      - last N verbatim conversation turns
      - the new user message
    """

    def __init__(
        self,
        config: Config,
        project_dir: str,
        active_chapter: str | None,
    ) -> None:
        self.config = config
        self.project_dir = Path(project_dir)
        self.active_chapter = active_chapter
        self._chapters_dir = self.project_dir / "chapters"

    # ------------------------------------------------------------------
    # File loaders
    # ------------------------------------------------------------------

    def _load_bible(self) -> str:
        p = self.project_dir / "bible.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _load_agent_memory(self) -> str:
        p = self.project_dir / "agent_memory.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _sorted_chapters(self) -> list[Path]:
        if not self._chapters_dir.exists():
            return []
        return sorted(
            f for f in self._chapters_dir.glob("*.md")
            if not f.name.endswith(".summary.md")
        )

    def _load_chapter_summaries(self) -> list[tuple[str, str]]:
        """Return (slug, summary_text) pairs for all non-active chapters."""
        result = []
        for ch in self._sorted_chapters():
            if self.active_chapter and ch.stem == self.active_chapter:
                continue
            sm = self._chapters_dir / f"{ch.stem}.summary.md"
            if sm.exists():
                result.append((ch.stem, sm.read_text(encoding="utf-8")))
        return result

    def _load_full_chapters(self) -> list[tuple[str, str]]:
        """Return full text of the last *max_full_chapters* completed chapters."""
        limit = self.config.agent.max_full_chapters
        if limit <= 0:
            return []
        candidates = [
            ch for ch in self._sorted_chapters()
            if not (self.active_chapter and ch.stem == self.active_chapter)
        ]
        return [
            (ch.stem, ch.read_text(encoding="utf-8"))
            for ch in candidates[-limit:]
        ]

    def _load_active_chapter(self) -> str:
        if not self.active_chapter:
            return ""
        ch = self._chapters_dir / f"{self.active_chapter}.md"
        return ch.read_text(encoding="utf-8") if ch.exists() else ""

    def is_chapter_summary_stale(self) -> bool:
        """Return True if the active chapter file is newer than its summary."""
        if not self.active_chapter:
            return False
        ch = self._chapters_dir / f"{self.active_chapter}.md"
        sm = self._chapters_dir / f"{self.active_chapter}.summary.md"
        if not ch.exists() or not sm.exists():
            return False
        return ch.stat().st_mtime > sm.stat().st_mtime

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load_conversation_summary(self) -> str:
        p = self.project_dir / "conversation_summary.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save_conversation_summary(self, text: str) -> None:
        _atomic_write(self.project_dir / "conversation_summary.md", text)

    def load_conversation_history(self) -> list[dict]:
        p = self.project_dir / "conversation_history.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return []

    def save_conversation_history(self, history: list[dict]) -> None:
        _atomic_write(
            self.project_dir / "conversation_history.json",
            json.dumps(history, ensure_ascii=False, indent=2),
        )

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def build_system_prompt(self, base: str) -> str:
        """Build the stable system prompt prefix.

        Ordered from most stable (instructions) to least stable (summaries)
        to maximise KV-cache hit rate when using slot-pinned local inference.
        """
        parts = [f"<system_instructions_tools>\n{base}\n</system_instructions_tools>"]

        bible = self._load_bible()
        if bible:
            parts.append(f"<project_bible>\n{bible}\n</project_bible>")

        summaries = self._load_chapter_summaries()
        if summaries:
            body = "\n\n".join(f"### {n}\n{t}" for n, t in summaries)
            parts.append(f"<chapter_summaries>\n{body}\n</chapter_summaries>")

        full_chapters = self._load_full_chapters()
        if full_chapters:
            body = "\n\n---\n\n".join(f"### {n}\n{t}" for n, t in full_chapters)
            parts.append(f"<chapters_full_text>\n{body}\n</chapters_full_text>")

        return "\n\n".join(parts)

    def build_messages(
        self,
        history: list[dict],
        conversation_summary: str,
        user_message: str,
    ) -> list[dict]:
        """Build the per-turn message list.

        Injects dynamic context (active chapter, memory, summary, recent history)
        as a synthetic preamble exchange, then appends the new user message.
        """
        messages: list[dict] = []
        dynamic_parts: list[str] = []

        # Active chapter in full — most important dynamic context
        active_full = self._load_active_chapter()
        if active_full:
            dynamic_parts.append(
                f"<active_chapter>\n{active_full}\n</active_chapter>"
            )

        # Agent working memory — key facts and decisions
        memory = self._load_agent_memory()
        if memory.strip():
            dynamic_parts.append(f"<agent_memory>\n{memory}\n</agent_memory>")

        # Rolling conversation summary for context beyond the verbatim window
        if conversation_summary:
            dynamic_parts.append(
                f"<conversation_summary>\n{conversation_summary}\n</conversation_summary>"
            )

        # Last N verbatim turns (tool metadata stripped)
        clean_history = _strip_tools_from_history(history)
        if clean_history:
            history_text = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in clean_history
            )
            dynamic_parts.append(
                f"<recent_conversation>\n{history_text}\n</recent_conversation>"
            )

        if dynamic_parts:
            messages.append({"role": "user", "content": "\n\n".join(dynamic_parts)})
            messages.append({"role": "assistant", "content": "Context received. Ready."})

        messages.append({"role": "user", "content": user_message})
        return messages
