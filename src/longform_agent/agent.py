"""Core agent loop: tool dispatch and agentic turn execution."""
from __future__ import annotations

import json
import re
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich import box

from longform_agent.context import tool_note
from longform_agent.llm import LLMClient
from longform_agent.tools import WebSearch, PatchEditor, ShellRunner, open_in_editor

console = Console()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _show_search_results(results: list[dict]) -> None:
    t = Table(title="Web Search Results", box=box.ROUNDED, show_lines=True)
    t.add_column("#", style="dim", width=3)
    t.add_column("Title", style="bold", max_width=40)
    t.add_column("URL", style="blue", max_width=50)
    t.add_column("Snippet")
    for i, r in enumerate(results, 1):
        t.add_row(str(i), r["title"], r["url"], r["snippet"])
    console.print(t)


def _show_patch(patch: str) -> None:
    console.print()
    console.print(Panel(
        Syntax(patch, "diff", theme="monokai"),
        title="[bold yellow]Proposed Edit[/bold yellow]",
        border_style="yellow",
    ))


def _review_patch(patch: str, editor: PatchEditor) -> tuple[bool, str]:
    """Show the patch to the author and ask for accept/reject/edit."""
    _show_patch(patch)
    while True:
        choice = Prompt.ask(
            "\n[bold]Review[/bold] [dim](a=accept  r=reject  e=edit)[/dim]",
            choices=["a", "r", "e"],
            default="a",
        )
        if choice == "r":
            console.print("[red]✗ Patch rejected[/red]")
            return False, "Patch rejected by the author."
        if choice == "e":
            patch = open_in_editor(patch)
            _show_patch(patch)
            continue
        ok, path, err = editor.apply(patch)
        if ok:
            console.print(f"[green]✓ Applied → {path}[/green]")
            return True, f"Patch accepted and applied to {path}."
        console.print(f"[red]✗ Apply failed: {err}[/red]")
        retry = Prompt.ask("Retry?", choices=["edit", "reject"], default="reject")
        if retry == "reject":
            return False, f"Patch rejected (apply failed: {err})."
        patch = open_in_editor(patch)


def _review_shell(command: str, reason: str) -> bool:
    """Show the shell command to the author and require explicit approval."""
    console.print()
    console.print(Panel(
        f"[bold white]{command}[/bold white]\n\n[dim]{reason}[/dim]",
        title="[bold yellow]⚙ Shell Command[/bold yellow]",
        border_style="yellow",
    ))
    choice = Prompt.ask(
        "[bold]Run this command?[/bold] [dim](y=yes  n=no)[/dim]",
        choices=["y", "n"],
        default="n",
    )
    return choice == "y"


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def _is_memory_only_patch(patch: str) -> bool:
    """Return True if the patch only targets agent_memory.md."""
    targets = re.findall(r'^\+\+\+ b/(.+)$', patch, re.MULTILINE)
    return bool(targets) and all(t.strip() == "agent_memory.md" for t in targets)


