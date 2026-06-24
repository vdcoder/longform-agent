"""Rolling conversation and chapter summariser."""
from __future__ import annotations

from longform_agent.llm import LLMClient


_CONV_PROMPT = """\
Summarize the following conversation between an author and their AI writing assistant.
Capture: key decisions made, topics discussed, edits proposed/applied, open questions, facts found.
Be concise. Output only the summary text (no preamble)."""

_CHAPTER_PROMPT = """\
Summarize the following chapter.
Capture: main topics, key arguments, definitions introduced, narrative arc.
Be concise. Output only the summary text (no preamble)."""

_ROLLING_PROMPT = """\
You are maintaining a running prose summary of a writing project conversation.
One exchange (a user message and assistant response) is aging out of the verbatim window \
and must be folded in.
Incorporate it naturally into the existing summary as a concise prose record — written at \
the moment of that exchange, unaware of anything that came after.
Remove redundancy, merge overlapping points. Hard limit: ~400 words.
Output only the updated summary text (no preamble)."""


class Summarizer:
    """Produces rolling conversation summaries and per-chapter summaries."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def summarize_conversation(
        self,
        exchanges: list[dict],
        existing_summary: str = "",
        max_tokens: int = 600,
    ) -> str:
        """Fold evicted *exchanges* into *existing_summary* and return the updated text."""
        turns_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in exchanges
            if isinstance(m.get("content"), str) and m.get("content", "").strip()
        )
        if existing_summary:
            prompt = (
                f"{_ROLLING_PROMPT}\n\n"
                f"### Existing Summary\n{existing_summary}\n\n"
                f"### New Conversation Turns\n{turns_text}"
            )
        else:
            prompt = f"{_CONV_PROMPT}\n\n{turns_text}"
        return self._llm.complete(prompt, max_tokens=max_tokens)

    def summarize_chapter(
        self,
        content: str,
        title: str,
        max_tokens: int = 512,
    ) -> str:
        """Summarise a single chapter for inclusion in the stable system prompt."""
        prompt = f"{_CHAPTER_PROMPT}\n\nChapter '{title}':\n\n{content}"
        return self._llm.complete(prompt, max_tokens=max_tokens)
