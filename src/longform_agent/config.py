"""Configuration dataclasses loaded from config.toml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore


@dataclass
class LLMConfig:
    """OpenAI-compatible endpoint settings.

    Works with llama.cpp (--server), Ollama, LM Studio, or any endpoint
    that implements the OpenAI Chat Completions API.
    """

    base_url: str = "http://localhost:8080/v1"
    model: str = "local"
    api_key: str = "not-needed"
    max_tokens: int = 4096
    temperature: float = 0.7
    # llama.cpp KV-cache slot IDs — set to -1 to disable slot pinning.
    # Requires --parallel 2 on the server for separate chat/summarise slots.
    chat_slot: int = 0
    summarize_slot: int = 1


@dataclass
class SearchConfig:
    """Web search behaviour."""

    max_results: int = 5
    max_snippet_chars: int = 300


@dataclass
class AgentConfig:
    """Project and agent-loop settings."""

    project_dir: str = "project"
    keep_last_n: int = 6          # verbatim turns to keep in history window
    max_full_chapters: int = 0    # completed chapters included as full text (0 = summaries only)
    max_tool_iterations: int = 6  # tool-call cap per turn
    memory_max_chars: int = 4000  # rolling cap on agent_memory.md
    summary_max_chars: int = 3000 # rolling cap on conversation_summary.md


@dataclass
class Config:
    """Top-level configuration object."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    @classmethod
    def load(cls, path: str = "config.toml") -> "Config":
        """Load config from *path*; return defaults if the file is absent."""
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        cfg = cls()
        if "llm" in data:
            cfg.llm = LLMConfig(**data["llm"])
        if "search" in data:
            cfg.search = SearchConfig(**data["search"])
        if "agent" in data:
            cfg.agent = AgentConfig(**data["agent"])
        return cfg
