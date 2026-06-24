"""LLM client and tool schema definitions."""
from __future__ import annotations

import json
from openai import OpenAI

from longform_agent.config import Config


# ---------------------------------------------------------------------------
# System prompt — injected as the first system message every turn.
# Keep language generic; any project-specific style guidance belongs in the
# project's bible.md file, which the ContextManager includes automatically.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BASE = """\
You are a long-form writing assistant helping the author research, write, and refine \
their project chapter by chapter.

Guidelines:
- Match the author's established voice and style from the project bible.
- When proposing text edits, always use the edit_file tool with a unified diff patch.
  Use --- a/chapters/filename.md and +++ b/chapters/filename.md headers.
  Context lines start with a space, removed lines with -, added lines with +.
- You may also use edit_file to update agent_memory.md to remember important facts,
  decisions, or context. Use --- a/agent_memory.md and +++ b/agent_memory.md in that case.
- Use web_search to verify facts or find citations before writing new claims.
- When you need to rename, move, or perform file operations on chapter files, use run_shell.
  The working directory is the project root (parent of the project dir).
- Never invent citations; search first.\
"""


# ---------------------------------------------------------------------------
# Tool registry — OpenAI function-calling schema.
# To add a new tool: append an entry here and handle it in agent.py:_execute_tool.
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for information, facts, or references to include in the project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edit a file using a git-style unified diff patch. "
                "Targets: project/chapters/*.md for chapter edits, "
                "or agent_memory.md for working-memory updates. "
                "Patch format: --- a/<path> and +++ b/<path> headers, "
                "@@ hunk headers, then +/- lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "Unified diff patch in standard format",
                    },
                },
                "required": ["patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command in the project root directory. "
                "Use for file operations such as renaming or moving chapter files. "
                "Always requires explicit author approval before the command runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of what this command does and why",
                    },
                },
                "required": ["command", "reason"],
            },
        },
    },
]


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client = OpenAI(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )

    def health_check(self) -> bool:
        """Return True if the endpoint is reachable."""
        try:
            self._client.models.list()
            return True
        except Exception:
            return False

    def chat(self, messages: list[dict], system: str = "") -> object:
        """Send a chat completion request with tool support.

        *system* is prepended as a system message when provided.
        Tool results are expected to be injected into *messages* by the caller.
        """
        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages

        # extra_body pins this request to a specific llama.cpp KV-cache slot,
        # improving cache reuse when the system prompt is long and stable.
        # These fields are silently ignored by non-llama-cpp endpoints.
        extra: dict = {}
        if self.config.llm.chat_slot >= 0:
            extra = {
                "id_slot": self.config.llm.chat_slot,
                "cache_prompt": True,
            }

        return self._client.chat.completions.create(
            model=self.config.llm.model,
            messages=full_messages,
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
            tools=TOOLS,
            tool_choice="auto",
            extra_body=extra or None,
        )

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        """Simple single-turn completion without tools — used for summarisation."""
        extra: dict = {}
        if self.config.llm.summarize_slot >= 0:
            extra = {
                "id_slot": self.config.llm.summarize_slot,
                "cache_prompt": True,
            }

        resp = self._client.chat.completions.create(
            model=self.config.llm.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
            extra_body=extra or None,
        )
        content = resp.choices[0].message.content
        return (content or "").strip()