def _enforce_cap(path: Path, max_chars: int) -> None:
    """Truncate *path* from the top if it exceeds *max_chars*, preserving the newest content."""
    if not path.exists() or max_chars <= 0:
        return
    content = path.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return
    tail = content[-max_chars:]
    nl = tail.find("\n")
    tail = tail[nl + 1:] if nl != -1 else tail
    path.write_text("<!-- ...truncated... -->\n" + tail, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _execute_tool(
    tool_name: str,
    tool_args: dict,
    search: WebSearch,
    editor: PatchEditor,
    shell: ShellRunner,
    project_dir: Path,
    memory_max_chars: int,
) -> str:
    """Dispatch a tool call and return the result string to inject into context."""

    if tool_name == "web_search":
        console.print(f"\n[bold cyan]🔍 Searching:[/bold cyan] {tool_args.get('query', '')}")
        results = search.search(tool_args.get("query", ""))
        _show_search_results(results)
        return json.dumps(results, ensure_ascii=False)

    if tool_name == "edit_file":
        patch = tool_args.get("patch", "")
        # Memory-only patches are applied silently without author review
        if _is_memory_only_patch(patch):
            ok, _, err = editor.apply(patch)
            if ok:
                _enforce_cap(project_dir / "agent_memory.md", memory_max_chars)
                console.print("[dim]🧠 Memory updated[/dim]")
                return "Memory updated."
            console.print(f"[red]⚠ Memory patch failed: {err}[/red]")
            return f"Memory patch failed: {err}"
        # All other edits require author approval
        _, msg = _review_patch(patch, editor)
        return msg

    if tool_name == "run_shell":
        command = tool_args.get("command", "").strip()
        reason = tool_args.get("reason", "")
        if not command:
            return "No command provided."
        if not _review_shell(command, reason):
            console.print("[red]✗ Shell command rejected[/red]")
            return "Shell command rejected by the author."
        try:
            returncode, output = shell.run(command)
        except Exception as exc:
            console.print(f"[red]⚠ Shell error: {exc}[/red]")
            return f"Shell error: {exc}"
        status = "[green]✓[/green]" if returncode == 0 else f"[red]✗ exit {returncode}[/red]"
        console.print(f"{status} [dim]{command}[/dim]")
        if output != "(no output)":
            console.print(Panel(output, title="Output", border_style="dim"))
        result = f"exit={returncode}\n{output}"
        if returncode != 0:
            result += (
                "\n\nThe command failed. Analyse the error output above and retry "
                "with a corrected command, or explain the issue to the author if "
                "no correction is possible."
            )
        return result

    return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# Agent turn
# ---------------------------------------------------------------------------

def run_turn(
    llm: LLMClient,
    messages: list[dict],
    system: str,
    search: WebSearch,
    editor: PatchEditor,
    shell: ShellRunner,
    project_dir: Path,
    memory_max_chars: int,
    max_tool_iterations: int = 6,
) -> tuple[list[dict], str]:
    """Execute one complete agent turn, handling tool calls in a loop.

    The function sends the initial request, then iterates: executing any tool
    calls returned by the model, injecting the results, and re-querying, until
    the model produces a plain text response or the iteration cap is reached.

    Returns ``(updated_messages, final_text)``.
    """
    final_text = ""

    with console.status("[bold blue]Thinking…[/bold blue]"):
        response = llm.chat(messages, system)

    for _ in range(max_tool_iterations):
        choice = response.choices[0]
        msg = choice.message
        stop_reason = choice.finish_reason

        text = msg.content or ""
        if text:
            final_text = text
            console.print(Rule("[dim]assistant[/dim]", style="dim blue"))
            console.print(Markdown(text))

        # Build a serialisable dict for the message history
        msg_dict: dict = {"role": "assistant", "content": text or None}
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(msg_dict)

        if stop_reason != "tool_calls" or not msg.tool_calls:
            break

        tool_result_messages: list[dict] = []
        notes: list[str] = []

        for tc in msg.tool_calls:
            # Validate JSON args defensively
            try:
                args = json.loads(tc.function.arguments)
                if not isinstance(args, dict):
                    raise ValueError("args not a dict")
            except Exception:
                args = {}
                console.print(
                    f"[red]⚠ Bad tool args for {tc.function.name} — skipping[/red]"
                )

            result_text = _execute_tool(
                tc.function.name,
                args,
                search,
                editor,
                shell,
                project_dir,
                memory_max_chars,
            )
            tool_result_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_text,
            })
            # Inline a compact note into the assistant message for the clean history
            notes.append(tool_note(tc.function.name, args, result_text))

        if notes and messages:
            prev = messages[-1]
            prev["content"] = (
                (prev.get("content") or "") + "\n" + "\n".join(notes)
            ).strip()

        messages.extend(tool_result_messages)

        with console.status("[bold blue]Processing…[/bold blue]"):
            response = llm.chat(messages, system)

    # Catchall: model went silent after tool use — nudge it once
    if not final_text and any(m.get("role") == "tool" for m in messages):
        nudge = messages + [{
            "role": "user",
            "content": "Please tell the author what you just did and what the result was.",
        }]
        with console.status("[bold blue]Processing…[/bold blue]"):
            resp = llm.chat(nudge, system)
        text = (resp.choices[0].message.content or "").strip()
        if text:
            final_text = text
            console.print(Rule("[dim]assistant[/dim]", style="dim blue"))
            console.print(Markdown(text))
            messages.append({"role": "assistant", "content": text})

    return messages, final_text
