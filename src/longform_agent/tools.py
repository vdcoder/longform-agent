"""Tool implementations: web search, patch editor, shell runner."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import whatthepatch
from duckduckgo_search import DDGS

from longform_agent.config import Config


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via a temp-file rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

class WebSearch:
    """DuckDuckGo web search with configurable result limits."""

    def __init__(self, config: Config) -> None:
        self._max_results = config.search.max_results
        self._max_snippet = config.search.max_snippet_chars

    def search(self, query: str) -> list[dict]:
        """Return a list of {title, url, snippet} dicts for *query*."""
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=self._max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[: self._max_snippet],
            }
            for r in raw
        ]


# ---------------------------------------------------------------------------
# Patch editor
# ---------------------------------------------------------------------------

# The agent may only modify files under these paths (relative to project_dir).
_ALLOWED_ROOTS = ("chapters", "agent_memory.md")


class PatchEditor:
    """Apply unified-diff patches to chapter files and agent memory.

    All writes are validated against an allow-list of paths so the agent
    cannot modify arbitrary files on the filesystem.
    """

    def __init__(self, project_dir: str) -> None:
        self.project_dir = Path(project_dir).resolve()

    def _safe_path(self, raw: str) -> Path | None:
        """Resolve *raw* from a patch header; return None if disallowed."""
        clean = raw.strip().lstrip("/")
        for prefix in ("a/", "b/"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        if clean in ("/dev/null", "dev/null"):
            return None

        resolved = (self.project_dir / clean).resolve()
        try:
            resolved.relative_to(self.project_dir)
        except ValueError:
            return None

        rel = resolved.relative_to(self.project_dir).as_posix()
        if not (rel.startswith("chapters/") or rel == "agent_memory.md"):
            return None

        return resolved

    def apply(self, patch_text: str) -> tuple[bool, str, str]:
        """Apply *patch_text* (unified diff). Returns ``(success, display_path, error)``."""
        patch_text = patch_text.replace("\r\n", "\n")
        try:
            diffs = list(whatthepatch.parse_patch(patch_text))
        except Exception as exc:
            return False, "", f"Could not parse patch: {exc}"

        if not diffs:
            return False, "", "No valid diff found in patch"

        applied: list[str] = []
        for diff in diffs:
            new_raw = getattr(diff.header, "new_path", None) or ""
            if not new_raw or new_raw in ("/dev/null", "dev/null"):
                continue

            target = self._safe_path(new_raw)
            if target is None:
                return False, new_raw, f"Path '{new_raw}' is outside the allowed directories"

            original = (
                target.read_text(encoding="utf-8").replace("\r\n", "\n")
                if target.exists()
                else ""
            )

            try:
                result = whatthepatch.apply_diff(diff, original)
            except Exception as exc:
                return False, str(target), f"Patch application error: {exc}"

            if result is None:
                return False, str(target), "Patch did not apply cleanly (context mismatch)"

            # apply_diff may return a list of lines or a single string depending on version
            if isinstance(result, list):
                result = "\n".join(result)

            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, result)
            applied.append(target.name)

        if not applied:
            return False, "", "No files were modified by the patch"
        return True, ", ".join(applied), ""


# ---------------------------------------------------------------------------
# Shell runner
# ---------------------------------------------------------------------------

class ShellRunner:
    """Run shell commands in a fixed working directory with a timeout."""

    def __init__(self, project_dir: str | Path) -> None:
        self.cwd = Path(project_dir).resolve()

    def run(self, command: str, timeout: int = 30) -> tuple[int, str]:
        """Run *command* and return ``(returncode, combined_output)``."""
        if sys.platform == "win32":
            args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
            )
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
            )
        parts = []
        if result.stdout.strip():
            parts.append(result.stdout.strip())
        if result.stderr.strip():
            parts.append(result.stderr.strip())
        return result.returncode, "\n".join(parts) if parts else "(no output)"


# ---------------------------------------------------------------------------
# Editor helper
# ---------------------------------------------------------------------------

def open_in_editor(content: str) -> str:
    """Open *content* in the user's $EDITOR and return the edited text."""
    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or ("notepad.exe" if sys.platform == "win32" else "nano")
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name
    subprocess.run([editor, tmp_path], check=False)
    edited = Path(tmp_path).read_text(encoding="utf-8")
    Path(tmp_path).unlink(missing_ok=True)
    return edited
